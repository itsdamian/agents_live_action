// The small card a desk opens (plan §11.7: "點桌子開小卡顯示最近 5 筆動作"). Wider than 640 px it
// pops up under the desk; at 640 px and below Stage shows the same list in a full-screen Sheet.
// Escape (or 關閉, or a click elsewhere) closes the popover and focus goes back to the desk.

import { useEffect, useId, useRef, type KeyboardEvent } from "react";
import { useFocusOnOpen } from "../../hooks/useFocusTrap";
import { clockTime, recentText } from "../../lib/recentText";
import { useFactoryState } from "../../state/FactoryProvider";
import ui from "../../styles/ui.module.css";
import styles from "./DeskCard.module.css";

/** The list itself (shared by the popover and the phone Sheet). */
export function RecentList({ deskId }: { deskId: string }) {
  const state = useFactoryState();
  const list = state.recent[deskId] ?? [];
  const nameOf = (id: string) => state.agents[id]?.display_name ?? id;
  if (list.length === 0) {
    return <p className={styles.empty}>還沒有動作。打開這個頁面之後的動作才會記在這裡。</p>;
  }
  return (
    <ol className={styles.list} aria-label="最近的動作（新的在上）">
      {list.map((a, i) => (
        <li key={`${a.at}-${i}`} className={styles.item} data-kind={a.kind}>
          <time className={styles.time} dateTime={new Date(a.at).toISOString()}>{clockTime(a.at)}</time>
          <span className={styles.text}>{recentText(a, nameOf)}</span>
        </li>
      ))}
    </ol>
  );
}

export function deskCardTitle(name: string): string {
  return `${name} 最近的動作`;
}

export interface DeskCardProps {
  deskId: string;
  name: string;
  onClose: () => void;
}

export function DeskCard({ deskId, name, onClose }: DeskCardProps) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useFocusOnOpen(ref);

  // A press anywhere outside this desk closes the card (the desk's own button toggles it).
  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      const target = e.target instanceof Element ? e.target : null;
      if (target?.closest(`[data-desk="${CSS.escape(deskId)}"]`)) return;
      onClose();
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [deskId, onClose]);

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
    }
  };

  return (
    <div ref={ref} id={`desk-card-${deskId}`} className={styles.card} role="dialog" aria-labelledby={titleId} tabIndex={-1} onKeyDown={onKeyDown} data-desk-card={deskId}>
      <div className={styles.head}>
        <h3 id={titleId} className={styles.title}>{deskCardTitle(name)}</h3>
        <button type="button" className={`${ui.btn} ${ui.small}`} onClick={onClose}>關閉</button>
      </div>
      <RecentList deskId={deskId} />
    </div>
  );
}
