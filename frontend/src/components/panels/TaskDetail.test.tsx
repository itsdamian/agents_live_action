import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiRequestError } from "../../net/api";
import type { TaskDetailResponse } from "../../types/api";
import { agent, apply, ready, record, summary } from "../../test/factories";
import { renderWith, stubApi } from "../../test/render";
import { TaskDetail } from "./TaskDetail";

const seats = [
  { id: "pm", display_name: "PM", look: "glasses" as const, kind: "make" as const },
  { id: "designer", display_name: "設計師", look: "beret" as const, kind: "make" as const },
];

function detail(over: Partial<TaskDetailResponse> = {}): TaskDetailResponse {
  return {
    task: { ...record("task-004", "in_progress", 1), title: "加登入頁", description: "要有記住我", approval: "after_qa", priority: "high" },
    status_history: [{ agent: "PM_agent", status: "success", timestamp: "2026-11-17T09:00:03.5" }],
    logs: [{ name: "pm_001.log", agent: "PM_agent", timestamp: "2026-11-17T09:00:03", status: "success", execution_time: 2.4, size: 1536, content: "<script>alert(1)</script>\nline 2", content_url: "/api/projects/demo/tasks/task-004/logs/pm_001.log" }],
    deliverables: [{ name: "PM_agent_01.md", size: 2048, download_url: "/api/projects/demo/tasks/task-004/deliverables/PM_agent_01.md" }],
    usage: { input_tokens: 1200, output_tokens: 800, cost: 0.0123, call_count: 3 },
    runs: [
      {
        run_no: 2, pipeline_id: "main", outcome: "failed", reason: "QA still failing after 3 reworks", rework_count: 3, started_at: "2026-11-17T09:00:00", finished_at: "2026-11-17T09:04:00",
        cost_usd: 0.05, input_tokens: 100, output_tokens: 50, imported: false, seats,
        steps: [
          { seq: 1, kind: "station", station: "pm", status: "done", note: null, at: "2026-11-17T09:00:05" },
          { seq: 2, kind: "gate", station: "designer", status: "rejected", note: "再改一下", at: "2026-11-17T09:01:00" },
          { seq: 3, kind: "rework", station: "ghost", status: "rework", note: null, at: "2026-11-17T09:02:00" },
        ],
      },
      { run_no: 1, pipeline_id: null, outcome: null, reason: null, rework_count: 0, started_at: "2026-11-16T09:00:00", finished_at: null, cost_usd: 0, input_tokens: 0, output_tokens: 0, imported: true, seats, steps: [] },
    ],
    agent_outputs: [
      { station: "pm", display_name: "PM", content: "## 規格\n- 登入頁", truncated: false },
      { station: "designer", display_name: "設計師", content: "x".repeat(50), truncated: true },
    ],
    ...over,
  };
}

const live = (d: TaskDetailResponse = detail()) => {
  const api = stubApi({ taskDetail: vi.fn().mockResolvedValue(d) });
  return { api, mount: (state = apply(ready(), "station:update", { station: "pm", status: "working", task: summary("task-004") })) => renderWith(<TaskDetail taskId="task-004" />, state, { api, projectId: "demo" }) };
};
const tab = (name: string) => screen.getByRole("tab", { name });

describe("TaskDetail tabs", () => {
  it("loads the detail from the API for the project and task", async () => {
    const { api, mount } = live();
    mount();
    expect(await screen.findByRole("heading", { name: "加登入頁" })).toBeInTheDocument();
    expect(api.taskDetail).toHaveBeenCalledWith("demo", "task-004");
  });

  it("has the five tabs, the first selected", async () => {
    live().mount();
    await screen.findByRole("heading", { name: "加登入頁" });
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["概況", "日誌", "交付檔", "各 agent 產出", "執行紀錄"]);
    expect(tab("概況")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveAccessibleName("概況");
  });

  it("概況 shows status, priority, approval timing, reworks, usage and description", async () => {
    live().mount();
    await screen.findByRole("heading", { name: "加登入頁" });
    const panel = screen.getByRole("tabpanel");
    expect(within(panel).getByTestId("detail-status")).toHaveTextContent("進行中");
    expect(panel).toHaveTextContent("高");
    expect(panel).toHaveTextContent("全部通過後給我看");
    expect(panel).toHaveTextContent("1200 輸入 / 800 輸出 tokens，$0.01（3 次呼叫）");
    expect(panel).toHaveTextContent("要有記住我");
    expect(panel).toHaveTextContent("2026-11-17 09:00:03 PM_agent success");
  });

  it("日誌 shows each log as plain text (markup is not interpreted) with a link to the raw file", async () => {
    live().mount();
    await screen.findByRole("heading", { name: "加登入頁" });
    await userEvent.click(tab("日誌"));
    const panel = screen.getByRole("tabpanel");
    expect(panel.querySelector("script")).toBeNull();
    expect(panel).toHaveTextContent("<script>alert(1)</script>");
    expect(within(panel).getByRole("link", { name: "原始檔" })).toHaveAttribute("href", "/api/projects/demo/tasks/task-004/logs/pm_001.log");
    expect(panel).toHaveTextContent("1.5 KB");
  });

  it("狀態紀錄 shows the display name from the server, falling back to the log name when it is absent (Task 26a)", async () => {
    live(detail({
      status_history: [
        { agent: "security", status: "success", timestamp: "2026-11-17T09:01:00", display_name: "資安把關" },
        { agent: "PM_agent", status: "success", timestamp: "2026-11-17T09:00:03.5" },
      ],
    })).mount();
    await screen.findByRole("heading", { name: "加登入頁" });
    const items = screen.getByRole("tabpanel").querySelectorAll("ol li");
    expect(items[0]).toHaveTextContent("2026-11-17 09:01:00 資安把關 success");
    expect(items[0]).not.toHaveTextContent("security");
    expect(items[1]).toHaveTextContent("PM_agent success");
  });

  it("日誌 names each log's agent from the roster (built-in log names map to pm/swe/qa), else shows the name as is (Task 26a)", async () => {
    const log = detail().logs[0]!;
    const logs = [
      { ...log, name: "pm_001.log", agent: "PM_agent" },
      { ...log, name: "security_001.log", agent: "security" },
      { ...log, name: "ghost_001.log", agent: "ghost" },
    ];
    const state = ready({ agents: [agent("pm", { display_name: "產品經理" }), agent("swe"), agent("qa"), agent("security", { display_name: "資安把關", kind: "check" })] });
    live(detail({ logs })).mount(state);
    await screen.findByRole("heading", { name: "加登入頁" });
    await userEvent.click(tab("日誌"));
    const names = [...screen.getByRole("tabpanel").querySelectorAll("b")].map((b) => b.textContent);
    expect(names).toEqual(["產品經理", "資安把關", "ghost"]);
  });

  it("日誌 prefers the display_name the API gives each log (a deleted agent keeps its name), else the roster (Task 26a)", async () => {
    const log = detail().logs[0]!;
    const logs = [
      { ...log, name: "pm_001.log", agent: "PM_agent" },
      { ...log, name: "maker_001.log", agent: "maker", display_name: "打樣師" },
    ];
    const state = ready({ agents: [agent("pm", { display_name: "產品經理" }), agent("swe"), agent("qa")] });
    live(detail({ logs })).mount(state);
    await screen.findByRole("heading", { name: "加登入頁" });
    await userEvent.click(tab("日誌"));
    const names = [...screen.getByRole("tabpanel").querySelectorAll("b")].map((b) => b.textContent);
    expect(names).toEqual(["產品經理", "打樣師"]);
  });

  it("交付檔 lists downloads", async () => {
    live().mount();
    await screen.findByRole("heading", { name: "加登入頁" });
    await userEvent.click(tab("交付檔"));
    const link = screen.getByRole("link", { name: "PM_agent_01.md" });
    expect(link).toHaveAttribute("href", "/api/projects/demo/tasks/task-004/deliverables/PM_agent_01.md");
    expect(link).toHaveAttribute("download", "PM_agent_01.md");
  });

  it("各 agent 產出 shows each agent's text and says when it was cut off", async () => {
    live().mount();
    await screen.findByRole("heading", { name: "加登入頁" });
    await userEvent.click(tab("各 agent 產出"));
    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveTextContent("## 規格");
    expect(panel).toHaveTextContent("設計師");
    expect(panel.querySelectorAll("li")).toHaveLength(2);
    expect(within(panel).getAllByText("內容太長，只顯示前 20,000 字。")).toHaveLength(1);
  });

  it("執行紀錄 shows runs newest first with outcome, reason, seats and steps (names from the run's seat snapshot)", async () => {
    live().mount();
    await screen.findByRole("heading", { name: "加登入頁" });
    await userEvent.click(tab("執行紀錄"));
    const runs = document.querySelectorAll("[data-run]");
    expect([...runs].map((r) => r.getAttribute("data-run"))).toEqual(["2", "1"]);
    expect(runs[0]).toHaveTextContent("失敗");
    expect(runs[0]).toHaveTextContent("QA still failing after 3 reworks");
    expect(runs[0]).toHaveTextContent("座位：PM → 設計師");
    expect(runs[0]).toHaveTextContent("2. 閘門 設計師 rejected 再改一下");
    expect(runs[0]).toHaveTextContent("3. 重做 ghost"); // unknown station falls back to its id
    expect(runs[1]).toHaveTextContent("進行中");
    expect(runs[1]).toHaveTextContent("（匯入的歷史）");
  });

  it("empty tabs explain themselves", async () => {
    live(detail({ logs: [], deliverables: [], agent_outputs: [], runs: [] })).mount();
    await screen.findByRole("heading", { name: "加登入頁" });
    for (const [name, text] of [["日誌", "還沒有日誌。"], ["交付檔", "還沒有交付檔。"], ["各 agent 產出", "還沒有產出。"], ["執行紀錄", "沒有執行紀錄。"]]) {
      await userEvent.click(tab(name!));
      expect(screen.getByText(text!)).toBeInTheDocument();
    }
  });

  it("arrow keys move between tabs", async () => {
    live().mount();
    await screen.findByRole("heading", { name: "加登入頁" });
    tab("概況").focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(tab("日誌")).toHaveFocus();
    expect(tab("日誌")).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{End}");
    expect(tab("執行紀錄")).toHaveFocus();
    await userEvent.keyboard("{ArrowRight}");
    expect(tab("概況")).toHaveFocus();
    await userEvent.keyboard("{ArrowLeft}");
    expect(tab("執行紀錄")).toHaveFocus();
  });
});

describe("TaskDetail: approval and refresh", () => {
  it("shows the approval controls when the task waits at a gate, and they call the API", async () => {
    const { api, mount } = live();
    (api.approve as ReturnType<typeof vi.fn>).mockResolvedValue({ task_id: "task-004", decision: "approved" });
    const s = apply(ready(), "task:awaiting_approval", { task: summary("task-004", "awaiting_approval"), station: "qa", approval: "after_qa" });
    mount(s);
    await screen.findByRole("heading", { name: "加登入頁" });
    expect(screen.getByTestId("detail-status")).toHaveTextContent("等你核准");
    await userEvent.click(within(screen.getByRole("tabpanel")).getByRole("button", { name: "可以" }));
    expect(api.approve).toHaveBeenCalledWith("demo", "task-004");
  });

  it("no approval controls without a gate", async () => {
    live().mount();
    await screen.findByRole("heading", { name: "加登入頁" });
    expect(screen.queryByRole("button", { name: "可以" })).toBeNull();
  });

  it("reloads when the task's status changes", async () => {
    const { api, mount } = live();
    const s = apply(ready(), "station:update", { station: "pm", status: "working", task: summary("task-004") });
    const { update } = mount(s);
    await screen.findByRole("heading", { name: "加登入頁" });
    expect(api.taskDetail).toHaveBeenCalledTimes(1);
    await act(async () => { update(apply(s, "task:completed", { task: summary("task-004", "done"), done_count: 1, deliverables: [] })); });
    expect(api.taskDetail).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("完成")).toBeInTheDocument();
  });

  it("shows the server's error with a retry button", async () => {
    const taskDetail = vi.fn().mockRejectedValueOnce(new ApiRequestError(404, "Unknown task: task-004")).mockResolvedValue(detail());
    const api = stubApi({ taskDetail });
    renderWith(<TaskDetail taskId="task-004" />, ready(), { api, projectId: "demo" });
    expect(await screen.findByRole("alert")).toHaveTextContent("Unknown task: task-004");
    await userEvent.click(screen.getByRole("button", { name: "重試" }));
    expect(await screen.findByRole("heading", { name: "加登入頁" })).toBeInTheDocument();
  });
});

describe("TaskDetail (replay)", () => {
  it("builds a summary from the store and never calls the API", async () => {
    const api = stubApi();
    const s = apply(ready(), "station:update", { station: "pm", status: "working", task: summary("task-004") });
    renderWith(<TaskDetail taskId="task-004" />, s, { api, mode: "replay" });
    expect(await screen.findByRole("heading", { name: "加登入頁" })).toBeInTheDocument();
    expect(screen.getByText("回放模式沒有後端，這裡只顯示摘要。")).toBeInTheDocument();
    expect(screen.getByTestId("detail-status")).toHaveTextContent("進行中");
    expect(api.taskDetail).not.toHaveBeenCalled();
  });

  it("an unknown task says so", async () => {
    renderWith(<TaskDetail taskId="nope" />, ready(), { api: stubApi(), mode: "replay" });
    expect(await screen.findByText("找不到這張任務。")).toBeInTheDocument();
  });
});
