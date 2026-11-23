import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, vi } from "vitest";
import type { Api } from "../../net/api";
import { ApiRequestError } from "../../net/api";
import type { ReplayHandle } from "../../net/replay";
import { StaticFactoryProvider, type FactoryEnv } from "../../state/FactoryProvider";
import type { FactoryState } from "../../state/reducer";
import { agent, apply, ready, snapshot, summary } from "../../test/factories";
import type { ServerEvent } from "../../types/events";
import { STALE_GATE_TEXT, UNLOCK_AFTER_MS, reworkTarget } from "./GateControls";
import { Stage } from "./Stage";

function gated(station = "qa", extra: Partial<Parameters<typeof ready>[0]> = {}): FactoryState {
  const s = ready(extra);
  return apply(s, "task:awaiting_approval", { task: summary("t1", "awaiting_approval"), station, approval: "after_qa" }, 1);
}

function stub(over: Partial<Record<"approve" | "reject", ReturnType<typeof vi.fn>>> = {}) {
  const approve = over.approve ?? vi.fn().mockResolvedValue({ task_id: "t1", decision: "approved" });
  const reject = over.reject ?? vi.fn().mockResolvedValue({ task_id: "t1", decision: "rejected", note: "" });
  return { approve, reject, api: { approve, reject } as unknown as Api };
}

function mount(state: FactoryState, env: Partial<FactoryEnv> = {}) {
  const view = (s: FactoryState) => (
    <StaticFactoryProvider state={s} env={env}>
      <Stage />
    </StaticFactoryProvider>
  );
  const r = render(view(state));
  return { ...r, update: (s: FactoryState) => r.rerender(view(s)) };
}

beforeEach(() => vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener: () => {}, removeEventListener: () => {} })));
afterEach(() => vi.unstubAllGlobals());

describe("GateControls (live)", () => {
  it("shows 可以 / 重做 only on the desk that holds the gate", () => {
    const { container } = mount(gated("qa"));
    const qa = container.querySelector<HTMLElement>('[data-desk="qa"]')!;
    expect(within(qa).getByRole("button", { name: "可以" })).toBeInTheDocument();
    expect(within(qa).getByRole("button", { name: "重做" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "可以" })).toHaveLength(1);
    expect(container.querySelector('[data-desk="swe"] [data-gate-task]')).toBeNull();
  });

  it("is absent when nothing waits", () => {
    mount(ready());
    expect(screen.queryByRole("button", { name: "可以" })).toBeNull();
  });

  it("可以 calls approve(project, task) and stays locked while the request is out", async () => {
    let resolve!: (v: unknown) => void;
    const approve = vi.fn(() => new Promise((r) => { resolve = r; }));
    const { api } = stub({ approve });
    mount(gated(), { api, projectId: "demo" });
    await userEvent.click(screen.getByRole("button", { name: "可以" }));
    expect(approve).toHaveBeenCalledWith("demo", "t1");
    expect(screen.getByRole("button", { name: "可以" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "重做" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "可以" }));
    expect(approve).toHaveBeenCalledTimes(1);
    await act(async () => resolve({ task_id: "t1", decision: "approved" }));
    // accepted: still locked until the server's event removes the gate
    expect(screen.getByRole("button", { name: "可以" })).toBeDisabled();
  });

  it("works from the keyboard: Tab to the button and press Enter", async () => {
    const { api, approve } = stub();
    mount(gated(), { api, projectId: "demo" });
    await userEvent.tab(); // first focusable: the 可以 button (no other buttons on the stage)
    expect(screen.getByRole("button", { name: "可以" })).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(approve).toHaveBeenCalledWith("demo", "t1");
  });

  it("重做 opens a note field; Enter sends the note", async () => {
    const { api, reject } = stub();
    mount(gated(), { api, projectId: "demo" });
    await userEvent.click(screen.getByRole("button", { name: "重做" }));
    const input = screen.getByLabelText("哪裡要改？（選填）");
    expect(input).toHaveFocus();
    await userEvent.type(input, "  補上錯誤處理 {Enter}");
    expect(reject).toHaveBeenCalledWith("demo", "t1", "補上錯誤處理");
  });

  it("the note is optional: 送出重做 with no text sends an empty note", async () => {
    const { api, reject } = stub();
    mount(gated(), { api, projectId: "demo" });
    await userEvent.click(screen.getByRole("button", { name: "重做" }));
    await userEvent.click(screen.getByRole("button", { name: "送出重做" }));
    expect(reject).toHaveBeenCalledWith("demo", "t1", "");
  });

  it("Escape and 取消 go back to the two buttons without calling the API", async () => {
    const { api, reject } = stub();
    mount(gated(), { api, projectId: "demo" });
    await userEvent.click(screen.getByRole("button", { name: "重做" }));
    await userEvent.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "可以" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重做" }));
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.getByRole("button", { name: "可以" })).toBeInTheDocument();
    expect(reject).not.toHaveBeenCalled();
  });

  it("409 shows 這張已經不在等你了 and unlocks the buttons", async () => {
    const approve = vi.fn().mockRejectedValue(new ApiRequestError(409, "Task t1 is not awaiting approval"));
    mount(gated(), { api: stub({ approve }).api, projectId: "demo" });
    await userEvent.click(screen.getByRole("button", { name: "可以" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(STALE_GATE_TEXT);
    expect(screen.getByRole("button", { name: "可以" })).toBeEnabled();
  });

  it("other API errors show the server's message", async () => {
    const reject = vi.fn().mockRejectedValue(new ApiRequestError(500, "資料庫忙碌中"));
    mount(gated(), { api: stub({ reject }).api, projectId: "demo" });
    await userEvent.click(screen.getByRole("button", { name: "重做" }));
    await userEvent.click(screen.getByRole("button", { name: "送出重做" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("資料庫忙碌中");
  });

  it("a network failure shows a readable message", async () => {
    const approve = vi.fn().mockRejectedValue(new Error("boom"));
    mount(gated(), { api: stub({ approve }).api, projectId: "demo" });
    await userEvent.click(screen.getByRole("button", { name: "可以" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("送出失敗");
  });

  it("disappears when the server's approved event arrives", async () => {
    const { api } = stub();
    const s = gated();
    const { update } = mount(s, { api, projectId: "demo" });
    await userEvent.click(screen.getByRole("button", { name: "可以" }));
    update(apply(s, "task:approved", { task: summary(), station: "qa" }, 5));
    expect(screen.queryByRole("button", { name: "可以" })).toBeNull();
  });

  it("unlocks after 10 s if the event never arrives", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { api } = stub();
      mount(gated(), { api, projectId: "demo" });
      await userEvent.click(screen.getByRole("button", { name: "可以" }));
      expect(screen.getByRole("button", { name: "可以" })).toBeDisabled();
      await act(async () => { await vi.advanceTimersByTimeAsync(UNLOCK_AFTER_MS); });
      expect(screen.getByRole("button", { name: "可以" })).toBeEnabled();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("GateControls (replay)", () => {
  const replay = () => {
    const emitted: ServerEvent[] = [];
    const handle: ReplayHandle = { stop: () => {}, emit: (e) => emitted.push(e), finished: false };
    return { emitted, handle };
  };

  it("可以 emits a fake task:approved and never touches the API", async () => {
    const { api, approve } = stub();
    const r = replay();
    mount(gated(), { api, mode: "replay", replay: r.handle });
    await userEvent.click(screen.getByRole("button", { name: "可以" }));
    expect(approve).not.toHaveBeenCalled();
    expect(r.emitted).toEqual([
      { event: "task:approved", data: { task: { id: "t1", title: "加登入頁", priority: "high", status: "in_progress", rework_count: 0 }, station: "qa" } },
    ]);
  });

  it("重做 emits task:rejected with the note, then a rework event for the nearest make seat", async () => {
    const r = replay();
    mount(gated(), { mode: "replay", replay: r.handle });
    await userEvent.click(screen.getByRole("button", { name: "重做" }));
    await userEvent.type(screen.getByLabelText("哪裡要改？（選填）"), "再改一下{Enter}");
    expect(r.emitted.map((e) => e.event)).toEqual(["task:rejected", "station:update"]);
    expect(r.emitted[0]).toMatchObject({ data: { station: "qa", note: "再改一下" } });
    expect(r.emitted[1]).toMatchObject({ data: { station: "swe", status: "rework", task: { rework_count: 1 } } });
  });
});

describe("reworkTarget", () => {
  const roster = (...a: Array<[string, "make" | "check"]>) => Object.fromEntries(a.map(([id, kind]) => [id, agent(id, { kind })]));

  it("picks the nearest make seat before the gate", () => {
    expect(reworkTarget(["pm", "swe", "qa"], roster(["pm", "make"], ["swe", "make"], ["qa", "check"]), "qa")).toBe("swe");
    expect(reworkTarget(["pm", "writer"], roster(["pm", "make"], ["writer", "make"]), "writer")).toBe("pm");
  });

  it("skips check seats and falls back to the first seat", () => {
    expect(reworkTarget(["pm", "swe", "qa", "sec"], roster(["pm", "make"], ["swe", "make"], ["qa", "check"], ["sec", "check"]), "sec")).toBe("swe");
    expect(reworkTarget(["a", "b"], roster(["a", "check"], ["b", "check"]), "b")).toBe("a");
  });
});

describe("gate in the default snapshot", () => {
  it("a restored gate (E5) also shows its controls", () => {
    const s = ready({
      pipeline: {
        ...snapshot().pipeline,
        stations: { ...snapshot().pipeline.stations, qa: { status: "awaiting_approval", task_id: "t1", task_title: "X" } },
        awaiting_approval: [{ task: summary("t1", "in_progress"), station: "qa", approval: "after_qa" }],
      },
    });
    mount(s);
    expect(screen.getByRole("button", { name: "可以" })).toBeInTheDocument();
  });
});
