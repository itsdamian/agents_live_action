// Critical DevOps alerts pop a toast (plan §11.7). Alerts that were already in the snapshot,
// and lower levels, only light the dot on the 機房 button.

import { useEffect, useRef } from "react";
import { usePanels } from "../components/panels/PanelsContext";
import { useToasts } from "../components/panels/Toasts";
import { useFactoryState } from "../state/FactoryProvider";

export function useAlertToasts(): void {
  const state = useFactoryState();
  const toasts = useToasts();
  const panels = usePanels();
  const lastSeen = useRef<number | null>(null);

  useEffect(() => {
    if (!state.ready) return;
    const latest = state.alertSeq;
    // First time with a snapshot: everything up to now is history, not news.
    if (lastSeen.current === null || latest < lastSeen.current) {
      lastSeen.current = latest;
      return;
    }
    for (const alert of state.alerts) {
      if (alert.id <= lastSeen.current) continue;
      if (alert.level === "critical") {
        toasts.push({
          kind: "error",
          text: `機房警示：${alert.message}`,
          ttlMs: 0,
          action: { label: "看機房", onClick: () => panels.open({ kind: "devops" }) },
        });
      }
    }
    lastSeen.current = latest;
  }, [state.ready, state.alerts, state.alertSeq, toasts, panels]);
}
