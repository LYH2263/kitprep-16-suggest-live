import math

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import Ingredient

router = APIRouter(prefix="/inventory", tags=["inventory"])


class StockIn(BaseModel):
    stock_qty: float


def _get_all(db: Session):
    return [{"id": r.id, "code": r.code, "name": r.name, "unit": r.unit,
             "stock_qty": r.stock_qty, "version": r.version}
            for r in db.scalars(select(Ingredient).order_by(Ingredient.id)).all()]


@router.get("")
def list_inventory(db: Session = Depends(get_db)):
    return _get_all(db)


@router.patch("/{ingredient_id}")
def update_stock(ingredient_id: int, body: StockIn, db: Session = Depends(get_db)):
    """调整结存。版本号原子 +1：任何未刷新的旧建议快照都会因此在落单时被拒。"""
    if not math.isfinite(body.stock_qty) or body.stock_qty < 0:
        raise HTTPException(422, "结存必须是非负有限数")
    ing = db.get(Ingredient, ingredient_id, with_for_update=True)
    if ing is None:
        raise HTTPException(404, "原料不存在")
    ing.stock_qty = body.stock_qty
    ing.version += 1
    db.commit()
    db.refresh(ing)
    return {"id": ing.id, "code": ing.code, "name": ing.name, "unit": ing.unit,
            "stock_qty": ing.stock_qty, "version": ing.version}
