"""Drive the acceptance scenario against a running backend (Task 14, AC10).

1. Connect to ``ws://<host>/ws/<project>`` and wait for the first ``snapshot``.
2. Rewrite the project's tasks.yaml with ``status: pending`` -- *inside the
   backend container* (``docker compose exec``), because a write made inside the
   container is certain to reach the watchdog watcher, while editing a bind
   mount from a macOS host is not.
3. Record every event as one JSONL line ``{"event", "data"}``. When
   ``task:awaiting_approval`` for ``task-gate`` arrives, REST-reject the first
   time and REST-approve the second time.
4. Stop after 4 ``task:completed`` / ``task:failed`` events (exit 0); exit 2 on
   timeout.

``--local-projects-dir DIR`` replaces step 2 for runs without Docker: the
prepare script is run directly on ``DIR/<project>`` (the directory the locally
started backend watches).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
from websockets.asyncio.client import connect

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_TASK_ID = "task-gate"
TERMINAL_EVENTS = ("task:completed", "task:failed")
EXPECTED_TERMINAL = 4


def prepare_in_container(project: str) -> None:
    path = f"/app/projects/{project}"
    subprocess.run(
        ["docker", "compose", "exec", "-T", "backend", "python",
         "scripts/prepare_accept_project.py", "--source", path,
         "--project-dir", path, "--status", "pending"],
        cwd=REPO_ROOT, check=True,
    )


def prepare_locally(projects_dir: Path, project: str) -> None:
    path = projects_dir / project
    subprocess.run(
        [sys.executable, str(Path(__file__).with_name("prepare_accept_project.py")),
         "--source", str(path), "--project-dir", str(path), "--status", "pending"],
        check=True,
    )


async def post(url: str, **kwargs) -> httpx.Response:
    return await asyncio.to_thread(httpx.post, url, timeout=10, **kwargs)


async def run(args: argparse.Namespace) -> int:
    ws_url = args.base_url.replace("http", "ws", 1) + f"/ws/{args.project}"
    api = f"{args.base_url}/api/projects/{args.project}/tasks/{GATE_TASK_ID}"
    deadline = time.monotonic() + args.timeout
    terminal = 0
    gate_entries = 0

    async with connect(ws_url) as ws:
        with open(args.out, "w", encoding="utf-8") as out:

            async def next_message() -> dict | None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    return None
                message = json.loads(raw)
                out.write(json.dumps({"event": message["event"], "data": message["data"]},
                                     ensure_ascii=False) + "\n")
                out.flush()
                return message

            first = await next_message()
            if first is None or first["event"] != "snapshot":
                print("did not receive an initial snapshot", file=sys.stderr)
                return 2

            if args.local_projects_dir:
                await asyncio.to_thread(prepare_locally, args.local_projects_dir, args.project)
            else:
                await asyncio.to_thread(prepare_in_container, args.project)

            while terminal < EXPECTED_TERMINAL:
                message = await next_message()
                if message is None:
                    print(f"timeout after {args.timeout}s with {terminal}/{EXPECTED_TERMINAL} "
                          "terminal events", file=sys.stderr)
                    return 2
                event, data = message["event"], message["data"]
                if event in TERMINAL_EVENTS:
                    terminal += 1
                elif event == "task:awaiting_approval" and data["task"]["id"] == GATE_TASK_ID:
                    gate_entries += 1
                    if gate_entries == 1:
                        resp = await post(f"{api}/reject",
                                          json={"note": "acceptance: please add validation"})
                    else:
                        resp = await post(f"{api}/approve")
                    resp.raise_for_status()
    print(f"done: {terminal} terminal events, {gate_entries} gate entries -> {args.out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--local-projects-dir", type=Path, default=None,
                        help="run the prepare step locally instead of via docker compose exec")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
