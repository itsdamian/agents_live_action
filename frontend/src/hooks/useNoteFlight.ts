// FLIP-style note flight (plan D13): the note is positioned at its target with `transform`,
// then `element.animate` plays from where it was to where it is now. Reduced motion skips the
// animation; a resize or a layout change (the stage's size) re-places the note instantly.

import { useLayoutEffect, useRef, type RefObject } from "react";
import {
  anchorKey,
  flightDuration,
  flightKeyframes,
  notePath,
  spotOffset,
  spotSelector,
  translate,
  type Point,
} from "../lib/notePath";
import type { NoteAnchor } from "../state/selectors";
import { useReducedMotion } from "./useReducedMotion";

export interface NoteFlightOptions {
  noteRef: RefObject<HTMLElement | null>;
  /** The positioning context (the stage element); null until it has mounted. */
  stage: HTMLElement | null;
  anchor: NoteAnchor;
  /** Seat ids in order, so a forward move can pass the desks in between. */
  seatIds: string[];
}

function measure(stage: HTMLElement, a: NoteAnchor): Point | null {
  const spot = stage.querySelector(spotSelector(a));
  if (!spot) return null;
  return spotOffset(stage.getBoundingClientRect(), spot.getBoundingClientRect());
}

export function useNoteFlight({ noteRef, stage, anchor, seatIds }: NoteFlightOptions): void {
  const reduced = useReducedMotion();
  const placed = useRef<{ anchor: NoteAnchor; point: Point } | null>(null);
  const animation = useRef<Animation | null>(null);
  const key = anchorKey(anchor);
  const seatKey = seatIds.join("|");

  // Move to the (new) anchor.
  useLayoutEffect(() => {
    const note = noteRef.current;
    if (!note || !stage) return;
    const target = measure(stage, anchor);
    if (!target) return;

    const prev = placed.current;
    const mid = animation.current;
    const from = prev && mid && mid.playState === "running" ? currentPoint(note, stage, prev.point) : prev?.point;
    mid?.cancel();
    animation.current = null;

    const route = prev ? notePath(prev.anchor, anchor, seatIds) : [anchor];
    note.style.transform = translate(target);
    placed.current = { anchor, point: target };

    if (!prev || !from || reduced || route.length === 0 || typeof note.animate !== "function") return;

    const points: Point[] = [from];
    for (const a of route) points.push(measure(stage, a) ?? target);
    points[points.length - 1] = target;
    const anim = note.animate(flightKeyframes(points), { duration: flightDuration(route.length) });
    animation.current = anim;
    anim.onfinish = () => {
      if (animation.current === anim) animation.current = null;
    };
    // `key` and `seatKey` stand for `anchor` and `seatIds` (stable primitives).
  }, [key, seatKey, reduced, noteRef, stage]);

  // Re-place instantly when the window or the stage changes size.
  useLayoutEffect(() => {
    const note = noteRef.current;
    if (!note || !stage) return;
    const replace = () => {
      const cur = placed.current;
      if (!cur) return;
      const target = measure(stage, cur.anchor);
      if (!target) return;
      animation.current?.cancel();
      animation.current = null;
      note.style.transform = translate(target);
      placed.current = { anchor: cur.anchor, point: target };
    };
    window.addEventListener("resize", replace);
    const ro = typeof ResizeObserver === "function" ? new ResizeObserver(replace) : null;
    ro?.observe(stage);
    return () => {
      window.removeEventListener("resize", replace);
      ro?.disconnect();
    };
  }, [noteRef, stage]);

  // Cancel a running flight on unmount.
  useLayoutEffect(
    () => () => {
      animation.current?.cancel();
    },
    [],
  );
}

/** Where the note is right now (mid-flight), in the stage's coordinates. */
function currentPoint(note: HTMLElement, stage: HTMLElement, fallback: Point): Point {
  const r = note.getBoundingClientRect();
  const s = stage.getBoundingClientRect();
  if (r.width === 0 && r.height === 0) return fallback;
  // The note's margin (-23px, -19px) puts its box centre on the translate origin.
  return { x: r.left - s.left + r.width / 2, y: r.top - s.top + r.height / 2 };
}
