// Pure geometry and routing for the flying note (plan D13). The hook that touches the DOM is
// hooks/useNoteFlight.ts; everything that can be computed lives here so it can be tested.

import type { NoteAnchor } from "../state/selectors";

export interface Point {
  x: number;
  y: number;
}

export interface RectLike {
  left: number;
  top: number;
}

/** Duration of one hop, as in the prototype's `.note` transition. */
export const HOP_MS = 600;
export const MAX_FLIGHT_MS = 1500;
/** CSS easing from the prototype's `.note` transition (a little overshoot). */
export const FLIGHT_EASING = "cubic-bezier(.5,-0.3,.4,1.3)";

export function anchorKey(a: NoteAnchor): string {
  return a.kind === "desk" ? `desk:${a.id}` : a.kind;
}

/** Selector of the `.spot` element a note lands on (see Desk / Tray `data-spot`). */
export function spotSelector(a: NoteAnchor): string {
  const id = a.kind === "desk" ? a.id : a.kind;
  return `[data-spot="${id.replace(/["\\]/g, "")}"]`;
}

/**
 * The anchors a note visits when it moves from `from` to `to`, excluding `from` and ending at `to`.
 *
 *  - forward moves pass every desk in between (so a skipped desk is still visited);
 *  - backward moves (a rework or a rejected gate) fly straight to the target;
 *  - an anchor that is not on the floor (unknown desk id) flies straight there.
 */
export function notePath(from: NoteAnchor | null, to: NoteAnchor, seatIds: string[]): NoteAnchor[] {
  if (from === null) return [to];
  if (anchorKey(from) === anchorKey(to)) return [];
  const order: NoteAnchor[] = [{ kind: "inbox" }, ...seatIds.map((id): NoteAnchor => ({ kind: "desk", id })), { kind: "outbox" }];
  const keys = order.map(anchorKey);
  const i = keys.indexOf(anchorKey(from));
  const j = keys.indexOf(anchorKey(to));
  if (i === -1 || j === -1 || j < i) return [to];
  return order.slice(i + 1, j + 1);
}

/** Offset of a spot inside the stage: the note sits at `left:0; top:0` and is moved by `translate`. */
export function spotOffset(stage: RectLike, spot: RectLike): Point {
  return { x: spot.left - stage.left, y: spot.top - stage.top };
}

export function translate(p: Point): string {
  return `translate(${round(p.x)}px, ${round(p.y)}px)`;
}

const round = (n: number) => Math.round(n * 100) / 100;

export function flightDuration(hops: number): number {
  return Math.min(MAX_FLIGHT_MS, Math.max(1, hops) * HOP_MS);
}

/** WAAPI keyframes through `points` (start first), equal time per hop. */
export function flightKeyframes(points: Point[]): Keyframe[] {
  if (points.length < 2) return points.map((p) => ({ transform: translate(p) }));
  return points.map((p, i) => ({
    transform: translate(p),
    offset: i / (points.length - 1),
    ...(i < points.length - 1 ? { easing: FLIGHT_EASING } : {}),
  }));
}

/** "task-004" → "#4"; ids without a number fall back to their first four characters. */
export function taskLabel(taskId: string): string {
  const m = /(\d+)$/.exec(taskId);
  return m ? `#${Number(m[1])}` : `#${taskId.slice(0, 4)}`;
}
