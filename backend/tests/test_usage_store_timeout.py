"""An unreachable database must not hang the event loop forever (QA gate A, item 2).

``UsageTracker.record`` writes through ``PgUsageStore.add`` synchronously on the event loop. The
"database" here is a local socket that accepts TCP connections and never answers, the way a
stalled server or a half-open network looks; nothing leaves the machine. Without a connect
timeout the connection attempt waits forever.
"""

from __future__ import annotations

import logging
import socket
import threading
import time

import pytest

from core.stores.usage_store import PgUsageStore
from core.usage_tracker import UsageTracker
from db.session import SYNC_CONNECT_TIMEOUT_S, create_sync_engine_for


@pytest.fixture
def silent_server():
    """``host:port`` of a server that accepts connections and never sends a byte."""

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    accepted: list[socket.socket] = []
    stop = threading.Event()

    def accept_forever():
        server.settimeout(0.1)
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except OSError:
                continue
            accepted.append(conn)  # held open, never answered

    thread = threading.Thread(target=accept_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{server.getsockname()[1]}"
    stop.set()
    thread.join(2)
    for conn in accepted:
        conn.close()
    server.close()


def _url(hostport: str) -> str:
    return f"postgresql+psycopg://u:p@{hostport}/db"


def test_the_sync_engine_gives_up_on_a_silent_database(silent_server):
    store = PgUsageStore(create_sync_engine_for(_url(silent_server)))
    record = UsageTracker().record("p", "t", "anthropic", "claude-opus-5-5", 10, 5)

    started = time.monotonic()
    with pytest.raises(Exception):
        store.add(record)
    elapsed = time.monotonic() - started
    store.dispose()

    assert SYNC_CONNECT_TIMEOUT_S <= 10
    assert elapsed < SYNC_CONNECT_TIMEOUT_S * 2 + 1  # gave up, did not wait forever


def test_record_returns_and_logs_when_the_database_is_silent(silent_server, caplog):
    store = PgUsageStore(create_sync_engine_for(_url(silent_server)))
    tracker = UsageTracker(store=store)
    caplog.set_level(logging.ERROR)

    started = time.monotonic()
    record = tracker.record("p", "t", "anthropic", "claude-opus-5-5", 10, 5)
    elapsed = time.monotonic() - started
    store.dispose()

    assert elapsed < SYNC_CONNECT_TIMEOUT_S * 2 + 1
    assert record.input_tokens == 10
    assert tracker.project_usage("p").call_count == 1
    assert "Could not store usage record" in caplog.text
