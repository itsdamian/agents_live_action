import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import { FLIGHT_EASING } from "../../lib/notePath";
import { StaticFactoryProvider } from "../../state/FactoryProvider";
import type { FactoryState } from "../../state/reducer";
import { apply, ready, summary } from "../../test/factories";
import { OUTBOX_HOLD_MS } from "./Note";
import { Stage } from "./Stage";

// Fake layout: the stage starts at (100, 50); spots sit at known places inside it.
const SPOTS: Record<string, { left: number; top: number }> = {
  inbox: { left: 150, top: 200 },
  pm: { left: 300, top: 180 },
  swe: { left: 480, top: 180 },
  qa: { left: 660, top: 180 },
  outbox: { left: 820, top: 200 },
};
let rectOf: (el: Element) => DOMRect;
const animate = vi.fn();
let mediaReduced = false;

beforeEach(() => {
  vi.useFakeTimers();
  animate.mockReset().mockImplementation(() => ({ cancel: vi.fn(), playState: "finished", onfinish: null }));
  Object.defineProperty(HTMLElement.prototype, "animate", { configurable: true, writable: true, value: animate });
  rectOf = (el) => {
    const id = el.getAttribute("data-spot");
    const p = id ? SPOTS[id] : el.matches("section") ? { left: 100, top: 50 } : { left: 0, top: 0 };
    return { left: p?.left ?? 0, top: p?.top ?? 0, width: 0, height: 0, right: 0, bottom: 0, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
  };
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) { return rectOf(this); });
  mediaReduced = false;
  vi.stubGlobal("matchMedia", () => ({ get matches() { return mediaReduced; }, addEventListener: () => {}, removeEventListener: () => {} }));
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const note = () => document.querySelector<HTMLElement>("[data-task]")!;
const mount = (state: FactoryState) =>
  render(<StaticFactoryProvider state={state}><Stage /></StaticFactoryProvider>);
const rerender = (r: ReturnType<typeof mount>, state: FactoryState) =>
  r.rerender(<StaticFactoryProvider state={state}><Stage /></StaticFactoryProvider>);

const created = () => apply(ready(), "task:created", { task: summary("task-004", "pending"), queue_size: 0 }, 1);

describe("Note", () => {
  it("is drawn at the inbox, with the task number, without animating on first appearance", () => {
    mount(created());
    expect(note()).toHaveTextContent("#4");
    expect(note().style.transform).toBe("translate(50px, 150px)"); // inbox spot − stage origin
    expect(animate).not.toHaveBeenCalled();
    expect(screen.getByRole("img", { name: "便條「加登入頁」" })).toBeInTheDocument();
  });

  it("flies to the next desk: placed at the target, animated from the previous spot", () => {
    const r = mount(created());
    rerender(r, apply(created(), "station:update", { station: "pm", status: "working", task: summary("task-004") }, 2));
    expect(note().style.transform).toBe("translate(200px, 130px)");
    expect(animate).toHaveBeenCalledTimes(1);
    const [keyframes, options] = animate.mock.calls[0]!;
    expect(keyframes).toEqual([
      { transform: "translate(50px, 150px)", offset: 0, easing: FLIGHT_EASING },
      { transform: "translate(200px, 130px)", offset: 1 },
    ]);
    expect(options).toEqual({ duration: 600 });
  });

  it("a rework sends it straight back (one hop) and turns it red with the count", () => {
    let s = created();
    s = apply(s, "station:update", { station: "qa", status: "working", task: summary("task-004") }, 2);
    const r = mount(s);
    animate.mockClear();
    s = apply(s, "station:update", { station: "swe", status: "rework", task: summary("task-004", "in_progress", 1) }, 3);
    rerender(r, s);
    expect(animate).toHaveBeenCalledTimes(1);
    expect(animate.mock.calls[0]![0]).toHaveLength(2);
    expect(note().style.transform).toBe("translate(380px, 130px)");
    expect(note().dataset.tone).toBe("bad");
    expect(screen.getByTestId("rework-badge")).toHaveTextContent("1");
  });

  it("with reduced motion it jumps without animating", () => {
    mediaReduced = true;
    const r = mount(created());
    rerender(r, apply(created(), "station:update", { station: "swe", status: "working", task: summary("task-004") }, 2));
    expect(note().style.transform).toBe("translate(380px, 130px)");
    expect(animate).not.toHaveBeenCalled();
  });

  it("re-places itself instantly on window resize", () => {
    const r = mount(created());
    rerender(r, apply(created(), "station:update", { station: "pm", status: "working", task: summary("task-004") }, 2));
    animate.mockClear();
    SPOTS.pm = { left: 340, top: 260 };
    try {
      act(() => { window.dispatchEvent(new Event("resize")); });
      expect(note().style.transform).toBe("translate(240px, 210px)");
      expect(animate).not.toHaveBeenCalled();
    } finally {
      SPOTS.pm = { left: 300, top: 180 };
    }
  });

  it("lands green in the outbox and disappears after the cheer", () => {
    let s = created();
    s = apply(s, "station:update", { station: "qa", status: "working", task: summary("task-004") }, 2);
    const r = mount(s);
    rerender(r, apply(s, "task:completed", { task: summary("task-004", "done"), done_count: 1, deliverables: [] }, 3));
    expect(note().dataset.tone).toBe("ok");
    expect(note().style.transform).toBe("translate(720px, 150px)");
    expect(note()).toBeVisible();
    act(() => { vi.advanceTimersByTime(OUTBOX_HOLD_MS - 1); });
    expect(note()).toBeVisible();
    act(() => { vi.advanceTimersByTime(1); });
    expect(note()).not.toBeVisible();
  });

  it("a failed task's note is red in the outbox", () => {
    const s = apply(created(), "task:failed", { task: summary("task-004", "failed", 3), reason: "x", failed_count: 1 }, 3);
    mount(s);
    expect(note().dataset.tone).toBe("bad");
    expect(screen.getByRole("img", { name: "便條「加登入頁」失敗" })).toBeInTheDocument();
  });

  it("a note created by a snapshot mid-run appears at its desk without flying", () => {
    const s = ready({
      pipeline: {
        project_id: "demo", queue_size: 0, done_count: 0, failed_count: 0, budget_paused: false, awaiting_approval: [],
        stations: {
          pm: { status: "idle", task_id: null, task_title: null },
          swe: { status: "working", task_id: "task-007", task_title: "X" },
          qa: { status: "idle", task_id: null, task_title: null },
        },
      },
    });
    mount(s);
    expect(note().style.transform).toBe("translate(380px, 130px)");
    expect(animate).not.toHaveBeenCalled();
  });
});
