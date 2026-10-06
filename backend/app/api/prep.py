from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import prep_service

router = APIRouter(prefix="/prep", tags=["prep"])


class IssueIn(BaseModel):
    version: int = Field(..., ge=1)


@router.post("/run")
def run_prep(order_id: int = 1, db: Session = Depends(get_db)):
    """刷新建议缺料：按当前结存/订单重算，建议列与缺料贴同一响应同源。"""
    try:
        run = prep_service.refresh_draft(db, order_id)
    except prep_service.RunNotFoundError as e:
        raise HTTPException(404, e.detail)
    return prep_service.serialize_run(run)


@router.get("/latest")
def latest(order_id: int = 1, db: Session = Depends(get_db)):
    """当前 draft 快照（不重算；重算只走 POST /run 显式刷新）。"""
    try:
        run = prep_service.get_or_create_draft(db, order_id)
    except prep_service.RunNotFoundError as e:
        raise HTTPException(404, e.detail)
    return prep_service.serialize_run(run)


@router.get("/shortages")
def shortages(order_id: int = 1, db: Session = Depends(get_db)):
    """缺料贴：备料台快照的同源子集。"""
    try:
        run = prep_service.get_or_create_draft(db, order_id)
    except prep_service.RunNotFoundError as e:
        raise HTTPException(404, e.detail)
    data = prep_service.serialize_run(run)
    return {
        "id": data["id"],
        "status": data["status"],
        "version": data["version"],
        "refreshed_at": data["refreshed_at"],
        "order": data.get("order"),
        "shortages": data.get("shortages", []),
        "stats": data.get("stats", {}),
    }


@router.post("/{run_id}/issue")
def issue(run_id: int, body: IssueIn, order_id: int = 1, db: Session = Depends(get_db)):
    """落单。建议列过期或落单当时重算对不上，一律 409 拒绝，库存不动。"""
    try:
        run = prep_service.issue_draft(db, run_id, body.version, order_id)
    except prep_service.RunNotFoundError as e:
        raise HTTPException(404, e.detail)
    except prep_service.StaleRunError as e:
        raise HTTPException(409, detail={"reason": e.reason, "message": e.detail})
    return prep_service.serialize_run(run)
