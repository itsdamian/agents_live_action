// Test-only builders for events and state. Not imported by app code.
import { factoryReducer, initialState, type FactoryState } from "../state/reducer";
import type {
  AgentView,
  EventDataMap,
  EventName,
  Priority,
  Snapshot,
  TaskRecord,
  TaskStatus,
  TaskSummary,
} from "../types/events";

export const mockEff = (reason: "configured_mock" | "force_mock" | "missing_key" = "configured_mock") => ({
  provider: "mock" as const,
  model: "mock-model",
  source: "project" as const,
  mock_reason: reason,
});

export function agent(id: string, over: Partial<AgentView> = {}): AgentView {
  const kind = id === "qa" ? "check" : "make";
  return {
    id,
    display_name: id.toUpperCase(),
    duty: "do things",
    kind,
    look: "cap",
    builtin: ["pm", "swe", "qa"].includes(id),
    llm: null,
    effective_llm: mockEff(),
    ...over,
  };
}

export const BUILTIN = (): AgentView[] => [agent("pm"), agent("swe"), agent("qa")];

export function summary(id = "t1", status: TaskStatus = "in_progress", rework = 0, title = "加登入頁"): TaskSummary {
  return { id, title, priority: "high" as Priority, status, rework_count: rework };
}

export function record(id = "t1", status: TaskStatus = "pending", rework = 0): TaskRecord {
  return {
    ...summary(id, status, rework),
    description: "d",
    approval: "none",
    max_reworks: 3,
    created_at: "2026-11-17T01:00:00",
    updated_at: "2026-11-17T01:00:00",
    pm_output: null,
    swe_output: null,
    qa_output: null,
  };
}

export function snapshot(over: Partial<Snapshot> = {}): Snapshot {
  const agents = over.agents ?? BUILTIN();
  return {
    pipeline: {
      project_id: "demo",
      stations: {
        pm: { status: "idle", task_id: null, task_title: null },
        swe: { status: "idle", task_id: null, task_title: null },
        qa: { status: "idle", task_id: null, task_title: null },
      },
      queue_size: 0,
      done_count: 0,
      failed_count: 0,
      budget_paused: false,
      awaiting_approval: [],
    },
    tasks: [],
    mock_mode: true,
    providers: [],
    devops_alerts: [],
    usage: {
      project: { input_tokens: 0, output_tokens: 0, cost: 0, call_count: 0 },
      daily_cost: 0,
      daily_budget_usd: 5,
      project_budget_usd: 20,
      budget_paused: false,
    },
    engine: "langgraph",
    task_store: "db",
    force_mock: false,
    llm: { project: "mock", force_mock: false, anthropic_key_present: true, models: [{ id: "claude-opus-5-5", label: "Claude Opus 5.5" }] },
    agents,
    pipelines: [{ id: "main", name: "主線", seats: agents.map((a) => a.id), pending_seats: null }],
    live: {},
    history_counts: { pending: 0, in_progress: 0, awaiting_approval: 0, done: 0, failed: 0 },
    ...over,
  };
}

/** Apply one server event at time `now`. */
export function apply<E extends EventName>(state: FactoryState, event: E, data: EventDataMap[E], now = 0): FactoryState {
  return factoryReducer(state, { event, data, now } as never);
}

/** A state that has already received a default three-seat snapshot. */
export function ready(over: Partial<Snapshot> = {}, now = 0): FactoryState {
  return apply(initialState("demo"), "snapshot", snapshot(over), now);
}
