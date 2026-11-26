// Counters under the header (plan §11.7 "Manager's Office、footer 計數"): done, reworks,
// in flight and today's spend.

import { formatCost } from "../../lib/format";
import { useFactoryState } from "../../state/FactoryProvider";
import { selectStats } from "../../state/selectors";
import ui from "../../styles/ui.module.css";
import styles from "./StatsBar.module.css";

export interface StatsBarProps {
  /** Makes "進行中" a button that opens the running task (F9b). */
  onOpenInFlight?: () => void;
}

export function StatsBar({ onOpenInFlight }: StatsBarProps) {
  const state = useFactoryState();
  const stats = selectStats(state);
  const inFlight = (
    <>
      <span className={styles.label}>進行中</span>
      <b className={styles.value} data-testid="stat-in-flight">{stats.inFlight}</b>
    </>
  );
  return (
    <div className={styles.bar} role="group" aria-label="統計">
      <ul className={styles.list}>
        <li className={styles.item}><span className={styles.label}>完成</span><b className={styles.value} data-testid="stat-completed">{stats.completed}</b></li>
        <li className={styles.item}><span className={styles.label}>重做</span><b className={styles.value} data-testid="stat-reworks">{stats.reworks}</b></li>
        {onOpenInFlight ? (
          <li>
            <button type="button" className={`${styles.item} ${styles.link}`} disabled={stats.inFlight === 0} onClick={onOpenInFlight} aria-label={`進行中 ${stats.inFlight}，看目前任務`}>
              {inFlight}
            </button>
          </li>
        ) : (
          <li className={styles.item}>{inFlight}</li>
        )}
        <li className={styles.item}><span className={styles.label}>今日花費</span><b className={styles.value} data-testid="stat-cost">{formatCost(stats.dailyCost)}</b></li>
      </ul>
      {state.usage.budgetPaused && (
        <p className={`${ui.err} ${styles.paused}`} role="status" data-testid="budget-paused">
          預算已達上限，暫停派發新任務。
        </p>
      )}
    </div>
  );
}
