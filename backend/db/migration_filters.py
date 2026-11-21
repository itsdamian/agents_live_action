"""What Alembic must leave alone.

LangGraph's ``AsyncPostgresSaver.setup()`` creates and migrates its own tables in the same
database. Alembic must neither propose to drop them (autogenerate) nor touch them.

Confirmed in Task 13 against what ``setup()`` of langgraph-checkpoint-postgres 3.2.1 really
creates (``information_schema.tables`` after setup, ``tests/integration/test_checkpointing_pg.py``):
exactly these four tables, plus the ``*_thread_id_idx`` indexes on three of them (excluded with
their table below). Re-check this list whenever that package is upgraded.
"""

from __future__ import annotations

CHECKPOINTER_TABLES = frozenset(
    {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}
)


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Alembic ``include_object`` hook: skip checkpointer tables and their indexes/constraints."""

    if type_ == "table" and name in CHECKPOINTER_TABLES:
        return False
    table = getattr(obj, "table", None)
    if table is not None and getattr(table, "name", None) in CHECKPOINTER_TABLES:
        return False
    return True
