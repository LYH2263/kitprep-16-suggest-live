"""备料建议列的唯一计算入口与落单事务。

建议列（备料台）、缺料贴、落单结果全部来自这里同一套取数；
任何展示接口都不允许各自重算。
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.models import BomLine, Ingredient, KitchenOrder, OrderLine, PrepRun
from app.services.bom_engine import explode_and_merge, result_to_dict, snapshot_mismatches

DRAFT = "draft"
ISSUED = "issued"


class PrepError(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class RunNotFoundError(PrepError):
    pass


class StaleRunError(PrepError):
    """建议列快照已过期或与落单当时数据不一致——拒绝落单。"""

    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def _load_inputs(db: Session, order_id: int):
    order = db.get(KitchenOrder, order_id)
    if not order:
        raise RunNotFoundError("订单不存在")
    ols = [{"dish_id": l.dish_id, "portions": l.portions}
           for l in db.scalars(select(OrderLine).where(OrderLine.order_id == order_id)).all()]
    bom = [{"dish_id": b.dish_id, "ingredient_id": b.ingredient_id, "qty_per_portion": b.qty_per_portion}
           for b in db.scalars(select(BomLine)).all()]
    ings = {i.id: {"code": i.code, "name": i.name, "unit": i.unit, "stock_qty": i.stock_qty}
            for i in db.scalars(select(Ingredient)).all()}
    return order, ols, bom, ings


def compute_result(db: Session, order_id: int) -> dict:
    """全系统唯一的建议数计算：订单行 × BOM，对照当前结存。"""
    order, ols, bom, ings = _load_inputs(db, order_id)
    result = result_to_dict(explode_and_merge(ols, bom, ings))
    result["order"] = {"id": order.id, "code": order.code,
                       "outlet": order.outlet, "status": order.status}
    return result


def serialize_run(run: PrepRun) -> dict:
    data = json.loads(run.result_json)
    return {
        "id": run.id,
        "status": run.status,
        "version": run.version,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "refreshed_at": run.refreshed_at.isoformat() if run.refreshed_at else None,
        "issued_at": run.issued_at.isoformat() if run.issued_at else None,
        **data,
    }


def _get_draft(db: Session, order_id: int) -> PrepRun | None:
    # 部分唯一索引保证每订单至多一条 draft
    return db.scalars(
        select(PrepRun).where(PrepRun.order_id == order_id, PrepRun.status == DRAFT)
    ).first()


def _store_draft(db: Session, order_id: int, run: PrepRun | None) -> PrepRun:
    result = compute_result(db, order_id)
    now = datetime.utcnow()
    payload = json.dumps(result, ensure_ascii=False)
    if run is None:
        run = PrepRun(order_id=order_id, created_at=now, refreshed_at=now,
                      status=DRAFT, version=1, result_json=payload)
        db.add(run)
    else:
        run.result_json = payload
        run.version += 1
        run.refreshed_at = now
    try:
        db.commit()
    except IntegrityError:
        # 两个客户端首次并发刷新、同时插入 draft：部分唯一索引兜底，
        # 回退到已存在的 draft 上原地更新，不抛 500
        db.rollback()
        existing = _get_draft(db, order_id)
        if existing is None:
            raise
        return _store_draft(db, order_id, existing)
    db.refresh(run)
    return run


def get_or_create_draft(db: Session, order_id: int) -> PrepRun:
    run = _get_draft(db, order_id)
    if run is not None:
        return run
    if not db.get(KitchenOrder, order_id):
        raise RunNotFoundError("订单不存在")
    return _store_draft(db, order_id, None)


def refresh_draft(db: Session, order_id: int) -> PrepRun:
    """显式刷新：按当前结存/订单重算，原地更新 draft 并 version+1。"""
    if not db.get(KitchenOrder, order_id):
        raise RunNotFoundError("订单不存在")
    return _store_draft(db, order_id, _get_draft(db, order_id))


def issue_draft(db: Session, run_id: int, expected_version: int, order_id: int) -> PrepRun:
    """落单：版本号 + 落单当时重算 双重校验，全有或全无。

    - 旧 version 或结存/需求已变 → StaleRunError，库存不动；
    - 完全一致才按需求全额扣减结存（允许为负），并把快照冻结为 issued。
    """
    run = db.scalars(select(PrepRun).where(PrepRun.id == run_id).with_for_update()).first()
    if run is None or run.order_id != order_id:
        db.rollback()
        raise RunNotFoundError("备料单不存在")
    if run.status != DRAFT:
        db.rollback()
        raise StaleRunError("already_issued", f"备料单 #{run_id} 已落单，旧单不可更改或重复落单")
    if run.version != expected_version:
        db.rollback()
        raise StaleRunError("version_stale",
                            "建议列已过期（结存或订单在刷新后发生过变化），请重新刷新后再落单")

    # 先锁全部结存行，再用当前数据重算，使重算与扣减处于同一锁定瞬间，
    # 并与库存 PATCH 串行
    db.scalars(select(Ingredient).with_for_update()).all()
    frozen = json.loads(run.result_json)
    current = compute_result(db, order_id)
    diffs = snapshot_mismatches(frozen["prep_lines"], current["prep_lines"])
    if diffs:
        db.rollback()
        names = ", ".join(sorted({
            d.get("ingredient_name") or f"原料#{d['ingredient_id']}" for d in diffs
        }))
        raise StaleRunError(
            "snapshot_changed",
            f"落单当时重算与建议列不一致（{names}），已拒绝落单；请刷新建议列后再试",
        )

    need_by_id = {int(l["ingredient_id"]): float(l["need_qty"]) for l in current["prep_lines"]}
    for ing in db.scalars(select(Ingredient).where(Ingredient.id.in_(need_by_id))).all():
        ing.stock_qty = round(float(ing.stock_qty) - need_by_id[ing.id], 3)
    run.status = ISSUED
    run.issued_at = datetime.utcnow()
    db.commit()
    db.refresh(run)
    return run
