import { useId, useState } from "react";
import { Bench } from "./components/roster/Bench";
import { AddAgentForm } from "./components/roster/AddAgentForm";
import { ConnectionBanner } from "./components/panels/ConnectionBanner";
import { Header } from "./components/panels/Header";
import { StatsBar } from "./components/panels/StatsBar";
import { ToastProvider } from "./components/panels/Toasts";
import { PendingNotice } from "./components/stage/PendingNotice";
import { Stage } from "./components/stage/Stage";
import { useLineup } from "./hooks/useLineup";
import ui from "./styles/ui.module.css";

// The page is assembled piece by piece (Header and the other panels arrive in F9a+).
function AppBody() {
  const lineup = useLineup();
  const [adding, setAdding] = useState(false);
  const panelId = useId();
  return (
    <main className="wrap">
      <Header />
      <ConnectionBanner />
      <StatsBar />
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
