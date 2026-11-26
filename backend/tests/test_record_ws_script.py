"""Tests for ``scripts/record_ws.py``'s pure helpers (Task 26); the recording itself runs against Docker."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "record_ws.py"
_spec = importlib.util.spec_from_file_location("record_ws", SCRIPT)
record_ws = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(record_ws)


def _line(t, event="x", data=None):
    return {"t": t, "event": event, "data": data or {}}


def test_to_replay_keeps_order_and_data_and_starts_at_zero():
    lines = [_line(5, "snapshot", {"a": 1}), _line(6, "task:created"), _line(900, "task:completed")]

    out = record_ws.to_replay(lines, 0)

    assert [(l["t"], l["event"]) for l in out] == [(0, "snapshot"), (6, "task:created"), (900, "task:completed")]
    assert out[0]["data"] == {"a": 1}


def test_to_replay_stretches_frames_closer_than_the_minimum_gap_only():
    lines = [_line(0, "snapshot"), _line(3), _line(4), _line(2000)]

    assert [l["t"] for l in record_ws.to_replay(lines, 300)] == [0, 300, 600, 2000]


def test_gate_is_rejected_once_then_approved_per_task():
    rejected: set[str] = set()

    assert [record_ws.gate_action(t, rejected) for t in ("a", "b", "a", "a", "b")] == [
        "reject", "reject", "approve", "approve", "approve"]


def test_default_base_url_is_the_frontend():
    assert 'default="http://localhost:8080"' in SCRIPT.read_text(encoding="utf-8")
