import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { agent, apply, mockEff, ready, snapshot, summary } from "../../test/factories";
import { StaticFactoryProvider } from "../../state/FactoryProvider";
import type { FactoryState } from "../../state/reducer";
import type { AgentView, Look } from "../../types/events";
import { LOOKS } from "../../types/events";
import { Avatar } from "./Avatar";
import { Stage } from "./Stage";
import { Tray } from "./Tray";

function renderStage(state: FactoryState, props: Parameters<typeof Stage>[0] = {}) {
  return render(
    <StaticFactoryProvider state={state}>
      <Stage {...props} />
    </StaticFactoryProvider>,
  );
}
const deskIds = (c: HTMLElement) => [...c.querySelectorAll("[data-desk]")].map((e) => e.getAttribute("data-desk"));
const deskEl = (c: HTMLElement, id: string) => c.querySelector<HTMLElement>(`[data-desk="${id}"]`)!;

function lineup(seats: string[], extra: AgentView[] = []): FactoryState {
  const agents = [...snapshot().agents, ...extra];
  return ready({ agents, pipelines: [{ id: "main", name: "主線", seats, pending_seats: null }] });
}

describe("Stage", () => {
  it("draws one desk per seat in seat order, between the two trays", () => {
    const { container } = renderStage(lineup(["pm", "designer", "swe", "qa"], [agent("designer", { builtin: false })]));
    expect(deskIds(container)).toEqual(["pm", "designer", "swe", "qa"]);
    const kinds = [...container.querySelectorAll("[data-kind], [data-desk]")].map((e) => e.getAttribute("data-kind") ?? "desk");
    expect(kinds).toEqual(["inbox", "desk", "desk", "desk", "desk", "outbox"]);
  });

  it("follows a reordered lineup and ignores agents that sit on the bench", () => {
    const { container } = renderStage(lineup(["qa", "pm"]));
    expect(deskIds(container)).toEqual(["qa", "pm"]);
  });

  it("exposes each desk's state in data-state", () => {
    let s = lineup(["pm", "swe", "qa"]);
    s = apply(s, "station:update", { station: "pm", status: "working", task: summary() }, 1);
    s = apply(s, "station:update", { station: "swe", status: "rework", task: summary("t1", "in_progress", 1) }, 2);
    s = apply(s, "task:awaiting_approval", { task: summary("t1", "awaiting_approval"), station: "qa", approval: "after_qa" }, 3);
    const { container } = renderStage(s);
    expect(deskEl(container, "pm").dataset.state).toBe("working");
    expect(deskEl(container, "swe").dataset.state).toBe("reject");
    expect(deskEl(container, "qa").dataset.state).toBe("waiting");
  });

  it("shows idle desks as 打瞌睡中 with the duty in the bubble", () => {
    const { container } = renderStage(lineup(["pm"]));
    const d = deskEl(container, "pm");
    expect(within(d).getByText("打瞌睡中")).toBeInTheDocument();
    expect(within(d).getByText("do things")).toBeInTheDocument();
    expect(d).toHaveAccessibleName("PM：打瞌睡中");
  });

  it("shows queue, done and failed counts on the trays", () => {
    // The trays show history_counts (§6.3a point 7), the same numbers as the note wall.
    const s = ready({
      pipeline: { ...snapshot().pipeline, queue_size: 4, done_count: 7, failed_count: 2 },
      history_counts: { pending: 4, in_progress: 0, awaiting_approval: 0, done: 7, failed: 2 },
    });
    renderStage(s);
    expect(screen.getByTestId("inbox-count")).toHaveTextContent("4");
    expect(screen.getByTestId("outbox-count")).toHaveTextContent("7");
    expect(screen.getByText("失敗 2")).toBeInTheDocument();
  });

  it("says so when there are no seats yet", () => {
    renderStage(ready({ agents: [], pipelines: [] }));
    expect(screen.getByText("還沒有座位")).toBeInTheDocument();
  });

  it("renders the × only when a remove handler is given, and calls it with the desk id", async () => {
    const { container, unmount } = renderStage(lineup(["pm", "swe"]));
    expect(container.querySelectorAll("button")).toHaveLength(0);
    unmount();
    const onRemove = vi.fn();
    renderStage(lineup(["pm", "swe"]), { onRemoveAgent: onRemove });
    await userEvent.click(screen.getByRole("button", { name: "請 SWE 離開" }));
    expect(onRemove).toHaveBeenCalledWith("swe");
  });

  it("marks the floor compact for 8 seats and for a 375 px viewport", () => {
    const eight = ["pm", "swe", "qa", "a1", "a2", "a3", "a4", "a5"];
    const extra = eight.slice(3).map((id) => agent(id, { builtin: false }));
    const { container } = renderStage(lineup(eight, extra));
    expect(container.querySelector("[data-floor]")).toHaveAttribute("data-compact");
    expect((container.querySelector("[data-floor]") as HTMLElement).style.getPropertyValue("--desk-w")).toBe("120px");
  });

  it("reacts to viewport changes", () => {
    const original = window.innerWidth;
    try {
      window.innerWidth = 375;
      const { container } = renderStage(lineup(["pm", "swe", "qa"]));
      const floor = () => container.querySelector("[data-floor]") as HTMLElement;
      expect(floor().style.getPropertyValue("--desk-w")).toBe("150px");
      act(() => {
        window.innerWidth = 1280;
        window.dispatchEvent(new Event("resize"));
      });
      expect(floor().style.getPropertyValue("--desk-w")).toBe("166px");
    } finally {
      window.innerWidth = original;
    }
  });
});

describe("model badge (D21)", () => {
  const eff = (model: string, reason: AgentView["effective_llm"]["mock_reason"], provider: "mock" | "anthropic" = "anthropic") =>
    ({ provider, model, source: "agent" as const, mock_reason: reason });
  const badgeOf = (c: HTMLElement, id: string) => deskEl(c, id).querySelector<HTMLElement>("[data-model]")!;

  it("shows Opus / Sonnet / Haiku / Fable / Mock", () => {
    const agents = [
      agent("pm", { effective_llm: eff("claude-opus-5-5", null) }),
      agent("swe", { effective_llm: eff("claude-sonnet-5-5", null) }),
      agent("qa", { effective_llm: eff("claude-haiku-5-5", null) }),
      agent("a1", { builtin: false, effective_llm: eff("claude-fable-5-2", null) }),
      agent("a2", { builtin: false, effective_llm: mockEff() }),
    ];
    const s = ready({ agents, pipelines: [{ id: "main", name: "m", seats: agents.map((a) => a.id), pending_seats: null }] });
    const { container } = renderStage(s);
    expect(["pm", "swe", "qa", "a1", "a2"].map((id) => badgeOf(container, id).textContent)).toEqual(["Opus", "Sonnet", "Haiku", "Fable", "Mock"]);
  });

  it("explains a forced or key-less Mock, but not a chosen Mock", () => {
    const agents = [
      agent("pm", { effective_llm: { ...mockEff("force_mock"), source: "agent" } }),
      agent("swe", { effective_llm: { ...mockEff("missing_key"), source: "agent" } }),
      agent("qa", { effective_llm: mockEff("configured_mock") }),
    ];
    const s = ready({ agents, pipelines: [{ id: "main", name: "m", seats: ["pm", "swe", "qa"], pending_seats: null }] });
    const { container } = renderStage(s);
    expect(badgeOf(container, "pm")).toHaveAttribute("data-hint", "force_mock");
    expect(badgeOf(container, "pm").title).toContain("強制");
    expect(badgeOf(container, "swe")).toHaveAttribute("data-hint", "missing_key");
    expect(badgeOf(container, "swe").title).toContain("金鑰");
    expect(badgeOf(container, "qa")).not.toHaveAttribute("data-hint");
    expect(badgeOf(container, "qa").title).toBe("mock-model");
  });

  it("follows llm:updated", () => {
    let s = lineup(["pm", "swe", "qa"]);
    const opus = agent("qa", { llm: { provider: "anthropic", model: "claude-opus-5-5" }, effective_llm: eff("claude-opus-5-5", null) });
    s = apply(s, "llm:updated", { project_llm: "mock", agents: [agent("pm"), agent("swe"), opus], effective: "now" });
    const { container } = renderStage(s);
    expect(badgeOf(container, "qa").textContent).toBe("Opus");
    expect(badgeOf(container, "pm").textContent).toBe("Mock");
  });
});

describe("Avatar", () => {
  it("renders six distinct looks", () => {
    const html = LOOKS.map((look: Look) => {
      const { container, unmount } = render(<Avatar look={look} color="#FFC83D" state="idle" />);
      const svg = container.querySelector("svg")!;
      expect(svg).toHaveAttribute("data-look", look);
      expect(svg).toHaveAttribute("aria-hidden", "true");
      const out = svg.innerHTML;
      unmount();
      return out;
    });
    expect(LOOKS).toHaveLength(6);
    expect(new Set(html).size).toBe(6);
  });

  it("uses the agent colour for the body, screen and arms", () => {
    const { container } = render(<Avatar look="cap" color="#123456" state="working" />);
    expect(container.querySelectorAll('[fill="#123456"]').length).toBeGreaterThanOrEqual(4);
  });
});

describe("Tray", () => {
  it("is a plain group without onOpen and a button with it", async () => {
    const { rerender } = render(<Tray kind="outbox" count={3} />);
    expect(screen.queryByRole("button")).toBeNull();
    const onOpen = vi.fn();
    rerender(<Tray kind="outbox" count={3} onOpen={onOpen} />);
    await userEvent.click(screen.getByRole("button", { name: "完成：3 張，開啟便條牆" }));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});
