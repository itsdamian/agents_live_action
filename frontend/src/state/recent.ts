// Per-desk "recent actions" (plan §11.7: clicking a desk opens a small card with the last five
// actions; it replaces the old station cards' log). Built only from frozen events (§6), the same
// way the old frontend/js/stations.js built its log: actions that happened before the page loaded
// are not known to the client, so a fresh page starts with an empty list.

import type { ServerEvent } from "../types/events";

export const RECENT_LIMIT = 5;

export type RecentKind =
  | "start" // station:update working
  | "done" // station:update done
  | "error" // station:update error
  | "rework" // station:update rework (this desk is the rework target)
  | "pass" // agent:phase verdict, passed
  | "fail" // agent:phase verdict, sent back
  | "waiting" // task:awaiting_approval
  | "approved" // task:approved
  | "rejected"; // task:rejected

export interface RecentAction {
  /** Caller's clock (ms) when the event arrived. */
  at: number;
  taskId: string;
  title: string;
  kind: RecentKind;
  /** Rework count of the task at that moment. */
  reworks: number;
  /** Server text that goes with the action: error message, verdict text or rejection note. */
  detail: string | null;
  /** Desk the note was sent back to (`fail`). */
  target: string | null;
}

export type RecentByDesk = Record<string, RecentAction[]>;

/** The desk and action an event adds, or null for events that are not desk actions. */
export function recentFromEvent(ev: ServerEvent, now: number): { desk: string; action: RecentAction } | null {
  const base = (taskId: string, title: string, reworks: number) => ({ at: now, taskId, title, reworks, detail: null, target: null });
  switch (ev.event) {
    case "station:update": {
      const d = ev.data;
      const b = base(d.task.id, d.task.title, d.task.rework_count);
      if (d.status === "working") return { desk: d.station, action: { ...b, kind: "start" } };
      if (d.status === "rework") return { desk: d.station, action: { ...b, kind: "rework" } };
      if (d.status === "error") return { desk: d.station, action: { ...b, kind: "error", detail: d.error } };
      return { desk: d.station, action: { ...b, kind: "done" } };
    }
    case "agent:phase": {
      const d = ev.data;
      if (d.phase !== "verdict" || d.passed === null) return null;
      // The phase event carries no title; the caller fills it in from its task table.
      const b = base(d.task_id, "", 0);
      return d.passed
        ? { desk: d.station, action: { ...b, kind: "pass", detail: d.text } }
        : { desk: d.station, action: { ...b, kind: "fail", detail: d.text, target: d.rework_target } };
    }
    case "task:awaiting_approval": {
      const d = ev.data;
      return { desk: d.station, action: { ...base(d.task.id, d.task.title, d.task.rework_count), kind: "waiting" } };
    }
    case "task:approved": {
      const d = ev.data;
      return { desk: d.station, action: { ...base(d.task.id, d.task.title, d.task.rework_count), kind: "approved" } };
    }
    case "task:rejected": {
      const d = ev.data;
      return {
        desk: d.station,
        action: { ...base(d.task.id, d.task.title, d.task.rework_count), kind: "rejected", detail: d.note || null },
      };
    }
    default:
      return null;
  }
}

/** Prepend `action` to the desk's list, newest first, keeping at most RECENT_LIMIT. */
export function pushRecent(recent: RecentByDesk, desk: string, action: RecentAction): RecentByDesk {
  const list = [action, ...(recent[desk] ?? [])].slice(0, RECENT_LIMIT);
  return { ...recent, [desk]: list };
}

/** Keep only the lists of desks that still exist. */
export function pruneRecent(recent: RecentByDesk, desks: Iterable<string>): RecentByDesk {
  const keep = new Set(desks);
  const out: RecentByDesk = {};
  let changed = false;
  for (const [id, list] of Object.entries(recent)) {
    if (keep.has(id)) out[id] = list;
    else changed = true;
  }
  return changed ? out : recent;
}
