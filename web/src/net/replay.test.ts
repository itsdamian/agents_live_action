import { afterEach, beforeEach, vi } from "vitest";
import defaultRun from "../../public/replay/default-run.jsonl?raw";
import { isValidReplayName, loadReplay, ReplayLoadError, replayUrl, startReplay, type FetchLike } from "./replay";
import type { ReplayLine, ServerEvent } from "../types/events";

const left = (id: string, t: number): ReplayLine => ({
  t,
  event: "agent:left",
  data: { agent_id: id, pipeline_id: "main" },
});

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("startReplay", () => {
  it("emits each line at its t, in order, without the t key", () => {
    const got: ServerEvent[] = [];
    startReplay({ lines: [left("a", 0), left("b", 1000), left("c", 1500)], onEvent: (e) => got.push(e) });
    expect(got).toEqual([]);
    vi.advanceTimersByTime(0);
    expect(got.map((e) => (e.data as { agent_id: string }).agent_id)).toEqual(["a"]);
    vi.advanceTimersByTime(999);
    expect(got).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(got).toHaveLength(2);
    vi.advanceTimersByTime(500);
    expect(got).toHaveLength(3);
    expect(got[0]).not.toHaveProperty("t");
  });

  it("honours a first line with t > 0 and equal timestamps", () => {
    const got: ServerEvent[] = [];
    startReplay({ lines: [left("a", 300), left("b", 300)], onEvent: (e) => got.push(e) });
    vi.advanceTimersByTime(299);
    expect(got).toHaveLength(0);
    vi.advanceTimersByTime(1);
    expect(got).toHaveLength(2);
  });

  it("speed divides every gap", () => {
    const got: ServerEvent[] = [];
    startReplay({ lines: [left("a", 0), left("b", 3000)], onEvent: (e) => got.push(e), speed: 3 });
    vi.advanceTimersByTime(999);
    expect(got).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(got).toHaveLength(2);
  });

  it("an invalid speed falls back to 1", () => {
    const got: ServerEvent[] = [];
    startReplay({ lines: [left("a", 0), left("b", 100)], onEvent: (e) => got.push(e), speed: 0 });
    vi.advanceTimersByTime(100);
    expect(got).toHaveLength(2);
  });

  it("stop() cancels the rest; finished flips after the last line", () => {
    const got: ServerEvent[] = [];
    const onFinished = vi.fn();
    const h = startReplay({ lines: [left("a", 0), left("b", 100), left("c", 200)], onEvent: (e) => got.push(e), onFinished });
    vi.advanceTimersByTime(100);
    h.stop();
    vi.advanceTimersByTime(1000);
    expect(got).toHaveLength(2);
    expect(h.finished).toBe(false);

    const h2 = startReplay({ lines: [left("a", 0)], onEvent: () => {}, onFinished });
    vi.advanceTimersByTime(0);
    expect(h2.finished).toBe(true);
    expect(onFinished).toHaveBeenCalledTimes(1);
  });

  it("emit() injects an event immediately and does nothing after stop()", () => {
    const got: ServerEvent[] = [];
    const h = startReplay({ lines: [left("a", 5000)], onEvent: (e) => got.push(e) });
    const fake = { event: "agent:left", data: { agent_id: "x", pipeline_id: "main" } } as const;
    h.emit(fake);
    expect(got).toEqual([fake]);
    h.stop();
    h.emit(fake);
    expect(got).toHaveLength(1);
  });

  it("an empty replay is finished immediately", () => {
    expect(startReplay({ lines: [], onEvent: () => {} }).finished).toBe(true);
  });
});

describe("loadReplay", () => {
  const ok = (text: string): FetchLike => async () => ({ ok: true, status: 200, text: async () => text });

  it("fetches /replay/<name>.jsonl and parses every line", async () => {
    const fetchImpl = vi.fn(ok(defaultRun));
    const lines = await loadReplay("default-run", fetchImpl);
    expect(fetchImpl).toHaveBeenCalledWith(replayUrl("default-run"));
    expect(replayUrl("default-run", "/")).toBe("/replay/default-run.jsonl");
    expect(lines[0]?.event).toBe("snapshot");
    expect(lines.length).toBeGreaterThan(30);
  });

  it("rejects bad names without fetching", async () => {
    const fetchImpl = vi.fn(ok(""));
    await expect(loadReplay("../secret", fetchImpl)).rejects.toThrow(ReplayLoadError);
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(isValidReplayName("custom-lineup")).toBe(true);
    expect(isValidReplayName("a/b")).toBe(false);
    expect(isValidReplayName("")).toBe(false);
  });

  it("reports 404, network errors, empty files and the first bad line", async () => {
    await expect(loadReplay("nope", async () => ({ ok: false, status: 404, text: async () => "" }))).rejects.toThrow(/not found \(404\)/);
    await expect(loadReplay("x", async () => { throw new Error("net"); })).rejects.toThrow(/Cannot load/);
    await expect(loadReplay("x", ok("\n\n"))).rejects.toThrow(/empty/);
    await expect(loadReplay("x", ok('{"t":0,"event":"agent:left","data":{}}\n'))).rejects.toThrow(/line 1/);
  });
});
