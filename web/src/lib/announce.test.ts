import { agent, apply, ready, summary } from "../test/factories";
import { announceSnapshot, announcements } from "./announce";

const diff = (a: ReturnType<typeof ready>, b: ReturnType<typeof ready>) => announcements(announceSnapshot(a), announceSnapshot(b), b);

describe("announcements", () => {
  const base = ready();
  it("stays silent for routine work", () => {
    const next = apply(base, "station:update", { station: "pm", status: "working", task: summary("t1") }, 1);
    expect(diff(base, next)).toEqual([]);
  });
  it("announces a waiting gate, a rejection and arrivals", () => {
    expect(diff(base, apply(base, "task:awaiting_approval", { task: summary("t1", "awaiting_approval"), station: "qa", approval: "after_qa" }, 1))).toEqual(["QA 等你點頭"]);
    expect(diff(base, apply(base, "station:update", { station: "swe", status: "rework", task: summary("t1", "in_progress", 1) }, 1))).toEqual(["SWE 被退件了"]);
    const joined = apply(base, "agent:joined", { agent: agent("designer", { builtin: false, display_name: "設計師" }), pipeline_id: "main", position: 1 }, 1);
    expect(diff(base, joined)).toEqual(["設計師 報到了"]);
  });
  it("announces completed and failed tasks", () => {
    expect(diff(base, apply(base, "task:completed", { task: summary("t1", "done"), done_count: 1, deliverables: [] }, 1))).toContain("一張任務完成了");
    expect(diff(base, apply(base, "task:failed", { task: summary("t1", "failed", 3), reason: "x", failed_count: 1 }, 1))).toContain("一張任務失敗了");
  });
  it("announces someone leaving when the walk-out starts, and not again when the desk is removed", () => {
    const leaving = apply(base, "agent:left", { agent_id: "qa", pipeline_id: "main" }, 1);
    expect(leaving.desks.qa?.state).toBe("leaving");
    expect(diff(base, leaving)).toEqual(["QA 離開了"]);
    const gone = { ...leaving, desks: Object.fromEntries(Object.entries(leaving.desks).filter(([id]) => id !== "qa")) };
    expect(announcements(announceSnapshot(leaving), announceSnapshot(gone), gone)).toEqual([]);
  });
  it("announces someone leaving the stage", () => {
    const left = apply(apply(base, "agent:left", { agent_id: "qa", pipeline_id: "main" }, 1), "agent:left", { agent_id: "qa", pipeline_id: "main" }, 2);
    const gone = { ...left, desks: Object.fromEntries(Object.entries(left.desks).filter(([id]) => id !== "qa")) };
    expect(announcements(announceSnapshot(base), announceSnapshot(gone), gone)).toEqual(["QA 離開了"]);
  });
});
