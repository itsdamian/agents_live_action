import defaultRun from "../../public/replay/default-run.jsonl?raw";
import customLineup from "../../public/replay/custom-lineup.jsonl?raw";
import crowded from "../../public/replay/crowded.jsonl?raw";
import crowded3x8 from "../../public/replay/crowded-3x8.jsonl?raw";
import gateRun from "../../public/replay/gate.jsonl?raw";
import recordedMvp from "../../public/replay/recorded-mvp.jsonl?raw";
import historyJson from "../../public/replay/history.json?raw";
import {
  isHistoryResponse,
  parseReplayLine,
  parseReplayText,
  parseServerFrame,
  parseServerFrameText,
  validateEventData,
  validateHistoryResponse,
} from "./guards";
import type { HistoryResponse } from "../types/api";
import type { EventName } from "../types/events";

const FIXTURES = { "default-run": defaultRun, "custom-lineup": customLineup, crowded, "crowded-3x8": crowded3x8, gate: gateRun, "recorded-mvp": recordedMvp } as const;

describe("replay fixtures", () => {
  for (const [name, text] of Object.entries(FIXTURES)) {
    describe(name, () => {
      const rawLines = text.split("\n").filter((l) => l.trim() !== "");

      it("every line passes the guard", () => {
        const { lines, errors } = parseReplayText(text);
        expect(errors).toEqual([]);
        expect(lines).toHaveLength(rawLines.length);
      });

      it("starts with a snapshot at t=0 and t never goes backwards", () => {
        const { lines } = parseReplayText(text);
        expect(lines[0]?.event).toBe("snapshot");
        expect(lines[0]?.t).toBe(0);
        for (let i = 1; i < lines.length; i++) {
          expect(lines[i]!.t).toBeGreaterThanOrEqual(lines[i - 1]!.t);
        }
      });

      it("has exactly one snapshot", () => {
        const { lines } = parseReplayText(text);
        expect(lines.filter((l) => l.event === "snapshot")).toHaveLength(1);
      });
    });
  }

  const events = (text: string) => parseReplayText(text).lines.map((l) => l.event);

  it("default-run covers rework, the after_qa gate, usage and completion", () => {
    const { lines } = parseReplayText(defaultRun);
    const names = lines.map((l) => l.event);
    expect(names).toContain("task:awaiting_approval");
    expect(names).toContain("task:approved");
    expect(names).toContain("task:completed");
    expect(names).toContain("usage:update");
    const rework = lines.filter((l) => l.event === "station:update" && l.data.status === "rework");
    expect(rework).toHaveLength(1);
    const gate = lines.find((l) => l.event === "task:awaiting_approval");
    expect(gate?.event === "task:awaiting_approval" && gate.data.station).toBe("qa");
    // 3 phases per seat visit: 5 visits
    expect(names.filter((n) => n === "agent:phase")).toHaveLength(15);
  });

  it("recorded-mvp (Task 26, real backend) keeps all 118 events: joined, llm:updated, security rework, reject then approve", () => {
    const names = events(recordedMvp);
    expect(names).toHaveLength(118);
    for (const n of ["agent:joined", "pipeline:updated", "llm:updated", "task:rejected", "task:approved", "task:completed"] as const) {
      expect(names).toContain(n);
    }
    const gate = names.filter((n) => n === "task:awaiting_approval" || n === "task:rejected" || n === "task:approved");
    expect(gate).toEqual(["task:awaiting_approval", "task:rejected", "task:awaiting_approval", "task:approved"]);
  });

  it("custom-lineup covers joined, pending, llm:updated and a security rework", () => {
    const names = events(customLineup);
    expect(names).toContain("agent:joined");
    expect(names).toContain("pipeline:pending");
    expect(names).toContain("pipeline:updated");
    expect(names).toContain("llm:updated");
    const { lines } = parseReplayText(customLineup);
    const secRework = lines.some(
      (l) => l.event === "agent:phase" && l.data.station === "security" && l.data.passed === false,
    );
    expect(secRework).toBe(true);
  });

  it("crowded-3x8 has three pipelines of eight seats each", () => {
    const snap = parseReplayText(crowded3x8).lines[0];
    expect(snap?.event === "snapshot" && snap.data.pipelines.map((p) => p.seats.length)).toEqual([8, 8, 8]);
    expect(snap?.event === "snapshot" && snap.data.pipelines.every((p) => new Set(p.seats).size === 8)).toBe(true);
  });

  it("gate restores an approval gate from the snapshot alone (E5)", () => {
    const snap = parseReplayText(gateRun).lines[0];
    expect(snap?.event === "snapshot" && snap.data.pipeline.awaiting_approval).toHaveLength(1);
  });

  it("crowded has 8 seats in one pipeline", () => {
    const { lines } = parseReplayText(crowded);
    const snap = lines[0];
    expect(snap?.event === "snapshot" && snap.data.agents).toHaveLength(8);
    expect(snap?.event === "snapshot" && snap.data.pipelines[0]?.seats).toHaveLength(8);
  });
});

describe("history.json", () => {
  const data: unknown = JSON.parse(historyJson);

  it("passes the guard", () => {
    expect(validateHistoryResponse(data)).toEqual([]);
    expect(isHistoryResponse(data)).toBe(true);
  });

  it("has 30 items whose counts add up", () => {
    const h = data as HistoryResponse;
    expect(h.items).toHaveLength(30);
    expect(h.total).toBe(30);
    const sum = Object.values(h.counts).reduce((a, b) => a + b, 0);
    expect(sum).toBe(30);
  });

  it("every path id is in the item's seat snapshot", () => {
    for (const item of (data as HistoryResponse).items) {
      const ids = item.seats.map((s) => s.id);
      for (const station of item.path) expect(ids).toContain(station);
    }
  });

  it("repeats stations on rework (plan §6.3a) and every id is still in seats", () => {
    const h = data as HistoryResponse;
    const reworked = h.items.find((i) => i.rework_count > 0 && i.status === "done");
    expect(reworked).toBeDefined();
    expect(reworked!.path).toEqual(["pm", "swe", "qa", "swe", "qa"].slice(0, reworked!.path.length));
    expect(new Set(reworked!.path).size).toBeLessThan(reworked!.path.length);
  });

  it("keeps seat snapshots of agents that no longer exist", () => {
    const h = data as HistoryResponse;
    const snapshotIds = new Set(h.items.flatMap((i) => i.seats.map((s) => s.id)));
    expect(snapshotIds.has("designer")).toBe(true);
    expect(snapshotIds.has("security")).toBe(true);
  });
});

describe("guards reject bad data", () => {
  const task = { id: "t1", title: "x", priority: "high", status: "pending", rework_count: 0 };
  const ok = (event: string, data: unknown) => parseServerFrame({ event, data });

  it("accepts a minimal valid frame", () => {
    expect(ok("task:created", { task, queue_size: 0 }).ok).toBe(true);
  });

  it("reports a missing key with its path", () => {
    expect(validateEventData("task:created", { task })).toEqual(["data.queue_size: missing"]);
  });

  it("reports wrong types and bad enum values", () => {
    const errs = validateEventData("task:created", {
      task: { ...task, priority: "urgent", rework_count: "1" },
      queue_size: 0,
    });
    expect(errs.some((e) => e.startsWith("data.task.priority"))).toBe(true);
    expect(errs.some((e) => e.startsWith("data.task.rework_count"))).toBe(true);
  });

  it("validates the three station:update shapes", () => {
    expect(ok("station:update", { station: "pm", status: "working", task }).ok).toBe(true);
    expect(ok("station:update", { station: "swe", status: "rework", task }).ok).toBe(true);
    expect(
      ok("station:update", {
        station: "pm", status: "error", task, success: false, error: "boom", execution_time: 1,
      }).ok,
    ).toBe(true);
    // finished shape without the finished-only keys
    expect(ok("station:update", { station: "pm", status: "done", task }).ok).toBe(false);
    expect(ok("station:update", { station: "pm", status: "paused", task }).ok).toBe(false);
  });

  it("agent:phase always carries passed / rework_target (null outside verdict, plan §6.3a)", () => {
    const base = { task_id: "t1", station: "qa", step: 3, of: 3, phase: "verdict", text: "x", passed: null, rework_target: null };
    expect(ok("agent:phase", base).ok).toBe(true);
    expect(ok("agent:phase", { ...base, passed: false, rework_target: "swe" }).ok).toBe(true);
    expect(ok("agent:phase", { ...base, passed: "no" }).ok).toBe(false);
    expect(ok("agent:phase", { ...base, phase: "dreaming" }).ok).toBe(false);
    const { passed: _p, ...withoutPassed } = base;
    void _p;
    expect(validateEventData("agent:phase", withoutPassed)).toEqual(["data.passed: missing"]);
  });

  it("agent:left, llm:updated and agent:joined need their shapes", () => {
    expect(ok("agent:left", { agent_id: "designer", pipeline_id: "main" }).ok).toBe(true);
    expect(ok("agent:left", { agent_id: "designer" }).ok).toBe(false);
    expect(ok("llm:updated", { project_llm: "mock", agents: [], effective: "now" }).ok).toBe(true);
    expect(ok("llm:updated", { project_llm: "gpt", agents: [], effective: "now" }).ok).toBe(false);
    expect(
      ok("llm:updated", {
        project_llm: { provider: "anthropic", model: "claude-sonnet-5-5" }, agents: [], effective: "next_task",
      }).ok,
    ).toBe(true);
  });

  it("flags unknown event names as unknown (not as errors)", () => {
    const r = parseServerFrame({ event: "staffing:progress", data: {} });
    expect(r).toMatchObject({ ok: false, unknown: true });
  });

  it("rejects non-frames, bad JSON and bad envelope fields", () => {
    expect(parseServerFrame(null)).toMatchObject({ ok: false, unknown: false });
    expect(parseServerFrame({ data: {} })).toMatchObject({ ok: false });
    expect(parseServerFrameText("{not json")).toMatchObject({ ok: false, reason: "frame is not valid JSON" });
    expect(parseServerFrame({ event: "task:created", data: { task, queue_size: 0 }, pipeline_id: 3 }).ok).toBe(false);
  });

  it("accepts pipeline_id on the envelope", () => {
    const r = parseServerFrame({
      event: "agent:left", data: { agent_id: "a", pipeline_id: "main" }, pipeline_id: "main",
    });
    expect(r.ok).toBe(true);
  });

  it("replay lines need a non-negative numeric t", () => {
    const frame = { event: "agent:left", data: { agent_id: "a", pipeline_id: "main" } };
    expect(parseReplayLine({ t: 0, ...frame }).ok).toBe(true);
    expect(parseReplayLine({ ...frame }).ok).toBe(false);
    expect(parseReplayLine({ t: -1, ...frame }).ok).toBe(false);
    expect(parseReplayLine({ t: "1", ...frame }).ok).toBe(false);
  });

  it("parseReplayText reports bad lines with line numbers and keeps the good ones", () => {
    const good = JSON.stringify({ t: 1, event: "agent:left", data: { agent_id: "a", pipeline_id: "main" } });
    const { lines, errors } = parseReplayText(`${good}\n\nnope\n{"t":2,"event":"agent:left","data":{}}\n`);
    expect(lines).toHaveLength(1);
    expect(errors.map((e) => e.line)).toEqual([3, 4]);
  });

  it("history guard flags a missing seats key", () => {
    const h = JSON.parse(historyJson) as HistoryResponse;
    const bad = { ...h, items: [{ ...h.items[0], seats: undefined }] };
    expect(validateHistoryResponse(JSON.parse(JSON.stringify(bad))).some((e) => e.includes("seats"))).toBe(true);
  });

  it("covers every event name with a checker", () => {
    const names: EventName[] = [
      "snapshot", "task:created", "station:update", "task:completed", "task:failed", "usage:update",
      "devops:alert", "task:awaiting_approval", "task:approved", "task:rejected", "agent:phase",
      "agent:token", "agent:joined", "agent:left", "pipeline:updated", "pipeline:pending", "llm:updated",
    ];
    for (const n of names) expect(validateEventData(n, 42).length).toBeGreaterThan(0);
  });
});

describe("replay fixtures agree with their snapshot's FORCE_MOCK (plan D22)", () => {
  const files = import.meta.glob("../../public/replay/*.jsonl", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
  it.each(Object.keys(files))("%s", (name) => {
    const { lines } = parseReplayText(files[name]!);
    const snap = lines[0]!;
    if (snap.event !== "snapshot") throw new Error("first line must be a snapshot");
    const reasons = JSON.stringify(lines.map((l) => l.data)).match(/"mock_reason":"[a-z_]+"/g) ?? [];
    if (snap.data.force_mock) expect(new Set(reasons)).toEqual(new Set(['"mock_reason":"force_mock"']));
    else expect(reasons).not.toContain('"mock_reason":"force_mock"');
  });
});
