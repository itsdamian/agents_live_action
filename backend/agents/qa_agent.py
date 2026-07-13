"""QA (Quality Assurance) agent — the third and final station in the pipeline.

:class:`QAAgent` receives the SWE agent's implementation (handed over as
upstream *context*), designs and "runs" tests against it, and produces a test
report that carries an explicit **PASS / FAIL** verdict (Requirement 2.4).

The verdict drives the pipeline's rework loop: when QA fails, the task and QA's
report are re-sent to :class:`~agents.swe_agent.SWEAgent` for rework, and
PM_agent records the rework state (Requirements 2.4, 6.3). Because
:meth:`process` returns plain text (the :class:`BaseAgent` contract), the pass /
fail decision is exposed separately via :meth:`is_pass` / :meth:`is_fail`, which
parse a report the same way regardless of who produced it. This keeps
detection aligned with the :class:`~core.llm_client.MockLLMClient` QA renderer,
whose reports contain a ``Result: PASS`` line.

On failure, :meth:`process` does not return the raw model text as-is: it wraps
it in a *structured failure report* (a clear verdict line plus the failing
details) so the SWE agent has actionable, consistently-shaped guidance on the
rework pass. That report is what the pipeline stores in ``task.qa_output`` and
feeds back to SWE under one of its ``qa_output`` / ``qa_report`` keys
(Requirement 6.4).

Requirements:
    2.4 (QA_agent runs tests and returns a pass/fail verdict; on failure the
         report is returned to SWE_agent for repair and PM_agent records the
         rework),
    6.3 (on QA failure the task and its failure report are re-sent to SWE for
         rework and PM is notified),
    6.4 (the failure report is structured so SWE can act on it during rework).
"""

from __future__ import annotations

import re

from agents.base_agent import BaseAgent
from core.llm_client import MOCK_MODE_MARKER
from models.task import Task

# Default system prompt used when an agent config does not supply one. Real
# deployments configure this via the QA_agent.yaml ``system_prompt`` field; it
# is kept here so the agent is usable stand-alone (e.g. in tests).
DEFAULT_QA_SYSTEM_PROMPT = (
    "You are QA_agent, an experienced quality-assurance engineer. Design and "
    "run tests for the implementation you are given, then report a clear "
    "verdict. Begin your report with a line 'Result: PASS' or 'Result: FAIL'. "
    "When the result is FAIL, list every failing check with enough detail for "
    "the engineer (SWE_agent) to fix it."
)

# Verdict labels used in QA reports and failure-report wrappers.
VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"

# Context key carrying the SWE agent's implementation to verify (matches the
# Task model's ``swe_output`` field).
SWE_OUTPUT_KEY = "swe_output"

# Context key carrying the PM plan / acceptance criteria, useful for QA to know
# what to verify against (matches the Task model's ``pm_output`` field).
PM_OUTPUT_KEY = "pm_output"

# Matches an explicit verdict line such as "Result: PASS" / "Verdict - FAIL".
_RESULT_PATTERN = re.compile(r"result\s*[:\-]\s*(pass|fail)", re.IGNORECASE)
# Word-boundary fallbacks. ``\bfail\b`` deliberately does not match "Failed" and
# ``\bpass\b`` does not match "Passed", so summary lines like "Failed: 0" do not
# flip the verdict.
_FAIL_WORD = re.compile(r"\bfail\b", re.IGNORECASE)
_PASS_WORD = re.compile(r"\bpass\b", re.IGNORECASE)


class QAAgent(BaseAgent):
    """Quality-Assurance agent: verifies SWE output and returns a pass/fail report.

    Subclasses :class:`BaseAgent`; only :meth:`process` is implemented.
    Orchestration (timing, retry, logging, failure handling) is inherited from
    :meth:`BaseAgent.run`. The pass/fail decision is exposed via the
    :meth:`is_pass` / :meth:`is_fail` classmethods so the pipeline can route the
    task (proceed vs. rework) without re-running the agent.
    """

    def process(self, task: Task, context: dict | None = None) -> str:
        """Test ``task``'s implementation and return a verdict-bearing report.

        The SWE implementation is read from ``context`` (Requirement 6.2) and
        folded into the prompt along with the PM plan / acceptance criteria.
        The model's report is returned unchanged when it passes; when it fails,
        it is wrapped in a structured failure report so SWE_agent has
        actionable, consistently-shaped guidance for rework (Requirement 6.4).

        Args:
            task: The task being verified.
            context: Upstream context. Normally carries the SWE implementation
                under ``swe_output`` and the PM plan under ``pm_output``.

        Returns:
            The QA report. Callers determine the outcome with :meth:`is_pass` /
            :meth:`is_fail`; a failing report is a structured failure report.
        """

        prompt = self._build_prompt(task, context)
        system_prompt = self.config.system_prompt or DEFAULT_QA_SYSTEM_PROMPT
        report = self.call_llm(prompt, system_prompt=system_prompt)

        if self.is_pass(report):
            return report
        return self._build_failure_report(task, report)

    # -- verdict detection ---------------------------------------------------

    @classmethod
    def is_pass(cls, report: str | None) -> bool:
        """Return whether a QA ``report`` represents a passing verdict.

        Detection is intentionally conservative so an unclear report is treated
        as a failure (routing the task to rework rather than silently accepting
        unverified work):

            1. An explicit ``Result: PASS`` / ``Result: FAIL`` line wins.
            2. Otherwise, a lone word-boundary ``PASS`` (with no ``FAIL``) is a
               pass; anything else — a lone ``FAIL``, both, or neither — is a
               failure.
        """

        text = (report or "").strip()
        if not text:
            return False

        match = _RESULT_PATTERN.search(text)
        if match is not None:
            return match.group(1).upper() == VERDICT_PASS

        has_pass = _PASS_WORD.search(text) is not None
        has_fail = _FAIL_WORD.search(text) is not None
        return has_pass and not has_fail

    @classmethod
    def is_fail(cls, report: str | None) -> bool:
        """Return whether a QA ``report`` represents a failing verdict."""

        return not cls.is_pass(report)

    # -- prompt / failure-report construction --------------------------------

    @classmethod
    def _build_prompt(cls, task: Task, context: dict | None) -> str:
        """Compose the QA prompt from the task, the SWE output, and the PM plan.

        The prompt always states the task and asks for an explicit verdict
        line; the SWE implementation (the thing under test) and the PM plan /
        acceptance criteria are included when present.
        """

        context = context or {}

        lines: list[str] = [
            "Act as QA. Design and run tests for the following task's "
            "implementation, then report the outcome.",
            "Start your report with a line 'Result: PASS' or 'Result: FAIL'.",
            "",
            f"Task ID: {task.id}",
            f"Title: {task.title}",
            f"Description: {task.description}",
            f"Priority: {task.priority.value}",
        ]

        swe_code = _clean(context.get(SWE_OUTPUT_KEY))
        if swe_code:
            lines.append("")
            lines.append("Implementation to verify (from SWE_agent):")
            lines.append(swe_code)

        pm_plan = _clean(context.get(PM_OUTPUT_KEY))
        if pm_plan:
            lines.append("")
            lines.append("PM plan / acceptance criteria to verify against:")
            lines.append(pm_plan)

        extra = _format_extra_context(context)
        if extra:
            lines.append("")
            lines.append("Additional context:")
            lines.append(extra)

        return "\n".join(lines)

    @staticmethod
    def _build_failure_report(task: Task, report: str) -> str:
        """Wrap a failing model report into a structured failure report.

        The result leads with an unambiguous ``Result: FAIL`` verdict (so
        :meth:`is_pass` continues to classify it as a failure) and preserves the
        original report as the failing details, giving SWE_agent everything it
        needs for the rework pass (Requirement 6.4). Any ``[MOCK MODE]`` marker
        on the raw report is preserved so downstream consumers still see it.
        """

        marker = ""
        body = report.strip()
        if body.startswith(MOCK_MODE_MARKER):
            marker = f"{MOCK_MODE_MARKER} "
            body = body[len(MOCK_MODE_MARKER):].strip()

        return "\n".join(
            [
                f"{marker}QA FAILURE REPORT",
                f"Task ID: {task.id}",
                f"Task title: {task.title}",
                f"Result: {VERDICT_FAIL}",
                "Verdict: The implementation did not pass QA and must be reworked.",
                "",
                "Failing details (from the test report):",
                body,
                "",
                "Required action for SWE_agent: address every failing item "
                "listed above and resubmit for verification.",
            ]
        )


def _clean(value: object) -> str:
    """Normalise a context value to a stripped string ("" for missing/empty)."""

    if value is None:
        return ""
    return str(value).strip()


def _format_extra_context(context: dict) -> str:
    """Render context entries not already handled into readable prompt text.

    The SWE output and PM plan keys are consumed explicitly elsewhere, so they
    are excluded here to avoid duplicating them in the prompt.
    """

    handled = {SWE_OUTPUT_KEY, PM_OUTPUT_KEY}
    parts: list[str] = []
    for key, value in context.items():
        if key in handled:
            continue
        cleaned = _clean(value)
        if not cleaned:
            continue
        parts.append(f"- {key}: {cleaned}")
    return "\n".join(parts)
