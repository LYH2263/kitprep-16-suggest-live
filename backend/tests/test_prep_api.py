"""端到端：建议列 / 缺料贴 / 落单 同源一致性。

需真实 Postgres（本地 localhost:5451 或 compose 内 db:5432，由 DATABASE_URL 决定）。
每个用例使用 uuid 后缀的独立菜品/原料/订单，结束时清理，不碰种子数据。
"""
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models.models import BomLine, Dish, Ingredient, KitchenOrder, OrderLine, PrepRun


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


class World:
    """单菜品 + 若干原料/BOM 的专用测试世界。"""

    def __init__(self, portions: int, ings: list[tuple], boms: list[tuple]):
        suffix = uuid4().hex[:8]
        self.db = SessionLocal()
        self.dish = Dish(code=f"T-D-{suffix}", name=f"测试菜{suffix}", portion_unit="份")
        self.db.add(self.dish)
        self.db.flush()
        self.ing = {}
        for key, name, unit, stock in ings:
            i = Ingredient(code=f"T-I-{suffix}-{key}", name=f"{name}{suffix}", unit=unit, stock_qty=stock)
            self.db.add(i)
            self.db.flush()
            self.ing[key] = i
        for key, qty in boms:
            self.db.add(BomLine(dish_id=self.dish.id, ingredient_id=self.ing[key].id, qty_per_portion=qty))
        self.order = KitchenOrder(code=f"T-KO-{suffix}", outlet="测试门店", status="open")
        self.db.add(self.order)
        self.db.flush()
        self.db.add(OrderLine(order_id=self.order.id, dish_id=self.dish.id, portions=portions))
        self.db.commit()
        self.oid = self.order.id

    def stock(self, key) -> float:
        self.db.expire_all()
        return round(float(self.db.get(Ingredient, self.ing[key].id).stock_qty), 3)

    def set_stock_direct(self, key, v):
        i = self.db.get(Ingredient, self.ing[key].id)
        i.stock_qty = v
        self.db.commit()

    def add_bom(self, key, qty):
        self.db.add(BomLine(dish_id=self.dish.id, ingredient_id=self.ing[key].id, qty_per_portion=qty))
        self.db.commit()

    def get_run(self, run_id) -> dict:
        row = self.db.get(PrepRun, run_id)
        return {"status": row.status, "version": row.version,
                "result_json": json.loads(row.result_json),
                "issued_at": row.issued_at}

    def cleanup(self):
        self.db.query(PrepRun).filter(PrepRun.order_id == self.oid).delete()
        self.db.query(OrderLine).filter(OrderLine.order_id == self.oid).delete()
        bom_ids = [b.id for b in self.db.query(BomLine).filter(BomLine.dish_id == self.dish.id).all()]
        if bom_ids:
            self.db.query(BomLine).filter(BomLine.id.in_(bom_ids)).delete(synchronize_session=False)
        self.db.delete(self.db.get(KitchenOrder, self.oid))
        for i in self.ing.values():
            self.db.delete(self.db.get(Ingredient, i.id))
        self.db.delete(self.db.get(Dish, self.dish.id))
        self.db.commit()
        self.db.close()


def _world(portions=10):
    # a: 每份 0.2，结存 1 → 需求 2、缺料 1；b: 每份 0.1，结存 10 → 需求 1、不缺
    w = World(
        portions,
        ings=[("a", "测试肉", "kg", 1.0), ("b", "测试米", "kg", 10.0)],
        boms=[("a", 0.2), ("b", 0.1)],
    )
    return w


def test_refresh_and_shortages_same_snapshot(client):
    w = _world()
    try:
        r1 = client.post(f"/api/prep/run?order_id={w.oid}").json()
        assert r1["version"] == 1 and r1["status"] == "draft"
        assert [(s["ingredient_id"], s["shortage"]) for s in r1["shortages"]] == \
               [(w.ing["a"].id, 1.0)]

        # latest / shortages 与刷新结果同源同数
        latest = client.get(f"/api/prep/latest?order_id={w.oid}").json()
        assert latest["id"] == r1["id"] and latest["version"] == 1
        sh = client.get(f"/api/prep/shortages?order_id={w.oid}").json()
        assert sh["id"] == r1["id"] and sh["version"] == 1
        assert sh["shortages"] == r1["shortages"]

        # 改了结存：latest 仍返回冻结旧数（不偷偷重算）
        client.patch(f"/api/inventory/{w.ing['a'].id}", json={"stock_qty": 5.0})
        latest = client.get(f"/api/prep/latest?order_id={w.oid}").json()
        assert latest["version"] == 1
        assert latest["prep_lines"][0]["stock_qty"] == 1.0

        # 显式刷新后：同一 draft 行、version+1、缺料按新结存消失，三处同源
        r2 = client.post(f"/api/prep/run?order_id={w.oid}").json()
        assert r2["id"] == r1["id"] and r2["version"] == 2
        assert r2["shortages"] == []
        sh2 = client.get(f"/api/prep/shortages?order_id={w.oid}").json()
        assert sh2["id"] == r2["id"] and sh2["version"] == 2 and sh2["shortages"] == []
    finally:
        w.cleanup()


def test_stale_snapshot_and_version_rejected(client):
    w = _world()
    try:
        r = client.post(f"/api/prep/run?order_id={w.oid}").json()
        # 刷新后结存被改：用旧建议列落单 → 落单当时重算不一致，409，库存不动
        client.patch(f"/api/inventory/{w.ing['a'].id}", json={"stock_qty": 5.0})
        resp = client.post(f"/api/prep/{r['id']}/issue?order_id={w.oid}", json={"version": 1})
        assert resp.status_code == 409
        assert resp.json()["detail"]["reason"] == "snapshot_changed"
        assert w.stock("a") == 5.0
        assert w.get_run(r["id"])["status"] == "draft"

        # 再刷一次产生 v2；拿着 v1 落单 → 版本号先拦下
        client.post(f"/api/prep/run?order_id={w.oid}")
        resp = client.post(f"/api/prep/{r['id']}/issue?order_id={w.oid}", json={"version": 1})
        assert resp.status_code == 409
        assert resp.json()["detail"]["reason"] == "version_stale"
        assert w.stock("a") == 5.0
    finally:
        w.cleanup()


def test_issue_deducts_full_need_and_locks_order(client):
    w = _world()
    try:
        r = client.post(f"/api/prep/run?order_id={w.oid}").json()
        resp = client.post(f"/api/prep/{r['id']}/issue?order_id={w.oid}", json={"version": 1})
        assert resp.status_code == 200, resp.text
        out = resp.json()

        # 落单结果与建议列逐项一致
        assert out["id"] == r["id"] and out["status"] == "issued"
        assert out["prep_lines"] == r["prep_lines"]
        assert out["shortages"] == r["shortages"]

        # 按需求全额扣减结存（缺料的 a 扣成负数，即待采购量）
        assert w.stock("a") == -1.0
        assert w.stock("b") == 9.0

        # 旧单不可重复落
        again = client.post(f"/api/prep/{r['id']}/issue?order_id={w.oid}", json={"version": 1})
        assert again.status_code == 409
        assert again.json()["detail"]["reason"] == "already_issued"
        assert w.stock("a") == -1.0
    finally:
        w.cleanup()


def test_issued_run_immutable_new_draft_after_refresh(client):
    w = _world()
    try:
        r = client.post(f"/api/prep/run?order_id={w.oid}").json()
        frozen = r["prep_lines"]
        assert client.post(f"/api/prep/{r['id']}/issue?order_id={w.oid}",
                           json={"version": 1}).status_code == 200

        # 落单后改结存：旧单 result_json 原样冻结
        client.patch(f"/api/inventory/{w.ing['a'].id}", json={"stock_qty": 50.0})
        old = w.get_run(r["id"])
        assert old["status"] == "issued" and old["issued_at"] is not None
        assert old["result_json"]["prep_lines"] == frozen

        # 刷新产生新的 draft 行（新 id，version 从 1 起）
        new = client.post(f"/api/prep/run?order_id={w.oid}").json()
        assert new["id"] != r["id"] and new["status"] == "draft" and new["version"] == 1
        assert new["prep_lines"][0]["stock_qty"] == 50.0  # PATCH 为绝对设值

        # 旧单依然不可落、不可改
        resp = client.post(f"/api/prep/{r['id']}/issue?order_id={w.oid}", json={"version": 1})
        assert resp.status_code == 409
        assert w.get_run(r["id"])["result_json"]["prep_lines"] == frozen

        # 新 draft 可正常落单，按新快照扣减
        assert client.post(f"/api/prep/{new['id']}/issue?order_id={w.oid}",
                           json={"version": 1}).status_code == 200
        assert w.stock("a") == 48.0
        assert w.get_run(r["id"])["result_json"]["prep_lines"] == frozen
    finally:
        w.cleanup()


def test_bom_change_after_refresh_blocks_issue(client):
    w = _world()
    try:
        r = client.post(f"/api/prep/run?order_id={w.oid}").json()
        # 订单不变，但 BOM 新增原料（需求侧变化），落单当场重算必须发现
        w.add_bom("b", 0.5)  # b 需求从 1 变 6
        resp = client.post(f"/api/prep/{r['id']}/issue?order_id={w.oid}", json={"version": 1})
        assert resp.status_code == 409
        assert resp.json()["detail"]["reason"] == "snapshot_changed"
        assert w.stock("b") == 10.0
    finally:
        w.cleanup()


def test_inventory_patch_validation(client):
    w = _world()
    try:
        assert client.patch(f"/api/inventory/{w.ing['a'].id}", json={"stock_qty": -1}).status_code == 422
        assert w.stock("a") == 1.0
    finally:
        w.cleanup()
