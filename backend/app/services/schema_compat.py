"""轻量 schema 兼容：项目没有迁移工具，老库缺列时在启动阶段补齐（只增不改）。"""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

_ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "ingredients": [
        ("version", "INTEGER NOT NULL DEFAULT 0"),
    ],
    "prep_runs": [
        ("status", "VARCHAR(16) NOT NULL DEFAULT 'suggested'"),
        ("signature", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("placed_at", "TIMESTAMP NULL"),
    ],
}


def ensure_columns(engine: Engine) -> None:
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if table not in existing:
                continue  # create_all 会按新模型建表
            present = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in columns:
                if name not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
