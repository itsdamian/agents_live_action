"""The LangGraph checkpointer stored in PostgreSQL (plan D7).

With ``PIPELINE_CHECKPOINTER=postgres`` the runtime opens ONE ``AsyncPostgresSaver`` at startup
and hands it to every project's graph runner (the ``thread_id`` already holds project and task,
so the projects never collide). It is closed at shutdown, after the workers have stopped.

This module and ``core/pipeline_graph.py`` are the only production modules that import
``langgraph`` (AC23); the runtime imports this one lazily, so ``PIPELINE_ENGINE=legacy`` never
loads it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.engine import make_url


def conninfo_from_database_url(database_url: str) -> str:
    """``postgresql+psycopg://...`` (SQLAlchemy) -> ``postgresql://...`` (what psycopg takes)."""

    return make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)


# LangGraph 學習: checkpointer 是 graph 的「存檔」：每個 node 結束後把 state 寫成一個 checkpoint，
# interrupt() 暫停時停在最後一個 checkpoint。InMemorySaver 存在程序記憶體裡，程序一結束就沒了；
# AsyncPostgresSaver 存進 Postgres，所以停在核准閘門的 graph 在後端重啟後還能用同一個 thread_id 找回來。
#
# LangGraph 學習: setup() 一定要呼叫一次——它建立 checkpointer 自己的表並跑它自己的遷移
# （版本記在 checkpoint_migrations 表，所以重複呼叫只會補跑還沒跑過的，是冪等的）。
# langgraph-checkpoint-postgres 3.2.1 的 setup() 實際建立的表（Task 13 整合測試對真 Postgres 查
# information_schema 確認）：checkpoint_migrations、checkpoints、checkpoint_blobs、checkpoint_writes，
# 外加三個 *_thread_id_idx 索引。這些表不歸 Alembic 管（db/migration_filters.py 排除它們）。
#
# LangGraph 學習: from_conn_string 用的連線是 autocommit=True、prepare_threshold=0、
# row_factory=dict_row（aio.py 的 from_conn_string）：autocommit 是因為 setup() 裡有
# CREATE INDEX CONCURRENTLY（不能在交易內執行），而且每次 put 都要立刻寫進去、不能等誰 commit；
# prepare_threshold=0 關掉 server-side prepared statements，經過 pgbouncer 之類的連線池也不會出錯；
# dict_row 是 saver 讀資料列的方式（row["v"]）。這些都由 from_conn_string 設好，我們不必自己開連線。
@asynccontextmanager
async def open_postgres_checkpointer(database_url: str) -> AsyncIterator[AsyncPostgresSaver]:
    """Open a saver on ``database_url``, run ``setup()``, and close the connection on exit."""

    # LangGraph 學習: from_conn_string 是 @asynccontextmanager：離開 async with 時連線就關了，
    # 所以 saver 的壽命必須涵蓋所有用它的 graph（runtime 在 shutdown 停掉 worker 之後才關）。
    async with AsyncPostgresSaver.from_conn_string(conninfo_from_database_url(database_url)) as saver:
        await saver.setup()
        yield saver
