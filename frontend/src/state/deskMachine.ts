// Pure single-desk state machine (plan §11.2). No clocks, no timers: callers pass `now`.
//
// States:  idle | working | reject | waiting | cheer | hello | leaving
// Rules:
//  - a phase that arrives before `station:update working` still moves the desk to working;
//  - a phase for the same task within DONE_IGNORE_MS after that station's `done` is stale: ignored;
//  - a rework target sits in `reject` until its next `working` (or phase);
//  - `waiting` follows `task:awaiting_approval`, and ends when the gate is resolved;
//  - after `task:completed`, desks that are idle cheer for CHEER_MS;
//  - `agent:joined` starts `hello` (HELLO_MS), `agent:left` starts `leaving` (LEAVE_MS, then removal).

import type { LivePhase } from "../types/events";

export const DONE_IGNORE_MS = 600;
export const CHEER_MS = 1100;
export const HELLO_MS = 2500;
export const LEAVE_MS = 600;

export type DeskStateName = "idle" | "working" | "reject" | "waiting" | "cheer" | "hello" | "leaving";

export interface DeskState {
  id: string;
  state: DeskStateName;
  /** The task this desk is currently on (working / reject / waiting). */
  taskId: string | null;
  /** Latest `agent:phase` for the current visit (bubble text in the MVP). */
  phase: LivePhase | null;
  text: string | null;
  /** When the current state began (restarts the bubble's typewriter). */
  since: number;
  /** Expiry for cheer / hello / leaving; null otherwise. */
  until: number | null;
  /** The last task this station finished, for the stale-phase window. */
  doneTask: { id: string; at: number } | null;
  /** True while a pending lineup change will remove this seat ("這張做完就離開"). */
  pendingExit: boolean;
}

export type DeskInput =
  | { type: "working"; taskId: string }
  | { type: "phase"; taskId: string; phase: LivePhase; text: string }
  | { type: "done"; taskId: string }
  | { type: "rework"; taskId: string }
  | { type: "awaiting"; taskId: string }
  | { type: "gate_resolved" }
  | { type: "completed" }
  | { type: "task_ended"; taskId: string }
  | { type: "joined" }
  | { type: "leave" }
  | { type: "tick" };

export function newDesk(id: string, now: number): DeskState {
  return {
    id,
    state: "idle",
    taskId: null,
    phase: null,
    text: null,
    since: now,
    until: null,
    doneTask: null,
    pendingExit: false,
  };
}

function enter(desk: DeskState, state: DeskStateName, now: number, patch: Partial<DeskState> = {}): DeskState {
  return { ...desk, state, since: now, until: null, ...patch };
}

export function deskStep(desk: DeskState, input: DeskInput, now: number): DeskState {
  // A desk that is leaving only finishes leaving.
  if (desk.state === "leaving") return desk;

  switch (input.type) {
    case "working":
      return enter(desk, "working", now, {
        taskId: input.taskId,
        phase: null,
        text: null,
        doneTask: null,
      });

    case "phase": {
      if (desk.state === "waiting") return desk;
      const stale =
        desk.doneTask !== null &&
        desk.doneTask.id === input.taskId &&
        now - desk.doneTask.at < DONE_IGNORE_MS &&
        desk.state !== "working";
      if (stale) return desk;
      if (desk.state === "working") {
        return { ...desk, taskId: input.taskId, phase: input.phase, text: input.text };
      }
      // Phase arrived before `working`: the desk starts working now.
      return enter(desk, "working", now, {
        taskId: input.taskId,
        phase: input.phase,
        text: input.text,
        doneTask: null,
      });
    }

    case "done":
      return enter(desk, "idle", now, {
        taskId: null,
        phase: null,
        text: null,
        doneTask: { id: input.taskId, at: now },
      });

    case "rework":
      return enter(desk, "reject", now, { taskId: input.taskId, phase: null, text: null });

    case "awaiting":
      return enter(desk, "waiting", now, { taskId: input.taskId, phase: null, text: null });

    case "gate_resolved":
      return desk.state === "waiting"
        ? enter(desk, "idle", now, { taskId: null, phase: null, text: null })
        : desk;

    case "completed":
      return desk.state === "idle" ? enter(desk, "cheer", now, { until: now + CHEER_MS }) : desk;

    case "task_ended":
      if (
        desk.taskId === input.taskId &&
        (desk.state === "working" || desk.state === "reject" || desk.state === "waiting")
      ) {
        return enter(desk, "idle", now, { taskId: null, phase: null, text: null });
      }
      return desk;

    case "joined":
      return enter(desk, "hello", now, { until: now + HELLO_MS });

    case "leave":
      return enter(desk, "leaving", now, { until: now + LEAVE_MS });

    case "tick":
      if ((desk.state === "cheer" || desk.state === "hello") && desk.until !== null && now >= desk.until) {
        return enter(desk, "idle", now, { taskId: null, phase: null, text: null });
      }
      return desk;
  }
}

/** True once a leaving desk should be removed from the stage. */
export function isGone(desk: DeskState, now: number): boolean {
  return desk.state === "leaving" && desk.until !== null && now >= desk.until;
}
