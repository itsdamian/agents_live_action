import { selectStats } from "./state/selectors";
import { useFactoryEnv, useFactoryState } from "./state/FactoryProvider";

// Placeholder until the stage components (F5+) land: shows that data is flowing.
export default function App() {
  const state = useFactoryState();
  const env = useFactoryEnv();
  const stats = selectStats(state);
  return (
    <main className="wrap">
      <h1>Agent 工廠現場</h1>
      <p data-testid="status">
        {env.mode === "replay" ? "回放" : "連線"}：{state.connection.status}
        {state.ready ? `｜專案 ${state.projectId ?? "—"}｜完成 ${stats.completed}｜重做 ${stats.reworks}` : ""}
      </p>
    </main>
  );
}
