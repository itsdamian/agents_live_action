import defaultRun from "../../public/replay/default-run.jsonl?raw";
import { parseReplayText } from "../net/guards";
import { apply, ready, snapshot, summary } from "../test/factories";
import { LEAVE_MS } from "./deskMachine";
import { RECENT_LIMIT, pushRecent, type RecentAction } from "./recent";
import { factoryReducer, initialState, type FactoryState } from "./reducer";

const t1 = summary("t1", "in_progress", 0, "加登入頁");
const finished = (station: string, status: "done" | "error", error: string | null = null) =>
  ({ station, status, task: t1, success: status === "done", error, execution_time: 1 }) as const;

describe("recent desk actions (plan §11.7)", () => {
  it("records start, done, error and rework per desk, newest first", () => {
    let s = ready();
    s = apply(s, "station:update", { station: "pm", status: "working", task: t1 }, 10);
    s = apply(s, "station:update", finished("pm", "done"), 20);
    s = apply(s, "station:update", { station: "swe", status: "working", task: t1 }, 30);
    s = apply(s, "station:update", finished("swe", "error", "timeout"), 40);
    s = apply(s, "station:update", { station: "swe", status: "rework", task: { ...t1, rework_count: 1 } }, 50);
    expect(s.recent["pm"]!.map((a) => [a.kind, a.at])).toEqual([["done", 20], ["start", 10]]);
    expect(s.recent["swe"]!.map((a) => a.kind)).toEqual(["rework", "error", "start"]);
    expect(s.recent["swe"]![1]).toMatchObject({ detail: "timeout", title: "加登入頁", taskId: "t1" });
    expect(s.recent["swe"]![0]!.reworks).toBe(1);
    expect(s.recent["qa"]).toBeUndefined();
  });

  it("records verdicts with the server text and the rework target, title from the task table", () => {
    let s = ready();
    s = apply(s, "station:update", { station: "qa", status: "working", task: { ...t1, rework_count: 2 } }, 1);
    for (const phase of ["intake", "thinking", "output"] as const) {
      s = apply(s, "agent:phase", { task_id: "t1", station: "qa", step: 1, of: 3, phase, text: "x", passed: null, rework_target: null }, 2);
    }
    s = apply(s, "agent:phase", { task_id: "t1", station: "qa", step: 3, of: 3, phase: "verdict", text: "退件 → SWE", passed: false, rework_target: "swe" }, 3);
    expect(s.recent["qa"]!.map((a) => a.kind)).toEqual(["fail", "start"]);
    expect(s.recent["qa"]![0]).toMatchObject({ title: "加登入頁", detail: "退件 → SWE", target: "swe", reworks: 2 });
  });

  it("records gate actions on the gate desk", () => {
    let s = ready();
    s = apply(s, "task:awaiting_approval", { task: t1, station: "qa", approval: "after_qa" }, 1);
    s = apply(s, "task:rejected", { task: t1, station: "qa", note: "再改一下" }, 2);
    s = apply(s, "task:awaiting_approval", { task: t1, station: "qa", approval: "after_qa" }, 3);
    s = apply(s, "task:approved", { task: t1, station: "qa" }, 4);
    expect(s.recent["qa"]!.map((a) => a.kind)).toEqual(["approved", "waiting", "rejected", "waiting"]);
    expect(s.recent["qa"]![2]!.detail).toBe("再改一下");
  });

  it("keeps only the last five", () => {
    let s = ready();
    for (let i = 0; i < 8; i++) s = apply(s, "station:update", { station: "pm", status: "working", task: summary(`t${i}`) }, i);
    expect(s.recent["pm"]).toHaveLength(RECENT_LIMIT);
    expect(s.recent["pm"]!.map((a) => a.taskId)).toEqual(["t7", "t6", "t5", "t4", "t3"]);
  });

  it("ignores events for desks that do not exist", () => {
    const s = apply(ready(), "station:update", { station: "ghost", status: "working", task: t1 }, 1);
    expect(s.recent).toEqual({});
  });

  it("survives a reconnect snapshot of the same project, cleared by a project reset", () => {
    let s = apply(ready(), "station:update", { station: "pm", status: "working", task: t1 }, 1);
    s = apply(s, "snapshot", snapshot(), 2);
    expect(s.recent["pm"]).toHaveLength(1);
    s = factoryReducer(s, { type: "reset", projectId: "other" });
    expect(s.recent).toEqual({});
    expect(initialState().recent).toEqual({});
  });

  it("drops the list once a desk has walked out", () => {
    let s: FactoryState = apply(ready(), "station:update", { station: "qa", status: "working", task: t1 }, 1);
    s = apply(s, "station:update", finished("qa", "done"), 2);
    s = apply(s, "agent:left", { agent_id: "qa", pipeline_id: "main" }, 3);
    expect(s.recent["qa"]).toHaveLength(2);
    s = factoryReducer(s, { type: "tick", now: 3 + LEAVE_MS + 1 });
    expect(s.desks["qa"]).toBeUndefined();
    expect(s.recent["qa"]).toBeUndefined();
  });

  it("pushRecent does not mutate its input", () => {
    const a: RecentAction = { at: 1, taskId: "t", title: "T", kind: "start", reworks: 0, detail: null, target: null };
    const before = { pm: [a] };
    const after = pushRecent(before, "pm", { ...a, at: 2 });
    expect(before.pm).toHaveLength(1);
    expect(after.pm!.map((x) => x.at)).toEqual([2, 1]);
  });

  it("default-run replay leaves at most five actions on every desk", () => {
    let s = initialState("demo");
    for (const line of parseReplayText(defaultRun).lines) s = factoryReducer(s, { ...line, now: line.t } as never);
    for (const id of ["pm", "swe", "qa"]) {
      expect(s.recent[id]!.length).toBeGreaterThan(0);
      expect(s.recent[id]!.length).toBeLessThanOrEqual(RECENT_LIMIT);
    }
  });
});
