"""PM (Project Manager) agent — the first station in the pipeline.

:class:`PMAgent` analyses an incoming task and produces a task summary plus a
concrete execution plan (a set of sub-steps). That output becomes the upstream
*context* handed to :class:`SWE_agent` (and, transitively, :class:`QA_agent`),
so it is written to be directly usable as guidance for implementation and
verification (Requirements 2.3, 6.2).

The agent is deliberately thin: it builds a prompt from the task (and any
upstream context), delegates the LLM call — with retry and logging — to
:class:`~agents.base_agent.BaseAgent`, and returns the model's text. In mock
mode the :class:`~core.llm_client.MockLLMClient` PM renderer already emits a
task-analysis-plus-execution-plan shape tagged ``[MOCK MODE]``.

Requirements:
    2.3 (PM_agent outputs a task summary and execution plan usable as context
         for downstream agents),
    6.2 (each station is called with the previous station's output as context;
         PM is the first station and normally has no upstream output).
"""

from __future__ import annotations

from agents.base_agent import BaseAgent
from models.task import Task

# Default system prompt used when an agent config does not supply one. Real
# deployments configure this via the PM_agent.yaml ``system_prompt`` field; it
# is kept here so the agent is usable stand-alone (e.g. in tests).
DEFAULT_PM_SYSTEM_PROMPT = (
    "You are PM_agent, an experienced technical project manager. "
    "Analyse the incoming task, summarise it, and break it into a clear, "
    "ordered execution plan of concrete sub-steps for the engineering (SWE) "
    "and QA agents that follow you. Be specific and actionable."
)


class PMAgent(BaseAgent):
    """Project-Manager agent: turns a task into a summary and execution plan.

    Subclasses :class:`BaseAgent`; only :meth:`process` is implemented.
    Orchestration (timing, retry, logging, failure handling) is inherited from
    :meth:`BaseAgent.run`.
    """

    def process(self, task: Task, context: dict | None = None) -> str:
        """Analyse ``task`` and return a summary + execution plan.

        Args:
            task: The task to analyse.
            context: Optional upstream context. PM is the first station so this
                is normally empty; any provided values are appended to the
                prompt so the agent can react to (re-)planning requests.

        Returns:
            The PM's analysis text — a task summary followed by an ordered
            execution plan — suitable for use as downstream-agent context.
        """

        prompt = self._build_prompt(task, context)
        system_prompt = self.config.system_prompt or DEFAULT_PM_SYSTEM_PROMPT
        return self.call_llm(prompt, system_prompt=system_prompt)

    @staticmethod
    def _build_prompt(task: Task, context: dict | None) -> str:
        """Compose the PM analysis prompt from the task and any upstream context."""

        lines = [
            "Analyse the following task and produce:",
            "1. A concise task summary.",
            "2. An ordered execution plan of concrete sub-steps for the SWE and "
            "QA agents.",
            "",
            f"Task ID: {task.id}",
            f"Title: {task.title}",
            f"Description: {task.description}",
            f"Priority: {task.priority.value}",
        ]

        extra = _format_context(context)
        if extra:
            lines.append("")
            lines.append("Additional context:")
            lines.append(extra)

        return "\n".join(lines)


def _format_context(context: dict | None) -> str:
    """Render an upstream ``context`` dict into readable prompt text.

    Empty/None values are skipped so the prompt stays clean when PM is invoked
    as the first station (the common case).
    """

    if not context:
        return ""

    parts: list[str] = []
    for key, value in context.items():
        if value is None or value == "":
            continue
        parts.append(f"- {key}: {value}")
    return "\n".join(parts)
