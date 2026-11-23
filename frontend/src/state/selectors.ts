// Pure selectors over FactoryState: desk views, note anchors, counters, history grouping.

import type { HistoryItem } from "../types/api";
import type { AgentView, EffectiveLlm, TaskStatus } from "../types/events";
import type { DeskState } from "./deskMachine";
import type { FactoryState, Gate, NoteState } from "./reducer";

export const DEFAULT_PIPELINE_ID = "main";

export interface DeskView {
  id: string;
  /** Undefined only while an event for an unknown agent is in flight. */
  agent: AgentView | undefined;
  desk: DeskState;
  /** Gate waiting on this desk, if any (drives `GateControls`). */
  gate: Gate | null;
}

/** Seat ids of a pipeline in order (`main` by default, else the first pipeline). */
export function selectSeatIds(state: FactoryState, pipelineId?: string): string[] {
  const p =
    state.pipelines.find((x) => x.id === (pipelineId ?? DEFAULT_PIPELINE_ID)) ??
    (pipelineId === undefined ? state.pipelines[0] : undefined);
  return p ? p.seats : [];
}

/** Desks of one pipeline in seat order, with their agent and gate. */
export function selectDeskViews(state: FactoryState, pipelineId?: string): DeskView[] {
  const gates = Object.values(state.awaiting);
  const views: DeskView[] = [];
  for (const id of selectSeatIds(state, pipelineId)) {
    const desk = state.desks[id];
    if (!desk) continue;
    views.push({
      id,
      agent: state.agents[id],
      desk,
      gate: gates.find((g) => g.station === id) ?? null,
    });
  }
  return views;
}

/** Agents that are in the roster but not seated in any pipeline (the bench). */
export function selectBench(state: FactoryState): AgentView[] {
  const seated = new Set(state.pipelines.flatMap((p) => p.seats));
  return Object.values(state.agents).filter((a) => !seated.has(a.id));
}

/** The effective model of every desk, keyed by desk id. */
export function selectDeskModels(state: FactoryState): Record<string, EffectiveLlm> {
  const out: Record<string, EffectiveLlm> = {};
  for (const id of Object.keys(state.desks)) {
    const agent = state.agents[id];
    if (agent) out[id] = agent.effective_llm;
  }
  return out;
}

export interface ModelBadge {
  label: "Opus" | "Sonnet" | "Haiku" | "Fable" | "Mock";
  /** Why a Claude choice runs as Mock; null when nothing needs explaining. */
  hint: "force_mock" | "missing_key" | null;
}

/** Badge next to the desk's name plate (D21). */
export function modelBadge(llm: EffectiveLlm): ModelBadge {
  const hint = llm.mock_reason === "force_mock" || llm.mock_reason === "missing_key" ? llm.mock_reason : null;
  if (llm.provider === "mock") return { label: "Mock", hint };
  const m = llm.model.toLowerCase();
  if (m.includes("opus")) return { label: "Opus", hint };
  if (m.includes("sonnet")) return { label: "Sonnet", hint };
  if (m.includes("haiku")) return { label: "Haiku", hint };
  if (m.includes("fable")) return { label: "Fable", hint };
  return { label: "Mock", hint };
}

export interface Stats {
  completed: number;
  failed: number;
  reworks: number;
  inFlight: number;
  queue: number;
  dailyCost: number;
}

export function selectStats(state: FactoryState): Stats {
  const inFlight = Object.values(state.tasks).filter(
    (t) => t.status === "in_progress" || t.status === "awaiting_approval",
  ).length;
  // Done/failed come from history_counts (§6.3a point 7) so the stats bar, the outbox tray and the
  // note wall show the same numbers.
  return {
    completed: state.historyCounts.done,
    failed: state.historyCounts.failed,
    reworks: state.counters.reworks,
    inFlight,
    queue: state.queueSize,
    dailyCost: state.usage.dailyCost,
  };
}

export type NoteAnchor = { kind: "inbox" } | { kind: "outbox" } | { kind: "desk"; id: string };

/** Where a note should sit (the target of its next flight). */
export function noteAnchor(note: NoteState): NoteAnchor {
  if (note.at === "inbox") return { kind: "inbox" };
  if (note.at === "outbox") return { kind: "outbox" };
  return { kind: "desk", id: note.at };
}

/** Notes to draw: in-flight ones first, then finished ones that are still on the outbox. */
export function selectNotes(state: FactoryState): NoteState[] {
  return Object.values(state.notes);
}

export function selectGates(state: FactoryState): Gate[] {
  return Object.values(state.awaiting);
}

export function selectUnseenAlerts(state: FactoryState): number {
  return Math.max(0, state.alerts.length - state.alertsSeen);
}

export function selectHasCritical(state: FactoryState): boolean {
  return state.alerts.slice(state.alertsSeen).some((a) => a.level === "critical");
}

// ---- history grouping ------------------------------------------------------

export const STATUS_ORDER: readonly TaskStatus[] = ["in_progress", "awaiting_approval", "pending", "done", "failed"];

export interface HistoryGroup {
  key: string;
  items: HistoryItem[];
}

/** Group note-wall items by pipeline (first-seen order) or by status (STATUS_ORDER). */
export function groupHistory(items: HistoryItem[], by: "pipeline" | "status"): HistoryGroup[] {
  const groups = new Map<string, HistoryItem[]>();
  for (const item of items) {
    const key = by === "status" ? item.status : (item.pipeline_id ?? "main");
    const list = groups.get(key);
    if (list) list.push(item);
    else groups.set(key, [item]);
  }
  const keys = [...groups.keys()];
  if (by === "status") {
    keys.sort((a, b) => STATUS_ORDER.indexOf(a as TaskStatus) - STATUS_ORDER.indexOf(b as TaskStatus));
  }
  return keys.map((key) => ({ key, items: groups.get(key) ?? [] }));
}

/** The task that is running now (or waiting at a gate), if any: the one to open from the stats bar. */
export function selectCurrentTaskId(state: FactoryState): string | null {
  const gate = Object.values(state.awaiting)[0];
  if (gate) return gate.taskId;
  const running = Object.values(state.tasks).find((t) => t.status === "in_progress");
  return running ? running.id : null;
}
