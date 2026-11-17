import defaultRun from "../../public/replay/default-run.jsonl?raw";
import customLineup from "../../public/replay/custom-lineup.jsonl?raw";
import { parseReplayText } from "../net/guards";
import { agent, apply, mockEff, ready, record, snapshot, summary } from "../test/factories";
import { CHEER_MS, HELLO_MS, LEAVE_MS } from "./deskMachine";
import { factoryReducer, initialState, type FactoryState } from "./reducer";
import type { AgentView, UsageSummary } from "../types/events";

const usage = (n: number): UsageSummary => ({ input_tokens: n * 100, output_tokens: n * 50, cost: n / 100, call_count: n });
const station = (s: FactoryState, id: string) => s.desks[id]!;

describe("snapshot", () => {
  it("rebuilds everything from scratch", () => {
    const stale = apply(ready(), "task:created", { task: summary("old", "pending"), queue_size: 3 }, 1);
    const s = apply(
      stale,
      "snapshot",
      snapshot({
        tasks: [record("a", "done", 2), record("b", "pending")],
        pipeline: {
          ...snapshot().pipeline,
          queue_size: 1,
          done_count: 7,
          failed_count: 2,
          stations: {
            pm: { status: "idle", task_id: null, task_title: null },
            swe: { status: "working", task_id: "b", task_title: "B title" },
            qa: { status: "idle", task_id: null, task_title: null },
          },
        },
        usage: { project: usage(3), daily_cost: 0.5, daily_budget_usd: null, project_budget_usd: 9, budget_paused: true },
        history_counts: { pending: 1, in_progress: 0, awaiting_approval: 0, done: 1, failed: 0 },
        force_mock: true,
        engine: "langgraph",
        task_store: "file",
      }),
      10,
    );
    expect(s.ready).toBe(true);
    expect(s.tasks).not.toHaveProperty("old");
    expect(Object.keys(s.tasks)).toEqual(["a", "b"]);
    expect(s.counters).toEqual({ created: 0, completed: 7, failed: 2, reworks: 2 });
    expect(s.queueSize).toBe(1);
    expect(s.usage).toMatchObject({ project: usage(3), dailyCost: 0.5, dailyBudgetUsd: null, budgetPaused: true });
    expect(s.forceMock).toBe(true);
    expect(s.taskStore).toBe("file");
    expect(s.historyCounts.done).toBe(1);
    expect(station(s, "swe")).toMatchObject({ state: "working", taskId: "b" });
    expect(s.notes["b"]).toMatchObject({ at: "swe", title: "B title" });
    expect(Object.keys(s.desks)).toEqual(["pm", "swe", "qa"]);
  });

  it("keeps the connection state and projectId", () => {
    let s = factoryReducer(initialState("demo"), { type: "connection", status: "open" });
    s = apply(s, "snapshot", snapshot(), 0);
    expect(s.connection.status).toBe("open");
    expect(s.projectId).toBe("demo");
  });

  it("rebuilds gates from awaiting_approval without needing the event (E5)", () => {
    const s = ready({
      tasks: [record("t1", "awaiting_approval")],
      pipeline: {
        ...snapshot().pipeline,
        stations: { ...snapshot().pipeline.stations, qa: { status: "awaiting_approval", task_id: "t1", task_title: "X" } },
        awaiting_approval: [{ task: summary("t1", "in_progress"), station: "qa", approval: "after_qa" }],
      },
    });
    expect(s.awaiting["t1"]).toMatchObject({ station: "qa", approval: "after_qa" });
    expect(station(s, "qa").state).toBe("waiting");
    expect(s.tasks["t1"]!.status).toBe("awaiting_approval");
    expect(s.notes["t1"]!.at).toBe("qa");
  });

  it("restores bubble text from `live` for working desks", () => {
    const s = ready({
      pipeline: {
        ...snapshot().pipeline,
        stations: { ...snapshot().pipeline.stations, pm: { status: "working", task_id: "t1", task_title: "X" } },
      },
      live: { pm: { task_id: "t1", phase: "thinking", text: "思考中…（mock-model）" } },
    });
    expect(station(s, "pm")).toMatchObject({ state: "working", phase: "thinking", text: "思考中…（mock-model）" });
  });

  it("creates desks for every seat of every pipeline, in the agents map too", () => {
    const agents = [...snapshot().agents, agent("designer", { builtin: false })];
    const s = ready({
      agents,
      pipelines: [{ id: "main", name: "主線", seats: ["pm", "designer", "swe", "qa"], pending_seats: null }],
    });
    expect(Object.keys(s.desks).sort()).toEqual(["designer", "pm", "qa", "swe"]);
    expect(s.agents["designer"]!.display_name).toBe("DESIGNER");
  });

  it("replays recent devops alerts as already seen", () => {
    const s = ready({ devops_alerts: [{ level: "warning", message: "m", metric: "cpu", value: 90 }] });
    expect(s.alerts).toHaveLength(1);
    expect(s.alertsSeen).toBe(1);
  });
});

describe("task lifecycle events", () => {
  it("task:created adds a note in the inbox, counts and sets the queue size", () => {
    const s = apply(ready({ tasks: [record("t1", "pending")], history_counts: { pending: 1, in_progress: 0, awaiting_approval: 0, done: 0, failed: 0 } }),
      "task:created", { task: summary("t1", "pending"), queue_size: 4 }, 5);
    expect(s.notes["t1"]).toMatchObject({ at: "inbox", tone: "normal" });
    expect(s.queueSize).toBe(4);
    expect(s.counters.created).toBe(1);
    expect(s.tasks["t1"]!.status).toBe("in_progress");
    expect(s.historyCounts).toMatchObject({ pending: 0, in_progress: 1 });
  });

  it("task:created drops finished notes of earlier tasks", () => {
    let s = apply(ready(), "task:created", { task: summary("t1", "pending"), queue_size: 0 });
    s = apply(s, "task:completed", { task: summary("t1", "done"), done_count: 1, deliverables: [] });
    expect(s.notes["t1"]!.at).toBe("outbox");
    s = apply(s, "task:created", { task: summary("t2", "pending"), queue_size: 0 });
    expect(Object.keys(s.notes)).toEqual(["t2"]);
  });

  it("station:update working moves the note to the desk and starts the desk", () => {
    let s = apply(ready(), "task:created", { task: summary("t1", "pending"), queue_size: 0 });
    s = apply(s, "station:update", { station: "pm", status: "working", task: summary("t1") }, 20);
    expect(station(s, "pm")).toMatchObject({ state: "working", taskId: "t1", since: 20 });
    expect(s.notes["t1"]).toMatchObject({ at: "pm", tone: "normal" });
  });

  it("station:update done and error return the desk to idle", () => {
    let s = apply(ready(), "station:update", { station: "pm", status: "working", task: summary() }, 1);
    s = apply(s, "station:update", { station: "pm", status: "done", task: summary(), success: true, error: null, execution_time: 1 }, 2);
    expect(station(s, "pm").state).toBe("idle");
    s = apply(s, "station:update", { station: "swe", status: "working", task: summary() }, 3);
    s = apply(s, "station:update", { station: "swe", status: "error", task: summary(), success: false, error: "boom", execution_time: 1 }, 4);
    expect(station(s, "swe").state).toBe("idle");
  });

  it("task:completed sets done_count, lands the note green and cheers idle desks", () => {
    let s = apply(ready(), "station:update", { station: "qa", status: "working", task: summary() }, 1);
    s = apply(s, "task:completed", { task: summary("t1", "done", 1), done_count: 9, deliverables: ["a.md"] }, 100);
    expect(s.counters.completed).toBe(9);
    expect(s.tasks["t1"]!.status).toBe("done");
    expect(s.notes["t1"]).toMatchObject({ at: "outbox", tone: "ok", reworks: 1 });
    expect(station(s, "pm")).toMatchObject({ state: "cheer", until: 100 + CHEER_MS });
    expect(station(s, "qa").state).toBe("working"); // busy desks do not cheer
  });

  it("task:failed counts, lands the note red and releases its desks", () => {
    let s = apply(ready(), "station:update", { station: "swe", status: "working", task: summary() }, 1);
    s = apply(s, "task:failed", { task: summary("t1", "failed", 3), reason: "QA still failing after 3 reworks", failed_count: 4 }, 50);
    expect(s.counters.failed).toBe(4);
    expect(s.tasks["t1"]!.status).toBe("failed");
    expect(s.notes["t1"]).toMatchObject({ at: "outbox", tone: "bad", failed: true });
    expect(station(s, "swe").state).toBe("idle");
  });

  it("history counts follow task status transitions", () => {
    let s = ready({ tasks: [record("t1", "pending")], history_counts: { pending: 1, in_progress: 0, awaiting_approval: 0, done: 0, failed: 0 } });
    s = apply(s, "station:update", { station: "pm", status: "working", task: summary() });
    expect(s.historyCounts).toMatchObject({ pending: 0, in_progress: 1 });
    s = apply(s, "task:awaiting_approval", { task: summary("t1", "awaiting_approval"), station: "qa", approval: "after_qa" });
    expect(s.historyCounts).toMatchObject({ in_progress: 0, awaiting_approval: 1 });
    s = apply(s, "task:approved", { task: summary(), station: "qa" });
    expect(s.historyCounts).toMatchObject({ in_progress: 1, awaiting_approval: 0 });
    s = apply(s, "task:completed", { task: summary("t1", "done"), done_count: 1, deliverables: [] });
    expect(s.historyCounts).toMatchObject({ in_progress: 0, done: 1 });
  });
});

describe("rework", () => {
  it("puts the rework target in reject until its next working, and counts it", () => {
    let s = ready();
    s = apply(s, "station:update", { station: "qa", status: "working", task: summary() }, 1);
    s = apply(s, "station:update", { station: "qa", status: "done", task: summary(), success: true, error: null, execution_time: 1 }, 2);
    s = apply(s, "station:update", { station: "swe", status: "rework", task: summary("t1", "in_progress", 1) }, 3);
    expect(station(s, "swe")).toMatchObject({ state: "reject", taskId: "t1" });
    expect(s.counters.reworks).toBe(1);
    expect(s.notes["t1"]).toMatchObject({ at: "swe", tone: "bad", reworks: 1 });
    s = apply(s, "station:update", { station: "swe", status: "working", task: summary("t1", "in_progress", 1) }, 4);
    expect(station(s, "swe").state).toBe("working");
    expect(s.notes["t1"]).toMatchObject({ at: "swe", tone: "normal", reworks: 1 });
  });

  it("a custom check seat sends the note back to the nearest make seat", () => {
    const agents = [...snapshot().agents, agent("security", { kind: "check" })];
    let s = ready({ agents, pipelines: [{ id: "main", name: "m", seats: ["pm", "swe", "qa", "security"], pending_seats: null }] });
    s = apply(s, "station:update", { station: "swe", status: "rework", task: summary("t1", "in_progress", 1) }, 1);
    expect(station(s, "swe").state).toBe("reject");
    expect(station(s, "security").state).toBe("idle");
  });
});

describe("approval gate", () => {
  const gated = () => apply(ready(), "task:awaiting_approval", { task: summary("t1", "awaiting_approval"), station: "qa", approval: "after_qa" }, 10);

  it("awaiting_approval puts the gate desk in waiting and registers the gate", () => {
    const s = gated();
    expect(station(s, "qa")).toMatchObject({ state: "waiting", taskId: "t1" });
    expect(s.awaiting["t1"]).toEqual({ taskId: "t1", title: "加登入頁", station: "qa", approval: "after_qa" });
    expect(s.tasks["t1"]!.status).toBe("awaiting_approval");
  });

  it("approved clears the gate and releases the desk", () => {
    const s = apply(gated(), "task:approved", { task: summary(), station: "qa" }, 20);
    expect(s.awaiting).toEqual({});
    expect(station(s, "qa").state).toBe("idle");
    expect(s.tasks["t1"]!.status).toBe("in_progress");
  });

  it("rejected clears the gate; the rework event then marks the target", () => {
    let s = apply(gated(), "task:rejected", { task: summary(), station: "qa", note: "再改一下" }, 20);
    expect(s.awaiting).toEqual({});
    expect(station(s, "qa").state).toBe("idle");
    s = apply(s, "station:update", { station: "swe", status: "rework", task: summary("t1", "in_progress", 1) }, 21);
    expect(station(s, "swe").state).toBe("reject");
  });

  it("ignores phases that arrive while the desk is waiting", () => {
    const s = apply(gated(), "agent:phase", { task_id: "t1", station: "qa", step: 3, of: 3, phase: "verdict", text: "檢查通過" }, 11);
    expect(station(s, "qa").state).toBe("waiting");
  });
});

describe("agent:phase and out-of-order delivery", () => {
  const phase = (s: FactoryState, station_: string, ph: "intake" | "thinking" | "output" | "verdict", text: string, now: number) =>
    apply(s, "agent:phase", { task_id: "t1", station: station_, step: 1, of: 3, phase: ph, text }, now);

  it("phase before working moves the desk to working, working then keeps the text", () => {
    let s = phase(ready(), "pm", "intake", "讀任務：「加登入頁」", 5);
    expect(station(s, "pm")).toMatchObject({ state: "working", phase: "intake", text: "讀任務：「加登入頁」" });
    s = apply(s, "station:update", { station: "pm", status: "working", task: summary() }, 6);
    expect(station(s, "pm").state).toBe("working");
  });

  it("drops a stale phase that arrives right after done, accepts it after the window", () => {
    let s = apply(ready(), "station:update", { station: "pm", status: "working", task: summary() }, 0);
    s = apply(s, "station:update", { station: "pm", status: "done", task: summary(), success: true, error: null, execution_time: 1 }, 1000);
    const late = phase(s, "pm", "output", "交出成果，往下傳", 1300);
    expect(station(late, "pm").state).toBe("idle");
    const later = phase(s, "pm", "output", "x", 1600);
    expect(station(later, "pm").state).toBe("working");
  });

  it("ignores a phase for an unknown station", () => {
    const s = ready();
    expect(phase(s, "ghost", "intake", "x", 1)).toBe(s);
  });

  it("agent:joined before pipeline:updated and the reverse converge to the same lineup", () => {
    const designer = agent("designer", { builtin: false, look: "beret" });
    const roster = [...snapshot().agents, designer];
    const pipeline = { id: "main", name: "主線", seats: ["pm", "designer", "swe", "qa"] };

    let a = apply(ready(), "agent:joined", { agent: designer, pipeline_id: "main", position: 1 }, 10);
    a = apply(a, "pipeline:updated", { pipeline, agents: roster }, 11);
    let b = apply(ready(), "pipeline:updated", { pipeline, agents: roster }, 10);
    b = apply(b, "agent:joined", { agent: designer, pipeline_id: "main", position: 1 }, 11);

    for (const s of [a, b]) {
      expect(s.pipelines[0]!.seats).toEqual(["pm", "designer", "swe", "qa"]);
      expect(station(s, "designer").state).toBe("hello");
      expect(s.agents["designer"]!.look).toBe("beret");
    }
  });

  it("usage:update may arrive after task:completed without disturbing the task", () => {
    let s = apply(ready(), "task:completed", { task: summary("t1", "done"), done_count: 1, deliverables: [] });
    s = apply(s, "usage:update", {
      task_id: "t1", task: summary("t1", "in_progress"),
      record: { project_id: "demo", task_id: "t1", provider: "mock", model: "mock-model", input_tokens: 1, output_tokens: 1, cost: 0, timestamp: "x" },
      task_usage: usage(1), project_usage: usage(1), daily_cost: 0,
    });
    expect(s.tasks["t1"]!.status).toBe("done");
    expect(s.usage.byTask["t1"]).toEqual(usage(1));
  });
});

describe("cheer and hello timing (injected now)", () => {
  it("cheer lasts CHEER_MS; tick before the deadline changes nothing", () => {
    let s = apply(ready(), "task:completed", { task: summary("t1", "done"), done_count: 1, deliverables: [] }, 1000);
    expect(station(s, "pm").state).toBe("cheer");
    const early = factoryReducer(s, { type: "tick", now: 1000 + CHEER_MS - 1 });
    expect(early).toBe(s);
    s = factoryReducer(s, { type: "tick", now: 1000 + CHEER_MS });
    expect(station(s, "pm").state).toBe("idle");
  });

  it("hello ends after HELLO_MS", () => {
    let s = apply(ready(), "agent:joined", { agent: agent("designer", { builtin: false }), pipeline_id: "main", position: 3 }, 0);
    expect(station(s, "designer").state).toBe("hello");
    s = factoryReducer(s, { type: "tick", now: HELLO_MS });
    expect(station(s, "designer").state).toBe("idle");
  });
});

describe("lineup events", () => {
  it("agent:left marks the desk leaving and a tick removes it", () => {
    let s = apply(ready(), "agent:left", { agent_id: "qa", pipeline_id: "main" }, 100);
    expect(s.pipelines[0]!.seats).toEqual(["pm", "swe"]);
    expect(station(s, "qa").state).toBe("leaving");
    s = factoryReducer(s, { type: "tick", now: 100 + LEAVE_MS });
    expect(s.desks).not.toHaveProperty("qa");
    expect(s.agents).toHaveProperty("qa"); // still in the roster: it is on the bench
  });

  it("pipeline:pending flags seats that will leave; pipeline:updated clears it", () => {
    let s = apply(ready(), "pipeline:pending", { pipeline_id: "main", seats: ["pm", "qa"], reason: "task_in_flight", task_id: "t1" }, 1);
    expect(s.desks["swe"]!.pendingExit).toBe(true);
    expect(s.desks["pm"]!.pendingExit).toBe(false);
    expect(s.pendingPipelines["main"]).toMatchObject({ seats: ["pm", "qa"], taskId: "t1" });
    s = apply(s, "pipeline:updated", { pipeline: { id: "main", name: "主線", seats: ["pm", "qa"] }, agents: snapshot().agents }, 2);
    expect(s.pendingPipelines).toEqual({});
    expect(station(s, "swe").state).toBe("leaving");
    expect(s.pipelines[0]!.pending_seats).toBeNull();
  });

  it("llm:updated replaces each agent's effective model and the project llm", () => {
    const opus: AgentView = agent("qa", {
      llm: { provider: "anthropic", model: "claude-opus-5-5" },
      effective_llm: { provider: "anthropic", model: "claude-opus-5-5", source: "agent", mock_reason: null },
    });
    const s = apply(ready(), "llm:updated", {
      project_llm: { provider: "anthropic", model: "claude-sonnet-5-5" },
      agents: [opus, agent("pm", { effective_llm: { ...mockEff("missing_key"), source: "project" } })],
      effective: "next_task",
    });
    expect(s.agents["qa"]!.effective_llm.model).toBe("claude-opus-5-5");
    expect(s.agents["pm"]!.effective_llm.mock_reason).toBe("missing_key");
    expect(s.agents["swe"]!.effective_llm.provider).toBe("mock"); // untouched
    expect(s.llm!.project).toEqual({ provider: "anthropic", model: "claude-sonnet-5-5" });
    expect(s.llm!.models).toHaveLength(1);
  });
});

describe("usage, alerts, tokens", () => {
  it("usage:update stores project usage, daily cost and per-task usage", () => {
    const s = apply(ready(), "usage:update", {
      task_id: "t1", task: summary(),
      record: { project_id: "demo", task_id: "t1", provider: "anthropic", model: "claude-opus-5-5", input_tokens: 10, output_tokens: 5, cost: 0.01, timestamp: "x" },
      task_usage: usage(2), project_usage: usage(5), daily_cost: 0.07,
    });
    expect(s.usage.project).toEqual(usage(5));
    expect(s.usage.dailyCost).toBe(0.07);
    expect(s.usage.byTask["t1"]).toEqual(usage(2));
  });

  it("devops:alert appends with increasing ids and caps the list; alerts_seen clears the dot", () => {
    let s = ready();
    for (let i = 0; i < 55; i++) s = apply(s, "devops:alert", { level: i === 54 ? "critical" : "info", message: `m${i}`, metric: "cpu", value: i });
    expect(s.alerts).toHaveLength(50);
    expect(s.alerts[49]!.id).toBe(55);
    expect(s.alertsSeen).toBe(0);
    s = factoryReducer(s, { type: "alerts_seen" });
    expect(s.alertsSeen).toBe(50);
  });

  it("agent:token is ignored by the reducer (it feeds the liveText store)", () => {
    const s = ready();
    expect(apply(s, "agent:token", { task_id: "t1", station: "pm", attempt: 1, seq: 1, text: "x", final: false, truncated: false })).toBe(s);
  });
});

describe("UI actions", () => {
  it("connection tracks status, attempt, delay and reason", () => {
    let s = factoryReducer(initialState(), { type: "connection", status: "reconnecting", attempt: 2, delay: 5000 });
    expect(s.connection).toEqual({ status: "reconnecting", attempt: 2, delay: 5000, reason: null });
    s = factoryReducer(s, { type: "connection", status: "error", reason: "Unknown project" });
    expect(s.connection).toMatchObject({ status: "error", reason: "Unknown project" });
    s = factoryReducer(s, { type: "connection", status: "open" });
    expect(s.connection).toEqual({ status: "open", attempt: 0, delay: null, reason: null });
  });

  it("reset clears the state for a new project", () => {
    const s = factoryReducer(ready(), { type: "reset", projectId: "other" });
    expect(s.projectId).toBe("other");
    expect(s.ready).toBe(false);
    expect(s.desks).toEqual({});
  });

  it("agents_loaded replaces the roster and seats new desks", () => {
    const s = factoryReducer(ready(), {
      type: "agents_loaded", now: 7,
      agents: [...snapshot().agents, agent("writer", { builtin: false })],
      pipelines: [{ id: "main", name: "主線", seats: ["pm", "swe", "qa", "writer"], pending_seats: null }],
    });
    expect(station(s, "writer")).toMatchObject({ state: "idle", since: 7 });
    expect(Object.keys(s.agents)).toContain("writer");
  });
});

describe("replay fixtures", () => {
  const feed = (text: string): { state: FactoryState; lastT: number } => {
    let state = initialState("example-project");
    let lastT = 0;
    for (const line of parseReplayText(text).lines) {
      state = factoryReducer(state, { ...line, now: line.t } as never);
      lastT = line.t;
    }
    return { state, lastT };
  };

  it("default-run ends with the task done, the gate cleared and counters updated", () => {
    const { state, lastT } = feed(defaultRun);
    const settled = factoryReducer(state, { type: "tick", now: lastT + CHEER_MS });
    const view = {
      connection: settled.connection.status,
      tasks: Object.fromEntries(Object.entries(settled.tasks).map(([id, t]) => [id, `${t.status}/${t.rework_count}`])),
      desks: Object.fromEntries(Object.entries(settled.desks).map(([id, d]) => [id, d.state])),
      notes: settled.notes,
      awaiting: settled.awaiting,
      counters: settled.counters,
      queueSize: settled.queueSize,
      usage: settled.usage,
      historyCounts: settled.historyCounts,
      alerts: settled.alerts.map((a) => `${a.level}:${a.metric}`),
    };
    expect(view).toMatchSnapshot();
    expect(state.desks["pm"]!.state).toBe("cheer"); // right at the last event
    expect(settled.tasks["task-004"]).toMatchObject({ status: "done", rework_count: 1 });
  });

  it("custom-lineup ends with five seats, the security badge on Opus and no pending lineup", () => {
    const { state } = feed(customLineup);
    expect(state.pipelines[0]!.seats).toEqual(["pm", "designer", "swe", "qa", "security"]);
    expect(state.pendingPipelines).toEqual({});
    expect(state.agents["security"]!.effective_llm).toMatchObject({ provider: "anthropic", model: "claude-opus-5-5" });
    expect(state.counters).toMatchObject({ completed: 2, reworks: 1 });
    expect(state.usage.dailyCost).toBeGreaterThan(0);
  });
});
