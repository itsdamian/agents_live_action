// Runtime type checks for server frames, replay lines and history responses.
// The TypeScript types in ../types are the contract; these checks make sure data that actually
// arrives (WebSocket, replay files) matches them. Extra keys are tolerated (forward compatible);
// missing keys and wrong types are reported with a path.

import {
  EVENT_NAMES,
  LOOKS,
  PRIORITIES,
  TASK_STATUSES,
  type EventName,
  type ReplayLine,
  type ServerEvent,
} from "../types/events";
import type { HistoryResponse } from "../types/api";

type Errors = string[];
type Check = (v: unknown, path: string, errs: Errors) => void;

const OPTIONAL = Symbol("optional");
type Optional = { [OPTIONAL]: true; check: Check };
type Shape = Record<string, Check | Optional>;

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str: Check = (v, p, e) => {
  if (typeof v !== "string") e.push(`${p}: expected string`);
};
const num: Check = (v, p, e) => {
  if (typeof v !== "number" || !Number.isFinite(v)) e.push(`${p}: expected number`);
};
const int: Check = (v, p, e) => {
  if (typeof v !== "number" || !Number.isInteger(v)) e.push(`${p}: expected integer`);
};
const bool: Check = (v, p, e) => {
  if (typeof v !== "boolean") e.push(`${p}: expected boolean`);
};
const literal =
  (...values: readonly string[]): Check =>
  (v, p, e) => {
    if (typeof v !== "string" || !values.includes(v)) {
      e.push(`${p}: expected one of ${values.join("|")}`);
    }
  };
const nullable =
  (c: Check): Check =>
  (v, p, e) => {
    if (v !== null) c(v, p, e);
  };
const optional = (check: Check): Optional => ({ [OPTIONAL]: true, check });
const arr =
  (c: Check): Check =>
  (v, p, e) => {
    if (!Array.isArray(v)) {
      e.push(`${p}: expected array`);
      return;
    }
    v.forEach((item, i) => c(item, `${p}[${i}]`, e));
  };
const record =
  (c: Check): Check =>
  (v, p, e) => {
    if (!isRecord(v)) {
      e.push(`${p}: expected object`);
      return;
    }
    for (const [k, item] of Object.entries(v)) c(item, `${p}.${k}`, e);
  };
const obj =
  (shape: Shape): Check =>
  (v, p, e) => {
    if (!isRecord(v)) {
      e.push(`${p}: expected object`);
      return;
    }
    for (const [key, spec] of Object.entries(shape)) {
      const isOpt = typeof spec === "object" && OPTIONAL in spec;
      const check = isOpt ? (spec as Optional).check : (spec as Check);
      if (!(key in v)) {
        if (!isOpt) e.push(`${p}.${key}: missing`);
        continue;
      }
      check(v[key], `${p}.${key}`, e);
    }
  };

// ---- shared shapes ---------------------------------------------------------

const priority = literal(...PRIORITIES);
const taskStatus = literal(...TASK_STATUSES);
const approval = literal("none", "after_swe", "after_qa");
const look = literal(...LOOKS);
const kind = literal("make", "check");
const livePhase = literal("intake", "thinking", "output", "verdict");

const taskSummary = obj({
  id: str,
  title: str,
  priority,
  status: taskStatus,
  rework_count: int,
});

const taskRecord = obj({
  id: str,
  title: str,
  priority,
  status: taskStatus,
  rework_count: int,
  description: str,
  approval,
  max_reworks: int,
  created_at: str,
  updated_at: str,
  pm_output: nullable(str),
  swe_output: nullable(str),
  qa_output: nullable(str),
});

const usageSummary = obj({ input_tokens: int, output_tokens: int, cost: num, call_count: int });
const usageRecord = obj({
  project_id: str,
  task_id: str,
  provider: str,
  model: str,
  input_tokens: int,
  output_tokens: int,
  cost: num,
  timestamp: str,
});

const llmChoice: Check = (v, p, e) => {
  if (v === "mock") return;
  obj({ provider: literal("anthropic"), model: str })(v, p, e);
};

const effectiveLlm = obj({
  provider: literal("mock", "anthropic"),
  model: str,
  source: literal("agent", "project"),
  mock_reason: nullable(literal("force_mock", "missing_key", "configured_mock")),
});

const agentView = obj({
  id: str,
  display_name: str,
  duty: str,
  kind,
  look,
  builtin: bool,
  llm: nullable(llmChoice),
  effective_llm: effectiveLlm,
});

const pipelineView = obj({
  id: str,
  name: str,
  seats: arr(str),
  pending_seats: nullable(arr(str)),
});
const pipelineCore = obj({ id: str, name: str, seats: arr(str) });

const devopsAlert = obj({
  level: literal("info", "warning", "critical"),
  message: str,
  metric: str,
  value: num,
});

const historyCounts = obj({
  pending: int,
  in_progress: int,
  awaiting_approval: int,
  done: int,
  failed: int,
});

const llmInfo = obj({
  project: llmChoice,
  force_mock: bool,
  anthropic_key_present: bool,
  models: arr(obj({ id: str, label: str })),
});

const snapshot = obj({
  pipeline: obj({
    project_id: str,
    stations: record(
      obj({
        status: literal("idle", "working", "awaiting_approval"),
        task_id: nullable(str),
        task_title: nullable(str),
      }),
    ),
    queue_size: int,
    done_count: int,
    failed_count: int,
    budget_paused: bool,
    awaiting_approval: arr(obj({ task: taskSummary, station: str, approval })),
    usage: optional(usageSummary),
  }),
  tasks: arr(taskRecord),
  mock_mode: bool,
  providers: arr(str),
  devops_alerts: arr(devopsAlert),
  usage: obj({
    project: usageSummary,
    daily_cost: num,
    daily_budget_usd: nullable(num),
    project_budget_usd: nullable(num),
    budget_paused: bool,
  }),
  engine: str,
  task_store: literal("file", "db"),
  force_mock: bool,
  llm: llmInfo,
  agents: arr(agentView),
  pipelines: arr(pipelineView),
  live: record(obj({ task_id: str, phase: livePhase, text: str })),
  history_counts: historyCounts,
});

// `station:update` has three shapes keyed by `status`.
const stationWorking = obj({ station: str, status: literal("working"), task: taskSummary });
const stationFinished = obj({
  station: str,
  status: literal("done", "error"),
  task: taskSummary,
  success: bool,
  error: nullable(str),
  execution_time: num,
});
const stationRework = obj({ station: str, status: literal("rework"), task: taskSummary });
const stationUpdate: Check = (v, p, e) => {
  if (!isRecord(v)) {
    e.push(`${p}: expected object`);
    return;
  }
  if (v.status === "working") stationWorking(v, p, e);
  else if (v.status === "rework") stationRework(v, p, e);
  else if (v.status === "done" || v.status === "error") stationFinished(v, p, e);
  else e.push(`${p}.status: expected working|done|error|rework`);
};

const EVENT_CHECKS: Record<EventName, Check> = {
  snapshot,
  "task:created": obj({ task: taskSummary, queue_size: int }),
  "station:update": stationUpdate,
  "task:completed": obj({ task: taskSummary, done_count: int, deliverables: arr(str) }),
  "task:failed": obj({ task: taskSummary, reason: str, failed_count: int }),
  "usage:update": obj({
    task_id: str,
    task: taskSummary,
    record: usageRecord,
    task_usage: usageSummary,
    project_usage: usageSummary,
    daily_cost: num,
  }),
  "devops:alert": devopsAlert,
  "task:awaiting_approval": obj({ task: taskSummary, station: str, approval }),
  "task:approved": obj({ task: taskSummary, station: str }),
  "task:rejected": obj({ task: taskSummary, station: str, note: str }),
  "agent:phase": obj({
    task_id: str,
    station: str,
    step: int,
    of: int,
    phase: livePhase,
    text: str,
    passed: optional(bool),
    rework_target: optional(nullable(str)),
  }),
  "agent:token": obj({
    task_id: str,
    station: str,
    attempt: int,
    seq: int,
    text: str,
    final: bool,
    truncated: bool,
  }),
  "agent:joined": obj({ agent: agentView, pipeline_id: str, position: int }),
  "agent:left": obj({ agent_id: str, pipeline_id: str }),
  "pipeline:updated": obj({ pipeline: pipelineCore, agents: arr(agentView) }),
  "pipeline:pending": obj({ pipeline_id: str, seats: arr(str), reason: str, task_id: str }),
  "llm:updated": obj({
    project_llm: llmChoice,
    agents: arr(agentView),
    effective: literal("now", "next_task"),
  }),
};

const seatSnapshot = obj({ id: str, display_name: str, look, kind });

const historyItem = obj({
  task_id: str,
  title: str,
  priority,
  status: taskStatus,
  pipeline_id: nullable(str),
  outcome: nullable(literal("done", "failed")),
  reason: nullable(str),
  rework_count: int,
  path: arr(str),
  cost_usd: nullable(num),
  started_at: nullable(str),
  finished_at: nullable(str),
  deliverable_count: int,
  runs: int,
  stale: bool,
  seats: arr(seatSnapshot),
});

const historyResponse = obj({
  items: arr(historyItem),
  total: int,
  counts: historyCounts,
});

// ---- public API ------------------------------------------------------------

export function isEventName(name: unknown): name is EventName {
  return typeof name === "string" && (EVENT_NAMES as readonly string[]).includes(name);
}

/** Validation errors for one event's `data` (empty array = valid). */
export function validateEventData(event: EventName, data: unknown): string[] {
  const errs: Errors = [];
  EVENT_CHECKS[event](data, "data", errs);
  return errs;
}

export type FrameResult =
  | { ok: true; event: ServerEvent }
  | { ok: false; unknown: boolean; reason: string };

/**
 * Validate one decoded WebSocket frame `{event, data, project_id?, pipeline_id?}`.
 * Unknown event names return `unknown: true` so callers can ignore them quietly
 * (later-stage events such as `staffing:*` are not part of the MVP contract).
 */
export function parseServerFrame(raw: unknown): FrameResult {
  if (!isRecord(raw) || typeof raw.event !== "string") {
    return { ok: false, unknown: false, reason: "frame is not {event, data}" };
  }
  if (!isEventName(raw.event)) {
    return { ok: false, unknown: true, reason: `unknown event: ${raw.event}` };
  }
  if (raw.project_id !== undefined && typeof raw.project_id !== "string") {
    return { ok: false, unknown: false, reason: "project_id: expected string" };
  }
  if (raw.pipeline_id !== undefined && typeof raw.pipeline_id !== "string") {
    return { ok: false, unknown: false, reason: "pipeline_id: expected string" };
  }
  const errs = validateEventData(raw.event, raw.data);
  if (errs.length > 0) {
    return { ok: false, unknown: false, reason: `${raw.event}: ${errs.join("; ")}` };
  }
  return { ok: true, event: raw as unknown as ServerEvent };
}

/** Decode and validate a raw WebSocket text frame. */
export function parseServerFrameText(text: string): FrameResult {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return { ok: false, unknown: false, reason: "frame is not valid JSON" };
  }
  return parseServerFrame(raw);
}

export type ReplayLineResult =
  | { ok: true; line: ReplayLine }
  | { ok: false; unknown: boolean; reason: string };

/** Validate one decoded replay line `{t, event, data}`. */
export function parseReplayLine(raw: unknown): ReplayLineResult {
  if (!isRecord(raw)) return { ok: false, unknown: false, reason: "line is not an object" };
  if (typeof raw.t !== "number" || !Number.isFinite(raw.t) || raw.t < 0) {
    return { ok: false, unknown: false, reason: "t: expected non-negative number (ms)" };
  }
  const frame = parseServerFrame(raw);
  if (!frame.ok) return frame;
  return { ok: true, line: raw as unknown as ReplayLine };
}

/** Parse a whole `.jsonl` replay file. Blank lines are skipped; any bad line is reported. */
export function parseReplayText(text: string): {
  lines: ReplayLine[];
  errors: Array<{ line: number; reason: string }>;
} {
  const lines: ReplayLine[] = [];
  const errors: Array<{ line: number; reason: string }> = [];
  text.split("\n").forEach((rawLine, i) => {
    if (rawLine.trim() === "") return;
    let decoded: unknown;
    try {
      decoded = JSON.parse(rawLine);
    } catch {
      errors.push({ line: i + 1, reason: "invalid JSON" });
      return;
    }
    const result = parseReplayLine(decoded);
    if (result.ok) lines.push(result.line);
    else errors.push({ line: i + 1, reason: result.reason });
  });
  return { lines, errors };
}

/** Validation errors for a `GET /history` body (empty array = valid). */
export function validateHistoryResponse(data: unknown): string[] {
  const errs: Errors = [];
  historyResponse(data, "history", errs);
  return errs;
}

export function isHistoryResponse(data: unknown): data is HistoryResponse {
  return validateHistoryResponse(data).length === 0;
}
