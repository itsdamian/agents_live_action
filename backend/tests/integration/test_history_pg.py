"""History on a real PostgreSQL (Task 15a, plan D20, AC5 history part).

After a restart at the approval gate the task still has ONE run, its steps are numbered without a
gap across the restart, and its cost and tokens are the usage records of the run, including the
calls made before the restart. Checked straight in the tables.
"""

from __future__ import annotations

import psycopg
import pytest

from tests.integration.test_gate_restart import _reference, _restart, pg_env  # noqa: F401  (fixture)
from tests.test_gate_restart import llm_calls  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration

TASK = "task-g"


def _history(plain_url: str, project: str):
    with psycopg.connect(plain_url) as conn:
        runs = conn.execute(
            "SELECT id, run_no, outcome, reason, rework_count, started_at, finished_at, cost_usd, "
            "input_tokens, output_tokens, seats FROM task_runs WHERE project_id = %s AND task_id = %s",
            (project, TASK),
        ).fetchall()
        steps = conn.execute(
            "SELECT seq, kind, station, status FROM task_run_steps WHERE run_id = %s ORDER BY seq",
            (runs[0][0],),
        ).fetchall() if runs else []
        usage = conn.execute(
            "SELECT coalesce(sum(input_tokens), 0), coalesce(sum(output_tokens), 0), coalesce(sum(cost), 0), "
            "count(*) FROM usage_records WHERE project_id = %s AND task_id = %s AND created_at >= %s",
            (project, TASK, runs[0][5]),
        ).fetchone() if runs else None
    return runs, steps, usage


def test_ac5_history_one_run_continuous_seq_and_cost_including_before_the_restart(
    tmp_path, pg_env, migrated_db_url, llm_calls
):
    _restart(tmp_path / "run", migrated_db_url, "after_swe", [("approve", "")], at_event=False)

    runs, steps, usage = _history(migrated_db_url, "demo")
    assert len(runs) == 1
    _, run_no, outcome, reason, rework_count, started_at, finished_at, cost, tokens_in, tokens_out, seats = runs[0]
    assert (run_no, outcome, reason, rework_count) == (1, "done", None, 0)
    assert finished_at > started_at
    assert [s["id"] for s in seats] == ["pm", "swe", "qa"]
    assert steps == [(1, "station", "pm", "done"), (2, "station", "swe", "done"),
                     (3, "gate", "swe", "approved"), (4, "station", "qa", "done")]
    assert [s[0] for s in steps] == list(range(1, len(steps) + 1))  # no gap across the restart
    sum_in, sum_out, sum_cost, calls = usage
    assert calls == 3  # PM and SWE before the restart, QA after it
    assert (tokens_in, tokens_out, float(cost)) == (sum_in, sum_out, float(sum_cost))
    assert tokens_in > 0


def test_a_run_without_a_restart_is_recorded_the_same_way(tmp_path, pg_env, migrated_db_url, llm_calls):
    _reference(tmp_path / "ref", "after_qa", [("reject", "tighten tests"), ("approve", "")])

    runs, steps, usage = _history(migrated_db_url, "ref")
    assert [(r[1], r[2], r[4]) for r in runs] == [(1, "done", 1)]
    assert steps == [
        (1, "station", "pm", "done"), (2, "station", "swe", "done"), (3, "station", "qa", "done"),
        (4, "gate", "qa", "rejected"), (5, "rework", "swe", "rework"), (6, "station", "swe", "done"),
        (7, "station", "qa", "done"), (8, "gate", "qa", "approved"),
    ]
    assert (runs[0][8], runs[0][9]) == (usage[0], usage[1]) and usage[3] == 5
