"""SWE (Software Engineer) agent — the second station in the pipeline.

:class:`SWEAgent` takes the PM agent's execution plan (handed over as upstream
*context*) and produces the code that implements the task. Its output becomes
the context that :class:`QA_agent` verifies (Requirements 2.1, 6.2).

When QA rejects the work, the pipeline re-sends the task to SWE for *rework*.
On that second (and subsequent) pass the context additionally carries QA's
structured failure report; :meth:`process` folds that report into the prompt so
the model can fix precisely what QA flagged (Requirement 6.3).

Like :class:`~agents.pm_agent.PMAgent`, the agent is deliberately thin: it
builds a prompt from the task and context, delegates the LLM call — with retry
and logging — to :class:`~agents.base_agent.BaseAgent`, and returns the model's
text. In mock mode the :class:`~core.llm_client.MockLLMClient` SWE renderer
already emits a code block tagged ``[MOCK MODE]``.

Requirements:
    2.1 (SWE_agent writes code according to the PM plan it receives),
    6.2 (each station is called with the previous station's output as context;
         SWE receives the PM output),
    6.3 (on QA failure the task is re-sent to SWE with QA's failure report as
         supplementary context for the rework).
"""

from __future__ import annotations

from agents.base_agent import BaseAgent
from models.task import Task

# Default system prompt used when an agent config does not supply one. Real
# deployments configure this via the SWE_agent.yaml ``system_prompt`` field; it
# is kept here so the agent is usable stand-alone (e.g. in tests).
DEFAULT_SWE_SYSTEM_PROMPT = (
    "You are SWE_agent, an experienced software engineer. Implement the task "
    "by writing clean, correct, well-structured code that follows the PM's "
    "execution plan. When a QA failure report is provided, treat it as the "
    "primary guidance and fix exactly the problems it identifies."
)

# Context key carrying the PM agent's execution plan (matches the Task model's
# ``pm_output`` field and the base-class example ``{"pm_output": "..."}``).
PM_OUTPUT_KEY = "pm_output"

# Context keys that may carry QA's failure report on a rework pass. ``qa_output``
# mirrors the Task model field; the aliases are accepted so callers can be
# explicit that the payload is a *failure* report.
QA_REPORT_KEYS = ("qa_output", "qa_report", "qa_failure_report")


class SWEAgent(BaseAgent):
    """Software-Engineer agent: turns a PM plan (and QA feedback) into code.

    Subclasses :class:`BaseAgent`; only :meth:`process` is implemented.
    Orchestration (timing, retry, logging, failure handling) is inherited from
    :meth:`BaseAgent.run`.
    """

    def process(self, task: Task, context: dict | None = None) -> str:
        """Produce code for ``task`` from the PM plan and any QA feedback.

        Args:
            task: The task to implement.
            context: Upstream context. Normally contains the PM plan under
                ``pm_output`` (Requirement 6.2). On a rework pass it also
                carries QA's failure report under one of :data:`QA_REPORT_KEYS`
                (Requirement 6.3).

        Returns:
            The SWE agent's output — the implementation for the task.
        """

        prompt = self._build_prompt(task, context)
        system_prompt = self.with_no_execution_note(self.config.system_prompt or DEFAULT_SWE_SYSTEM_PROMPT)
        return self.call_llm(prompt, system_prompt=system_prompt)

    @classmethod
    def _build_prompt(cls, task: Task, context: dict | None) -> str:
        """Compose the SWE implementation prompt from the task and context.

        The prompt always states the task; when a PM plan is present it is
        included as the plan to follow, and when a QA failure report is present
        (rework) it is called out explicitly so the model prioritises fixing
        the reported problems.
        """

        context = context or {}
        qa_report = cls._extract_qa_report(context)
        is_rework = qa_report is not None or task.rework_count > 0

        lines: list[str] = []
        if is_rework:
            lines.append(
                "This is a REWORK. A previous implementation was rejected by "
                "QA. Fix the problems described in the QA failure report below."
            )
            lines.append(f"Rework attempt: {task.rework_count + 1}")
        else:
            lines.append("Implement the following task by writing code.")

        lines.extend(
            [
                "",
                f"Task ID: {task.id}",
                f"Title: {task.title}",
                f"Description: {task.description}",
                f"Priority: {task.priority.value}",
            ]
        )

        pm_plan = _clean(context.get(PM_OUTPUT_KEY))
        if pm_plan:
            lines.append("")
            lines.append("PM execution plan (follow this):")
            lines.append(pm_plan)

        if qa_report:
            lines.append("")
            lines.append("QA failure report (address every issue below):")
            lines.append(qa_report)

        extra = _format_extra_context(context)
        if extra:
            lines.append("")
            lines.append("Additional context:")
            lines.append(extra)

        return "\n".join(lines)

    @staticmethod
    def _extract_qa_report(context: dict) -> str | None:
        """Return the QA failure report from ``context`` if one is present."""

        for key in QA_REPORT_KEYS:
            value = _clean(context.get(key))
            if value:
                return value
        return None


def _clean(value: object) -> str:
    """Normalise a context value to a stripped string ("" for missing/empty)."""

    if value is None:
        return ""
    return str(value).strip()


def _format_extra_context(context: dict) -> str:
    """Render context entries not already handled into readable prompt text.

    The PM plan and QA report keys are consumed explicitly elsewhere, so they
    are excluded here to avoid duplicating them in the prompt.
    """

    handled = {PM_OUTPUT_KEY, *QA_REPORT_KEYS}
    parts: list[str] = []
    for key, value in context.items():
        if key in handled:
            continue
        cleaned = _clean(value)
        if not cleaned:
            continue
        parts.append(f"- {key}: {cleaned}")
    return "\n".join(parts)
