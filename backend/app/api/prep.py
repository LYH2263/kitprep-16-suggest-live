from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import PrepRun
from app.services.prep_service import (
    SuggestionError,
    create_suggest_run,
    place_run,
    run_to_dict,
)

router = APIRouter(prefix="/prep", tags=["prep"])


class PlaceIn(BaseModel):
    # 落单只认建议快照 id（令牌）。服务端在锁内按当前事实校验，过期即拒。
    run_id: int


def _raise(err: SuggestionError):
    raise HTTPException(err.http_status, detail={"code": err.code, "message": err.message})


@router.post("/suggest")
def suggest(order_id: int = 1, db: Session = Depends(get_db)):
    """刷新建议列：按当前结存现算一份新快照。建议列与缺料贴取自同一份返回。"""
    try:
        run = create_suggest_run(db, order_id)
    except SuggestionError as e:
        _raise(e)
    return run_to_dict(run)


@router.post("/place")
def place(body: PlaceIn, db: Session = Depends(get_db)):
    """落单：必须带最新建议快照 id。过期建议列 → 409，不产生任何落成单。"""
    try:
        run = place_run(db, body.run_id)
    except SuggestionError as e:
        _raise(e)
    return run_to_dict(run)


@router.get("/latest")
def latest(order_id: int = 1, db: Session = Depends(get_db)):
    """最新一份快照。已有落成单则永远返回落成单（它不可被新建议盖掉）；
    否则返回最新建议；一张都没有则现算建议，绝不静默另算一套数。"""
    run = db.scalars(
        select(PrepRun).where(PrepRun.order_id == order_id, PrepRun.status == "placed")
        .order_by(PrepRun.id.desc())
    ).first()
    if run is None:
        run = db.scalars(
            select(PrepRun).where(PrepRun.order_id == order_id).order_by(PrepRun.id.desc())
        ).first()
    if run is None:
        try:
            run = create_suggest_run(db, order_id)
        except SuggestionError as e:
            _raise(e)
    return run_to_dict(run)


@router.get("/shortages")
def shortages(order_id: int = 1, db: Session = Depends(get_db)):
    """缺料便利贴 = 最新同一份快照里的 shortages，不另发重算。"""
    data = latest(order_id=order_id, db=db)
    return {
        "order_id": order_id,
        "run_id": data["id"],
        "status": data["status"],
        "shortages": data.get("shortages", []),
        "stats": data.get("stats", {}),
    }
