// Shown while a lineup change waits for the running task to finish (plan D17: 202 / `pipeline:pending`).

import { useFactoryState } from "../../state/FactoryProvider";
import styles from "./PendingNotice.module.css";

export function PendingNotice() {
  const state = useFactoryState();
  const pending = Object.entries(state.pendingPipelines);
  if (pending.length === 0) return null;
  const taskIds = [...new Set(pending.map(([, p]) => p.taskId))];
  return (
    <p className={styles.notice} role="status" data-testid="pending-notice">
      編制有變更，等 {taskIds.join("、")} 做完，下一張任務開始時生效。
    </p>
  );
}
