from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import settings
from .models import Base

engine = create_async_engine(f"sqlite+aiosqlite:///{settings.db_path}", echo=False)
session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    settings.ensure_dirs()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        os.chmod(settings.db_path, 0o600)  # contains provider API keys
    except OSError:
        pass


async def get_db():
    async with session_factory() as session:
        yield session
