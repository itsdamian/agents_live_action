import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { apply, ready, snapshot, summary } from "../../test/factories";
import { renderWith } from "../../test/render";
import { StatsBar } from "./StatsBar";

describe("StatsBar", () => {
  it("shows done, reworks, in flight and today's spend", () => {
    let s = ready({
      pipeline: { ...snapshot().pipeline, done_count: 7, failed_count: 1 },
      history_counts: { pending: 0, in_progress: 1, awaiting_approval: 0, done: 7, failed: 1 },
      usage: { ...snapshot().usage, daily_cost: 0.1234 },
      tasks: [{ ...snapshot().tasks[0]!, id: "a", title: "A", priority: "high", status: "in_progress", rework_count: 2, description: "", approval: "none", max_reworks: 3, created_at: "", updated_at: "", pm_output: null, swe_output: null, qa_output: null }],
    });
    renderWith(<StatsBar />, s);
    expect(screen.getByTestId("stat-completed")).toHaveTextContent("7");
    expect(screen.getByTestId("stat-reworks")).toHaveTextContent("2");
    expect(screen.getByTestId("stat-in-flight")).toHaveTextContent("1");
    expect(screen.getByTestId("stat-cost")).toHaveTextContent("$0.12");
    s = apply(s, "task:completed", { task: summary("a", "done"), done_count: 8, deliverables: [] });
    renderWith(<StatsBar />, s);
  });

  it("updates as events arrive", () => {
    const s = ready();
    const { update } = renderWith(<StatsBar />, s);
    const next = apply(apply(s, "station:update", { station: "pm", status: "working", task: summary("t1") }), "station:update", { station: "swe", status: "rework", task: summary("t1", "in_progress", 1) });
    update(next);
    expect(screen.getByTestId("stat-reworks")).toHaveTextContent("1");
    expect(screen.getByTestId("stat-in-flight")).toHaveTextContent("1");
  });

  it("flags a budget pause", () => {
    renderWith(<StatsBar />, ready({ usage: { ...snapshot().usage, budget_paused: true } }));
    expect(screen.getByTestId("budget-paused")).toHaveTextContent("預算已達上限");
  });

  it("in-flight is a button only when a handler is given, and disabled when nothing runs", async () => {
    const onOpen = vi.fn();
    const idle = renderWith(<StatsBar onOpenInFlight={onOpen} />, ready());
    expect(screen.getByRole("button", { name: /進行中 0/ })).toBeDisabled();
    idle.unmount();
    renderWith(<StatsBar onOpenInFlight={onOpen} />, apply(ready(), "station:update", { station: "pm", status: "working", task: summary("t1") }));
    await userEvent.click(screen.getByRole("button", { name: /進行中 1/ }));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});
