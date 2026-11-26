import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiRequestError, UNAVAILABLE_TEXT } from "../../net/api";
import { agent, ready, snapshot } from "../../test/factories";
import { fakeReplay, renderWith, stubApi } from "../../test/render";
import { AddAgentForm } from "./AddAgentForm";

const state = () => ready();
const label = (text: string | RegExp) => screen.getByLabelText(text);

async function fill(name: string, duty: string, prompt = "你是設計師，請畫草圖。") {
  if (name) await userEvent.type(label(/^名字/), name);
  if (duty) await userEvent.type(label(/^負責什麼/), duty);
  if (prompt) await userEvent.type(label(/^給他的指示/), prompt);
}
const submit = () => userEvent.click(screen.getByRole("button", { name: "請他上工" }));

describe("AddAgentForm validation", () => {
  it("shows a Chinese message per field and focuses the first invalid one", async () => {
    renderWith(<AddAgentForm />, state(), { api: stubApi(), projectId: "demo" });
    await submit();
    const alerts = screen.getAllByRole("alert").map((a) => a.textContent);
    expect(alerts).toEqual(["名字要填。", "負責的事要填。", "要寫下給他的指示。"]);
    expect(label(/^名字/)).toHaveFocus();
    expect(label(/^名字/)).toHaveAttribute("aria-invalid", "true");
  });

  it("rejects a duplicate name case-insensitively and an over-long duty", async () => {
    const api = stubApi();
    renderWith(<AddAgentForm />, state(), { api, projectId: "demo" });
    await fill("swe", "字".repeat(25));
    await submit();
    expect(screen.getByText("已經有一位 swe 了，換個名字。")).toBeInTheDocument();
    expect(screen.getByText("負責的事最多 24 個字。")).toBeInTheDocument();
    expect(api.createAgent).not.toHaveBeenCalled();
  });

  it("a check agent cannot be placed first", async () => {
    const api = stubApi();
    renderWith(<AddAgentForm />, state(), { api, projectId: "demo" });
    await fill("資安", "查風險");
    await userEvent.click(label("把關（可以退件）"));
    await userEvent.selectOptions(label("座位"), "排最前面");
    await submit();
    expect(screen.getByText(/把關的人不能排在第一席/)).toBeInTheDocument();
    expect(api.createAgent).not.toHaveBeenCalled();
  });

  it("explains a full line or a full roster before calling the server", async () => {
    const api = stubApi();
    const eight = ["pm", "swe", "qa", "a1", "a2", "a3", "a4", "a5"];
    const agents = [...snapshot().agents, ...eight.slice(3).map((id) => agent(id, { builtin: false }))];
    const full = ready({ agents, pipelines: [{ id: "main", name: "主線", seats: eight, pending_seats: null }] });
    renderWith(<AddAgentForm />, full, { api, projectId: "demo" });
    await fill("新人", "做事");
    await submit();
    expect(screen.getByText(/已經有 8 席/)).toBeInTheDocument();
    expect(api.createAgent).not.toHaveBeenCalled();
  });
});

describe("AddAgentForm submit (live)", () => {
  it("posts the fields, with the model choice and the seat", async () => {
    const api = stubApi({ createAgent: vi.fn().mockResolvedValue({ agent: {}, effective: "now" }) });
    const onDone = vi.fn();
    renderWith(<AddAgentForm onDone={onDone} />, state(), { api, projectId: "demo" });
    await fill("設計師", "畫介面草圖", "你是設計師。");
    await userEvent.selectOptions(label("造型"), "貝雷帽");
    await userEvent.selectOptions(label("座位"), "坐在 PM 後面");
    await submit();
    expect(api.createAgent).toHaveBeenCalledWith("demo", {
      display_name: "設計師", duty: "畫介面草圖", prompt: "你是設計師。", kind: "make", look: "beret", llm: null, after: "pm",
    });
    expect(onDone).toHaveBeenCalled();
    expect(await screen.findByText("設計師 報到了。")).toBeInTheDocument();
  });

  it("offers the models the server listed and sends the chosen one in `llm`", async () => {
    const api = stubApi({ createAgent: vi.fn().mockResolvedValue({ agent: {}, effective: "now" }) });
    renderWith(<AddAgentForm />, state(), { api, projectId: "demo" });
    const options = within(label("模型")).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["跟專案預設", "Mock", "Claude Opus 5.5"]); // exactly snapshot.llm.models
    await fill("資安", "查風險");
    await userEvent.click(label("把關（可以退件）"));
    await userEvent.selectOptions(label("模型"), "Claude Opus 5.5");
    await submit();
    expect(api.createAgent).toHaveBeenCalledWith("demo", expect.objectContaining({ kind: "check", llm: { provider: "anthropic", model: "claude-opus-5-5" } }));
  });

  it("Mock is sent as the string \"mock\"", async () => {
    const api = stubApi({ createAgent: vi.fn().mockResolvedValue({ agent: {}, effective: "now" }) });
    renderWith(<AddAgentForm />, state(), { api, projectId: "demo" });
    await fill("寫手", "寫文件");
    await userEvent.selectOptions(label("模型"), "Mock");
    await submit();
    expect(api.createAgent).toHaveBeenCalledWith("demo", expect.objectContaining({ llm: "mock" }));
  });

  it("marks a 'next_task' answer (the 202 case) instead of claiming the agent already sits", async () => {
    const api = stubApi({ createAgent: vi.fn().mockResolvedValue({ agent: {}, effective: "next_task" }) });
    renderWith(<AddAgentForm />, state(), { api, projectId: "demo" });
    await fill("寫手", "寫文件");
    await submit();
    expect(await screen.findByText("編制已排好，下一張任務開始時生效。")).toBeInTheDocument();
    expect(screen.queryByText("寫手 報到了。")).toBeNull();
  });

  it("shows the server's 422 message and its error list, and keeps the typed values", async () => {
    const createAgent = vi.fn().mockRejectedValue(new ApiRequestError(422, "資料不合格", ["prompt 太短", "id 不合法"]));
    renderWith(<AddAgentForm />, state(), { api: stubApi({ createAgent }), projectId: "demo" });
    await fill("寫手", "寫文件");
    await submit();
    expect(await screen.findByText("資料不合格")).toBeInTheDocument();
    expect(screen.getByText("prompt 太短")).toBeInTheDocument();
    expect(label(/^名字/)).toHaveValue("寫手");
  });
});

describe("AddAgentForm server answers", () => {
  it("lists the backend's `{field, message}` errors as text", async () => {
    const createAgent = vi.fn().mockRejectedValue(
      new ApiRequestError(422, "新增 agent 的內容不合格", [{ field: "after", message: "「ghost」不在這條線上" }]),
    );
    renderWith(<AddAgentForm />, state(), { api: stubApi({ createAgent }), projectId: "demo" });
    await fill("寫手", "寫文件");
    await submit();
    expect(await screen.findByText("新增 agent 的內容不合格")).toBeInTheDocument();
    expect(screen.getByText("「ghost」不在這條線上")).toBeInTheDocument();
  });

  it("503: says the project cannot take writes and how to recover", async () => {
    const createAgent = vi.fn().mockRejectedValue(new ApiRequestError(503, UNAVAILABLE_TEXT, [], "Project demo failed to initialise"));
    renderWith(<AddAgentForm />, state(), { api: stubApi({ createAgent }), projectId: "demo" });
    await fill("寫手", "寫文件");
    await submit();
    expect(await screen.findByText(UNAVAILABLE_TEXT)).toBeInTheDocument();
    expect(screen.queryByText(/failed to initialise/)).toBeNull();
  });
});

describe("AddAgentForm submit (replay)", () => {
  it("plays joined + pipeline:updated and never calls the API", async () => {
    const api = stubApi();
    const r = fakeReplay();
    renderWith(<AddAgentForm />, state(), { api, mode: "replay", replay: r.handle });
    await fill("Designer", "畫圖");
    await userEvent.selectOptions(label("座位"), "坐在 PM 後面");
    await submit();
    expect(api.createAgent).not.toHaveBeenCalled();
    expect(r.emitted.map((e) => e.event)).toEqual(["agent:joined", "pipeline:updated"]);
    expect(r.emitted[0]).toMatchObject({ data: { agent: { id: "designer", look: "beret" }, position: 1 } });
  });
});
