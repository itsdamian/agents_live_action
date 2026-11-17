import { agent, apply, mockEff, ready, record, snapshot, summary } from "../test/factories";
import type { HistoryItem } from "../types/api";
import {
  groupHistory,
  modelBadge,
  noteAnchor,
  selectBench,
  selectDeskModels,
  selectDeskViews,
  selectHasCritical,
  selectSeatIds,
  selectStats,
  selectUnseenAlerts,
} from "./selectors";
import { factoryReducer } from "./reducer";

describe("desk views", () => {
  it("lists desks in seat order with their agent", () => {
    const s = ready();
    const views = selectDeskViews(s);
    expect(views.map((v) => v.id)).toEqual(["pm", "swe", "qa"]);
    expect(views[0]!.agent?.display_name).toBe("PM");
    expect(views.every((v) => v.gate === null)).toBe(true);
  });

  it("follows a custom lineup order and attaches the gate to its desk", () => {
    let s = ready({
      agents: [...snapshot().agents, agent("security", { kind: "check" })],
      pipelines: [{ id: "main", name: "m", seats: ["pm", "security", "swe", "qa"], pending_seats: null }],
    });
    s = apply(s, "task:awaiting_approval", { task: summary("t1", "awaiting_approval"), station: "security", approval: "after_qa" });
    const views = selectDeskViews(s);
    expect(views.map((v) => v.id)).toEqual(["pm", "security", "swe", "qa"]);
    expect(views.find((v) => v.id === "security")!.gate?.taskId).toBe("t1");
    expect(selectSeatIds(s, "nope")).toEqual([]);
  });

  it("bench = roster agents that sit in no pipeline", () => {
    const s = factoryReducer(ready(), {
      type: "agents_loaded", now: 0,
      agents: [...snapshot().agents, agent("writer", { builtin: false })],
      pipelines: [{ id: "main", name: "m", seats: ["pm", "swe", "qa"], pending_seats: null }],
    });
    expect(selectBench(s).map((a) => a.id)).toEqual(["writer"]);
  });

  it("deskModels reads each seated agent's effective model", () => {
    const s = ready();
    expect(selectDeskModels(s)["pm"]).toMatchObject({ provider: "mock", model: "mock-model" });
  });
});

describe("modelBadge", () => {
  it("names the model family", () => {
    const eff = (model: string) => ({ provider: "anthropic" as const, model, source: "agent" as const, mock_reason: null });
    expect(modelBadge(eff("claude-opus-5-5")).label).toBe("Opus");
    expect(modelBadge(eff("claude-sonnet-5-5")).label).toBe("Sonnet");
    expect(modelBadge(eff("claude-haiku-5-5")).label).toBe("Haiku");
    expect(modelBadge(eff("claude-fable-5-2")).label).toBe("Fable");
  });

  it("shows a hint only when Mock stands in for something else", () => {
    expect(modelBadge(mockEff("configured_mock"))).toEqual({ label: "Mock", hint: null });
    expect(modelBadge(mockEff("force_mock"))).toEqual({ label: "Mock", hint: "force_mock" });
    expect(modelBadge(mockEff("missing_key"))).toEqual({ label: "Mock", hint: "missing_key" });
  });
});

describe("stats and alerts", () => {
  it("counts in-flight tasks from task statuses", () => {
    let s = ready({ tasks: [record("a", "pending"), record("b", "pending")], pipeline: { ...snapshot().pipeline, queue_size: 2, done_count: 4, failed_count: 1 } });
    s = apply(s, "station:update", { station: "pm", status: "working", task: summary("a") });
    expect(selectStats(s)).toMatchObject({ completed: 4, failed: 1, inFlight: 1, queue: 2, dailyCost: 0 });
    s = apply(s, "task:awaiting_approval", { task: summary("a", "awaiting_approval"), station: "qa", approval: "after_qa" });
    expect(selectStats(s).inFlight).toBe(1);
  });

  it("unseen alerts and the critical flag clear with alerts_seen", () => {
    let s = apply(ready(), "devops:alert", { level: "info", message: "a", metric: "cpu", value: 1 });
    expect(selectHasCritical(s)).toBe(false);
    s = apply(s, "devops:alert", { level: "critical", message: "b", metric: "budget", value: 9 });
    expect(selectUnseenAlerts(s)).toBe(2);
    expect(selectHasCritical(s)).toBe(true);
    s = factoryReducer(s, { type: "alerts_seen" });
    expect(selectUnseenAlerts(s)).toBe(0);
    expect(selectHasCritical(s)).toBe(false);
  });
});

describe("notes and history", () => {
  it("noteAnchor maps `at` to a target", () => {
    const n = { taskId: "t", title: "x", tone: "normal" as const, reworks: 0, failed: false };
    expect(noteAnchor({ ...n, at: "inbox" })).toEqual({ kind: "inbox" });
    expect(noteAnchor({ ...n, at: "outbox" })).toEqual({ kind: "outbox" });
    expect(noteAnchor({ ...n, at: "swe" })).toEqual({ kind: "desk", id: "swe" });
  });

  const item = (id: string, status: HistoryItem["status"], pipeline: string | null): HistoryItem => ({
    task_id: id, title: id, priority: "medium", status, pipeline_id: pipeline, outcome: null, reason: null,
    rework_count: 0, path: [], cost_usd: null, started_at: null, finished_at: null, deliverable_count: 0,
    runs: 0, stale: false, seats: [],
  });

  it("groups by status in a fixed order and by pipeline in first-seen order", () => {
    const items = [item("a", "done", "main"), item("b", "pending", "bugs"), item("c", "in_progress", "main"), item("d", "done", null)];
    expect(groupHistory(items, "status").map((g) => [g.key, g.items.length])).toEqual([
      ["in_progress", 1], ["pending", 1], ["done", 2],
    ]);
    expect(groupHistory(items, "pipeline").map((g) => [g.key, g.items.map((i) => i.task_id)])).toEqual([
      ["main", ["a", "c", "d"]], ["bugs", ["b"]],
    ]);
  });
});
