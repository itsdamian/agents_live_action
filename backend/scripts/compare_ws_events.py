"""Compare two captured WebSocket event logs (Task 14, AC10).

Usage: ``python scripts/compare_ws_events.py a.jsonl b.jsonl``

Each line is ``{"event": ..., "data": ...}``. ``snapshot`` events are ignored
and the rest are compared by ``event_signature`` (the same function as
``tests/pipeline_harness.py``, copied here so scripts never import tests) --
with one deliberate difference: ``task:created``'s ``queue_size`` is left out of
the signature. It is a timing sample (how many tasks the watcher had enqueued
when the worker picked the task up), not part of the event contract. Differences
in it are printed as a warning and do not affect the exit code.

Exit 0 when the signature sequences are identical, 1 otherwise (printing the
first difference).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def event_signature(event: str, data: dict, *, with_queue_size: bool = True) -> tuple:
    task = data.get("task") or {}
    sig = [event, tuple(sorted(data.keys())), data.get("station"), data.get("status"),
           task.get("id"), task.get("status"), task.get("rework_count")]
    if event == "task:created":
        if with_queue_size:
            sig.append(data["queue_size"])
    elif event == "task:completed":
        sig += [data["done_count"], tuple(Path(p).name for p in data["deliverables"])]
    elif event == "task:failed":
        sig += [data["failed_count"], data["reason"]]
    elif event == "task:rejected":
        sig.append(data["note"])
    elif event == "usage:update":
        sig += [data["task_usage"]["call_count"], data["project_usage"]["call_count"]]
    elif event == "devops:alert":
        sig += [data["level"], data["metric"]]
    return tuple(sig)


def load_records(path: Path) -> list[tuple[str, dict]]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record["event"] != "snapshot":
            records.append((record["event"], record["data"]))
    return records


def load_signatures(path: Path) -> list[tuple]:
    """Signatures used for the verdict: ``queue_size`` is excluded (timing)."""

    return [event_signature(e, d, with_queue_size=False) for e, d in load_records(path)]


def queue_size_values(path: Path) -> list[tuple[str, int]]:
    return [
        (data["task"]["id"], data["queue_size"])
        for event, data in load_records(path)
        if event == "task:created"
    ]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: compare_ws_events.py a.jsonl b.jsonl", file=sys.stderr)
        return 2
    path_a, path_b = Path(args[0]), Path(args[1])

    queue_a, queue_b = queue_size_values(path_a), queue_size_values(path_b)
    if queue_a != queue_b:
        print("警告：queue_size 不同（時序取樣，非契約差異）")
        print(f"  {args[0]}: {queue_a}")
        print(f"  {args[1]}: {queue_b}")

    a, b = load_signatures(path_a), load_signatures(path_b)
    for index in range(max(len(a), len(b))):
        left = a[index] if index < len(a) else None
        right = b[index] if index < len(b) else None
        if left != right:
            print(f"first difference at index {index}:")
            print(f"  {args[0]}: {left}")
            print(f"  {args[1]}: {right}")
            return 1
    print(f"identical: {len(a)} events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
