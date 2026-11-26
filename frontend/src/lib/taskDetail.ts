// Replay mode has no backend: the detail panel shows what the store knows about the task.

import type { FactoryState } from "../state/reducer";
import type { TaskDetailResponse } from "../types/api";
import type { AgentView } from "../types/events";

const ZERO = { input_tokens: 0, output_tokens: 0, cost: 0, call_count: 0 };

export function synthesizeTaskDetail(state: FactoryState, taskId: string): TaskDetailResponse | null {
  const t = state.tasks[taskId];
  if (!t) return null;
  return {
    task: {
      ...t,
      description: "",
      approval: state.awaiting[taskId]?.approval ?? "none",
      max_reworks: 3,
      created_at: "",
      updated_at: "",
      pm_output: null,
      swe_output: null,
      qa_output: null,
    },
    status_history: [],
    logs: [],
    deliverables: [],
    usage: state.usage.byTask[taskId] ?? ZERO,
    runs: [],
    agent_outputs: [],
  };
}

// The log-file agent names of the built-in stations; a custom agent's logs carry its id
// (same table as the backend's status_history_names, backend/api/tasks.py).
const BUILTIN_LOG_AGENTS: Record<string, string> = { PM_agent: "pm", SWE_agent: "swe", QA_agent: "qa" };

/** The display name for an agent name in a log file: the roster's name, else the name itself. */
export function logAgentName(agents: Record<string, AgentView>, name: string): string {
  const id = Object.hasOwn(BUILTIN_LOG_AGENTS, name) ? BUILTIN_LOG_AGENTS[name]! : name;
  return (Object.hasOwn(agents, id) && agents[id]!.display_name) || name;
}
