import { useId, useState } from "react";
import { Bench } from "./components/roster/Bench";
import { AddAgentForm } from "./components/roster/AddAgentForm";
import { ToastProvider } from "./components/panels/Toasts";
import { PendingNotice } from "./components/stage/PendingNotice";
import { Stage } from "./components/stage/Stage";
import { useLineup } from "./hooks/useLineup";
import { selectStats } from "./state/selectors";
import { useFactoryEnv, useFactoryState } from "./state/FactoryProvider";
import ui from "./styles/ui.module.css";

// The page is assembled piece by piece (Header and the other panels arrive in F9a+).
function AppBody() {
  const state = useFactoryState();
  const env = useFactoryEnv();
  const lineup = useLineup();
  const [adding, setAdding] = useState(false);
  const panelId = useId();
  const stats = selectStats(state);
  return (
    <main className="wrap">
      <h1>Agent 工廠現場</h1>
      <p data-testid="status">
        {env.mode === "replay" ? "回放" : "連線"}：{state.connection.status}
        {state.ready ? `｜專案 ${state.projectId ?? "—"}｜完成 ${stats.completed}｜重做 ${stats.reworks}` : ""}
      </p>
      <div>
        <button type="button" className={ui.btn} aria-expanded={adding} aria-controls={panelId} onClick={() => setAdding((v) => !v)}>
          新增 agent
        </button>
      </div>
      {adding && (
        <section id={panelId} className={ui.panel} aria-label="新增 agent">
          <h2>新同事報到</h2>
          <AddAgentForm onDone={() => setAdding(false)} />
        </section>
      )}
      <PendingNotice />
      <Stage onRemoveAgent={(id) => void lineup.removeSeat(id)} />
      <Bench />
    </main>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <AppBody />
    </ToastProvider>
  );
}
