// One desk: bubble, character, name plate with model badge, status line, and the `spot`
// where notes land (the note layer measures it via data-spot).

import { agentColor } from "../../lib/colors";
import { bubbleText, mockHint, statusText } from "../../lib/bubble";
import { modelBadge, type DeskView } from "../../state/selectors";
import { Avatar } from "./Avatar";
import styles from "./Desk.module.css";

export interface DeskProps {
  view: DeskView;
  /** Shows the "×" on the name plate (wired to `PUT /pipelines/main` in F8). */
  onRemove?: (id: string) => void;
  /** Compact floors (many seats / narrow screens) overlay the badge on the name plate. */
  compact?: boolean;
}

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

export function Desk({ view, onRemove, compact = false }: DeskProps) {
  const { desk, agent } = view;
  const name = agent?.display_name ?? view.id;
  const status = statusText(view);
  const cls = [styles.desk, desk.state === "hello" ? styles.enter : "", desk.state === "leaving" ? styles.leave : ""]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={cls} data-desk={view.id} data-state={desk.state} data-compact={compact || undefined} data-pending-exit={desk.pendingExit || undefined} role="group" aria-label={`${name}：${status}`}>
      <div className={styles.bubble}>
        <span className={styles.bubbleText}>{bubbleText(view)}</span>
      </div>
      <div className={styles.tail} />
      <Avatar look={agent?.look ?? "cap"} color={agentColor(view.id)} state={desk.state} />
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
    </div>
  );
}
