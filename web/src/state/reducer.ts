// factoryReducer (plan D12, §11.2): a pure function of (state, action).
// Actions are server events (stamped with `now` by the caller, since the reducer never reads a
// clock) plus a few UI actions. The state is rebuilt from scratch by every `snapshot`.
//
// Ported from the old frontend/js/state.js: task normalisation, counters, snapshot rebuild.
// Events on the live channel (agent:phase, agent:joined/left, pipeline:*, llm:updated) and the
// pipeline channel have no ordering guarantee between them (E4), so every case tolerates
// arriving early or late.

import type {
  AgentView,
  DevOpsAlertData,
  HistoryCounts,
  LlmChoice,
  LlmInfo,
  PipelineView,
  ServerEvent,
  Snapshot,
  TaskSummary,
  UsageSummary,
} from "../types/events";
import { deskStep, isGone, newDesk, type DeskInput, type DeskState } from "./deskMachine";
import { pruneRecent, pushRecent, recentFromEvent, type RecentByDesk } from "./recent";

// ---- state -----------------------------------------------------------------

export type ConnectionStatus = "connecting" | "open" | "reconnecting" | "closed" | "error";

export interface ConnectionState {
  status: ConnectionStatus;
  attempt: number;
  delay: number | null;
  reason: string | null;
}

export type NoteAt = "inbox" | "outbox" | string;

/** The paper note of one in-flight (or just finished) task. `at` is a desk id, "inbox" or "outbox". */
export interface NoteState {
  taskId: string;
  title: string;
  at: NoteAt;
  tone: "normal" | "bad" | "ok";
  reworks: number;
  /** Set when the task failed (note lands in the outbox, red). */
  failed: boolean;
}

export interface Gate {
  taskId: string;
  title: string;
  station: string;
  approval: "none" | "after_swe" | "after_qa";
}

export interface Counters {
  created: number;
  completed: number;
  failed: number;
  reworks: number;
}

export interface UsageState {
  project: UsageSummary;
  dailyCost: number;
  dailyBudgetUsd: number | null;
  projectBudgetUsd: number | null;
  budgetPaused: boolean;
  byTask: Record<string, UsageSummary>;
}

export interface PendingPipeline {
  seats: string[];
  reason: string;
  taskId: string;
}

export interface AlertEntry extends DevOpsAlertData {
  id: number;
}

export interface FactoryState {
  projectId: string | null;
  connection: ConnectionState;
  /** True once a snapshot has been applied. */
  ready: boolean;
  mockMode: boolean;
  forceMock: boolean;
  engine: string | null;
  taskStore: "file" | "db" | null;
  llm: LlmInfo | null;
  agents: Record<string, AgentView>;
  pipelines: PipelineView[];
  pendingPipelines: Record<string, PendingPipeline>;
  tasks: Record<string, TaskSummary>;
  desks: Record<string, DeskState>;
  notes: Record<string, NoteState>;
  awaiting: Record<string, Gate>;
  counters: Counters;
  queueSize: number;
  usage: UsageState;
  historyCounts: HistoryCounts;
  alerts: AlertEntry[];
  /** Number of alerts the user has already seen (drives the DevOps red dot). */
  alertsSeen: number;
  alertSeq: number;
  /** Last actions of each desk, newest first (plan §11.7 desk card). */
  recent: RecentByDesk;
}

const MAX_ALERTS = 50;

const zeroUsage = (): UsageSummary => ({ input_tokens: 0, output_tokens: 0, cost: 0, call_count: 0 });
const zeroCounts = (): HistoryCounts => ({ pending: 0, in_progress: 0, awaiting_approval: 0, done: 0, failed: 0 });

export function initialState(projectId: string | null = null): FactoryState {
  return {
    projectId,
    connection: { status: "connecting", attempt: 0, delay: null, reason: null },
    ready: false,
    mockMode: false,
    forceMock: false,
    engine: null,
    taskStore: null,
    llm: null,
    agents: {},
    pipelines: [],
    pendingPipelines: {},
    tasks: {},
    desks: {},
    notes: {},
    awaiting: {},
    counters: { created: 0, completed: 0, failed: 0, reworks: 0 },
    queueSize: 0,
    usage: {
      project: zeroUsage(),
      dailyCost: 0,
      dailyBudgetUsd: null,
      projectBudgetUsd: null,
      budgetPaused: false,
      byTask: {},
    },
    historyCounts: zeroCounts(),
    alerts: [],
    alertsSeen: 0,
    alertSeq: 0,
    recent: {},
  };
}

// ---- actions ---------------------------------------------------------------

/** A server event stamped with the caller's clock. */
export type ServerAction = ServerEvent & { now: number };

export type UiAction =
  | { type: "reset"; projectId: string | null }
  | { type: "connection"; status: ConnectionStatus; attempt?: number; delay?: number | null; reason?: string | null }
  | { type: "tick"; now: number }
  | { type: "alerts_seen" }
  /** Replace the roster after `GET /agents` (also lists agents that sit on the bench). */
  | { type: "agents_loaded"; agents: AgentView[]; pipelines: PipelineView[]; now: number };

export type FactoryAction = ServerAction | UiAction;

// ---- helpers ---------------------------------------------------------------

type TaskLike = Pick<TaskSummary, "id" | "title" | "priority" | "rework_count" | "status">;

function toSummary(t: TaskLike, status: TaskSummary["status"]): TaskSummary {
  return { id: t.id, title: t.title, priority: t.priority, status, rework_count: t.rework_count };
}

/** Upsert a task from an event; `status` is the lifecycle status the event implies. */
function upsertTask(state: FactoryState, t: TaskLike, status: TaskSummary["status"]): FactoryState {
  const prev = state.tasks[t.id];
  let counts = state.historyCounts;
  if (prev && prev.status !== status) counts = shiftCount(counts, prev.status, status);
  else if (!prev) counts = { ...counts, [status]: counts[status] + 1 };
  return {
    ...state,
    tasks: { ...state.tasks, [t.id]: toSummary(t, status) },
    historyCounts: counts,
  };
}

function shiftCount(c: HistoryCounts, from: TaskSummary["status"], to: TaskSummary["status"]): HistoryCounts {
  if (from === to) return c;
  return { ...c, [from]: Math.max(0, c[from] - 1), [to]: c[to] + 1 };
}

function setDesk(state: FactoryState, id: string, input: DeskInput, now: number): FactoryState {
  const desk = state.desks[id];
  if (!desk) return state;
  const next = deskStep(desk, input, now);
  return next === desk ? state : { ...state, desks: { ...state.desks, [id]: next } };
}

function mapDesks(state: FactoryState, input: DeskInput, now: number): FactoryState {
  let changed = false;
  const desks: Record<string, DeskState> = {};
  for (const [id, desk] of Object.entries(state.desks)) {
    const next = deskStep(desk, input, now);
    if (next !== desk) changed = true;
    desks[id] = next;
  }
  return changed ? { ...state, desks } : state;
}

function setNote(state: FactoryState, taskId: string, patch: Partial<NoteState>, base?: NoteState): FactoryState {
  const prev = state.notes[taskId] ?? base;
  if (!prev) return state;
  return { ...state, notes: { ...state.notes, [taskId]: { ...prev, ...patch } } };
}

function allSeatIds(pipelines: PipelineView[]): string[] {
  const seen = new Set<string>();
  for (const p of pipelines) for (const id of p.seats) seen.add(id);
  return [...seen];
}

/** Make sure every seated agent has a desk; desks for seats that left are set to `leaving`. */
function syncDesks(state: FactoryState, now: number): FactoryState {
  const seated = new Set(allSeatIds(state.pipelines));
  const desks = { ...state.desks };
  for (const id of seated) if (!desks[id]) desks[id] = newDesk(id, now);
  for (const [id, desk] of Object.entries(desks)) {
    if (!seated.has(id) && desk.state !== "leaving") desks[id] = deskStep(desk, { type: "leave" }, now);
  }
  return { ...state, ...markPendingExit({ ...state, desks }) };
}

/** Flag desks whose seat is not in the pending lineup ("這張做完就離開"). */
function markPendingExit(state: FactoryState): Pick<FactoryState, "desks"> {
  const desks: Record<string, DeskState> = {};
  for (const [id, desk] of Object.entries(state.desks)) {
    let exiting = false;
    for (const p of state.pipelines) {
      const pending = state.pendingPipelines[p.id];
      if (pending && p.seats.includes(id) && !pending.seats.includes(id)) exiting = true;
    }
    desks[id] = desk.pendingExit === exiting ? desk : { ...desk, pendingExit: exiting };
  }
  return { desks };
}

function mergeAgents(prev: Record<string, AgentView>, list: AgentView[]): Record<string, AgentView> {
  const next = { ...prev };
  for (const a of list) next[a.id] = a;
  return next;
}

function noteFor(taskId: string, title: string, at: NoteAt, reworks: number): NoteState {
  return { taskId, title, at, tone: "normal", reworks, failed: false };
}

// ---- snapshot --------------------------------------------------------------

function applySnapshot(prev: FactoryState, snap: Snapshot, now: number): FactoryState {
  const base = initialState(prev.projectId);
  let state: FactoryState = {
    ...base,
    projectId: snap.pipeline.project_id || prev.projectId,
    connection: prev.connection,
    ready: true,
    mockMode: snap.mock_mode,
    forceMock: snap.force_mock,
    engine: snap.engine,
    taskStore: snap.task_store,
    llm: snap.llm,
    agents: mergeAgents({}, snap.agents),
    pipelines: snap.pipelines.map((p) => ({ ...p })),
    queueSize: snap.pipeline.queue_size,
    counters: {
      created: 0,
      completed: snap.pipeline.done_count,
      failed: snap.pipeline.failed_count,
      reworks: snap.tasks.reduce((n, t) => n + t.rework_count, 0),
    },
    usage: {
      project: snap.usage.project,
      dailyCost: snap.usage.daily_cost,
      dailyBudgetUsd: snap.usage.daily_budget_usd,
      projectBudgetUsd: snap.usage.project_budget_usd,
      budgetPaused: snap.usage.budget_paused,
      byTask: {},
    },
    historyCounts: snap.history_counts,
    alertSeq: 0,
  };

  const tasks: Record<string, TaskSummary> = {};
  for (const t of snap.tasks) tasks[t.id] = toSummary(t, t.status);
  state.tasks = tasks;

  // Alerts replayed from the snapshot count as already seen.
  state.alerts = snap.devops_alerts.slice(-MAX_ALERTS).map((a, i) => ({ ...a, id: i + 1 }));
  state.alertSeq = state.alerts.length;
  state.alertsSeen = state.alerts.length;

  // Desks: every seat of every pipeline, plus stations the backend reports.
  const desks: Record<string, DeskState> = {};
  for (const id of allSeatIds(state.pipelines)) desks[id] = newDesk(id, now);
  for (const id of Object.keys(snap.pipeline.stations)) if (!desks[id]) desks[id] = newDesk(id, now);
  state.desks = desks;

  // Gates (E5: rebuilt from the snapshot, never re-emitted after a restart).
  const awaiting: Record<string, Gate> = {};
  for (const g of snap.pipeline.awaiting_approval) {
    awaiting[g.task.id] = { taskId: g.task.id, title: g.task.title, station: g.station, approval: g.approval };
    state.tasks[g.task.id] = toSummary(g.task, "awaiting_approval");
  }
  state.awaiting = awaiting;

  // Station occupancy → desk state and the notes in flight.
  const notes: Record<string, NoteState> = {};
  for (const [id, st] of Object.entries(snap.pipeline.stations)) {
    if (!st.task_id) continue;
    const title = st.task_title ?? state.tasks[st.task_id]?.title ?? st.task_id;
    const reworks = state.tasks[st.task_id]?.rework_count ?? 0;
    if (st.status === "working") {
      state = setDesk(state, id, { type: "working", taskId: st.task_id }, now);
      notes[st.task_id] = noteFor(st.task_id, title, id, reworks);
    } else if (st.status === "awaiting_approval") {
      state = setDesk(state, id, { type: "awaiting", taskId: st.task_id }, now);
      notes[st.task_id] = noteFor(st.task_id, title, id, reworks);
    }
  }
  for (const g of Object.values(awaiting)) {
    if (!notes[g.taskId]) {
      notes[g.taskId] = noteFor(g.taskId, g.title, g.station, state.tasks[g.taskId]?.rework_count ?? 0);
      state = setDesk(state, g.station, { type: "awaiting", taskId: g.taskId }, now);
    }
  }
  state.notes = notes;

  // Recent desk actions are client-side history: a reconnect to the same project keeps them.
  if (state.projectId === prev.projectId) state.recent = pruneRecent(prev.recent, Object.keys(state.desks));

  // Bubble text of in-flight phases.
  for (const [id, live] of Object.entries(snap.live)) {
    const desk = state.desks[id];
    if (desk && desk.state === "working") {
      state = {
        ...state,
        desks: { ...state.desks, [id]: { ...desk, phase: live.phase, text: live.text } },
      };
    }
  }
  return state;
}

// ---- reducer ---------------------------------------------------------------

export function factoryReducer(state: FactoryState, action: FactoryAction): FactoryState {
  if (!("event" in action)) return reduceUi(state, action);
  return reduceServer(state, action);
}

function reduceUi(state: FactoryState, action: UiAction): FactoryState {
  switch (action.type) {
    case "reset":
      return initialState(action.projectId);
    case "connection":
      return {
        ...state,
        connection: {
          status: action.status,
          attempt: action.attempt ?? (action.status === "reconnecting" ? state.connection.attempt : 0),
          delay: action.delay ?? null,
          reason: action.reason ?? null,
        },
      };
    case "alerts_seen":
      return state.alertsSeen === state.alerts.length ? state : { ...state, alertsSeen: state.alerts.length };
    case "agents_loaded": {
      const next: FactoryState = {
        ...state,
        agents: mergeAgents({}, action.agents),
        pipelines: action.pipelines.map((p) => ({ ...p })),
      };
      return syncDesks(next, action.now);
    }
    case "tick": {
      const now = action.now;
      let changed = false;
      const desks: Record<string, DeskState> = {};
      for (const [id, desk] of Object.entries(state.desks)) {
        if (isGone(desk, now)) {
          changed = true;
          continue;
        }
        const next = deskStep(desk, { type: "tick" }, now);
        if (next !== desk) changed = true;
        desks[id] = next;
      }
      if (!changed) return state;
      return { ...state, desks, recent: pruneRecent(state.recent, Object.keys(desks)) };
    }
  }
}

function reduceServer(state: FactoryState, action: ServerAction): FactoryState {
  const next = reduceEvent(state, action);
  const hit = recentFromEvent(action, action.now);
  if (!hit || !(hit.desk in next.desks)) return next;
  const task = next.tasks[hit.action.taskId];
  const entry = {
    ...hit.action,
    title: hit.action.title || task?.title || hit.action.taskId,
    reworks: hit.action.title ? hit.action.reworks : (task?.rework_count ?? 0),
  };
  return { ...next, recent: pushRecent(next.recent, hit.desk, entry) };
}

function reduceEvent(state: FactoryState, action: ServerAction): FactoryState {
  const now = action.now;
  switch (action.event) {
    case "snapshot":
      return applySnapshot(state, action.data, now);

    case "task:created": {
      const { task, queue_size } = action.data;
      let s = upsertTask(state, task, "in_progress");
      // Finished notes from earlier tasks are no longer needed.
      const notes: Record<string, NoteState> = {};
      for (const [id, n] of Object.entries(s.notes)) if (n.at !== "outbox") notes[id] = n;
      notes[task.id] = noteFor(task.id, task.title, "inbox", task.rework_count);
      s = {
        ...s,
        notes,
        queueSize: queue_size,
        counters: { ...s.counters, created: s.counters.created + 1 },
      };
      return s;
    }

    case "station:update": {
      const d = action.data;
      if (d.status === "working") {
        let s = upsertTask(state, d.task, "in_progress");
        s = setDesk(s, d.station, { type: "working", taskId: d.task.id }, now);
        return setNote(s, d.task.id, { at: d.station, tone: "normal", reworks: d.task.rework_count },
          noteFor(d.task.id, d.task.title, d.station, d.task.rework_count));
      }
      if (d.status === "rework") {
        let s = upsertTask(state, d.task, "in_progress");
        s = setDesk(s, d.station, { type: "rework", taskId: d.task.id }, now);
        s = { ...s, counters: { ...s.counters, reworks: s.counters.reworks + 1 } };
        return setNote(s, d.task.id, { at: d.station, tone: "bad", reworks: d.task.rework_count },
          noteFor(d.task.id, d.task.title, d.station, d.task.rework_count));
      }
      // done | error
      const s = upsertTask(state, d.task, state.tasks[d.task.id]?.status ?? "in_progress");
      return setDesk(s, d.station, { type: "done", taskId: d.task.id }, now);
    }

    case "task:completed": {
      const { task, done_count } = action.data;
      let s = upsertTask(state, task, "done");
      s = { ...s, counters: { ...s.counters, completed: done_count } };
      s = setNote(s, task.id, { at: "outbox", tone: "ok", reworks: task.rework_count, failed: false },
        noteFor(task.id, task.title, "outbox", task.rework_count));
      s = removeGate(s, task.id);
      return mapDesks(s, { type: "completed" }, now);
    }

    case "task:failed": {
      const { task, failed_count } = action.data;
      let s = upsertTask(state, task, "failed");
      s = { ...s, counters: { ...s.counters, failed: failed_count } };
      s = setNote(s, task.id, { at: "outbox", tone: "bad", reworks: task.rework_count, failed: true },
        noteFor(task.id, task.title, "outbox", task.rework_count));
      s = removeGate(s, task.id);
      return mapDesks(s, { type: "task_ended", taskId: task.id }, now);
    }

    case "task:awaiting_approval": {
      const { task, station, approval } = action.data;
      let s = upsertTask(state, task, "awaiting_approval");
      s = {
        ...s,
        awaiting: { ...s.awaiting, [task.id]: { taskId: task.id, title: task.title, station, approval } },
      };
      s = setNote(s, task.id, { at: station }, noteFor(task.id, task.title, station, task.rework_count));
      return setDesk(s, station, { type: "awaiting", taskId: task.id }, now);
    }

    case "task:approved":
    case "task:rejected": {
      const { task, station } = action.data;
      let s = upsertTask(state, task, "in_progress");
      s = removeGate(s, task.id);
      return setDesk(s, station, { type: "gate_resolved" }, now);
    }

    case "usage:update": {
      const d = action.data;
      return {
        ...state,
        usage: {
          ...state.usage,
          project: d.project_usage,
          dailyCost: d.daily_cost,
          byTask: { ...state.usage.byTask, [d.task_id]: d.task_usage },
        },
      };
    }

    case "devops:alert": {
      const id = state.alertSeq + 1;
      const alerts = [...state.alerts, { ...action.data, id }].slice(-MAX_ALERTS);
      return { ...state, alerts, alertSeq: id };
    }

    case "agent:phase": {
      const d = action.data;
      return setDesk(state, d.station, { type: "phase", taskId: d.task_id, phase: d.phase, text: d.text }, now);
    }

    case "agent:token":
      // Stage 2: tokens go to the liveText store, not the reducer.
      return state;

    case "agent:joined": {
      const { agent, pipeline_id, position } = action.data;
      const agents = { ...state.agents, [agent.id]: agent };
      let pipelines = state.pipelines;
      const target = pipelines.find((p) => p.id === pipeline_id);
      if (target && !target.seats.includes(agent.id)) {
        const seats = [...target.seats];
        seats.splice(Math.min(Math.max(position, 0), seats.length), 0, agent.id);
        pipelines = pipelines.map((p) => (p.id === pipeline_id ? { ...p, seats } : p));
      }
      let s: FactoryState = { ...state, agents, pipelines };
      s = syncDesks(s, now);
      return setDesk(s, agent.id, { type: "joined" }, now);
    }

    case "agent:left": {
      const { agent_id, pipeline_id } = action.data;
      const pipelines = state.pipelines.map((p) =>
        p.id === pipeline_id ? { ...p, seats: p.seats.filter((id) => id !== agent_id) } : p,
      );
      const s: FactoryState = { ...state, pipelines };
      return syncDesks(s, now);
    }

    case "pipeline:updated": {
      const { pipeline, agents } = action.data;
      const prevView = state.pipelines.find((p) => p.id === pipeline.id);
      const view: PipelineView = {
        id: pipeline.id,
        name: pipeline.name,
        seats: [...pipeline.seats],
        pending_seats: null,
      };
      const pipelines = prevView
        ? state.pipelines.map((p) => (p.id === pipeline.id ? view : p))
        : [...state.pipelines, view];
      const pending = { ...state.pendingPipelines };
      delete pending[pipeline.id];
      const s: FactoryState = {
        ...state,
        pipelines,
        pendingPipelines: pending,
        agents: mergeAgents({}, agents),
      };
      return syncDesks(s, now);
    }

    case "pipeline:pending": {
      const d = action.data;
      const s: FactoryState = {
        ...state,
        pendingPipelines: {
          ...state.pendingPipelines,
          [d.pipeline_id]: { seats: [...d.seats], reason: d.reason, taskId: d.task_id },
        },
        pipelines: state.pipelines.map((p) => (p.id === d.pipeline_id ? { ...p, pending_seats: [...d.seats] } : p)),
      };
      return { ...s, ...markPendingExit(s) };
    }

    case "llm:updated": {
      const d = action.data;
      const project: LlmChoice = d.project_llm;
      return {
        ...state,
        agents: mergeAgents({}, d.agents),
        llm: state.llm ? { ...state.llm, project } : state.llm,
      };
    }
  }
}

function removeGate(state: FactoryState, taskId: string): FactoryState {
  if (!(taskId in state.awaiting)) return state;
  const awaiting = { ...state.awaiting };
  delete awaiting[taskId];
  return { ...state, awaiting };
}
