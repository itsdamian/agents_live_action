"""Abstract base class shared by every pipeline agent.

Concrete agents (PM / SWE / QA / DevOps) subclass :class:`BaseAgent` and
implement a single method, :meth:`process`, which turns a task (plus the
upstream context handed over by the previous station) into this agent's text
output. Everything that is *not* agent-specific lives here so subclasses stay
small and never re-implement it (see steering ``structure.md``: "Agent 日誌統一
由 base_agent.py 寫入，不要在各 agent 子類別重複實作"):

* **Retry** — :meth:`call_llm` wraps every LLM request in a ``tenacity`` retry
  with exponential backoff, attempting at most ``max_retries`` times as
  configured on the :class:`~models.agent.AgentConfig` (design "LLM 呼叫失敗").
* **Logging** — :meth:`run` writes one log file per task execution to
  ``projects/{project}/logs/{timestamp}_{agent_name}_{task_id}.log`` capturing
  the input prompt(s), the output response(s), and the execution time
  (Requirements 9.1, 9.2).
* **Error handling** — if ``process`` (or the retried LLM call underneath it)
  ultimately raises, :meth:`run` captures the error, logs it, marks the task
  ``failed``, and returns a failed :class:`AgentResult` instead of propagating,
  so one bad task never crashes the whole pipeline (Requirement 6.5).

Requirements:
    9.1 (create a per-task log file named
         ``{timestamp}_{agent_name}_{task_id}.log`` under the project's logs/),
    9.2 (write the input prompt, output response, and execution time to it),
    6.5 (catch agent exceptions, log them, mark the task failed, do not crash
         the pipeline).
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.llm_client import LLMClient, get_llm_client
from core.project_scanner import DEFAULT_PROJECTS_DIR
from models.agent import AgentConfig
from models.task import Status, Task

logger = logging.getLogger(__name__)

# Subdirectory (relative to a project root) where per-task logs are written.
LOGS_DIRNAME = "logs"

# Timestamp format used in log filenames, e.g. 20260613_120000. Matches the
# design example: ``20260613_120000_PM_agent_task-001.log``.
_LOG_TIMESTAMP_FMT = "%Y%m%d_%H%M%S"


@dataclass
class AgentResult:
    """Outcome of a single :meth:`BaseAgent.run` call.

    Returned in both the success and failure cases so callers (the pipeline)
    can react without having to catch exceptions.
    """

    agent_name: str
    task_id: str
    success: bool
    output: str | None = None
    error: str | None = None
    execution_time: float = 0.0
    log_path: str | None = None
    # Ordered (prompt, response) pairs captured during the run, oldest first.
    interactions: list[tuple[str, str]] = field(default_factory=list)
    # LLM token usage accumulated across this run's calls (task 16).
    provider: str = "unknown"
    llm_model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


class BaseAgent(ABC):
    """Abstract base for all agents in the PM -> SWE -> QA pipeline.

    Subclasses implement :meth:`process` and typically call :meth:`call_llm`
    from within it. Orchestration (timing, retry, logging, error handling) is
    provided by :meth:`run`, which subclasses do not override.

    Args:
        config: The agent's parsed configuration. ``max_retries`` bounds the
            LLM retry attempts and ``system_prompt`` is used as the default
            system prompt for :meth:`call_llm`.
        llm_client: LLM client to use. When omitted, one is built from
            ``config`` via :func:`~core.llm_client.get_llm_client` (which falls
            back to mock mode when no API key is configured).
        projects_dir: Root ``projects/`` directory. Only used as a fallback for
            resolving log paths when :meth:`run` is not given an explicit
            ``project_path``. Defaults to the repo-root ``projects/`` dir.
        retry_wait_multiplier: Exponential-backoff multiplier (seconds) between
            LLM retries. Tests may pass ``0`` to disable waiting.
        retry_wait_max: Upper bound (seconds) on the backoff wait.
    """

    def __init__(
        self,
        config: AgentConfig,
        llm_client: LLMClient | None = None,
        projects_dir: str | Path | None = None,
        retry_wait_multiplier: float = 1.0,
        retry_wait_max: float = 10.0,
    ) -> None:
        self.config = config
        self.name = config.name
        self.llm_client = llm_client if llm_client is not None else get_llm_client(config)
        self._projects_dir = (
            Path(projects_dir) if projects_dir is not None else DEFAULT_PROJECTS_DIR
        )
        self._retry_wait_multiplier = retry_wait_multiplier
        self._retry_wait_max = retry_wait_max
        # (prompt, response) pairs captured during the current run; reset at the
        # start of every run() so logs only reflect that execution.
        self._interactions: list[tuple[str, str]] = []
        # Token usage accumulated across the current run's LLM calls (task 16).
        self._input_tokens = 0
        self._output_tokens = 0

    # -- subclass contract ---------------------------------------------------

    @abstractmethod
    def process(self, task: Task, context: dict | None = None) -> str:
        """Produce this agent's output for ``task``.

        Implementations build a prompt from the task and the upstream
        ``context`` (the previous station's output), obtain a response via
        :meth:`call_llm`, and return the agent's textual output.

        Should raise on unrecoverable failure; :meth:`run` converts a raised
        exception into a failed :class:`AgentResult` (Requirement 6.5).

        Args:
            task: The task being processed.
            context: Optional upstream context (e.g. ``{"pm_output": "..."}``).

        Returns:
            The agent's output text.
        """
        raise NotImplementedError

    # -- LLM access with retry ----------------------------------------------

    def call_llm(self, prompt: str, system_prompt: str | None = None) -> str:
        """Call the LLM with exponential-backoff retry, capturing the exchange.

        The call is retried up to ``config.max_retries`` times (at least once)
        with exponential backoff between attempts (design "LLM 呼叫失敗"). The
        final prompt/response pair is recorded so :meth:`run` can write it to
        the task log (Requirement 9.2).

        Args:
            prompt: The task prompt to send.
            system_prompt: System prompt override. Defaults to the agent's
                configured ``system_prompt``.

        Returns:
            The LLM's text response.

        Raises:
            Exception: Re-raises the last error if every attempt fails.
        """

        resolved_system_prompt = (
            system_prompt if system_prompt is not None else self.config.system_prompt
        )
        attempts = max(1, self.config.max_retries)

        retryer = Retrying(
            stop=stop_after_attempt(attempts),
            wait=wait_exponential(
                multiplier=self._retry_wait_multiplier,
                max=self._retry_wait_max,
            ),
            retry=retry_if_exception_type(Exception),
            reraise=True,
        )

        response = retryer(self.llm_client.call, prompt, resolved_system_prompt)
        self._interactions.append((prompt, response))
        # Accumulate token usage for cost tracking (task 16). Clients that do
        # not report usage leave last_usage as None and contribute nothing.
        usage = getattr(self.llm_client, "last_usage", None)
        if usage is not None:
            self._input_tokens += usage.input_tokens
            self._output_tokens += usage.output_tokens
        return response

    # -- orchestration -------------------------------------------------------

    def run(
        self,
        task: Task,
        context: dict | None = None,
        project_path: str | Path | None = None,
    ) -> AgentResult:
        """Process ``task`` end-to-end: time it, log it, and handle failures.

        On success the task's output is returned in an :class:`AgentResult` and
        a log file is written. If :meth:`process` raises (including after the
        LLM retries are exhausted), the error is captured and logged, the task
        is marked :attr:`~models.task.Status.FAILED`, and a failed
        :class:`AgentResult` is returned rather than propagating the exception
        (Requirement 6.5).

        Args:
            task: The task to process.
            context: Optional upstream context passed to :meth:`process`.
            project_path: Project root under which the ``logs/`` directory
                lives. Falls back to ``<projects_dir>/<project inferred from
                nothing>`` — callers (the pipeline) should pass this explicitly.

        Returns:
            An :class:`AgentResult` describing the outcome.
        """

        self._interactions = []
        self._input_tokens = 0
        self._output_tokens = 0
        start = time.perf_counter()

        try:
            output = self.process(task, context)
        except Exception as exc:  # Requirement 6.5: never crash the pipeline.
            elapsed = time.perf_counter() - start
            logger.exception(
                "Agent %s failed processing task %s: %s", self.name, task.id, exc
            )
            task.status = Status.FAILED
            log_path = self._write_log(task, project_path, elapsed, error=exc)
            return AgentResult(
                agent_name=self.name,
                task_id=task.id,
                success=False,
                output=None,
                error=str(exc),
                execution_time=elapsed,
                log_path=str(log_path) if log_path is not None else None,
                interactions=list(self._interactions),
                provider=getattr(self.llm_client, "provider", "unknown"),
                llm_model=self.config.llm_model,
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
            )

        elapsed = time.perf_counter() - start
        log_path = self._write_log(task, project_path, elapsed, error=None)
        return AgentResult(
            agent_name=self.name,
            task_id=task.id,
            success=True,
            output=output,
            error=None,
            execution_time=elapsed,
            log_path=str(log_path) if log_path is not None else None,
            interactions=list(self._interactions),
            provider=getattr(self.llm_client, "provider", "unknown"),
            llm_model=self.config.llm_model,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
        )

    # -- logging -------------------------------------------------------------

    def _resolve_logs_dir(self, project_path: str | Path | None) -> Path:
        """Return the ``logs/`` directory for a run, creating it if needed."""

        base = Path(project_path) if project_path is not None else self._projects_dir
        logs_dir = base / LOGS_DIRNAME
        logs_dir.mkdir(parents=True, exist_ok=True)
        return logs_dir

    def _log_filename(self, task_id: str, when: datetime | None = None) -> str:
        """Build the ``{timestamp}_{agent_name}_{task_id}.log`` filename."""

        stamp = (when or datetime.now()).strftime(_LOG_TIMESTAMP_FMT)
        return f"{stamp}_{self.name}_{task_id}.log"

    def _write_log(
        self,
        task: Task,
        project_path: str | Path | None,
        execution_time: float,
        error: BaseException | None = None,
    ) -> Path | None:
        """Write the per-task execution log file (Requirements 9.1, 9.2).

        Captures the input prompt(s), the output response(s), and the execution
        time; on failure the captured error is appended. Log writing must never
        itself break the pipeline, so any I/O error here is swallowed with a
        warning and ``None`` is returned.
        """

        try:
            logs_dir = self._resolve_logs_dir(project_path)
            log_path = logs_dir / self._log_filename(task.id)
            log_path.write_text(
                self._render_log(task, execution_time, error), encoding="utf-8"
            )
            return log_path
        except OSError as exc:  # pragma: no cover - defensive I/O guard
            logger.warning(
                "Failed to write log for agent %s / task %s: %s",
                self.name,
                task.id,
                exc,
            )
            return None

    def _render_log(
        self,
        task: Task,
        execution_time: float,
        error: BaseException | None,
    ) -> str:
        """Render the human-readable contents of a task log file."""

        status = "FAILED" if error is not None else "SUCCESS"
        lines: list[str] = [
            f"agent: {self.name}",
            f"role: {self.config.role}",
            f"task_id: {task.id}",
            f"task_title: {task.title}",
            f"llm_model: {self.config.llm_model}",
            f"mock_mode: {self.llm_client.is_mock}",
            f"status: {status}",
            f"execution_time_seconds: {execution_time:.4f}",
            f"logged_at: {datetime.now().isoformat()}",
            "",
        ]

        if self._interactions:
            for index, (prompt, response) in enumerate(self._interactions, start=1):
                lines.append(f"=== interaction {index} ===")
                lines.append("--- input prompt ---")
                lines.append(prompt)
                lines.append("--- output response ---")
                lines.append(response)
                lines.append("")
        else:
            lines.append("(no LLM interaction recorded)")
            lines.append("")

        if error is not None:
            lines.append("--- error ---")
            lines.append(f"{type(error).__name__}: {error}")
            lines.append("")

        return "\n".join(lines)
