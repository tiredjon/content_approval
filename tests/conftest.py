import os

# Must run before app.config.get_settings() / app.db.session.get_engine() are first
# called (both lru_cache'd), so tests never touch a real DB or leave a stray file behind.
os.environ.setdefault("APPROVAL_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest

from app.db.base import Base
from app.db.session import get_engine


@pytest.fixture(autouse=True)
async def _fresh_schema():
    """Recreate every table before each test and drop them after.

    The in-memory SQLite engine is a process-wide singleton (see get_engine's
    StaticPool), so without this, data from one test would leak into the next.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
