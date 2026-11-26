"""Record a project's WebSocket events while it runs (Task 26), optionally as a HUD replay.

Usage::

    python scripts/record_ws.py --project hud-accept --out recorded.jsonl \\
        [--base-url http://localhost:8080] [--expect-terminal 2] [--timeout 300] \\
        [--replay-out recorded-mvp.jsonl --min-gap-ms 300]

1. Connect to ``ws://<host>/ws/<project>`` (through the frontend's Nginx, plan §2) and wait
   for the first ``snapshot``.
2. Write every frame as one JSONL line ``{"t", "event", "data"}``; ``t`` is milliseconds since
   the snapshot arrived, so the raw file is already in the replay format (first line snapshot).
3. Gates: the first ``task:awaiting_approval`` of each task is REST-rejected with a note, any
   later one is approved (``--no-gate-actions`` leaves every gate alone).
4. Stop after ``--expect-terminal`` ``task:completed`` / ``task:failed`` events (exit 0), or
   exit 2 on timeout. ``--expect-terminal 0`` records until the timeout and exits 0.

``--replay-out`` writes a copy for ``frontend/public/replay/``: the same lines, with ``t``
stretched so consecutive frames are at least ``--min-gap-ms`` apart (Mock calls return at
once, so the real timings would play back in a blink). Order and content are unchanged.

Run the backend with ``FORCE_MOCK=true`` (plan D22): this script itself never calls an LLM,
but the tasks it waits for do.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
from websockets.asyncio.client import connect

TERMINAL_EVENTS = ("task:completed", "task:failed")
REJECT_NOTE = "錄製：請補上輸入驗證"


def to_replay(lines: list[dict], min_gap_ms: int) -> list[dict]:
    """Return ``lines`` with ``t`` made non-decreasing and at least ``min_gap_ms`` apart.

    The first line keeps ``t = 0`` (the snapshot); the rest keep their order and data.
    """

    out: list[dict] = []
    previous = None
    for line in lines:
        t = int(line["t"])
        if previous is None:
            t = 0
        else:
            t = max(t, previous + min_gap_ms)
        out.append({"t": t, "event": line["event"], "data": line["data"]})
        previous = t
    return out


def gate_action(task_id: str, rejected: set[str]) -> str:
    """``reject`` the first time a task waits at a gate, ``approve`` after that."""

    if task_id in rejected:
        return "approve"
    rejected.add(task_id)
    return "reject"


async def _post(url: str, **kwargs) -> httpx.Response:
    return await asyncio.to_thread(httpx.post, url, timeout=10, **kwargs)


async def record(args: argparse.Namespace) -> tuple[int, list[dict]]:
    ws_url = args.base_url.replace("http", "ws", 1) + f"/ws/{args.project}"
    tasks_api = f"{args.base_url}/api/projects/{args.project}/tasks"
    deadline = time.monotonic() + args.timeout
    lines: list[dict] = []
    terminal = 0
    rejected: set[str] = set()
    started: float | None = None

    with open(args.out, "w", encoding="utf-8") as out:
        async with connect(ws_url) as ws:
            while args.expect_terminal == 0 or terminal < args.expect_terminal:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                message = json.loads(raw)
                now = time.monotonic()
                if started is None:
                    if message["event"] != "snapshot":
                        print("the first frame was not a snapshot", file=sys.stderr)
                        return 2, lines
                    started = now
                line = {"t": int((now - started) * 1000), "event": message["event"], "data": message["data"]}
                lines.append(line)
                out.write(json.dumps(line, ensure_ascii=False) + "\n")
                out.flush()

                event, data = message["event"], message["data"]
                if event in TERMINAL_EVENTS:
                    terminal += 1
                elif event == "task:awaiting_approval" and not args.no_gate_actions:
                    task_id = data["task"]["id"]
                    action = gate_action(task_id, rejected)
                    body = {"json": {"note": REJECT_NOTE}} if action == "reject" else {}
                    response = await _post(f"{tasks_api}/{task_id}/{action}", **body)
                    response.raise_for_status()
                    print(f"{action} {task_id}: {response.status_code}")

    if args.expect_terminal and terminal < args.expect_terminal:
        print(f"timeout after {args.timeout}s with {terminal}/{args.expect_terminal} terminal events",
              file=sys.stderr)
        return 2, lines
    print(f"recorded {len(lines)} frames, {terminal} terminal events -> {args.out}")
    return 0, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--expect-terminal", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--no-gate-actions", action="store_true")
    parser.add_argument("--replay-out", type=Path, default=None)
    parser.add_argument("--min-gap-ms", type=int, default=0)
    args = parser.parse_args(argv)

    code, lines = asyncio.run(record(args))
    if args.replay_out is not None and lines:
        replay = to_replay(lines, args.min_gap_ms)
        args.replay_out.write_text(
            "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in replay), encoding="utf-8")
        print(f"replay: {len(replay)} lines -> {args.replay_out}")
    return code


if __name__ == "__main__":
    sys.exit(main())
