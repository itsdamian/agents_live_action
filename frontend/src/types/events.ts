// Server event contract. Source of truth: specs/live-action-hud/plan.md §4.2, §6 (FROZEN 2026-11-17)
// plus the pre-existing events emitted by backend/core/pipeline.py and agents/devops_agent.py.
// Do not add or remove fields here without a contract change.

export type Priority = "critical" | "high" | "medium" | "low";
export type TaskStatus = "pending" | "in_progress" | "awaiting_approval" | "done" | "failed";
export type Approval = "none" | "after_swe" | "after_qa";
export type AgentKind = "make" | "check";
export type Look = "cap" | "glasses" | "headset" | "bow" | "antenna" | "beret";

export const LOOKS: readonly Look[] = ["cap", "glasses", "headset", "bow", "antenna", "beret"];
export const PRIORITIES: readonly Priority[] = ["critical", "high", "medium", "low"];
export const TASK_STATUSES: readonly TaskStatus[] = [
  "pending",
  "in_progress",
  "awaiting_approval",
  "done",
  "failed",
];

// ---- shared shapes ---------------------------------------------------------

/** `Orchestrator._task_summary`. */
export interface TaskSummary {
  id: string;
  title: string;
  priority: Priority;
  status: TaskStatus;
  rework_count: number;
}

/** `Task.model_dump(mode="json")` as listed in the snapshot. */
export interface TaskRecord extends TaskSummary {
  description: string;
  approval: Approval;
  max_reworks: number;
  created_at: string;
  updated_at: string;
  pm_output: string | null;
  swe_output: string | null;
  qa_output: string | null;
}

export interface UsageSummary {
  input_tokens: number;
  output_tokens: number;
  cost: number;
  call_count: number;
}

export interface UsageRecord {
  project_id: string;
  task_id: string;
  provider: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cost: number;
  timestamp: string;
}

/** D21: `"mock"` or an Anthropic whitelist model. */
export type LlmChoice = "mock" | { provider: "anthropic"; model: string };

export type MockReason = "force_mock" | "missing_key" | "configured_mock";

export interface EffectiveLlm {
  provider: "mock" | "anthropic";
  model: string;
  source: "agent" | "project";
  mock_reason: MockReason | null;
}

/** plan §6.3. `llm` is the agent's own setting (`null` follows the project default). */
export interface AgentView {
  id: string;
  display_name: string;
  duty: string;
  kind: AgentKind;
  look: Look;
  builtin: boolean;
  llm: LlmChoice | null;
  effective_llm: EffectiveLlm;
}

export interface PipelineView {
  id: string;
  name: string;
  seats: string[];
  pending_seats: string[] | null;
}

/** `{id, name, seats}` as carried by `pipeline:updated`. */
export type PipelineCore = Pick<PipelineView, "id" | "name" | "seats">;

export type DevOpsLevel = "info" | "warning" | "critical";

export interface DevOpsAlertData {
  level: DevOpsLevel;
  message: string;
  metric: string;
  value: number;
}

// ---- snapshot --------------------------------------------------------------

export type StationStatus = "idle" | "working" | "awaiting_approval";

export interface StationSnapshot {
  status: StationStatus;
  task_id: string | null;
  task_title: string | null;
}

export interface AwaitingEntry {
  task: TaskSummary;
  station: string;
  approval: Approval;
}

export interface PipelineSnapshot {
  project_id: string;
  /** Keyed by seat id; the default lineup has exactly pm / swe / qa. */
  stations: Record<string, StationSnapshot>;
  queue_size: number;
  done_count: number;
  failed_count: number;
  budget_paused: boolean;
  awaiting_approval: AwaitingEntry[];
  usage?: UsageSummary;
}

export interface UsageOverview {
  project: UsageSummary;
  daily_cost: number;
  daily_budget_usd: number | null;
  project_budget_usd: number | null;
  budget_paused: boolean;
}

export interface LlmModelOption {
  id: string;
  label: string;
}

export interface LlmInfo {
  project: LlmChoice;
  force_mock: boolean;
  anthropic_key_present: boolean;
  models: LlmModelOption[];
}

export type LivePhase = "intake" | "thinking" | "output" | "verdict";

export interface LiveEntry {
  task_id: string;
  phase: LivePhase;
  text: string;
}

export interface HistoryCounts {
  pending: number;
  in_progress: number;
  awaiting_approval: number;
  done: number;
  failed: number;
}

export interface Snapshot {
  pipeline: PipelineSnapshot;
  tasks: TaskRecord[];
  mock_mode: boolean;
  providers: string[];
  devops_alerts: DevOpsAlertData[];
  usage: UsageOverview;
  // plan §6.4 additions
  engine: string;
  task_store: "file" | "db";
  force_mock: boolean;
  llm: LlmInfo;
  agents: AgentView[];
  pipelines: PipelineView[];
  live: Record<string, LiveEntry>;
  history_counts: HistoryCounts;
}

// ---- event data ------------------------------------------------------------

export interface TaskCreatedData {
  task: TaskSummary;
  queue_size: number;
}

export interface StationWorkingData {
  station: string;
  status: "working";
  task: TaskSummary;
}

export interface StationFinishedData {
  station: string;
  status: "done" | "error";
  task: TaskSummary;
  success: boolean;
  error: string | null;
  execution_time: number;
}

/** `station` is the rework *target* (plan §6.2). */
export interface StationReworkData {
  station: string;
  status: "rework";
  task: TaskSummary;
}

export type StationUpdateData = StationWorkingData | StationFinishedData | StationReworkData;

export interface TaskCompletedData {
  task: TaskSummary;
  done_count: number;
  deliverables: string[];
}

export interface TaskFailedData {
  task: TaskSummary;
  reason: string;
  failed_count: number;
}

export interface UsageUpdateData {
  task_id: string;
  task: TaskSummary;
  record: UsageRecord;
  task_usage: UsageSummary;
  project_usage: UsageSummary;
  daily_cost: number;
}

export interface AwaitingApprovalData {
  task: TaskSummary;
  station: string;
  approval: Approval;
}

export interface ApprovedData {
  task: TaskSummary;
  station: string;
}

export interface RejectedData {
  task: TaskSummary;
  station: string;
  note: string;
}

export interface AgentPhaseData {
  task_id: string;
  station: string;
  step: number;
  of: number;
  phase: LivePhase;
  text: string;
  /** Only meaningful on `verdict`; `null` for every other phase (plan §6.3a). */
  passed: boolean | null;
  rework_target: string | null;
}

/** Stage 2 (not in the MVP backend, typed so the reducer can ignore it explicitly). */
export interface AgentTokenData {
  task_id: string;
  station: string;
  attempt: number;
  seq: number;
  text: string;
  final: boolean;
  truncated: boolean;
}

export interface AgentJoinedData {
  agent: AgentView;
  pipeline_id: string;
  position: number;
}

export interface AgentLeftData {
  agent_id: string;
  pipeline_id: string;
}

export interface PipelineUpdatedData {
  pipeline: PipelineCore;
  agents: AgentView[];
}

export interface PipelinePendingData {
  pipeline_id: string;
  seats: string[];
  reason: string;
  task_id: string;
}

export interface LlmUpdatedData {
  project_llm: LlmChoice;
  agents: AgentView[];
  effective: "now" | "next_task";
}

export interface EventDataMap {
  snapshot: Snapshot;
  "task:created": TaskCreatedData;
  "station:update": StationUpdateData;
  "task:completed": TaskCompletedData;
  "task:failed": TaskFailedData;
  "usage:update": UsageUpdateData;
  "devops:alert": DevOpsAlertData;
  "task:awaiting_approval": AwaitingApprovalData;
  "task:approved": ApprovedData;
  "task:rejected": RejectedData;
  "agent:phase": AgentPhaseData;
  "agent:token": AgentTokenData;
  "agent:joined": AgentJoinedData;
  "agent:left": AgentLeftData;
  "pipeline:updated": PipelineUpdatedData;
  "pipeline:pending": PipelinePendingData;
  "llm:updated": LlmUpdatedData;
}

export type EventName = keyof EventDataMap;

export const EVENT_NAMES: readonly EventName[] = [
  "snapshot",
  "task:created",
  "station:update",
  "task:completed",
  "task:failed",
  "usage:update",
  "devops:alert",
  "task:awaiting_approval",
  "task:approved",
  "task:rejected",
  "agent:phase",
  "agent:token",
  "agent:joined",
  "agent:left",
  "pipeline:updated",
  "pipeline:pending",
  "llm:updated",
];

/** One WebSocket frame (`core/ws_manager.make_event`); `pipeline_id` only when non-null (E3). */
export type ServerEvent = {
  [K in EventName]: {
    event: K;
    data: EventDataMap[K];
    project_id?: string;
    pipeline_id?: string;
  };
}[EventName];

/** One line of a `public/replay/*.jsonl` file; `t` is milliseconds from the start. */
export type ReplayLine = ServerEvent & { t: number };
