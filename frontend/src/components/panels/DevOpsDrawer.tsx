// The machine room: the "機房" button (red dot for unseen alerts) and the alert list.

import { useEffect } from "react";
import { formatAlertValue, highestLevel, LEVEL_LABELS, metricLabel } from "../../lib/alerts";
import { useFactoryDispatch, useFactoryState } from "../../state/FactoryProvider";
import { selectUnseenAlerts } from "../../state/selectors";
import ui from "../../styles/ui.module.css";
import { usePanels } from "./PanelsContext";
import styles from "./DevOpsDrawer.module.css";

export function DevOpsButton() {
  const state = useFactoryState();
  const panels = usePanels();
  const unseen = selectUnseenAlerts(state);
  const level = highestLevel(state.alerts.slice(state.alertsSeen).map((a) => a.level));
  return (
    <button type="button" className={`${ui.btn} ${styles.button}`} aria-expanded={panels.panel?.kind === "devops"} onClick={() => panels.toggle({ kind: "devops" })}>
      機房
      {unseen > 0 && level && (
        <span className={styles.dot} data-level={level} data-testid="devops-dot">
          <span className={ui.srOnly}>（{unseen} 則新警示，最高：{LEVEL_LABELS[level]}）</span>
        </span>
      )}
    </button>
  );
}

export function DevOpsDrawer() {
  const state = useFactoryState();
  const dispatch = useFactoryDispatch();
  // Opening the drawer counts as having seen everything in it.
  useEffect(() => {
    dispatch({ type: "alerts_seen" });
  }, [dispatch, state.alerts.length]);

  const alerts = [...state.alerts].reverse();
  if (alerts.length === 0) return <p className={ui.hint}>目前沒有警示，機房一切正常。</p>;
  return (
    <ul className={styles.list} aria-label="警示">
      {alerts.map((a) => (
        <li key={a.id} className={styles.item} data-level={a.level}>
          <span className={styles.level}>{LEVEL_LABELS[a.level]}</span>
          <span className={styles.message}>{a.message}</span>
          <span className={styles.metric}>{metricLabel(a.metric)} {formatAlertValue(a.metric, a.value)}</span>
        </li>
      ))}
    </ul>
  );
}
