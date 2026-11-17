// Toasts: transient messages in a fixed live region. Errors use role="alert" (announced at once),
// the rest role="status". Each toast removes itself after `ttlMs` (0 = stays until dismissed).

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import ui from "../../styles/ui.module.css";
import styles from "./Toasts.module.css";

export type ToastKind = "info" | "success" | "warning" | "error";

export interface ToastInput {
  kind?: ToastKind;
  text: string;
  /** An optional button on the toast (e.g. "看詳情"). */
  action?: { label: string; onClick: () => void };
  ttlMs?: number;
}

interface Toast extends ToastInput {
  id: number;
  kind: ToastKind;
}

export interface ToastApi {
  push(toast: ToastInput): number;
  dismiss(id: number): void;
}

const ToastContext = createContext<ToastApi | null>(null);

export const DEFAULT_TTL_MS = 5000;
export const ERROR_TTL_MS = 8000;
const MAX_VISIBLE = 4;

export function useToasts(): ToastApi {
  const api = useContext(ToastContext);
  if (!api) throw new Error("useToasts must be used inside <ToastProvider>");
  return api;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: number) => {
    const t = timers.current.get(id);
    if (t) clearTimeout(t);
    timers.current.delete(id);
    setToasts((list) => list.filter((x) => x.id !== id));
  }, []);

  const push = useCallback(
    (input: ToastInput) => {
      const id = ++seq.current;
      const kind = input.kind ?? "info";
      setToasts((list) => [...list, { ...input, id, kind }].slice(-MAX_VISIBLE));
      const ttl = input.ttlMs ?? (kind === "error" ? ERROR_TTL_MS : DEFAULT_TTL_MS);
      if (ttl > 0) timers.current.set(id, setTimeout(() => dismiss(id), ttl));
      return id;
    },
    [dismiss],
  );

  useEffect(() => {
    const map = timers.current;
    return () => map.forEach((t) => clearTimeout(t));
  }, []);

  const api = useMemo(() => ({ push, dismiss }), [push, dismiss]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className={styles.region} aria-label="通知">
        {toasts.map((t) => (
          <div key={t.id} className={styles.toast} data-kind={t.kind} role={t.kind === "error" ? "alert" : "status"}>
            <span className={styles.text}>{t.text}</span>
            {t.action && (
              <button type="button" className={`${ui.btn} ${ui.small}`} onClick={() => { t.action!.onClick(); dismiss(t.id); }}>
                {t.action.label}
              </button>
            )}
            <button type="button" className={styles.close} aria-label="關閉通知" onClick={() => dismiss(t.id)}>
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
