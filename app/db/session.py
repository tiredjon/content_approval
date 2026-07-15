from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    database_url = get_settings().database_url
    connect_args: dict = {}
    engine_kwargs: dict = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if ":memory:" in database_url:
            # A file-less SQLite DB lives only on its one connection; without StaticPool
            # every checkout would get a fresh, empty database.
            engine_kwargs["poolclass"] = StaticPool
    engine = create_async_engine(database_url, connect_args=connect_args, **engine_kwargs)
    if database_url.startswith("sqlite"):
        # SQLite ignores FK constraints unless enabled per-connection — without this our
        # composite (workspace_id, id) foreign keys would silently stop enforcing tenant
        # isolation in tests (Postgres enforces them unconditionally).
        @event.listens_for(engine.sync_engine, "connect")
        def _enable_sqlite_fk(dbapi_connection, connection_record) -> None:  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    # One transaction per request: commits when the route handler returns normally,
    # rolls back on any exception. This is what makes "create + audit log + outbox
    # event" atomic — they all share this same session/transaction.
    async with get_sessionmaker()() as session, session.begin():
        yield session


async def ping() -> bool:
    """Best-effort DB connectivity check for the /ready probe — must never raise."""
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
