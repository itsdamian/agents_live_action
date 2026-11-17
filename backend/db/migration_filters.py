"""What Alembic must leave alone.

LangGraph's ``AsyncPostgresSaver.setup()`` creates and migrates its own tables in the same
database. Alembic must neither propose to drop them (autogenerate) nor touch them.

TODO(Task 13): these four names are the ones plan D5 lists; confirm them against what
``setup()`` really creates (``information_schema.tables``) and update the tuple.
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
