// A small modal confirmation. Focus starts on the cancel button (the safe choice), Tab stays
// inside, Escape cancels.

import { useId, useRef, type ReactNode } from "react";
import { useFocusTrap } from "../../hooks/useFocusTrap";
import ui from "../../styles/ui.module.css";
import styles from "./ConfirmDialog.module.css";

export interface ConfirmDialogProps {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({ title, children, confirmLabel, cancelLabel = "取消", onConfirm, onCancel }: ConfirmDialogProps) {
  const ref = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const bodyId = useId();
  useFocusTrap(ref, { onEscape: onCancel, initialFocus: cancelRef });
  return (
    <div className={styles.backdrop}>
      <div ref={ref} className={`${ui.panel} ${styles.dialog}`} role="alertdialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={bodyId}>
        <h2 id={titleId}>{title}</h2>
        <div id={bodyId} className={styles.body}>{children}</div>
        <div className={ui.actions}>
          <button ref={cancelRef} type="button" className={ui.btn} onClick={onCancel}>{cancelLabel}</button>
          <button type="button" className={`${ui.btn} ${ui.primary}`} onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}
