import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiRequestError } from "../../net/api";
import { apply, ready, snapshot } from "../../test/factories";
import { fakeReplay, renderWith, stubApi } from "../../test/render";
import { factoryReducer } from "../../state/reducer";
import type { ProjectInfo } from "../../types/api";
import { Header, connectionLabel } from "./Header";

const project = (id: string): ProjectInfo => ({ id, name: id, path: `/p/${id}`, agents: [], task_count: 0, active_task_count: 0, status: "idle" });
const models = [
  { id: "claude-opus-5-5", label: "Claude Opus 5.5" },
  { id: "claude-sonnet-5-5", label: "Claude Sonnet 5.5" },
];
const withLlm = (over: Partial<ReturnType<typeof snapshot>["llm"]> = {}, snap: Partial<ReturnType<typeof snapshot>> = {}) =>
  ready({ llm: { project: "mock", force_mock: false, anthropic_key_present: true, models, ...over }, ...snap });

describe("project select", () => {
  it("lists the projects and switches the project on change", async () => {
    const selectProject = vi.fn();
    renderWith(<Header />, ready(), { projects: [project("alpha"), project("beta")], projectId: "alpha", selectProject });
    const select = screen.getByRole("combobox", { name: "專案" });
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["alpha", "beta"]);
    await userEvent.selectOptions(select, "beta");
    expect(selectProject).toHaveBeenCalledWith("beta");
  });

  it("replay shows the project as plain text", () => {
    renderWith(<Header />, ready(), { mode: "replay" });
    expect(screen.queryByRole("combobox", { name: "專案" })).toBeNull();
    expect(screen.getByTestId("project-name")).toHaveTextContent("demo");
  });
});

describe("[MOCK MODE] and its reason", () => {
  it("is hidden when the server runs real models", () => {
    renderWith(<Header />, ready({ mock_mode: false }));
    expect(screen.queryByTestId("mock-indicator")).toBeNull();
  });
  it("plain [MOCK MODE] without a reason when Mock was simply chosen", () => {
    renderWith(<Header />, ready({ mock_mode: true }));
    expect(screen.getByTestId("mock-indicator")).toHaveTextContent("[MOCK MODE]");
    expect(screen.getByTestId("mock-indicator")).not.toHaveTextContent("（");
  });
  it("names FORCE_MOCK", () => {
    renderWith(<Header />, withLlm({ force_mock: true }, { mock_mode: true, force_mock: true }));
    expect(screen.getByTestId("mock-indicator")).toHaveTextContent("[MOCK MODE]（FORCE_MOCK 強制）");
  });
  it("names a missing API key", () => {
    renderWith(<Header />, withLlm({ anthropic_key_present: false }, { mock_mode: true }));
    expect(screen.getByTestId("mock-indicator")).toHaveTextContent("[MOCK MODE]（沒有 API 金鑰）");
  });
});

describe("connection status", () => {
  it("labels each state in Chinese", () => {
    expect(connectionLabel("connecting", 0, null, null, "live")).toBe("連線中…");
    expect(connectionLabel("open", 0, null, null, "live")).toBe("已連線");
    expect(connectionLabel("open", 0, null, null, "replay")).toBe("回放中");
    expect(connectionLabel("reconnecting", 2, 5000, null, "live")).toBe("重新連線中…（第 2 次，約 5 秒後）");
    expect(connectionLabel("closed", 0, null, null, "live")).toBe("已中斷");
    expect(connectionLabel("error", 0, null, "Unknown project", "live")).toBe("連線失敗：Unknown project");
  });
  it("follows the store", () => {
    const s = factoryReducer(ready(), { type: "connection", status: "reconnecting", attempt: 1, delay: 2000 });
    renderWith(<Header />, s);
    expect(screen.getByTestId("connection")).toHaveTextContent("重新連線中…（第 1 次，約 2 秒後）");
    expect(screen.getByTestId("connection")).toHaveAttribute("data-status", "reconnecting");
  });
});

describe("model menu (D21)", () => {
  it("offers Mock plus exactly the models in snapshot.llm.models", () => {
    renderWith(<Header />, withLlm());
    const options = within(screen.getByRole("combobox", { name: "模型" })).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Mock", "Claude Opus 5.5", "Claude Sonnet 5.5"]);
  });

  it("shows no model menu before the snapshot arrived", () => {
    renderWith(<Header />, factoryReducer(ready(), { type: "reset", projectId: "demo" }));
    expect(screen.queryByRole("combobox", { name: "模型" })).toBeNull();
  });

  it("selects the current project model", () => {
    renderWith(<Header />, withLlm({ project: { provider: "anthropic", model: "claude-sonnet-5-5" } }));
    expect(screen.getByRole("combobox", { name: "模型" })).toHaveValue("anthropic:claude-sonnet-5-5");
  });

  it("choosing Claude asks first: 會產生費用 with the budget limits from the snapshot", async () => {
    const api = stubApi({ putSettings: vi.fn().mockResolvedValue({ llm: {}, effective: "now" }) });
    renderWith(<Header />, withLlm(), { api, projectId: "demo" });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "模型" }), "Claude Opus 5.5");
    const dialog = screen.getByRole("alertdialog", { name: "切換到 Claude Opus 5.5？" });
    expect(dialog).toHaveTextContent("會產生費用，目前預算上限：每日 $5.00、專案 $20.00。");
    expect(api.putSettings).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "取消" })).toHaveFocus();
  });

  it("confirming calls PUT /settings with the chosen model", async () => {
    const api = stubApi({ putSettings: vi.fn().mockResolvedValue({ llm: {}, effective: "now" }) });
    renderWith(<Header />, withLlm(), { api, projectId: "demo" });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "模型" }), "Claude Sonnet 5.5");
    await userEvent.click(screen.getByRole("button", { name: "確定切換" }));
    expect(api.putSettings).toHaveBeenCalledWith("demo", { llm: { provider: "anthropic", model: "claude-sonnet-5-5" } });
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(screen.queryByTestId("llm-pending")).toBeNull();
  });

  it("cancel and Escape close the dialog without calling the API", async () => {
    const api = stubApi();
    renderWith(<Header />, withLlm(), { api, projectId: "demo" });
    const select = screen.getByRole("combobox", { name: "模型" });
    await userEvent.selectOptions(select, "Claude Opus 5.5");
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    await userEvent.selectOptions(select, "Claude Opus 5.5");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(api.putSettings).not.toHaveBeenCalled();
  });

  it("switching to Mock needs no confirmation", async () => {
    const api = stubApi({ putSettings: vi.fn().mockResolvedValue({ llm: "mock", effective: "now" }) });
    renderWith(<Header />, withLlm({ project: { provider: "anthropic", model: "claude-opus-5-5" } }), { api, projectId: "demo" });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "模型" }), "Mock");
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(api.putSettings).toHaveBeenCalledWith("demo", { llm: "mock" });
  });

  it("a 202 answer marks 下一張開始, which ends when the next task has finished", async () => {
    const api = stubApi({ putSettings: vi.fn().mockResolvedValue({ llm: {}, effective: "next_task" }) });
    const s = withLlm();
    const { update } = renderWith(<Header />, s, { api, projectId: "demo" });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "模型" }), "Claude Opus 5.5");
    await userEvent.click(screen.getByRole("button", { name: "確定切換" }));
    expect(await screen.findByTestId("llm-pending")).toHaveTextContent("下一張開始");
    expect(await screen.findByText("模型已換，下一張任務開始時生效。")).toBeInTheDocument();
    await act(async () => {
      update(apply(s, "task:completed", { task: { id: "t1", title: "x", priority: "high", status: "done", rework_count: 0 }, done_count: 1, deliverables: [] }));
    });
    expect(screen.queryByTestId("llm-pending")).toBeNull();
  });

  it("with only one limit set, the dialog lists just that one", async () => {
    const s = withLlm({}, { usage: { ...snapshot().usage, daily_budget_usd: null, project_budget_usd: 20 } });
    renderWith(<Header />, s, { api: stubApi(), projectId: "demo" });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "模型" }), "Claude Opus 5.5");
    expect(screen.getByRole("alertdialog")).toHaveTextContent("會產生費用，目前預算上限：專案 $20.00。");
  });

  it("without any budget limit the dialog suggests setting PROJECT_BUDGET_USD", async () => {
    const s = withLlm({}, { usage: { ...snapshot().usage, daily_budget_usd: null, project_budget_usd: null } });
    renderWith(<Header />, s, { api: stubApi(), projectId: "demo" });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "模型" }), "Claude Opus 5.5");
    expect(screen.getByRole("alertdialog")).toHaveTextContent("會產生費用。目前沒有設定預算上限，建議在後端設定 PROJECT_BUDGET_USD。");
  });

  it("warns that FORCE_MOCK / a missing key keep Mock running, next to the menu and in the dialog", async () => {
    const forced = withLlm({ force_mock: true, project: { provider: "anthropic", model: "claude-opus-5-5" } }, { force_mock: true });
    const { unmount } = renderWith(<Header />, forced, { api: stubApi(), projectId: "demo" });
    expect(screen.getByTestId("llm-mock-note")).toHaveTextContent("實際以 Mock 執行（伺服器強制 Mock）");
    unmount();
    renderWith(<Header />, withLlm({ anthropic_key_present: false }), { api: stubApi(), projectId: "demo" });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "模型" }), "Claude Opus 5.5");
    expect(screen.getByRole("alertdialog")).toHaveTextContent("伺服器目前會改用 Mock（找不到 API 金鑰），實際不會呼叫 Claude");
  });

  it("shows the server's message when PUT /settings fails (e.g. 409 in file mode)", async () => {
    const api = stubApi({ putSettings: vi.fn().mockRejectedValue(new ApiRequestError(409, "需要 langgraph＋db")) });
    renderWith(<Header />, withLlm(), { api, projectId: "demo" });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "模型" }), "Claude Opus 5.5");
    await userEvent.click(screen.getByRole("button", { name: "確定切換" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("需要 langgraph＋db");
  });

  it("replay: plays llm:updated instead of calling the API", async () => {
    const api = stubApi();
    const r = fakeReplay();
    renderWith(<Header />, withLlm(), { api, mode: "replay", replay: r.handle });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "模型" }), "Claude Opus 5.5");
    await userEvent.click(screen.getByRole("button", { name: "確定切換" }));
    expect(api.putSettings).not.toHaveBeenCalled();
    expect(r.emitted).toHaveLength(1);
    expect(r.emitted[0]).toMatchObject({ event: "llm:updated", data: { project_llm: { provider: "anthropic", model: "claude-opus-5-5" } } });
  });
});
