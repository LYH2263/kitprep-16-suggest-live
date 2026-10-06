"""备料建议快照与落单的一致性服务。

唯一事实源：build_snapshot()。建议列、落单结果、缺料便利贴都来自它的同一次输出。
快照带签名（订单行 + BOM 行 + 各原料结存及版本）。落单时在行锁内重算签名：
- 一致 → 原子置为 placed，存的就是建议列那份数字，不改一行业务数据；
- 不一致 → 拒绝（409），绝不落成单；
- 已落单 → 拒绝重复落单，也绝不回改旧单。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import BomLine, Ingredient, KitchenOrder, OrderLine, PrepRun
from app.services.bom_engine import explode_and_merge, result_to_dict


class SuggestionError(Exception):
    """落单被拒。code: NOT_FOUND / ALREADY_PLACED / STALE_SUGGESTION / ORDER_PLACED。"""

    def __init__(self, code: str, message: str, http_status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def _load_inputs(db: Session, order_id: int, lock: bool) -> tuple[
        KitchenOrder, list[dict], list[dict], dict[int, dict]]:
    order = db.get(KitchenOrder, order_id)
    if order is None:
        raise SuggestionError("NOT_FOUND", "订单不存在", 404)
    ol_stmt = select(OrderLine).where(OrderLine.order_id == order_id)
    bom_stmt = select(BomLine)
    ing_stmt = select(Ingredient).order_by(Ingredient.id)
    if lock:
        # 行锁：与结存调整/BOM 编辑互斥，保证「重算签名 → 置 placed」之间输入不会变。
        ol_stmt = ol_stmt.with_for_update()
        bom_stmt = bom_stmt.with_for_update()
        ing_stmt = ing_stmt.with_for_update()
    ols = [{"dish_id": l.dish_id, "portions": l.portions} for l in db.scalars(ol_stmt).all()]
    bom = [{"dish_id": b.dish_id, "ingredient_id": b.ingredient_id,
            "qty_per_portion": b.qty_per_portion}
           for b in db.scalars(bom_stmt).all()]
    ings = {i.id: {"code": i.code, "name": i.name, "unit": i.unit,
                   "stock_qty": i.stock_qty, "version": i.version}
            for i in db.scalars(ing_stmt).all()}
    return order, ols, bom, ings


def compute_signature(order_lines: list[dict], bom_lines: list[dict],
                      ingredients: dict[int, dict]) -> str:
    # 签名边界 = 这张订单真正的输入：本订单菜品的订单行/BOM 行，及其引用原料的结存。
    dish_ids = {l["dish_id"] for l in order_lines}
    relevant_bom = [b for b in bom_lines if b["dish_id"] in dish_ids]
    referenced = {b["ingredient_id"] for b in relevant_bom}
    payload = {
        "order_lines": sorted((l["dish_id"], l["portions"]) for l in order_lines),
        "bom_lines": sorted((l["dish_id"], l["ingredient_id"], repr(float(l["qty_per_portion"])))
                            for l in relevant_bom),
        "stock": {str(iid): [repr(float(ingredients[iid]["stock_qty"])),
                             int(ingredients[iid]["version"])]
                  for iid in sorted(referenced)},
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_snapshot(db: Session, order_id: int, lock: bool = False) -> dict:
    """同一瞬间算出建议列 + 缺料 + 统计 + 签名。所有展示端只能用这一份。"""
    order, ols, bom, ings = _load_inputs(db, order_id, lock=lock)
    signature = compute_signature(ols, bom, ings)
    calc_ings = {iid: {k: v for k, v in ing.items() if k != "version"}
                 for iid, ing in ings.items()}
    result = result_to_dict(explode_and_merge(ols, bom, calc_ings))
    result["order"] = {"id": order.id, "code": order.code, "outlet": order.outlet}
    result["signature"] = signature
    return result


def create_suggest_run(db: Session, order_id: int) -> PrepRun:
    """刷新建议列：按当前结存现算一份新快照落库（旧快照保留、永不变造）。

    订单已有落成单时拒绝再出建议列——旧单冻结，要改只能开新订单，
    避免新建议快照在 latest 视图里把旧单盖掉。
    """
    order = db.get(KitchenOrder, order_id)
    if order is None:
        raise SuggestionError("NOT_FOUND", "订单不存在", 404)
    existed = db.scalars(
        select(PrepRun.id).where(PrepRun.order_id == order_id,
                                 PrepRun.status == "placed").limit(1)
    ).first()
    if existed is not None:
        raise SuggestionError("ORDER_PLACED", "该订单已有落成的备料单，旧单不可改；请开新订单")
    snapshot = build_snapshot(db, order_id, lock=False)
    run = PrepRun(
        order_id=order_id,
        created_at=datetime.utcnow(),
        status="suggested",
        signature=snapshot["signature"],
        result_json=json.dumps(snapshot, ensure_ascii=False),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def place_run(db: Session, run_id: int) -> PrepRun:
    """按建议快照落单。过期/已落单一律拒绝；成功则冻结为 placed，数字就是建议列本身。"""
    try:
        run = db.get(PrepRun, run_id, with_for_update=True)
        if run is None:
            raise SuggestionError("NOT_FOUND", "建议快照不存在，请刷新建议列", 404)
        if run.status == "placed":
            raise SuggestionError("ALREADY_PLACED", "该建议列已落成单，请勿重复落单")
        # 先锁订单行：同一订单的并发落单在此串行，第二个等锁释放后必能看到首张落成单。
        db.get(KitchenOrder, run.order_id, with_for_update=True)
        # 同一订单只允许一张落成单，后到的建议快照即使数字恰好没变也拒绝。
        existed = db.scalars(
            select(PrepRun.id).where(PrepRun.order_id == run.order_id,
                                     PrepRun.status == "placed").limit(1)
        ).first()
        if existed is not None:
            raise SuggestionError("ORDER_PLACED", "该订单已有落成的备料单")

        # 锁内按当前事实重算。签名一致才允许落单——不重算另一套数，也不吃旧数。
        current = build_snapshot(db, run.order_id, lock=True)
        if current["signature"] != run.signature:
            raise SuggestionError(
                "STALE_SUGGESTION",
                "建议列已过期（订单行、BOM 或结存已变化），请刷新建议列后再落单",
            )

        run.status = "placed"
        run.placed_at = datetime.utcnow()
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(run)
    return run


def run_to_dict(run: PrepRun) -> dict:
    data = json.loads(run.result_json)
    data.pop("signature", None)
    return {
        "id": run.id,
        "status": run.status,
        "created_at": run.created_at.isoformat() + "Z" if run.created_at else None,
        "placed_at": run.placed_at.isoformat() + "Z" if run.placed_at else None,
        **data,
    }
