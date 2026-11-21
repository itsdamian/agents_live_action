// The shop floor. Reads the factory state and lays one Floor per pipeline inside the
// prototype's rounded `.stage` panel. The note layer (F6) is positioned against this element.

import { useCallback, useState } from "react";
import { deskMetrics } from "../../lib/layout";
import { useReducedMotion } from "../../hooks/useReducedMotion";
import { useViewportWidth } from "../../hooks/useViewport";
import { useFactoryState } from "../../state/FactoryProvider";
import { selectDeskViews, selectNotes, selectSeatIds, selectStats } from "../../state/selectors";
import { SHEET_BREAKPOINT, Sheet } from "../panels/Sheet";
import { RecentList, deskCardTitle } from "./DeskCard";
import { Floor } from "./Floor";
import { Note } from "./Note";
import styles from "./Stage.module.css";

export interface StageProps {
  onRemoveAgent?: (id: string) => void;
  onOpenInbox?: () => void;
  onOpenOutbox?: () => void;
  /** Desks open their recent-actions card when clicked (plan §11.7). */
  deskCards?: boolean;
}

function emptyText(state: ReturnType<typeof useFactoryState>): string {
  if (state.ready) return "還沒有座位";
  if (state.connection.status === "error") return state.connection.reason ?? "載入失敗";
  return "連線中…";
}

export function Stage({ onRemoveAgent, onOpenInbox, onOpenOutbox, deskCards = false }: StageProps) {
  const state = useFactoryState();
  const width = useViewportWidth();
  const reduced = useReducedMotion();
  const stats = selectStats(state);
  const many = state.pipelines.length > 1;
  // A callback ref in state: notes mount only once the stage element exists, because a child's
  // layout effect runs before the parent's ref would be attached.
  const [stage, setStage] = useState<HTMLElement | null>(null);
  const seatIds = selectSeatIds(state);
  // Desk card (plan §11.7): one open at a time; a popover on wide screens, a Sheet on phones.
  const [cardDesk, setCardDesk] = useState<string | null>(null);
  const toggleCard = useCallback((id: string) => setCardDesk((cur) => (cur === id ? null : id)), []);
  const closeCard = useCallback(() => setCardDesk(null), []);
  const cardInline = width > SHEET_BREAKPOINT;
  const openCard = cardDesk && state.desks[cardDesk] ? cardDesk : null;

  return (
    <section ref={setStage} className={styles.stage} aria-label="工廠現場" data-reduced-motion={reduced || undefined}>
      {state.pipelines.length === 0 && <p className={styles.empty}>{emptyText(state)}</p>}
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
            onToggleCard={deskCards ? toggleCard : undefined}
            cardDesk={openCard}
            cardInline={cardInline}
          />
        );
      })}
      {stage && selectNotes(state).map((n) => (
        <Note key={n.taskId} note={n} stage={stage} seatIds={seatIds} />
      ))}
      {openCard && !cardInline && (
        <Sheet title={deskCardTitle(state.agents[openCard]?.display_name ?? openCard)} onClose={closeCard}>
          <RecentList deskId={openCard} />
        </Sheet>
      )}
    </section>
  );
}
