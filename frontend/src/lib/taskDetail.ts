// Replay mode has no backend: the detail panel shows what the store knows about the task.

import type { FactoryState } from "../state/reducer";
import type { TaskDetailResponse } from "../types/api";

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
