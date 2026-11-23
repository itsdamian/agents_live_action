import {
  CHEER_MS,
  DONE_IGNORE_MS,
  HELLO_MS,
  LEAVE_MS,
  deskStep,
  isGone,
  newDesk,
  type DeskState,
} from "./deskMachine";

const idle = () => newDesk("swe", 0);
const working = (now = 0): DeskState => deskStep(idle(), { type: "working", taskId: "t1" }, now);

describe("deskMachine", () => {
  it("starts idle", () => {
    expect(idle()).toMatchObject({ state: "idle", taskId: null, phase: null, until: null });
  });

  it("working sets the task and clears the bubble", () => {
    const d = working(10);
    expect(d).toMatchObject({ state: "working", taskId: "t1", phase: null, text: null, since: 10 });
  });

  it("phase while working updates the bubble without restarting the state", () => {
    const d = deskStep(working(10), { type: "phase", taskId: "t1", phase: "thinking", text: "思考中…" }, 50);
    expect(d).toMatchObject({ state: "working", phase: "thinking", text: "思考中…", since: 10 });
  });

  it("a phase that arrives before `working` still moves the desk to working", () => {
    const d = deskStep(idle(), { type: "phase", taskId: "t1", phase: "intake", text: "讀任務" }, 5);
    expect(d).toMatchObject({ state: "working", taskId: "t1", phase: "intake", since: 5 });
  });

  it("done returns to idle and remembers the finished task", () => {
    const d = deskStep(working(), { type: "done", taskId: "t1" }, 100);
    expect(d).toMatchObject({ state: "idle", taskId: null, doneTask: { id: "t1", at: 100 } });
  });

  it("ignores a stale phase for the same task within the window after done", () => {
    const done = deskStep(working(), { type: "done", taskId: "t1" }, 100);
    const late = deskStep(done, { type: "phase", taskId: "t1", phase: "output", text: "x" }, 100 + DONE_IGNORE_MS - 1);
    expect(late).toBe(done);
  });

  it("ignores the visit's last phase for the finished task at any delay", () => {
    // The real backend emits the last phase after `station:update done` (recorded-mvp.jsonl).
    const done = deskStep(working(), { type: "done", taskId: "t1" }, 100);
    for (const phase of ["output", "verdict"] as const) {
      expect(deskStep(done, { type: "phase", taskId: "t1", phase, text: "x", last: true }, 100 + 10 * DONE_IGNORE_MS)).toBe(done);
    }
    const rejected = deskStep(done, { type: "rework", taskId: "t1" }, 200);
    expect(deskStep(rejected, { type: "phase", taskId: "t1", phase: "verdict", text: "x", last: true }, 300)).toBe(rejected);
    // Another task's last phase, or a new visit, still counts.
    expect(deskStep(done, { type: "phase", taskId: "t2", phase: "output", text: "x", last: true }, 300).state).toBe("working");
    const again = deskStep(done, { type: "working", taskId: "t1" }, 300);
    expect(deskStep(again, { type: "phase", taskId: "t1", phase: "output", text: "y", last: true }, 400).text).toBe("y");
  });

  it("accepts a phase after the window, for another task, or from a new visit", () => {
    const done = deskStep(working(), { type: "done", taskId: "t1" }, 100);
    const after = deskStep(done, { type: "phase", taskId: "t1", phase: "intake", text: "x" }, 100 + DONE_IGNORE_MS);
    expect(after.state).toBe("working");
    const other = deskStep(done, { type: "phase", taskId: "t2", phase: "intake", text: "x" }, 110);
    expect(other).toMatchObject({ state: "working", taskId: "t2" });
    const again = deskStep(done, { type: "working", taskId: "t1" }, 110);
    const phase = deskStep(again, { type: "phase", taskId: "t1", phase: "intake", text: "x" }, 120);
    expect(phase.text).toBe("x");
  });

  it("rework puts the target in reject until its next working", () => {
    const r = deskStep(idle(), { type: "rework", taskId: "t1" }, 10);
    expect(r).toMatchObject({ state: "reject", taskId: "t1" });
    expect(deskStep(r, { type: "tick" }, 99999).state).toBe("reject");
    expect(deskStep(r, { type: "working", taskId: "t1" }, 20).state).toBe("working");
  });

  it("a phase also ends reject (phase before working)", () => {
    const r = deskStep(idle(), { type: "rework", taskId: "t1" }, 10);
    expect(deskStep(r, { type: "phase", taskId: "t1", phase: "intake", text: "重做" }, 20).state).toBe("working");
  });

  it("awaiting → waiting, and ignores phases while waiting", () => {
    const w = deskStep(idle(), { type: "awaiting", taskId: "t1" }, 10);
    expect(w).toMatchObject({ state: "waiting", taskId: "t1" });
    expect(deskStep(w, { type: "phase", taskId: "t1", phase: "output", text: "x" }, 20)).toBe(w);
  });

  it("gate_resolved only ends waiting", () => {
    const w = deskStep(idle(), { type: "awaiting", taskId: "t1" }, 10);
    expect(deskStep(w, { type: "gate_resolved" }, 20)).toMatchObject({ state: "idle", taskId: null });
    const k = working();
    expect(deskStep(k, { type: "gate_resolved" }, 20)).toBe(k);
  });

  it("completed makes idle desks cheer for CHEER_MS and leaves busy desks alone", () => {
    const c = deskStep(idle(), { type: "completed" }, 1000);
    expect(c).toMatchObject({ state: "cheer", until: 1000 + CHEER_MS });
    const busy = working();
    expect(deskStep(busy, { type: "completed" }, 1000)).toBe(busy);
  });

  it("tick ends cheer exactly at the deadline (injected now)", () => {
    const c = deskStep(idle(), { type: "completed" }, 1000);
    expect(deskStep(c, { type: "tick" }, 1000 + CHEER_MS - 1)).toBe(c);
    expect(deskStep(c, { type: "tick" }, 1000 + CHEER_MS).state).toBe("idle");
  });

  it("joined starts hello which tick ends after HELLO_MS", () => {
    const h = deskStep(idle(), { type: "joined" }, 0);
    expect(h).toMatchObject({ state: "hello", until: HELLO_MS });
    expect(deskStep(h, { type: "tick" }, HELLO_MS).state).toBe("idle");
  });

  it("leave → leaving, which ignores everything and is gone after LEAVE_MS", () => {
    const l = deskStep(idle(), { type: "leave" }, 100);
    expect(l.state).toBe("leaving");
    expect(deskStep(l, { type: "working", taskId: "t1" }, 110)).toBe(l);
    expect(isGone(l, 100 + LEAVE_MS - 1)).toBe(false);
    expect(isGone(l, 100 + LEAVE_MS)).toBe(true);
    expect(isGone(idle(), 1e9)).toBe(false);
  });

  it("task_ended releases only desks of that task", () => {
    expect(deskStep(working(), { type: "task_ended", taskId: "t1" }, 5).state).toBe("idle");
    const other = working();
    expect(deskStep(other, { type: "task_ended", taskId: "t2" }, 5)).toBe(other);
    const w = deskStep(idle(), { type: "awaiting", taskId: "t1" }, 1);
    expect(deskStep(w, { type: "task_ended", taskId: "t1" }, 5).state).toBe("idle");
  });
});
