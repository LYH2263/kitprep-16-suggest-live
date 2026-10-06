from app.services.bom_engine import explode_and_merge, snapshot_mismatches

def test_explode_merge():
    order_lines = [{"dish_id": 1, "portions": 10}, {"dish_id": 2, "portions": 5}]
    bom = [
        {"dish_id": 1, "ingredient_id": 1, "qty_per_portion": 0.2},
        {"dish_id": 1, "ingredient_id": 2, "qty_per_portion": 0.1},
        {"dish_id": 2, "ingredient_id": 1, "qty_per_portion": 0.3},
    ]
    ings = {
        1: {"code": "A", "name": "肉", "unit": "kg", "stock_qty": 1.0},
        2: {"code": "B", "name": "米", "unit": "kg", "stock_qty": 5.0},
    }
    lines = explode_and_merge(order_lines, bom, ings)
    by_id = {l.ingredient_id: l for l in lines}
    assert by_id[1].need_qty == 3.5  # 10*0.2 + 5*0.3
    assert by_id[1].shortage == 2.5
    assert by_id[2].need_qty == 1.0
    assert by_id[2].shortage == 0.0

def test_no_negative_shortage():
    order_lines = [{"dish_id": 1, "portions": 1}]
    bom = [{"dish_id": 1, "ingredient_id": 1, "qty_per_portion": 1.0}]
    ings = {1: {"code": "A", "name": "油", "unit": "L", "stock_qty": 10.0}}
    lines = explode_and_merge(order_lines, bom, ings)
    assert lines[0].shortage == 0.0


def _line(iid, name, need, stock, shortage):
    return {"ingredient_id": iid, "ingredient_code": f"I{iid}", "ingredient_name": name,
            "unit": "kg", "need_qty": need, "stock_qty": stock, "shortage": shortage}


def test_snapshot_identical():
    frozen = [_line(1, "肉", 3.0, 1.0, 2.0)]
    assert snapshot_mismatches(frozen, [dict(_line(1, "肉", 3.0, 1.0, 2.0))]) == []


def test_snapshot_stock_changed_detected():
    frozen = [_line(1, "肉", 3.0, 1.0, 2.0)]
    # 结存改了：stock 与 shortage 都应被报出
    current = [_line(1, "肉", 3.0, 2.0, 1.0)]
    diffs = snapshot_mismatches(frozen, current)
    assert len(diffs) == 1 and diffs[0]["ingredient_id"] == 1
    assert set(diffs[0]["fields"]) == {"stock_qty", "shortage"}


def test_snapshot_need_changed_detected():
    frozen = [_line(1, "肉", 3.0, 1.0, 2.0)]
    current = [_line(1, "肉", 4.0, 1.0, 3.0)]
    diffs = snapshot_mismatches(frozen, current)
    assert set(diffs[0]["fields"]) == {"need_qty", "shortage"}


def test_snapshot_added_and_removed_ingredient():
    frozen = [_line(1, "肉", 3.0, 1.0, 2.0), _line(2, "米", 1.0, 5.0, 0.0)]
    current = [_line(1, "肉", 3.0, 1.0, 2.0), _line(3, "面", 2.0, 0.0, 2.0)]
    diffs = snapshot_mismatches(frozen, current)
    by_id = {d["ingredient_id"]: d["reason"] for d in diffs}
    assert by_id == {2: "missing_ingredient", 3: "new_ingredient"}
