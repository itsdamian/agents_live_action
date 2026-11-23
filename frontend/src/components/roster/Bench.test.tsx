import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { agent, ready, snapshot } from "../../test/factories";
import { fakeReplay, renderWith, stubApi } from "../../test/render";
import { Bench } from "./Bench";

const benchState = (extra: Parameters<typeof ready>[0] = {}) =>
  ready({
    agents: [...snapshot().agents, agent("writer", { builtin: false, display_name: "寫手", duty: "寫文件" }), agent("sec", { builtin: false, kind: "check", display_name: "資安" })],
    pipelines: [{ id: "main", name: "主線", seats: ["pm", "swe", "qa"], pending_seats: null }],
    ...extra,
  });

describe("Bench", () => {
  it("renders nothing when everyone sits", () => {
    const { container } = renderWith(<Bench />, ready(), {});
    expect(container.querySelector("section")).toBeNull();
  });

  it("lists agents without a seat, with their model badge", () => {
    renderWith(<Bench />, benchState());
    expect(screen.getByRole("region", { name: "板凳" })).toBeInTheDocument();
    expect(screen.getByText("寫手")).toBeInTheDocument();
    expect(screen.getByText("寫文件")).toBeInTheDocument();
    expect(screen.getAllByText("Mock").length).toBeGreaterThanOrEqual(2);
  });

  it("回座 puts the agent at the end of the main lineup (PUT /pipelines/main)", async () => {
    const api = stubApi({ putPipeline: vi.fn().mockResolvedValue({ pipeline: {}, effective: "now" }) });
    renderWith(<Bench />, benchState(), { api, projectId: "demo" });
    const row = screen.getByText("寫手").closest("li")!;
    await userEvent.click(row.querySelector("button")!);
    expect(api.putPipeline).toHaveBeenCalledWith("demo", "main", { seats: ["pm", "swe", "qa", "writer"] });
    expect(await screen.findByText("寫手 回座了。")).toBeInTheDocument();
  });

  it("a 202 answer is marked as 'next task'", async () => {
    const api = stubApi({ putPipeline: vi.fn().mockResolvedValue({ pipeline: {}, effective: "next_task" }) });
    renderWith(<Bench />, benchState(), { api, projectId: "demo" });
    await userEvent.click(screen.getByText("寫手").closest("li")!.querySelector("button")!);
    expect(await screen.findByText("編制已排好，下一張任務開始時生效。")).toBeInTheDocument();
  });

  it("refuses a check agent when it would leave no valid lineup (local rule)", async () => {
    const api = stubApi();
    const s = ready({
      agents: [agent("sec", { kind: "check", builtin: false, display_name: "資安" }), agent("pm")],
      pipelines: [{ id: "main", name: "主線", seats: [], pending_seats: null }],
    });
    renderWith(<Bench />, s, { api, projectId: "demo" });
    await userEvent.click(screen.getByText("資安").closest("li")!.querySelector("button")!);
    expect(await screen.findByText(/至少要有一位「做事」/)).toBeInTheDocument();
    expect(api.putPipeline).not.toHaveBeenCalled();
  });

  it("disables 回座 when the line already has 8 seats", () => {
    const eight = ["pm", "swe", "qa", "a1", "a2", "a3", "a4", "a5"];
    const s = ready({
      agents: [...snapshot().agents, ...eight.slice(3).map((id) => agent(id, { builtin: false })), agent("writer", { builtin: false, display_name: "寫手" })],
      pipelines: [{ id: "main", name: "主線", seats: eight, pending_seats: null }],
    });
    renderWith(<Bench />, s);
    expect(screen.getByText("寫手").closest("li")!.querySelector("button")).toBeDisabled();
  });

  it("delete needs an inline confirmation, then calls DELETE /agents/{id}", async () => {
    const api = stubApi({ deleteAgent: vi.fn().mockResolvedValue({ agent_id: "writer", effective: "now" }) });
    renderWith(<Bench />, benchState(), { api, projectId: "demo" });
    await userEvent.click(screen.getByRole("button", { name: "刪除 寫手" }));
    expect(api.deleteAgent).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "不刪了" }));
    expect(screen.getByRole("button", { name: "刪除 寫手" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "刪除 寫手" }));
    await userEvent.click(screen.getByRole("button", { name: "確定刪除" }));
    expect(api.deleteAgent).toHaveBeenCalledWith("demo", "writer");
    expect(await screen.findByText("寫手 已刪除。")).toBeInTheDocument();
  });

  it("built-in agents on the bench can sit down again but cannot be deleted", () => {
    const s = ready({ agents: snapshot().agents, pipelines: [{ id: "main", name: "主線", seats: ["pm", "qa"], pending_seats: null }] });
    renderWith(<Bench />, s);
    const row = screen.getByText("SWE").closest("li")!;
    expect(row.querySelectorAll("button")).toHaveLength(1);
  });

  it("replay: 回座 and delete play events instead of calling the API", async () => {
    const api = stubApi();
    const r = fakeReplay();
    renderWith(<Bench />, benchState(), { api, mode: "replay", replay: r.handle });
    await userEvent.click(screen.getByText("寫手").closest("li")!.querySelector("button")!);
    expect(r.emitted.map((e) => e.event)).toEqual(["agent:joined", "pipeline:updated"]);
    await userEvent.click(screen.getByRole("button", { name: "刪除 資安" }));
    await userEvent.click(screen.getByRole("button", { name: "確定刪除" }));
    expect(r.emitted.at(-1)).toMatchObject({ event: "pipeline:updated" });
    expect(api.putPipeline).not.toHaveBeenCalled();
    expect(api.deleteAgent).not.toHaveBeenCalled();
  });
});
