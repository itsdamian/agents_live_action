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


# Seconds a synchronous connection attempt may take. An unreachable database must fail fast
# instead of holding a usage write (and the station waiting for it) forever.
SYNC_CONNECT_TIMEOUT_S = 5
# After the connection is up: TCP keepalives find a peer that went away while idle, and
# tcp_user_timeout (milliseconds) bounds how long sent data may stay unacknowledged, so a
# server that stalls mid-INSERT also ends in an error instead of a hang (libpq parameters).
SYNC_KEEPALIVE_ARGS = {
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
    "tcp_user_timeout": 30_000,
}


def create_sync_engine_for(url: str, **kwargs):
    """Synchronous engine for ``url`` (same ``postgresql+psycopg`` URL; needs no greenlet).

    Used where code must write from a plain synchronous call, e.g. the usage store. Connecting
    gives up after :data:`SYNC_CONNECT_TIMEOUT_S` seconds (libpq ``connect_timeout``); an open
    connection that stops answering errors out by :data:`SYNC_KEEPALIVE_ARGS`."""

    import sqlalchemy

    connect_args = {
        "connect_timeout": SYNC_CONNECT_TIMEOUT_S,
        **SYNC_KEEPALIVE_ARGS,
        **kwargs.pop("connect_args", {}),
    }
    return sqlalchemy.create_engine(url, pool_pre_ping=True, connect_args=connect_args, **kwargs)


def session_factory(engine: "AsyncEngine") -> "async_sessionmaker":
    """``expire_on_commit=False``: rows read in a session stay usable after it commits."""

    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(engine, expire_on_commit=False)
