"""Central kitchen BOM explode: order lines × BOM qty, merge ingredients, shortage = need - stock."""
from __future__ import annotations
from dataclasses import asdict, dataclass

@dataclass
class NeedLine:
    ingredient_id: int
    ingredient_code: str
    ingredient_name: str
    unit: str
    need_qty: float
    stock_qty: float
    shortage: float

def explode_and_merge(
    order_lines: list[dict],
    bom_lines: list[dict],
    ingredients: dict[int, dict],
) -> list[NeedLine]:
    """order_lines: dish_id, portions; bom_lines: dish_id, ingredient_id, qty_per_portion."""
    need: dict[int, float] = {}
    for ol in order_lines:
        for bl in bom_lines:
            if bl["dish_id"] != ol["dish_id"]:
                continue
            need[bl["ingredient_id"]] = need.get(bl["ingredient_id"], 0.0) + ol["portions"] * bl["qty_per_portion"]
    lines: list[NeedLine] = []
    for iid, qty in sorted(need.items()):
        ing = ingredients[iid]
        stock = float(ing.get("stock_qty", 0))
        shortage = max(0.0, qty - stock)
        lines.append(NeedLine(
            ingredient_id=iid,
            ingredient_code=ing["code"],
            ingredient_name=ing["name"],
            unit=ing.get("unit", ""),
            need_qty=round(qty, 3),
            stock_qty=round(stock, 3),
            shortage=round(shortage, 3),
        ))
    return lines

def result_to_dict(lines: list[NeedLine]) -> dict:
    return {
        "prep_lines": [asdict(l) for l in lines],
        "shortages": [asdict(l) for l in lines if l.shortage > 0],
        "stats": {
            "ingredient_count": len(lines),
            "shortage_count": sum(1 for l in lines if l.shortage > 0),
            "total_shortage_qty": round(sum(l.shortage for l in lines), 3),
        },
    }

# 建议列快照与落单当时重算逐行比对的字段；三者任一不同即两套数
_SNAPSHOT_FIELDS = ("need_qty", "stock_qty", "shortage")


def snapshot_mismatches(frozen: list[dict], current: list[dict]) -> list[dict]:
    """比对冻结建议列与落单当时重算结果。

    返回所有不一致项（原料缺失/多出，或 need/stock/shortage 数值不同）。
    返回空列表表示两个快照是同一瞬间的同一套数，可以落单。
    """
    frozen_by_id = {int(l["ingredient_id"]): l for l in frozen}
    current_by_id = {int(l["ingredient_id"]): l for l in current}
    diffs: list[dict] = []
    for iid in sorted(frozen_by_id.keys() | current_by_id.keys()):
        f = frozen_by_id.get(iid)
        c = current_by_id.get(iid)
        if f is None:
            diffs.append({"ingredient_id": iid, "reason": "new_ingredient",
                          "ingredient_name": c.get("ingredient_name"), "current": c, "frozen": None})
            continue
        if c is None:
            diffs.append({"ingredient_id": iid, "reason": "missing_ingredient",
                          "ingredient_name": f.get("ingredient_name"), "frozen": f, "current": None})
            continue
        changed = {}
        for field in _SNAPSHOT_FIELDS:
            fv = round(float(f[field]), 3)
            cv = round(float(c[field]), 3)
            if fv != cv:
                changed[field] = {"frozen": fv, "current": cv}
        if changed:
            diffs.append({"ingredient_id": iid, "reason": "qty_changed",
                          "ingredient_name": f.get("ingredient_name"), "fields": changed})
    return diffs
