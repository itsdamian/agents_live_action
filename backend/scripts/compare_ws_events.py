"""Compare two captured WebSocket event logs (Task 14, AC10).

Usage: ``python scripts/compare_ws_events.py a.jsonl b.jsonl``

Each line is ``{"event": ..., "data": ...}``. ``snapshot`` events are ignored
and the rest are compared by ``event_signature`` (the same function as
``tests/pipeline_harness.py``; copied here so scripts never import tests).
Exit 0 when the signature sequences are identical, 1 otherwise (printing the
first difference).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def event_signature(event: str, data: dict) -> tuple:
    task = data.get("task") or {}
    sig = [event, tuple(sorted(data.keys())), data.get("station"), data.get("status"),
           task.get("id"), task.get("status"), task.get("rework_count")]
    if event == "task:created":
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


def load_signatures(path: Path) -> list[tuple]:
    sigs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record["event"] == "snapshot":
            continue
        sigs.append(event_signature(record["event"], record["data"]))
    return sigs


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: compare_ws_events.py a.jsonl b.jsonl", file=sys.stderr)
        return 2
    a, b = load_signatures(Path(args[0])), load_signatures(Path(args[1]))
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
