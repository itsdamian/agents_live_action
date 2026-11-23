// A panel container (plan §11.5). Wider than 640 px it is an ordinary inline panel with a 關閉
// button. At 640 px and below it becomes a full-screen modal with a back button, locked focus and
// a locked page scroll. Escape closes it in both cases.

import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from "react";
import { useFocusOnOpen, useFocusTrap } from "../../hooks/useFocusTrap";
import { useViewportWidth } from "../../hooks/useViewport";
import ui from "../../styles/ui.module.css";
import styles from "./Sheet.module.css";

export const SHEET_BREAKPOINT = 640;

export interface SheetProps {
  title: string;
  onClose: () => void;
  children: ReactNode;
}

export function Sheet({ title, onClose, children }: SheetProps) {
  const width = useViewportWidth();
  const full = width <= SHEET_BREAKPOINT;
  return full ? (
    <FullScreen title={title} onClose={onClose}>{children}</FullScreen>
  ) : (
    <Inline title={title} onClose={onClose}>{children}</Inline>
  );
}

function Inline({ title, onClose, children }: SheetProps) {
  const titleId = useId();
  const ref = useRef<HTMLElement>(null);
  useFocusOnOpen(ref);
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
    }
  };
  return (
    <section ref={ref} tabIndex={-1} className={`${ui.panel} ${styles.inline}`} aria-labelledby={titleId} data-sheet="inline" onKeyDown={onKeyDown}>
      <div className={styles.head}>
        <h2 id={titleId}>{title}</h2>
        <button type="button" className={ui.btn} onClick={onClose}>關閉</button>
      </div>
      {children}
    </section>
  );
}

function FullScreen({ title, onClose, children }: SheetProps) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useFocusTrap(ref, { onEscape: onClose });

  // The page behind must not scroll while the sheet covers it.
  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, []);

  return (
    <div ref={ref} className={styles.full} role="dialog" aria-modal="true" aria-labelledby={titleId} data-sheet="full">
      <div className={styles.fullHead}>
        <button type="button" className={ui.btn} onClick={onClose} aria-label="返回">← 返回</button>
        <h2 id={titleId}>{title}</h2>
      </div>
      <div className={styles.fullBody}>{children}</div>
    </div>
  );
}
