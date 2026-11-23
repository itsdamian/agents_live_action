// REST contract. Source of truth: specs/live-action-hud/plan.md §7 (FROZEN 2026-11-17) and the
// existing endpoints in backend/api/{projects,tasks}.py.
// Response shapes follow §7.1a (added 2026-11-17 as part of the same freeze).
// Times are ISO 8601 strings, money is a number (USD), missing values are null (keys are never omitted).

import type {
  AgentKind,
  AgentView,
  Approval,
  HistoryCounts,
  LlmChoice,
  LlmModelOption,
  Look,
  PipelineView,
  Priority,
  TaskRecord,
  TaskStatus,
  UsageSummary,
} from "./events";

/** FastAPI error body: `detail` is a string (our HTTPException) or Pydantic's 422 list. */
export interface ApiErrorBody {
  detail?: string | Array<{ loc?: Array<string | number>; msg?: string }>;
  errors?: unknown[];
}

/** Normalised error thrown by `net/api.ts` (F4). */
export interface ApiError {
  status: number;
  detail: string;
  errors: unknown[];
}

// ---- projects --------------------------------------------------------------

export interface AgentConfigInfo {
  name: string;
  role: string;
  description: string;
  llm_provider: string;
  llm_model: string;
}

export interface ProjectInfo {
  id: string;
  name: string;
  path: string;
  agents: unknown[];
  task_count: number;
  active_task_count: number;
  status: string;
}

export interface ProjectsResponse {
  projects: ProjectInfo[];
  mock_mode: boolean;
  providers: string[];
}

/** `GET /api/health`; `force_mock` is added by backend Task 3b. */
export interface HealthResponse {
  status: string;
  mock_mode: boolean;
  providers: string[];
  force_mock: boolean;
}

// ---- tasks -----------------------------------------------------------------

export interface TasksResponse {
  tasks: TaskRecord[];
}

export interface NewTaskRequest {
  title: string;
  description?: string;
  priority?: Priority;
  approval?: Approval;
}

export interface NewTaskResponse {
  task: TaskRecord;
}

export interface ApproveResponse {
  task_id: string;
  decision: "approved";
}

export interface RejectRequest {
  note: string;
}

export interface RejectResponse {
  task_id: string;
  decision: "rejected";
  note: string;
}

export interface StatusHistoryEntry {
  agent: string;
  status: string;
  timestamp: string;
}

export interface LogEntry {
  name: string;
  agent: string;
  timestamp: string;
  status: string;
  execution_time: number;
  size: number;
  content: string;
  content_url: string;
}

export interface DeliverableEntry {
  name: string;
  size: number;
  download_url: string;
}

export interface TaskRunStep {
  seq: number;
  kind: "station" | "rework" | "gate";
  station: string;
  status: string;
  note: string | null;
  at: string;
}

/** `GET /tasks/{tid}` → `runs[]`, newest `run_no` first; `[]` in file mode (§7.1a). */
export interface TaskRun {
  run_no: number;
  pipeline_id: string | null;
  outcome: "done" | "failed" | null;
  reason: string | null;
  rework_count: number;
  started_at: string;
  finished_at: string | null;
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  imported: boolean;
  seats: SeatSnapshot[];
  steps: TaskRunStep[];
}

/** One entry per station, ordered by the newest run's seats; `content` is capped at 20,000 chars. */
export interface AgentOutput {
  station: string;
  display_name: string;
  content: string;
  truncated: boolean;
}

export interface TaskDetailResponse {
  task: TaskRecord;
  status_history: StatusHistoryEntry[];
  logs: LogEntry[];
  deliverables: DeliverableEntry[];
  usage: UsageSummary;
  runs: TaskRun[];
  agent_outputs: AgentOutput[];
}

// ---- agents, pipelines, settings -------------------------------------------

export interface AgentsResponse {
  agents: AgentView[];
  pipelines: PipelineView[];
}

export interface NewAgentRequest {
  display_name: string;
  duty: string;
  prompt: string;
  kind: AgentKind;
  look: Look;
  llm?: LlmChoice | null;
  id?: string;
  after?: string | null;
  pipeline_id?: string;
}

export type Effective = "now" | "next_task";

export interface NewAgentResponse {
  agent: AgentView;
  effective: Effective;
}

export interface AgentDetailResponse extends AgentView {
  prompt: string;
  llm_provider: string;
  llm_model: string;
}

export interface UpdateAgentRequest {
  display_name?: string;
  duty?: string;
  prompt?: string;
  kind?: AgentKind;
  look?: Look;
  llm?: LlmChoice | null;
}

/** `PUT /agents/{id}`; 200 = now, 202 = next_task. */
export interface UpdateAgentResponse {
  agent: AgentView;
  effective: Effective;
}

export interface DeleteAgentResponse {
  agent_id: string;
  effective: Effective;
}

export interface ImportAgentsResponse {
  agents: AgentView[];
  pipelines: PipelineView[];
  effective: Effective;
}

export interface PipelinesResponse {
  pipelines: PipelineView[];
}

export interface PutPipelineRequest {
  seats: string[];
}

export interface PutPipelineResponse {
  pipeline: PipelineView;
  effective: Effective;
}

/** `GET /settings`: the same information as the snapshot's `llm` (`llm` is `llm.project`). */
export interface SettingsResponse {
  llm: LlmChoice;
  force_mock: boolean;
  anthropic_key_present: boolean;
  models: LlmModelOption[];
}

export interface PutSettingsRequest {
  llm: LlmChoice;
}

export interface PutSettingsResponse extends SettingsResponse {
  effective: Effective;
}

/** `POST /tasks/{tid}/rerun` → 202; 409 when the task is not done/failed. */
export interface RerunResponse {
  task: TaskRecord;
}

// ---- history ---------------------------------------------------------------

export interface SeatSnapshot {
  id: string;
  display_name: string;
  look: Look;
  kind: AgentKind;
}

export interface HistoryItem {
  task_id: string;
  title: string;
  priority: Priority;
  status: TaskStatus;
  pipeline_id: string | null;
  outcome: "done" | "failed" | null;
  reason: string | null;
  rework_count: number;
  /** Station ids the note passed through; every id is present in `seats`. */
  path: string[];
  cost_usd: number | null;
  started_at: string | null;
  finished_at: string | null;
  deliverable_count: number;
  runs: number;
  stale: boolean;
  /** Newest run's seat snapshot (current lineup when no run yet; built-in three in file mode). */
  seats: SeatSnapshot[];
}

export interface HistoryResponse {
  items: HistoryItem[];
  total: number;
  counts: HistoryCounts;
}

export interface HistoryQuery {
  status?: TaskStatus[];
  pipeline?: string;
  limit?: number;
  offset?: number;
}
