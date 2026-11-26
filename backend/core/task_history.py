"""Sticky-note history: pipeline events -> ``task_runs`` / ``task_run_steps`` (plan D20).

:class:`HistoryRecorder` listens to the same events the WebSocket clients get, so it works for
both engines and the orchestrator does not change. The runtime calls :meth:`HistoryRecorder.handle`
for every event of a project before the event goes out (DB mode only: it needs a
``RunRepository``).

* ``task:created`` starts a run (an unfinished older run of the task is closed as superseded).
* ``station:update`` done / error / rework and ``task:approved`` / ``task:rejected`` add steps.
* ``usage:update`` refreshes the run's cost and tokens, re-summed from the usage records since
  the run started (the records are the truth; numbers in the event are never added up, so a
  restart neither loses nor double-counts anything).
* ``task:completed`` / ``task:failed`` finish the run (outcome, reason, rework count, totals).

Across a restart (a task resumed at its gate gets no new ``task:created`` and the in-memory map
is empty) the recorder continues the task's newest unfinished run; ``seq`` keeps counting from
the last step because the repository numbers steps from what is stored.

Recording must never break the pipeline or the broadcast: every failure is logged and swallowed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from core.stores import RunRecord, RunRepository, UsageTotals
from core.usage_tracker import UsageTracker

logger = logging.getLogger(__name__)

REASON_SUPERSEDED = "superseded"

# The seat snapshot of the default line-up (plan §4.2 BUILTIN_SEATS). Until the dynamic line-up
# exists (Task 24 plugs in the real one) every run is recorded with these three seats.
DEFAULT_SEATS: list[dict] = [
    {"id": "pm", "display_name": "PM", "look": "glasses", "kind": "make"},
    {"id": "swe", "display_name": "SWE", "look": "headset", "kind": "make"},
    {"id": "qa", "display_name": "QA", "look": "cap", "kind": "check"},
]

_STATION_STEP_STATUSES = ("done", "error")


class TrackerUsage:
    """``UsageRepository.totals`` over a tracker's records (unit tests without a database).

    The tracker holds every usage record of the process, loaded from the store at startup and
    written through to it afterwards, so its records mirror ``usage_records``. In DB mode the
    runtime uses the real ``PgUsageRepository`` instead.
    """

    def __init__(self, tracker: UsageTracker) -> None:
        self._tracker = tracker

    async def totals(self, project_id: str, task_id: str, *, since: datetime | None = None) -> UsageTotals:
        records = [
            r for r in self._tracker.records()
            if r.project_id == project_id and r.task_id == task_id and (since is None or r.timestamp >= since)
        ]
        return UsageTotals(
            input_tokens=sum(r.input_tokens for r in records),
            output_tokens=sum(r.output_tokens for r in records),
            cost=round(sum(r.cost for r in records), 6),
            call_count=len(records),
        )


class HistoryRecorder:
    """Writes one ``task_runs`` row per execution of a task, plus its steps.

    Args:
        runs: Where runs and steps are stored.
        usage: Anything with ``UsageRepository.totals`` (async) over the usage records.
        seats: ``seats(project_id) -> [{id, display_name, look, kind}]``, the line-up at the
            moment a run starts. Defaults to :data:`DEFAULT_SEATS`.
        clock: "now", for ``started_at`` / ``finished_at`` / step times.
    """

    def __init__(
        self,
        runs: RunRepository,
        *,
        usage,
        seats: Callable[[str], list[dict]] | None = None,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._runs = runs
        self._usage = usage
        self._seats = seats or (lambda project_id: [dict(s) for s in DEFAULT_SEATS])
        self._clock = clock
        self._open: dict[tuple[str, str], RunRecord] = {}  # (project, task) -> the run in progress

    async def handle(self, project_id: str, event: str, data: dict) -> None:
        """Record what ``event`` means for the task's history; never raises."""

        try:
            await self._handle(project_id, event, data)
        except Exception:
            logger.exception("Could not record %s in the history of project %s", event, project_id)

    async def _handle(self, project_id: str, event: str, data: dict) -> None:
        if event == "task:created":
            await self._start(project_id, data["task"]["id"])
        elif event == "station:update":
            await self._station(project_id, data)
        elif event in ("task:approved", "task:rejected"):
            run = await self._run(project_id, data["task"]["id"])
            await self._runs.add_step(
                run.id, kind="gate", at=self._clock(), station=data.get("station"),
                status="approved" if event == "task:approved" else "rejected",
                note=data.get("note") if event == "task:rejected" else None,
            )
        elif event == "usage:update":
            await self._refresh_totals(await self._run(project_id, data["task_id"]))
        elif event in ("task:completed", "task:failed"):
            await self._finish(project_id, event, data)

    async def _start(self, project_id: str, task_id: str) -> None:
        now = self._clock()
        # A run left open (the task was re-queued, or a crash) will never be finished.
        await self._runs.supersede_open_runs(project_id, task_id, reason=REASON_SUPERSEDED, finished_at=now)
        self._open[(project_id, task_id)] = await self._runs.open_run(
            project_id, task_id, started_at=now, seats=self._seats(project_id)
        )

    async def _run(self, project_id: str, task_id: str) -> RunRecord:
        """The task's run in progress: from memory, else the newest unfinished one, else new."""

        key = (project_id, task_id)
        run = self._open.get(key)
        if run is None:
            run = await self._runs.find_open_run(project_id, task_id)  # continued after a restart
            if run is None:
                run = await self._runs.open_run(
                    project_id, task_id, started_at=self._clock(), seats=self._seats(project_id)
                )
            self._open[key] = run
        return run

    async def _station(self, project_id: str, data: dict) -> None:
        status = data.get("status")
        if status == "rework":  # the station is where the task goes back to
            run = await self._run(project_id, data["task"]["id"])
            await self._runs.add_step(run.id, kind="rework", at=self._clock(),
                                      station=data.get("station"), status="rework")
        elif status in _STATION_STEP_STATUSES:
            run = await self._run(project_id, data["task"]["id"])
            await self._runs.add_step(run.id, kind="station", at=self._clock(), station=data.get("station"),
                                      status=status, note=data.get("error") if status == "error" else None)

    async def _refresh_totals(self, run: RunRecord) -> None:
        totals = await self._usage.totals(run.project_id, run.task_id, since=run.started_at)
        await self._runs.update_totals(run.id, cost_usd=totals.cost, input_tokens=totals.input_tokens,
                                       output_tokens=totals.output_tokens)

    async def _finish(self, project_id: str, event: str, data: dict) -> None:
        task = data["task"]
        run = await self._run(project_id, task["id"])
        await self._refresh_totals(run)  # final, whatever usage:update was or was not seen
        await self._runs.finish_run(
            run.id,
            outcome="done" if event == "task:completed" else "failed",
            finished_at=self._clock(),
            reason=data.get("reason") if event == "task:failed" else None,
            rework_count=task.get("rework_count", 0),
        )
        self._open.pop((project_id, task["id"]), None)
