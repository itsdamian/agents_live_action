import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import historyJson from "../../../public/replay/history.json?raw";
import { ApiRequestError } from "../../net/api";
import type { HistoryItem, HistoryResponse } from "../../types/api";
import { agent, apply, ready, snapshot } from "../../test/factories";
import { renderWith, stubApi } from "../../test/render";
import { NoteWall, truncateReason, uniquePath } from "./NoteWall";
import { PanelsProvider, usePanels } from "./PanelsContext";

const fixture = JSON.parse(historyJson) as HistoryResponse;
const seats = [
  { id: "pm", display_name: "PM", look: "glasses" as const, kind: "make" as const },
  { id: "swe", display_name: "SWE", look: "headset" as const, kind: "make" as const },
  { id: "qa", display_name: "QA", look: "cap" as const, kind: "check" as const },
];

function item(n: number, over: Partial<HistoryItem> = {}): HistoryItem {
  return {
    task_id: `task-${String(n).padStart(3, "0")}`, title: `任務 ${n}`, priority: "medium", status: "done", pipeline_id: "main", outcome: "done", reason: null,
    rework_count: 0, path: ["pm", "swe", "qa"], cost_usd: null, started_at: "2026-11-17T09:00:00", finished_at: "2026-11-17T09:04:00", deliverable_count: 1, runs: 1, stale: false, seats, ...over,
  };
}
const counts = { pending: 10, in_progress: 1, awaiting_approval: 0, done: 14, failed: 4 };
const respond = (items: HistoryItem[], total = items.length) => ({ items, total, counts });

const mount = (api: ReturnType<typeof stubApi>, initial: "inbox" | "outbox" = "outbox", state = ready(), env = {}) =>
  renderWith(<PanelsProvider><NoteWall initial={initial} /><PanelProbe /></PanelsProvider>, state, { api, projectId: "demo", ...env });

function PanelProbe() {
  const p = usePanels();
  return <output data-testid="panel">{p.panel?.kind === "task-detail" ? `detail:${p.panel.taskId}` : (p.panel?.kind ?? "none")}</output>;
}
const lastCall = (api: ReturnType<typeof stubApi>) => (api.history as ReturnType<typeof vi.fn>).mock.calls.at(-1)!;
const chip = (name: RegExp | string) => screen.getByRole("button", { name });

describe("filters", () => {
  it("the outbox opens on done + failed, the inbox on pending", async () => {
    const api = stubApi({ history: vi.fn().mockResolvedValue(respond([item(1)])) });
    const out = mount(api, "outbox");
    await screen.findByText("任務 1");
    expect(lastCall(api)).toEqual(["demo", { status: ["done", "failed"], pipeline: undefined, limit: 50, offset: 0 }]);
    expect(chip(/^完成/)).toHaveAttribute("aria-pressed", "true");
    expect(chip(/^失敗/)).toHaveAttribute("aria-pressed", "true");
    expect(chip(/^待辦/)).toHaveAttribute("aria-pressed", "false");
    out.unmount();
    mount(api, "inbox");
    await screen.findByText("任務 1");
    expect(lastCall(api)[1]).toMatchObject({ status: ["pending"] });
  });

  it("has all five statuses as toggle chips with their counts", async () => {
    const api = stubApi({ history: vi.fn().mockResolvedValue(respond([item(1)])) });
    mount(api);
    await screen.findByText("任務 1");
    const group = screen.getByRole("group", { name: "狀態篩選" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent?.replace(/\s+/g, " "))).toEqual(["待辦 10", "進行中 1", "等你核准 0", "完成 14", "失敗 4"]);
  });

  it("toggling a chip refetches with the status list in a stable order", async () => {
    const api = stubApi({ history: vi.fn().mockResolvedValue(respond([item(1)])) });
    mount(api);
    await screen.findByText("任務 1");
    await userEvent.click(chip(/^待辦/));
    expect(lastCall(api)[1]).toMatchObject({ status: ["pending", "done", "failed"] });
    await userEvent.click(chip(/^完成/));
    expect(lastCall(api)[1]).toMatchObject({ status: ["pending", "failed"] });
  });

  it("turning the last chip off means 全部: every chip shows pressed and the query lists all five", async () => {
    const api = stubApi({ history: vi.fn().mockResolvedValue(respond([item(1)])) });
    mount(api);
    await screen.findByText("任務 1");
    await userEvent.click(chip(/^完成/));
    await userEvent.click(chip(/^失敗/));
    const group = screen.getByRole("group", { name: "狀態篩選" });
    for (const b of within(group).getAllByRole("button")) expect(b).toHaveAttribute("aria-pressed", "true");
    expect(lastCall(api)[1]).toMatchObject({ status: ["pending", "in_progress", "awaiting_approval", "done", "failed"] });
  });

  it("an empty filter result says so", async () => {
    const api = stubApi({ history: vi.fn().mockResolvedValue(respond([])) });
    mount(api);
    expect(await screen.findByText("這個篩選下沒有便條。")).toBeInTheDocument();
  });
});

describe("paging (50 per page)", () => {
  it("shows the first page, offers 載入更多, and appends the next page at offset 50", async () => {
    const first = Array.from({ length: 50 }, (_, i) => item(i + 1));
    const second = Array.from({ length: 20 }, (_, i) => item(i + 51));
    const history = vi.fn().mockResolvedValueOnce(respond(first, 70)).mockResolvedValueOnce(respond(second, 70));
    mount(stubApi({ history }));
    expect(await screen.findByText("已載入 50 / 共 70 張")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(50);
    await userEvent.click(screen.getByRole("button", { name: "載入更多" }));
    expect(await screen.findByText("已載入 70 / 共 70 張")).toBeInTheDocument();
    expect(history.mock.calls[1]![1]).toMatchObject({ limit: 50, offset: 50 });
    expect(screen.getAllByRole("listitem")).toHaveLength(70);
    expect(screen.queryByRole("button", { name: "載入更多" })).toBeNull();
  });

  it("no 載入更多 when everything fits on one page", async () => {
    mount(stubApi({ history: vi.fn().mockResolvedValue(respond([item(1), item(2)])) }));
    await screen.findByText("任務 1");
    expect(screen.queryByRole("button", { name: "載入更多" })).toBeNull();
  });

  it("a filter change starts again from the first page", async () => {
    const history = vi.fn().mockResolvedValue(respond(Array.from({ length: 50 }, (_, i) => item(i + 1)), 80));
    mount(stubApi({ history }));
    await screen.findByText("已載入 50 / 共 80 張");
    await userEvent.click(chip(/^失敗/));
    await screen.findByText(/已載入/);
    expect(history.mock.calls.at(-1)![1]).toMatchObject({ offset: 0, status: ["done"] });
  });
});

describe("cards", () => {
  it("shows title, result, reworks, cost, finish time, and a failure reason cut to 40 characters", async () => {
    const long = "字".repeat(50);
    const api = stubApi({ history: vi.fn().mockResolvedValue(respond([
      item(1, { rework_count: 2, cost_usd: 0.0123 }),
      item(2, { status: "failed", outcome: "failed", reason: long }),
    ])) });
    mount(api);
    const a = (await screen.findByText("任務 1")).closest("button")!;
    expect(a).toHaveTextContent("完成");
    expect(a).toHaveTextContent("重做 2");
    expect(a).toHaveTextContent("$0.01");
    expect(a).toHaveTextContent("2026-11-17 09:04:00");
    const b = screen.getByText("任務 2").closest("button")!;
    expect(b).toHaveAttribute("data-tone", "bad");
    expect(b).toHaveTextContent("字".repeat(40) + "…");
    expect(b).not.toHaveTextContent("字".repeat(41));
  });

  it("small avatars list each station once even when a rework repeated it", async () => {
    const api = stubApi({ history: vi.fn().mockResolvedValue(respond([item(1, { rework_count: 1, path: ["pm", "swe", "qa", "swe", "qa"] })])) });
    mount(api);
    await screen.findByText("任務 1");
    const crew = document.querySelectorAll("[data-agent]");
    expect([...crew].map((c) => c.getAttribute("data-agent"))).toEqual(["pm", "swe", "qa"]);
    expect(screen.getByLabelText("經過：PM、SWE、QA")).toBeInTheDocument();
    expect(uniquePath(["a", "b", "a", "c", "b"])).toEqual(["a", "b", "c"]);
  });

  it("an agent that was deleted since is still drawn from the seat snapshot, marked 已離開", async () => {
    const designer = { id: "designer", display_name: "設計師", look: "beret" as const, kind: "make" as const };
    const api = stubApi({ history: vi.fn().mockResolvedValue(respond([item(1, { path: ["pm", "designer", "swe"], seats: [seats[0]!, designer, seats[1]!] })])) });
    mount(api); // the roster has pm / swe / qa only
    await screen.findByText("任務 1");
    const gone = document.querySelector('[data-agent="designer"]')!;
    expect(gone).toHaveAttribute("data-gone");
    expect(gone).toHaveTextContent("設計師（已離開）");
    expect(gone.querySelector("svg")).toHaveAttribute("data-look", "beret");
    expect(document.querySelector('[data-agent="pm"]')).not.toHaveAttribute("data-gone");
  });

  it("a stale status is flagged", async () => {
    mount(stubApi({ history: vi.fn().mockResolvedValue(respond([item(1, { status: "in_progress", stale: true })])) }));
    expect(await screen.findByText("狀態可能已過期")).toBeInTheDocument();
  });

  it("clicking a card opens that task's detail", async () => {
    mount(stubApi({ history: vi.fn().mockResolvedValue(respond([item(7)])) }));
    await userEvent.click((await screen.findByText("任務 7")).closest("button")!);
    expect(screen.getByTestId("panel")).toHaveTextContent("detail:task-007");
  });

  it("truncateReason", () => {
    expect(truncateReason("短")).toBe("短");
    expect(truncateReason("a".repeat(40))).toBe("a".repeat(40));
    expect(truncateReason("a".repeat(41))).toBe("a".repeat(40) + "…");
  });
});

describe("several floors", () => {
  const twoFloors = () => ready({ pipelines: [{ id: "main", name: "功能線", seats: ["pm", "swe", "qa"], pending_seats: null }, { id: "bugs", name: "修 bug 線", seats: ["pm", "swe"], pending_seats: null }], agents: snapshot().agents });

  it("groups notes under their floor and filters by floor", async () => {
    const history = vi.fn().mockResolvedValue(respond([item(1, { pipeline_id: "main" }), item(2, { pipeline_id: "bugs" }), item(3, { pipeline_id: "main" })]));
    mount(stubApi({ history }), "outbox", twoFloors());
    await screen.findByText("任務 1");
    expect(screen.getByRole("heading", { name: "功能線" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "修 bug 線" })).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "樓層 功能線" })).getAllByRole("listitem")).toHaveLength(2);
    await userEvent.selectOptions(screen.getByLabelText("樓層"), "修 bug 線");
    expect(history.mock.calls.at(-1)![1]).toMatchObject({ pipeline: "bugs" });
  });

  it("a single floor has no floor controls", async () => {
    mount(stubApi({ history: vi.fn().mockResolvedValue(respond([item(1)])) }));
    await screen.findByText("任務 1");
    expect(screen.queryByLabelText("樓層")).toBeNull();
  });
});

describe("errors, refresh, replay", () => {
  it("shows the server's message with a retry", async () => {
    const history = vi.fn().mockRejectedValueOnce(new ApiRequestError(404, "Unknown project: demo")).mockResolvedValue(respond([item(1)]));
    mount(stubApi({ history }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Unknown project: demo");
    await userEvent.click(screen.getByRole("button", { name: "重試" }));
    expect(await screen.findByText("任務 1")).toBeInTheDocument();
  });

  it("re-fetches the first page when a task finishes, without blanking the list", async () => {
    const history = vi.fn().mockResolvedValueOnce(respond([item(1)])).mockResolvedValueOnce(respond([item(2), item(1)]));
    const s = ready();
    const { update } = mount(stubApi({ history }), "outbox", s);
    await screen.findByText("任務 1");
    update(apply(s, "task:completed", { task: { id: "task-002", title: "任務 2", priority: "medium", status: "done", rework_count: 0 }, done_count: 1, deliverables: [] }),
      <PanelsProvider><NoteWall initial="outbox" /><PanelProbe /></PanelsProvider>);
    expect(await screen.findByText("任務 2")).toBeInTheDocument();
    expect(screen.getByText("任務 1")).toBeInTheDocument();
    expect(history).toHaveBeenCalledTimes(2);
  });

  it("replay: serves the recorded history.json with the same filtering and never calls GET /history", async () => {
    const api = stubApi({ replayHistory: vi.fn().mockResolvedValue(fixture) });
    mount(api, "outbox", ready(), { mode: "replay" });
    expect(await screen.findByText("已載入 18 / 共 18 張")).toBeInTheDocument(); // 14 done + 4 failed
    expect(api.history).not.toHaveBeenCalled();
    await userEvent.click(chip(/^失敗/));
    expect(await screen.findByText("已載入 14 / 共 14 張")).toBeInTheDocument();
    expect(api.replayHistory).toHaveBeenCalledTimes(1); // fetched once, filtered locally
  });

  it("replay: notes of deleted agents (designer / security) still show their names", async () => {
    const api = stubApi({ replayHistory: vi.fn().mockResolvedValue(fixture) });
    mount(api, "outbox", ready({ agents: [agent("pm"), agent("swe"), agent("qa")] }), { mode: "replay" });
    await screen.findByText("已載入 18 / 共 18 張");
    expect(screen.getAllByText("設計師（已離開）").length).toBeGreaterThan(0);
    expect(screen.getAllByText("資安把關（已離開）").length).toBeGreaterThan(0);
  });
});
