import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FactoryProvider } from "./state/FactoryProvider";
import { apply, ready, summary } from "./test/factories";
import { renderWith, stubApi } from "./test/render";
import App from "./App";

describe("App", () => {
  it("renders the page heading", () => {
    // replay with an unknown file keeps the test off the network and the backend
    render(
      <FactoryProvider search="?replay=none" fetch={async () => new Response("", { status: 404 })}>
        <App />
      </FactoryProvider>,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Agent 工廠現場" })).toBeInTheDocument();
  });

  it("新增 agent toggles the form panel", async () => {
    renderWith(<App />, ready(), { api: stubApi(), projectId: "demo" });
    const button = screen.getByRole("button", { name: "新增 agent" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("form", { name: "新同事報到" })).toBeInTheDocument();
    await userEvent.click(button);
    expect(screen.queryByRole("form", { name: "新同事報到" })).toBeNull();
  });

  it("the × on a desk sends the lineup without that agent (PUT /pipelines/main)", async () => {
    const api = stubApi({ putPipeline: vi.fn().mockResolvedValue({ pipeline: {}, effective: "now" }) });
    renderWith(<App />, ready(), { api, projectId: "demo" });
    await userEvent.click(screen.getByRole("button", { name: "請 SWE 離開" }));
    expect(api.putPipeline).toHaveBeenCalledWith("demo", "main", { seats: ["pm", "qa"] });
    expect(await screen.findByText("SWE 離開座位了。")).toBeInTheDocument();
  });

  it("a busy desk's removal answers next_task: the toast says so and the desk shows 這張做完就離開 once pending", async () => {
    const api = stubApi({ putPipeline: vi.fn().mockResolvedValue({ pipeline: {}, effective: "next_task" }) });
    let s = apply(ready(), "station:update", { station: "swe", status: "working", task: summary() }, 1);
    const { update } = renderWith(<App />, s, { api, projectId: "demo" });
    await userEvent.click(screen.getByRole("button", { name: "請 SWE 離開" }));
    expect(await screen.findByText("編制已排好，下一張任務開始時生效。")).toBeInTheDocument();
    s = apply(s, "pipeline:pending", { pipeline_id: "main", seats: ["pm", "qa"], reason: "task_in_flight", task_id: "t1" }, 2);
    update(s);
    expect(screen.getByTestId("pending-notice")).toBeInTheDocument();
    expect(within(document.querySelector('[data-desk="swe"]')!).getByText("這張做完就離開")).toBeInTheDocument();
  });

  it("refuses to remove the last make seat and says why, without calling the API", async () => {
    const api = stubApi();
    const s = ready({ agents: ready().agents ? Object.values(ready().agents).filter((a) => a.id !== "pm") : [] });
    renderWith(<App />, s, { api, projectId: "demo" });
    await userEvent.click(screen.getByRole("button", { name: "請 SWE 離開" }));
    expect(await screen.findByText("至少要有一位「做事」的同事。")).toBeInTheDocument();
    expect(api.putPipeline).not.toHaveBeenCalled();
  });

  it("a server error on removal is shown as an error toast", async () => {
    const { ApiRequestError } = await import("./net/api");
    const api = stubApi({ putPipeline: vi.fn().mockRejectedValue(new ApiRequestError(409, "需要 langgraph＋db")) });
    renderWith(<App />, ready(), { api, projectId: "demo" });
    await userEvent.click(screen.getByRole("button", { name: "請 QA 離開" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("需要 langgraph＋db");
  });
});

describe("App panels (F9b)", () => {
  it("派一張任務 opens the task form and closes it again", async () => {
    renderWith(<App />, ready(), { api: stubApi(), projectId: "demo" });
    const button = screen.getByRole("button", { name: "派一張任務" });
    await userEvent.click(button);
    expect(screen.getByRole("form", { name: "派一張任務" })).toBeInTheDocument();
    expect(button).toHaveAttribute("aria-expanded", "true");
    await userEvent.click(screen.getByRole("button", { name: "關閉" }));
    expect(screen.queryByRole("form", { name: "派一張任務" })).toBeNull();
  });

  it("the in-flight counter opens the running task's detail", async () => {
    const api = stubApi({ taskDetail: vi.fn().mockResolvedValue({
      task: { id: "t1", title: "加登入頁", priority: "high", status: "in_progress", rework_count: 0, description: "", approval: "none", max_reworks: 3, created_at: "", updated_at: "", pm_output: null, swe_output: null, qa_output: null },
      status_history: [], logs: [], deliverables: [], usage: { input_tokens: 0, output_tokens: 0, cost: 0, call_count: 0 }, runs: [], agent_outputs: [],
    }) });
    renderWith(<App />, apply(ready(), "station:update", { station: "pm", status: "working", task: summary("t1") }), { api, projectId: "demo" });
    await userEvent.click(screen.getByRole("button", { name: /進行中 1/ }));
    expect(await screen.findByRole("heading", { name: "加登入頁" })).toBeInTheDocument();
    expect(api.taskDetail).toHaveBeenCalledWith("demo", "t1");
  });
});
