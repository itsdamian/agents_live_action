// Replay answers like the server: changes made while a task is in flight are 202 next_task
// (pipeline:pending now, the lineup after the task ends); 重做 explains that the script won't change.
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useReducer, type ReactNode } from "react";
import { Header } from "../components/panels/Header";
import { ToastProvider } from "../components/panels/Toasts";
import { REPLAY_REJECT_TEXT } from "../components/stage/GateControls";
import { LiveAnnouncer } from "../components/stage/LiveAnnouncer";
import { Stage } from "../components/stage/Stage";
import { finalSeats, pendingEvent, fakeSetProjectLlm } from "../lib/fakeServer";
import { deferUntilTaskEnds, hasDeferred, takeDeferred } from "../lib/replayDeferred";
import type { ReplayHandle } from "../net/replay";
import { StaticFactoryProvider } from "../state/FactoryProvider";
import { factoryReducer, type FactoryAction, type FactoryState } from "../state/reducer";
import { apply, ready, summary } from "../test/factories";
import type { ServerEvent } from "../types/events";
import { useLineup } from "./useLineup";
import { useReplayDeferred } from "./useReplayDeferred";

/** A replay handle whose emits go through the real reducer, like FactoryProvider's. */
function liveReplay() {
  const emitted: ServerEvent[] = [];
  const box: { dispatch: ((a: FactoryAction) => void) | null } = { dispatch: null };
  const handle: ReplayHandle = {
    stop: () => {},
    emit: (e) => {
      emitted.push(e);
      box.dispatch?.({ ...e, now: 0 } as FactoryAction);
    },
    finished: false,
  };
  return { emitted, handle, box };
}

function Harness({ initial, r, children }: { initial: FactoryState; r: ReturnType<typeof liveReplay>; children: ReactNode }) {
  const [state, dispatch] = useReducer(factoryReducer, initial);
  r.box.dispatch = dispatch;
  return (
    <StaticFactoryProvider state={state} dispatch={dispatch} env={{ mode: "replay", replay: r.handle }}>
      <ToastProvider>{children}</ToastProvider>
      <output data-testid="seats">{state.pipelines[0]?.seats.join(",")}</output>
    </StaticFactoryProvider>
  );
}

function LineupProbe() {
  const lineup = useLineup();
  useReplayDeferred();
  return <button type="button" onClick={() => void lineup.removeSeat("qa")}>remove qa</button>;
}

const busy = () => apply(ready(), "station:update", { station: "pm", status: "working", task: summary("t1") }, 1);

describe("replay lineup changes while a task is in flight", () => {
  it("idle: applies at once (effective now)", async () => {
    const r = liveReplay();
    render(<Harness initial={ready()} r={r}><LineupProbe /></Harness>);
    await userEvent.click(screen.getByRole("button", { name: "remove qa" }));
    expect(r.emitted.map((e) => e.event)).toEqual(["agent:left", "pipeline:updated"]);
    expect(screen.getByTestId("seats")).toHaveTextContent("pm,swe");
    expect(screen.getByText("QA 離開座位了。")).toBeInTheDocument();
  });

  it("busy: pipeline:pending + next_task toast, the lineup changes after task:completed", async () => {
    const r = liveReplay();
    render(<Harness initial={busy()} r={r}><LineupProbe /></Harness>);
    await userEvent.click(screen.getByRole("button", { name: "remove qa" }));
    expect(r.emitted).toEqual([pendingEvent(["pm", "swe"], "t1")]);
    expect(screen.getByText("編制已排好，下一張任務開始時生效。")).toBeInTheDocument();
    expect(screen.getByTestId("seats")).toHaveTextContent("pm,swe,qa");
    expect(hasDeferred(r.handle)).toBe(true);

    act(() => r.handle.emit({ event: "task:completed", data: { task: summary("t1", "done"), done_count: 1, deliverables: [] } }));
    expect(r.emitted.map((e) => e.event)).toEqual(["pipeline:pending", "task:completed", "agent:left", "pipeline:updated"]);
    expect(screen.getByTestId("seats")).toHaveTextContent("pm,swe");
    expect(hasDeferred(r.handle)).toBe(false);
  });
});

describe("replay model menu", () => {
  const withLlm = (s: FactoryState): FactoryState => ({
    ...s,
    llm: { project: "mock", force_mock: true, anthropic_key_present: false, models: [{ id: "claude-opus-5-5", label: "Claude Opus 5.5" }] },
  });

  it("busy: llm:updated says next_task and the 下一張開始 mark shows", async () => {
    const r = liveReplay();
    render(<Harness initial={withLlm(busy())} r={r}><Header /></Harness>);
    await userEvent.selectOptions(screen.getByLabelText("模型"), screen.getByRole("option", { name: /Opus/ }));
    await userEvent.click(screen.getByRole("button", { name: /確定|切換|好/ }));
    expect(r.emitted.at(-1)).toMatchObject({ event: "llm:updated", data: { effective: "next_task" } });
    expect(screen.getByTestId("llm-pending")).toHaveTextContent("下一張開始");
  });

  it("idle: effective now, no mark", async () => {
    const r = liveReplay();
    render(<Harness initial={withLlm(ready())} r={r}><Header /></Harness>);
    await userEvent.selectOptions(screen.getByLabelText("模型"), screen.getByRole("option", { name: /Opus/ }));
    await userEvent.click(screen.getByRole("button", { name: /確定|切換|好/ }));
    expect(r.emitted.at(-1)).toMatchObject({ event: "llm:updated", data: { effective: "now" } });
    expect(screen.queryByTestId("llm-pending")).toBeNull();
  });
});

describe("replay 重做", () => {
  it("explains that the recorded script does not branch", async () => {
    const r = liveReplay();
    const gated = apply(ready(), "task:awaiting_approval", { task: summary("t1", "awaiting_approval"), station: "qa", approval: "after_qa" }, 1);
    render(<Harness initial={gated} r={r}><Stage /></Harness>);
    await userEvent.click(screen.getByRole("button", { name: "重做" }));
    await userEvent.type(screen.getByLabelText("哪裡要改？（選填）"), "x{Enter}");
    expect(screen.getByText(REPLAY_REJECT_TEXT)).toBeInTheDocument();
  });
});

describe("fake helpers", () => {
  it("finalSeats reads the last pipeline:updated", () => {
    expect(finalSeats([])).toEqual([]);
    expect(finalSeats(fakeSetProjectLlm(ready(), "mock"))).toEqual([]);
    expect(fakeSetProjectLlm(ready(), "mock", "next_task")[0]).toMatchObject({ data: { effective: "next_task" } });
  });

  it("the deferred store releases only events whose task ended, in order", () => {
    const h: ReplayHandle = { stop: () => {}, emit: () => {}, finished: false };
    const e = (id: string): ServerEvent => ({ event: "agent:left", data: { agent_id: id, pipeline_id: "main" } });
    deferUntilTaskEnds(h, "t1", [e("a")]);
    deferUntilTaskEnds(h, "t2", [e("b")]);
    deferUntilTaskEnds(h, "t1", [e("c")]);
    expect(takeDeferred(h, (id) => id === "t1")).toEqual([e("a"), e("c")]);
    expect(takeDeferred(h, () => false)).toEqual([]);
    expect(takeDeferred(h, () => true)).toEqual([e("b")]);
    expect(hasDeferred(h)).toBe(false);
  });
});

describe("a lineup change is spoken once (the stage announcer), not again by its toast", () => {
  /** Every time a live region appears with text, or its text changes, a screen reader speaks. */
  function spokenSince(container: HTMLElement) {
    const seen = new WeakMap<Element, string>();
    const record = () => {
      const out: string[] = [];
      for (const el of container.querySelectorAll('[role="status"], [role="alert"], [aria-live]')) {
        if (el.getAttribute("aria-live") === "off") continue;
        const text = (el.textContent ?? "").trim();
        if (seen.get(el) !== text && text) out.push(text);
        seen.set(el, text);
      }
      return out;
    };
    record();
    return record;
  }

  function SeatProbe() {
    const lineup = useLineup();
    return (
      <>
        <button type="button" onClick={() => void lineup.removeSeat("qa")}>remove qa</button>
        <button type="button" onClick={() => void lineup.seatBack("qa")}>seat qa</button>
      </>
    );
  }

  it("leaving and coming back are each said once, while the toasts still show", async () => {
    const r = liveReplay();
    const { container } = render(
      <Harness initial={ready()} r={r}>
        <SeatProbe />
        <Stage />
        <LiveAnnouncer />
      </Harness>,
    );
    const record = spokenSince(container);
    await userEvent.click(screen.getByRole("button", { name: "remove qa" }));
    expect(screen.getByText("QA 離開座位了。")).toBeInTheDocument();
    expect(record()).toEqual(["QA 離開了"]);
    // the walk-out ends and the desk is removed: nothing more to say
    act(() => r.box.dispatch?.({ type: "tick", now: 60_000 }));
    expect(document.querySelector('[data-desk="qa"]')).toBeNull();
    expect(record()).toEqual([]);
    await userEvent.click(screen.getByRole("button", { name: "seat qa" }));
    expect(screen.getByText("QA 回座了。")).toBeInTheDocument();
    expect(record()).toEqual(["QA 報到了"]);
  });
});
