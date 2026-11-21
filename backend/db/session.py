"""Engine and session helpers (plan D5, D6): SQLAlchemy async on psycopg 3.

``DATABASE_URL`` is ``postgresql+psycopg://...`` (see ``config.get_database_url``). The same
URL works for the synchronous engine Alembic uses.

The asyncio extension of SQLAlchemy needs ``greenlet``; it is imported inside the functions
so that importing this module (and everything that only needs the models or Alembic) works
without it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker


def create_engine_for(url: str, *, null_pool: bool = False, **kwargs) -> "AsyncEngine":
    """Async engine for ``url``. ``null_pool=True`` opens a connection per use (tests that
    run each coroutine in its own event loop need it; a pooled connection is bound to a loop)."""

    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    if null_pool:
        kwargs["poolclass"] = NullPool
    return create_async_engine(url, **kwargs)


def create_sync_engine_for(url: str, **kwargs):
    """Synchronous engine for ``url`` (same ``postgresql+psycopg`` URL; needs no greenlet).

    Used where code must write from a plain synchronous call, e.g. the usage store."""

    from sqlalchemy import create_engine

    return create_engine(url, pool_pre_ping=True, **kwargs)


def session_factory(engine: "AsyncEngine") -> "async_sessionmaker":
    """``expire_on_commit=False``: rows read in a session stay usable after it commits."""

    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(engine, expire_on_commit=False)
