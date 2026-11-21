// One desk: bubble, character, name plate with model badge, status line, and the `spot`
// where notes land (the note layer measures it via data-spot).

import { agentColor } from "../../lib/colors";
import { bubbleText, mockHint, statusText } from "../../lib/bubble";
import { modelBadge, type DeskView } from "../../state/selectors";
import { Avatar } from "./Avatar";
import { Bubble } from "./Bubble";
import { DeskCard } from "./DeskCard";
import { GateControls } from "./GateControls";
import styles from "./Desk.module.css";

export interface DeskProps {
  view: DeskView;
  /** Shows the "×" on the name plate (wired to `PUT /pipelines/main` in F8). */
  onRemove?: (id: string) => void;
  /** Compact floors (many seats / narrow screens) overlay the badge on the name plate. */
  compact?: boolean;
  /** Clicking the desk (the character at it) toggles the recent-actions card (plan §11.7). */
  onToggleCard?: (id: string) => void;
  /** The card is open for this desk. */
  cardOpen?: boolean;
  /** Draw the card as a popover under the desk (wide screens); false when Stage shows a Sheet. */
  cardInline?: boolean;
}

/** States whose bubble text is typed out like the prototype's `say()`. */
const TYPED_STATES: ReadonlySet<string> = new Set(["working", "hello", "reject", "waiting"]);

function ModelBadge({ view }: { view: DeskView }) {
  const llm = view.agent?.effective_llm;
  if (!llm) return null;
  const badge = modelBadge(llm);
  const title = badge.hint ? mockHint(badge.hint) : llm.model;
  return (
    <span
      className={styles.badge}
      data-model={badge.label}
      data-hint={badge.hint ?? undefined}
      title={title}
      aria-label={`模型：${badge.label}${badge.hint ? `（${mockHint(badge.hint)}）` : ""}`}
    >
      {badge.label}
    </span>
  );
}

export function Desk({ view, onRemove, compact = false, onToggleCard, cardOpen = false, cardInline = true }: DeskProps) {
  const { desk, agent } = view;
  const name = agent?.display_name ?? view.id;
  const status = statusText(view);
  const cls = [styles.desk, desk.state === "hello" ? styles.enter : "", desk.state === "leaving" ? styles.leave : ""]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={cls} data-desk={view.id} data-state={desk.state} data-compact={compact || undefined} data-pending-exit={desk.pendingExit || undefined} role="group" aria-label={`${name}：${status}`}>
      <Bubble deskId={view.id} state={desk.state} text={bubbleText(view)} typed={TYPED_STATES.has(desk.state)} />
      <div className={styles.tail} />
      {view.gate && <GateControls gate={view.gate} />}
      {onToggleCard ? (
        <button
          type="button"
          className={styles.deskBtn}
          aria-label={`看 ${name} 最近的動作`}
          aria-expanded={cardOpen}
          aria-controls={cardOpen && cardInline ? `desk-card-${view.id}` : undefined}
          aria-haspopup="dialog"
          data-desk-button={view.id}
          onClick={() => onToggleCard(view.id)}
        >
          <Avatar look={agent?.look ?? "cap"} color={agentColor(view.id)} state={desk.state} />
        </button>
      ) : (
        <Avatar look={agent?.look ?? "cap"} color={agentColor(view.id)} state={desk.state} />
      )}
      <div className={styles.plate}>
        <b>{name}</b>
        <ModelBadge view={view} />
        {onRemove && (
          <button type="button" className={styles.x} aria-label={`請 ${name} 離開`} onClick={() => onRemove(view.id)}>
            ×
          </button>
        )}
      </div>
      <div className={styles.status}>{status}</div>
      <span className={styles.spot} data-spot={view.id} />
      {cardOpen && cardInline && onToggleCard && <DeskCard deskId={view.id} name={name} onClose={() => onToggleCard(view.id)} />}
    </div>
  );
}
