"""落单一致性测试：建议列、落单结果、缺料贴必须是同一快照的同一套数。

覆盖：
- 新鲜建议列落单成功，落单前后数字逐行一致；
- 改了结存后旧建议列落单 → 409，不产生落成单（旧数、新数两头都不算成功）；
- 重新生成建议列后可按新结存落单；
- 重复落单 → 409；旧单数字永不被后续变化改动；
- 缺料贴与最新快照同源；
- 结存调整带动 version。
"""
import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite://")  # 必须在导入 app.* 之前
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.models import Ingredient, PrepRun
from app.services.seed import seed_if_empty


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    db = TestingSession()
    seed_if_empty(db)
    db.close()

    def override_get_db():
        s = TestingSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app), TestingSession
    app.dependency_overrides.clear()


def _by_ing(snapshot):
    return {l["ingredient_id"]: l for l in snapshot["prep_lines"]}


def test_fresh_suggestion_places_with_identical_numbers(client):
    c, _ = client
    sug = c.post("/api/prep/suggest?order_id=1").json()
    assert sug["status"] == "suggested"

    placed = c.post("/api/prep/place", json={"run_id": sug["id"]})
    assert placed.status_code == 200
    out = placed.json()
    assert out["status"] == "placed"
    # 落单结果就是建议列本身：逐行需求/结存/缺料完全一致
    assert out["prep_lines"] == sug["prep_lines"]
    assert out["shortages"] == sug["shortages"]
    assert out["stats"] == sug["stats"]
    assert out["placed_at"]


def test_stock_change_rejects_stale_suggestion_without_placing(client):
    c, Session = client
    sug = c.post("/api/prep/suggest?order_id=1").json()
    old_lines = _by_ing(sug)

    # 改结存（模拟库存页调整）
    pork = c.get("/api/inventory").json()[0]  # 五花肉，种子结存 8
    r = c.patch(f"/api/inventory/{pork['id']}", json={"stock_qty": 0.1})
    assert r.status_code == 200
    assert r.json()["version"] == pork["version"] + 1

    # 拿旧建议列落单 → 拒绝，且库里没有任何落成单
    resp = c.post("/api/prep/place", json={"run_id": sug["id"]})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "STALE_SUGGESTION"

    db = Session()
    placed = db.scalars(select(PrepRun).where(PrepRun.status == "placed")).all()
    assert placed == []
    stale = db.get(PrepRun, sug["id"])
    assert stale.status == "suggested"  # 旧快照没有被偷偷改写或置位
    # 旧快照里存的数字保持原样（未被为对上新结存而篡改）
    import json
    assert json.loads(stale.result_json)["prep_lines"] == sug["prep_lines"]
    db.close()


def test_refresh_then_place_uses_new_stock_and_all_views_align(client):
    c, _ = client
    old = c.post("/api/prep/suggest?order_id=1").json()
    pork_id = next(l["ingredient_id"] for l in old["prep_lines"] if l["ingredient_name"] == "五花肉")

    c.patch(f"/api/inventory/{pork_id}", json={"stock_qty": 0.1})
    assert c.post("/api/prep/place", json={"run_id": old["id"]}).status_code == 409

    # 刷新建议列 → 缺料贴立刻与新建议同源
    new = c.post("/api/prep/suggest?order_id=1").json()
    new_pork = _by_ing(new)[pork_id]
    assert new_pork["stock_qty"] == 0.1
    assert new_pork["shortage"] == round(new_pork["need_qty"] - 0.1, 3)

    sh = c.get("/api/prep/shortages?order_id=1").json()
    assert sh["run_id"] == new["id"]
    assert sh["shortages"] == new["shortages"]

    out = c.post("/api/prep/place", json={"run_id": new["id"]}).json()
    assert out["status"] == "placed"
    assert out["prep_lines"] == new["prep_lines"]
    # 缺料贴在落单后仍指向同一份数字
    sh2 = c.get("/api/prep/shortages?order_id=1").json()
    assert sh2["run_id"] == new["id"]
    assert sh2["status"] == "placed"
    assert sh2["shortages"] == out["shortages"]


def test_duplicate_place_rejected_and_old_order_never_rewritten(client):
    c, Session = client
    sug = c.post("/api/prep/suggest?order_id=1").json()
    out = c.post("/api/prep/place", json={"run_id": sug["id"]}).json()

    again = c.post("/api/prep/place", json={"run_id": sug["id"]})
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "ALREADY_PLACED"

    # 再改结存：该订单已有落成单，重新生成建议列也拒绝（旧单不被新快照盖掉）
    pork_id = out["prep_lines"][0]["ingredient_id"]
    c.patch(f"/api/inventory/{pork_id}", json={"stock_qty": 999})
    re_sug = c.post("/api/prep/suggest?order_id=1")
    assert re_sug.status_code == 409
    assert re_sug.json()["detail"]["code"] == "ORDER_PLACED"

    # 已落成的旧单数字原封不动
    db = Session()
    placed = db.scalars(select(PrepRun).where(PrepRun.status == "placed")).all()
    assert len(placed) == 1
    import json
    assert json.loads(placed[0].result_json)["prep_lines"] == out["prep_lines"]
    assert c.get("/api/prep/latest?order_id=1").json()["prep_lines"] == out["prep_lines"]
    db.close()


def test_unrelated_stock_change_keeps_suggestion_fresh(client):
    # 改一个 BOM 根本没引用的原料结存，不应让本订单的建议列过期。
    c, Session = client
    sug = c.post("/api/prep/suggest?order_id=1").json()

    db = Session()
    db.add(Ingredient(code="I-X", name="无关调料", unit="kg", stock_qty=1.0, version=0))
    db.commit(); db.close()
    xid = next(r["id"] for r in c.get("/api/inventory").json() if r["code"] == "I-X")
    c.patch(f"/api/inventory/{xid}", json={"stock_qty": 99.0})

    out = c.post("/api/prep/place", json={"run_id": sug["id"]})
    assert out.status_code == 200
    assert out.json()["prep_lines"] == sug["prep_lines"]


def test_place_unknown_run_404(client):
    c, _ = client
    resp = c.post("/api/prep/place", json={"run_id": 9999})
    assert resp.status_code == 404


def test_patch_stock_validation(client):
    c, _ = client
    iid = c.get("/api/inventory").json()[0]["id"]
    assert c.patch(f"/api/inventory/{iid}", json={"stock_qty": -1}).status_code == 422
    assert c.patch("/api/inventory/9999", json={"stock_qty": 1}).status_code == 404


def test_suggest_refresh_failure_blocks_nothing_silent(client):
    # 不存在的订单刷新建议列直接 404，而不是静默给出一份数
    c, _ = client
    assert c.post("/api/prep/suggest?order_id=42").status_code == 404
    assert c.post("/api/prep/place", json={"run_id": 42}).status_code == 404
