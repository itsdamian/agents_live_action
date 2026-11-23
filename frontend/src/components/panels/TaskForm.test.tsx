import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiRequestError } from "../../net/api";
import { ready } from "../../test/factories";
import { fakeReplay, renderWith, stubApi } from "../../test/render";
import { PanelsProvider } from "./PanelsContext";
import { TaskForm } from "./TaskForm";

const mount = (env: Parameters<typeof renderWith>[2], state = ready()) =>
  renderWith(<PanelsProvider initial={{ kind: "task-form" }}><TaskForm /></PanelsProvider>, state, env);
const submit = () => userEvent.click(screen.getByRole("button", { name: "派出任務" }));
const created = (id = "task-009") => ({ task: { id, title: "x", priority: "medium", status: "pending", rework_count: 0 } });

describe("TaskForm: 核准時機 (AC11b)", () => {
  it("offers exactly 不用 / 做完後先給我看 / 全部通過後給我看, with 不用 selected", () => {
    mount({ api: stubApi(), projectId: "demo" });
    const group = screen.getByRole("group", { name: "核准時機" });
    const radios = within(group).getAllByRole("radio");
    expect(radios.map((r) => (r.parentElement as HTMLElement).textContent?.trim())).toEqual(["不用", "做完後先給我看", "全部通過後給我看"]);
    expect(screen.getByRole("radio", { name: "不用" })).toBeChecked();
  });

  it.each([
    ["不用", "none"],
    ["做完後先給我看", "after_swe"],
    ["全部通過後給我看", "after_qa"],
  ])("choosing %s sends approval=%s", async (label, value) => {
    const api = stubApi({ createTask: vi.fn().mockResolvedValue(created()) });
    mount({ api, projectId: "demo" });
    await userEvent.type(screen.getByLabelText("標題"), "加登入頁");
    await userEvent.click(screen.getByRole("radio", { name: label }));
    await submit();
    expect(api.createTask).toHaveBeenCalledWith("demo", expect.objectContaining({ title: "加登入頁", approval: value }));
  });
});

describe("TaskForm: submit", () => {
  it("sends title, description, priority and approval together", async () => {
    const api = stubApi({ createTask: vi.fn().mockResolvedValue(created()) });
    mount({ api, projectId: "demo" });
    await userEvent.type(screen.getByLabelText("標題"), "  修購物車金額 ");
    await userEvent.type(screen.getByLabelText("描述（選填）"), "小數點後兩位");
    await userEvent.selectOptions(screen.getByLabelText("優先序"), "高");
    await submit();
    expect(api.createTask).toHaveBeenCalledWith("demo", { title: "修購物車金額", description: "小數點後兩位", priority: "high", approval: "none" });
  });

  it("needs a title and does not call the API without one", async () => {
    const api = stubApi();
    mount({ api, projectId: "demo" });
    await submit();
    expect(screen.getByRole("alert")).toHaveTextContent("標題要填。");
    expect(screen.getByLabelText("標題")).toHaveFocus();
    expect(api.createTask).not.toHaveBeenCalled();
  });

  it("limits the title to 200 characters", async () => {
    const api = stubApi();
    mount({ api, projectId: "demo" });
    await userEvent.click(screen.getByLabelText("標題"));
    await userEvent.paste("字".repeat(201));
    await submit();
    expect(screen.getByRole("alert")).toHaveTextContent("標題最多 200 個字。");
  });

  it("announces the new task with a 看詳情 action", async () => {
    const api = stubApi({ createTask: vi.fn().mockResolvedValue(created("task-012")) });
    mount({ api, projectId: "demo" });
    await userEvent.type(screen.getByLabelText("標題"), "寄歡迎信");
    await submit();
    expect(await screen.findByText("任務「寄歡迎信」已派出。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "看詳情" })).toBeInTheDocument();
  });

  it("shows the server's message when it refuses", async () => {
    const api = stubApi({ createTask: vi.fn().mockRejectedValue(new ApiRequestError(422, "title: String should have at least 1 character")) });
    mount({ api, projectId: "demo" });
    await userEvent.type(screen.getByLabelText("標題"), "x");
    await submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("String should have at least 1 character");
  });

  it("replay: plays a task:created event instead of calling the API", async () => {
    const api = stubApi();
    const r = fakeReplay();
    mount({ api, mode: "replay", replay: r.handle });
    await userEvent.type(screen.getByLabelText("標題"), "新任務");
    await submit();
    expect(api.createTask).not.toHaveBeenCalled();
    expect(r.emitted).toHaveLength(1);
    expect(r.emitted[0]).toMatchObject({ event: "task:created", data: { task: { title: "新任務", status: "pending", priority: "medium" }, queue_size: 1 } });
  });
});
