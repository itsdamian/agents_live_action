import { useId } from "react";
import { Bench } from "./components/roster/Bench";
import { AddAgentForm } from "./components/roster/AddAgentForm";
import { ConnectionBanner } from "./components/panels/ConnectionBanner";
import { Header } from "./components/panels/Header";
import { PanelsProvider, usePanels } from "./components/panels/PanelsContext";
import { StatsBar } from "./components/panels/StatsBar";
import { TaskDetail } from "./components/panels/TaskDetail";
import { TaskForm } from "./components/panels/TaskForm";
import { ToastProvider } from "./components/panels/Toasts";
import { PendingNotice } from "./components/stage/PendingNotice";
import { Stage } from "./components/stage/Stage";
import { useLineup } from "./hooks/useLineup";
import { useFactoryState } from "./state/FactoryProvider";
import { selectCurrentTaskId } from "./state/selectors";
import ui from "./styles/ui.module.css";

// Panels are shown inline for now; F9c puts them in a Sheet (full screen on phones).
function PanelHost() {
  const { panel, close } = usePanels();
  const id = useId();
  if (!panel) return null;
  const closeButton = (
    <button type="button" className={ui.btn} onClick={close}>關閉</button>
  );
  switch (panel.kind) {
    case "add-agent":
      return (
        <section id={id} className={ui.panel} aria-label="新增 agent">
          <h2>新同事報到</h2>
          <AddAgentForm onDone={close} />
          <div className={ui.actions}>{closeButton}</div>
        </section>
      );
    case "task-form":
      return (
        <section id={id} className={ui.panel} aria-label="派一張任務">
          <h2>派一張任務</h2>
          <TaskForm />
          <div className={ui.actions}>{closeButton}</div>
        </section>
      );
    case "task-detail":
      return (
        <section id={id} className={ui.panel} aria-label="任務詳情">
          <TaskDetail taskId={panel.taskId} />
          <div className={ui.actions}>{closeButton}</div>
        </section>
      );
    default:
      return null;
  }
}

function AppBody() {
  const state = useFactoryState();
  const lineup = useLineup();
  const panels = usePanels();
  const current = selectCurrentTaskId(state);
  return (
    <main className="wrap">
      <Header />
      <ConnectionBanner />
      <StatsBar onOpenInFlight={current ? () => panels.open({ kind: "task-detail", taskId: current }) : undefined} />
      <div className={ui.actions}>
        <button type="button" className={`${ui.btn} ${ui.primary}`} aria-expanded={panels.panel?.kind === "task-form"} onClick={() => panels.toggle({ kind: "task-form" })}>
          派一張任務
        </button>
        <button type="button" className={ui.btn} aria-expanded={panels.panel?.kind === "add-agent"} onClick={() => panels.toggle({ kind: "add-agent" })}>
          新增 agent
        </button>
      </div>
      <PanelHost />
      <PendingNotice />
      <Stage onRemoveAgent={(id) => void lineup.removeSeat(id)} />
      <Bench />
    </main>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <PanelsProvider>
        <AppBody />
      </PanelsProvider>
    </ToastProvider>
  );
}
