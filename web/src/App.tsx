import { Bench } from "./components/roster/Bench";
import { AddAgentForm } from "./components/roster/AddAgentForm";
import { ConnectionBanner } from "./components/panels/ConnectionBanner";
import { DevOpsButton, DevOpsDrawer } from "./components/panels/DevOpsDrawer";
import { Header } from "./components/panels/Header";
import { NoteWall } from "./components/panels/NoteWall";
import { PanelsProvider, usePanels } from "./components/panels/PanelsContext";
import { Sheet } from "./components/panels/Sheet";
import { StatsBar } from "./components/panels/StatsBar";
import { TaskDetail } from "./components/panels/TaskDetail";
import { TaskForm } from "./components/panels/TaskForm";
import { ToastProvider } from "./components/panels/Toasts";
import { LiveAnnouncer } from "./components/stage/LiveAnnouncer";
import { PendingNotice } from "./components/stage/PendingNotice";
import { Stage } from "./components/stage/Stage";
import { useAlertToasts } from "./hooks/useAlertToasts";
import { useLineup } from "./hooks/useLineup";
import { useReplayDeferred } from "./hooks/useReplayDeferred";
import { useFactoryState } from "./state/FactoryProvider";
import { selectCurrentTaskId } from "./state/selectors";
import ui from "./styles/ui.module.css";

/** The open panel, inside a Sheet (inline on wide screens, full screen on phones). */
function PanelHost() {
  const { panel, close } = usePanels();
  if (!panel) return null;
  // Each panel gets its own Sheet instance: swapping content inside one Sheet would drop keyboard
  // focus (the clicked button disappears) and leave Escape without a target.
  const key = panel.kind === "task-detail" ? `task-detail:${panel.taskId}` : panel.kind;
  switch (panel.kind) {
    case "add-agent":
      return (
        <Sheet key={key} title="新同事報到" onClose={close}>
          <AddAgentForm onDone={close} />
        </Sheet>
      );
    case "task-form":
      return (
        <Sheet key={key} title="派一張任務" onClose={close}>
          <TaskForm />
        </Sheet>
      );
    case "task-detail":
      return (
        <Sheet key={key} title="任務詳情" onClose={close}>
          <TaskDetail taskId={panel.taskId} />
        </Sheet>
      );
    case "note-wall":
      return (
        <Sheet key={key} title="便條牆" onClose={close}>
          <NoteWall key={panel.initial ?? "outbox"} initial={panel.initial} />
        </Sheet>
      );
    case "devops":
      return (
        <Sheet key={key} title="機房" onClose={close}>
          <DevOpsDrawer />
        </Sheet>
      );
    default:
      return null;
  }
}

function AppBody() {
  const state = useFactoryState();
  const lineup = useLineup();
  const panels = usePanels();
  useAlertToasts();
  useReplayDeferred();
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
        <DevOpsButton />
      </div>
      <PanelHost />
      <LiveAnnouncer />
      <PendingNotice />
      <Stage
        deskCards
        onRemoveAgent={(id) => void lineup.removeSeat(id)}
        onOpenInbox={() => panels.open({ kind: "note-wall", initial: "inbox" })}
        onOpenOutbox={() => panels.open({ kind: "note-wall", initial: "outbox" })}
      />
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
