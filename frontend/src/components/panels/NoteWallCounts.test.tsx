// §6.3a point 7: history counts start from the snapshot, follow events, and are corrected by the
// `counts` of GET /history when the note wall opens. The trays and the wall must agree.
import { render, screen, within } from "@testing-library/react";
import { useReducer } from "react";
import { vi } from "vitest";
import { ToastProvider } from "./Toasts";
import { StaticFactoryProvider } from "../../state/FactoryProvider";
import { factoryReducer, type FactoryState } from "../../state/reducer";
import { apply, ready, snapshot, summary } from "../../test/factories";
import { stubApi } from "../../test/render";
import type { HistoryCounts } from "../../types/events";
import { Stage } from "../stage/Stage";
import { NoteWall } from "./NoteWall";
import { PanelsProvider } from "./PanelsContext";

const serverCounts: HistoryCounts = { pending: 10, in_progress: 1, awaiting_approval: 1, done: 14, failed: 4 };

function Harness({ initial, api }: { initial: FactoryState; api: ReturnType<typeof stubApi> }) {
  const [state, dispatch] = useReducer(factoryReducer, initial);
  return (
    <StaticFactoryProvider state={state} dispatch={dispatch} env={{ api, projectId: "demo" }}>
      <ToastProvider>
        <PanelsProvider>
          <Stage />
          <NoteWall />
          <output data-testid="counts">{JSON.stringify(state.historyCounts)}</output>
        </PanelsProvider>
      </ToastProvider>
    </StaticFactoryProvider>
  );
}

const outbox = () => screen.getByTestId("outbox-count");

describe("history counts (§6.3a point 7)", () => {
  it("the reducer takes the counts of GET /history; trays and wall then agree", async () => {
    const api = stubApi({ history: vi.fn().mockResolvedValue({ items: [], total: 0, counts: serverCounts }) });
    const s = ready({ history_counts: { pending: 1, in_progress: 0, awaiting_approval: 0, done: 2, failed: 1 } });
    render(<Harness initial={s} api={api} />);
    await screen.findByText("這個篩選下沒有便條。");
    expect(JSON.parse(screen.getByTestId("counts").textContent!)).toEqual(serverCounts);
    expect(outbox()).toHaveTextContent("14");
    expect(screen.getByText("失敗 4")).toBeInTheDocument();
    const done = screen.getByRole("button", { name: /^完成/ });
    expect(within(done).getByText("14")).toBeInTheDocument();
  });

  it("the trays follow events from the snapshot counts before the wall is opened", () => {
    let s = ready({ history_counts: { pending: 0, in_progress: 1, awaiting_approval: 0, done: 2, failed: 0 } });
    s = apply(s, "task:completed", { task: summary("t9", "done"), done_count: 1, deliverables: [] }, 1);
    expect(s.historyCounts.done).toBe(3);
    render(<StaticFactoryProvider state={s}><Stage /></StaticFactoryProvider>);
    expect(outbox()).toHaveTextContent("3");
  });

  it("a new snapshot replaces corrected counts (reconnect)", () => {
    let s = factoryReducer(ready(), { type: "history_counts", counts: serverCounts });
    expect(s.historyCounts).toEqual(serverCounts);
    s = apply(s, "snapshot", snapshot({ history_counts: { pending: 0, in_progress: 0, awaiting_approval: 0, done: 5, failed: 0 } }), 2);
    expect(s.historyCounts.done).toBe(5);
  });
});
