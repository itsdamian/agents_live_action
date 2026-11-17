import { agent, mockEff, ready, snapshot } from "../test/factories";
import { fakeCreateAgent, fakeDeleteAgent, fakeSetProjectLlm, fakeSetSeats } from "./fakeServer";

const names = (events: Array<{ event: string }>) => events.map((e) => e.event);

describe("fake server events", () => {
  it("create: joined at the requested position, then the new lineup with the whole roster", () => {
    const { agent: a, events } = fakeCreateAgent(ready(), { displayName: "Designer", duty: "畫圖", kind: "make", look: "beret", llm: null, after: "pm" });
    expect(a).toMatchObject({ id: "designer", builtin: false, llm: null, effective_llm: { provider: "mock", mock_reason: "configured_mock" } });
    expect(names(events)).toEqual(["agent:joined", "pipeline:updated"]);
    expect(events[0]).toMatchObject({ data: { position: 1, pipeline_id: "main" } });
    expect(events[1]).toMatchObject({ data: { pipeline: { seats: ["pm", "designer", "swe", "qa"] } } });
    expect((events[1] as { data: { agents: unknown[] } }).data.agents).toHaveLength(4);
  });

  it("create with null `after` seats the agent first; a Claude choice resolves against the environment", () => {
    const state = ready({ force_mock: true, llm: { ...snapshot().llm, force_mock: true } });
    const { agent: a, events } = fakeCreateAgent(state, { displayName: "Sec", duty: "查", kind: "check", look: "cap", llm: { provider: "anthropic", model: "claude-opus-5-5" }, after: null });
    expect(a.effective_llm).toMatchObject({ provider: "mock", source: "agent", mock_reason: "force_mock" });
    expect(events[1]).toMatchObject({ data: { pipeline: { seats: ["sec", "pm", "swe", "qa"] } } });
  });

  it("setSeats: left for removed, joined for new, then the lineup", () => {
    const withBench = ready({ agents: [...snapshot().agents, agent("writer", { builtin: false })], pipelines: [{ id: "main", name: "主線", seats: ["pm", "swe", "qa"], pending_seats: null }] });
    expect(names(fakeSetSeats(withBench, ["pm", "qa"]))).toEqual(["agent:left", "pipeline:updated"]);
    expect(names(fakeSetSeats(withBench, ["pm", "swe", "qa", "writer"]))).toEqual(["agent:joined", "pipeline:updated"]);
  });

  it("delete: a seated agent leaves first; the roster no longer lists it", () => {
    const events = fakeDeleteAgent(ready(), "swe");
    expect(names(events)).toEqual(["agent:left", "pipeline:updated"]);
    const data = (events[1] as { data: { agents: Array<{ id: string }>; pipeline: { seats: string[] } } }).data;
    expect(data.agents.map((x) => x.id)).toEqual(["pm", "qa"]);
    expect(data.pipeline.seats).toEqual(["pm", "qa"]);
  });

  it("project llm: every agent's effective model is recomputed, own choices keep winning", () => {
    const own = agent("qa", { llm: "mock", effective_llm: mockEff() });
    const state = ready({ agents: [agent("pm"), agent("swe"), own] });
    const [ev] = fakeSetProjectLlm(state, { provider: "anthropic", model: "claude-opus-5-5" });
    const list = (ev as { data: { agents: Array<{ id: string; effective_llm: { provider: string; model: string } }> } }).data.agents;
    expect(list.find((a) => a.id === "pm")!.effective_llm).toMatchObject({ provider: "anthropic", model: "claude-opus-5-5" });
    expect(list.find((a) => a.id === "qa")!.effective_llm.provider).toBe("mock");
  });
});
