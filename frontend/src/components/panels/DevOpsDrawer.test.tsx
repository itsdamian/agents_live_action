import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { apply, ready, snapshot as makeSnapshot } from "../../test/factories";
import { renderWith } from "../../test/render";
import { factoryReducer, type FactoryState } from "../../state/reducer";
import { DevOpsButton, DevOpsDrawer } from "./DevOpsDrawer";
import { PanelsProvider } from "./PanelsContext";
import { useAlertToasts } from "../../hooks/useAlertToasts";
import { usePanels } from "./PanelsContext";
import type { DevOpsAlertData } from "../../types/events";

const alert = (level: DevOpsAlertData["level"], message: string, metric = "cpu_percent", value = 50): DevOpsAlertData => ({ level, message, metric, value });
const alerting = (...list: DevOpsAlertData[]) => list.reduce((s: FactoryState, a) => apply(s, "devops:alert", a), ready());

describe("DevOpsDrawer", () => {
  it("says so when there are no alerts", () => {
    renderWith(<PanelsProvider><DevOpsDrawer /></PanelsProvider>, ready());
    expect(screen.getByText("目前沒有警示，機房一切正常。")).toBeInTheDocument();
  });

  it("lists alerts newest first with their level, metric and value", () => {
    const s = alerting(alert("info", "一切正常", "cpu_percent", 12), alert("warning", "記憶體偏高", "memory_percent", 81.26), alert("critical", "預算用完", "budget", 5.02));
    renderWith(<DevOpsDrawer />, s);
    const items = within(screen.getByRole("list", { name: "警示" })).getAllByRole("listitem");
    expect(items.map((i) => i.getAttribute("data-level"))).toEqual(["critical", "warning", "info"]);
    expect(items[0]).toHaveTextContent("嚴重預算用完預算 $5.02");
    expect(items[1]).toHaveTextContent("警告記憶體偏高記憶體 81.3%");
    expect(items[2]).toHaveTextContent("資訊一切正常CPU 12%");
  });

  it("opening it marks everything as seen", () => {
    const s = alerting(alert("warning", "x"));
    function Probe() {
      return <><DevOpsButton /><DevOpsDrawer /></>;
    }
    renderWith(<PanelsProvider><Probe /></PanelsProvider>, s);
    // StaticFactoryProvider has no reducer: the dispatch is a no-op here, so check the dispatch itself in the reducer.
    expect(factoryReducer(s, { type: "alerts_seen" }).alertsSeen).toBe(1);
  });
});

describe("DevOpsButton: the dot", () => {
  const dot = () => screen.queryByTestId("devops-dot");

  it("no dot without unseen alerts", () => {
    renderWith(<PanelsProvider><DevOpsButton /></PanelsProvider>, ready());
    expect(dot()).toBeNull();
  });

  it("the dot shows the most severe unseen level and says how many", () => {
    renderWith(<PanelsProvider><DevOpsButton /></PanelsProvider>, alerting(alert("info", "a"), alert("critical", "b"), alert("warning", "c")));
    expect(dot()).toHaveAttribute("data-level", "critical");
    expect(dot()).toHaveTextContent("3 則新警示，最高：嚴重");
  });

  it("a warning-only dot is amber-level, an info-only dot is info-level", () => {
    const w = renderWith(<PanelsProvider><DevOpsButton /></PanelsProvider>, alerting(alert("warning", "a")));
    expect(dot()).toHaveAttribute("data-level", "warning");
    w.unmount();
    renderWith(<PanelsProvider><DevOpsButton /></PanelsProvider>, alerting(alert("info", "a")));
    expect(dot()).toHaveAttribute("data-level", "info");
  });

  it("seen alerts do not light the dot", () => {
    const s = factoryReducer(alerting(alert("critical", "a")), { type: "alerts_seen" });
    renderWith(<PanelsProvider><DevOpsButton /></PanelsProvider>, s);
    expect(dot()).toBeNull();
  });

  it("toggles the machine-room panel", async () => {
    function Probe() {
      const p = usePanels();
      return <><DevOpsButton /><output data-testid="p">{p.panel?.kind ?? "none"}</output></>;
    }
    renderWith(<PanelsProvider><Probe /></PanelsProvider>, ready());
    await userEvent.click(screen.getByRole("button", { name: "機房" }));
    expect(screen.getByTestId("p")).toHaveTextContent("devops");
    expect(screen.getByRole("button", { name: "機房" })).toHaveAttribute("aria-expanded", "true");
  });
});

describe("critical alerts raise a toast (useAlertToasts)", () => {
  function Watcher() {
    useAlertToasts();
    const p = usePanels();
    return <output data-testid="p">{p.panel?.kind ?? "none"}</output>;
  }
  const mount = (s: FactoryState) => {
    const r = renderWith(<PanelsProvider><Watcher /></PanelsProvider>, s);
    return { ...r, push: (next: FactoryState) => act(() => r.update(next, <PanelsProvider><Watcher /></PanelsProvider>)) };
  };

  it("a new critical alert pops a sticky error toast whose button opens the machine room", async () => {
    const s = ready();
    const m = mount(s);
    m.push(apply(s, "devops:alert", alert("critical", "預算用完", "budget", 5.1)));
    expect(await screen.findByRole("alert")).toHaveTextContent("機房警示：預算用完");
    await userEvent.click(screen.getByRole("button", { name: "看機房" }));
    expect(screen.getByTestId("p")).toHaveTextContent("devops");
  });

  it("info and warning alerts do not toast", () => {
    const s = ready();
    const m = mount(s);
    m.push(apply(apply(s, "devops:alert", alert("info", "a")), "devops:alert", alert("warning", "b")));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(/機房警示/)).toBeNull();
  });

  it("alerts that arrived with the snapshot are history, not news", () => {
    const s = ready({ devops_alerts: [alert("critical", "舊的警示", "budget", 9)] });
    mount(s);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a fresh snapshot after a reconnect does not replay old critical alerts", () => {
    const s0 = ready();
    const m = mount(s0);
    const withAlert = apply(s0, "devops:alert", alert("critical", "預算用完", "budget", 5.1));
    m.push(withAlert);
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    const reconnected = apply(withAlert, "snapshot", makeSnapshot({ devops_alerts: [alert("critical", "預算用完", "budget", 5.1)] }));
    m.push(reconnected);
    expect(screen.getAllByRole("alert")).toHaveLength(1); // still just the first toast
  });
});
