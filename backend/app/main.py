from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text

from app.api.router import api_router
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models import models  # noqa: F401  # 确保所有表/索引注册到 metadata
from app.services.seed import seed_if_empty


# 老库（持久化卷）没有新列；create_all 不会补列，启动时幂等 ALTER。
# 必须在建部分唯一索引之前把同订单的多条旧 draft 收敛掉，否则建索引失败。
_PG_LEGACY_DDL = [
    "ALTER TABLE prep_runs ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'draft'",
    "ALTER TABLE prep_runs ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE prep_runs ADD COLUMN IF NOT EXISTS refreshed_at TIMESTAMP",
    "ALTER TABLE prep_runs ADD COLUMN IF NOT EXISTS issued_at TIMESTAMP",
    "UPDATE prep_runs SET refreshed_at = created_at WHERE refreshed_at IS NULL",
    # 老库同订单可能有多条旧快照：只保留最新一条为 draft，其余冻结为 issued
    """
    UPDATE prep_runs r SET status = 'issued', issued_at = COALESCE(r.issued_at, r.created_at)
    WHERE r.status = 'draft' AND r.id NOT IN (
        SELECT max_id FROM (
            SELECT max(id) AS max_id FROM prep_runs GROUP BY order_id
        ) latest
    )
    """,
]


def _init_schema() -> None:
    if engine.dialect.name == "postgresql" and inspect(engine).has_table("prep_runs"):
        # 老库：先补列并收敛旧 draft，再由 create_all 幂等补建部分唯一索引
        with engine.begin() as conn:
            for ddl in _PG_LEGACY_DDL:
                conn.execute(text(ddl))
    Base.metadata.create_all(bind=engine)
    # create_all 对已存在的表会跳过索引；老库升级需显式、幂等地补建
    for idx in models.PrepRun.__table__.indexes:
        idx.create(bind=engine, checkfirst=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _init_schema()
    if settings.seed_on_empty:
        db = SessionLocal()
        try:
            seed_if_empty(db)
        finally:
            db.close()
    yield


app = FastAPI(title="KitPrep", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix="/api")
