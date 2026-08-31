from __future__ import annotations

import os

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import settings
from .models import Base

engine = create_async_engine(f"sqlite+aiosqlite:///{settings.db_path}", echo=False)
session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def _add_missing_columns(conn) -> None:
    """Additive schema migration for existing databases.

    ``create_all`` creates new *tables* but never adds a column to a table that
    already exists, so an upgraded install would break on any new field. SQLite
    supports ALTER TABLE ADD COLUMN, which covers every schema change made so
    far (all new columns are nullable or have defaults).
    """
    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # create_all just made it, or will
        have = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in have:
                continue
            ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column.type.compile(conn.dialect)}"
            if column.default is not None and getattr(column.default, "is_scalar", False):
                ddl += f" DEFAULT {column.default.arg!r}"
            conn.execute(text(ddl))


async def init_db() -> None:
    settings.ensure_dirs()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
    try:
        os.chmod(settings.db_path, 0o600)  # contains provider API keys
    except OSError:
        pass


async def get_db():
    async with session_factory() as session:
        yield session
