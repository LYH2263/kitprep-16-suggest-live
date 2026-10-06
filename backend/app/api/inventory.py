import math

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import Ingredient

router = APIRouter(prefix="/inventory", tags=["inventory"])


class StockIn(BaseModel):
    stock_qty: float = Field(..., ge=0)

    @field_validator("stock_qty")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("结存必须是有限数字")
        return v


@router.get("")
def list_inventory(db: Session = Depends(get_db)):
    return [{"id": r.id, "code": r.code, "name": r.name, "unit": r.unit, "stock_qty": r.stock_qty}
            for r in db.scalars(select(Ingredient).order_by(Ingredient.id)).all()]


@router.patch("/{ingredient_id}")
def update_stock(ingredient_id: int, body: StockIn, db: Session = Depends(get_db)):
    ing = db.get(Ingredient, ingredient_id)
    if not ing:
        raise HTTPException(404, "原料不存在")
    ing.stock_qty = body.stock_qty
    db.commit()
    db.refresh(ing)
    return {"id": ing.id, "code": ing.code, "name": ing.name,
            "unit": ing.unit, "stock_qty": ing.stock_qty}
