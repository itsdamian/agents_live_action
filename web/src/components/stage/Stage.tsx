// The shop floor. Reads the factory state and lays one Floor per pipeline inside the
// prototype's rounded `.stage` panel. The note layer (F6) is positioned against this element.

import { deskMetrics } from "../../lib/layout";
import { useReducedMotion } from "../../hooks/useReducedMotion";
import { useViewportWidth } from "../../hooks/useViewport";
import { useFactoryState } from "../../state/FactoryProvider";
import { selectDeskViews, selectStats } from "../../state/selectors";
import { Floor } from "./Floor";
import styles from "./Stage.module.css";

export interface StageProps {
  onRemoveAgent?: (id: string) => void;
  onOpenInbox?: () => void;
  onOpenOutbox?: () => void;
}

export function Stage({ onRemoveAgent, onOpenInbox, onOpenOutbox }: StageProps) {
  const state = useFactoryState();
  const width = useViewportWidth();
  const reduced = useReducedMotion();
  const stats = selectStats(state);
  const many = state.pipelines.length > 1;

  return (
    <section className={styles.stage} aria-label="工廠現場" data-reduced-motion={reduced || undefined}>
      {state.pipelines.length === 0 && <p className={styles.empty}>{state.ready ? "還沒有座位" : "連線中…"}</p>}
      {state.pipelines.map((p) => {
        const views = selectDeskViews(state, p.id);
        return (
          <Floor
            key={p.id}
            pipelineId={p.id}
            name={many ? p.name : undefined}
            views={views}
            metrics={deskMetrics(width, views.length)}
            queue={stats.queue}
            done={stats.completed}
            failed={stats.failed}
            onRemoveAgent={onRemoveAgent}
            onOpenInbox={onOpenInbox}
            onOpenOutbox={onOpenOutbox}
          />
        );
      })}
    </section>
  );
}
