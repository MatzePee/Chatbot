from __future__ import annotations

import asyncio
import os
import tempfile
from typing import AsyncIterator

import pytest
import pytest_asyncio

os.environ.setdefault("MP_AUTOPOSTER_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("MP_AUTOPOSTER_SECRET_KEY", "test-secret-key")
os.environ.setdefault("MP_AUTOPOSTER_MEDIA_ROOT", tempfile.mkdtemp(prefix="autoposter-test-"))
os.environ.setdefault("MP_AUTOPOSTER_DRY_RUN", "true")

os.environ["MP_AUTOPOSTER_GLOBAL_PAUSE"] = "false"
os.environ["MP_AUTOPOSTER_DATA_DIR"] = tempfile.mkdtemp(prefix="creatorpilot-test-")

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from autoposter.db import Base  # noqa: E402
import autoposter.models  # noqa: E402,F401


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
    await engine.dispose()
