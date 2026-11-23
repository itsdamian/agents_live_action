// The bench: agents that exist but sit in no lineup. They can take their seat again ("回座",
// at the end of the line) or, if custom, be deleted for good (after an inline confirmation).

import { useState } from "react";
import { useLineup } from "../../hooks/useLineup";
import { modelBadge, selectBench } from "../../state/selectors";
import { LIMITS } from "../../lib/validation";
import { useFactoryState } from "../../state/FactoryProvider";
import ui from "../../styles/ui.module.css";
import styles from "./Bench.module.css";

export function Bench() {
  const state = useFactoryState();
  const lineup = useLineup();
  const [confirming, setConfirming] = useState<string | null>(null);
  const bench = selectBench(state);
  if (bench.length === 0) return null;
  const full = lineup.mainSeats.length >= LIMITS.seatsPerLine;

  return (
    <section className={`${ui.panel} ${styles.bench}`} aria-label="板凳">
      <h2>板凳</h2>
      <p className={ui.hint}>還沒入座的同事。</p>
      <ul className={styles.list}>
        {bench.map((a) => {
          const badge = modelBadge(a.effective_llm);
          return (
            <li key={a.id} className={styles.item} data-agent={a.id}>
              <span className={styles.name}>
                <b>{a.display_name}</b>
                <span className={styles.badge} data-model={badge.label}>{badge.label}</span>
              </span>
              <span className={styles.duty}>{a.duty}</span>
              <span className={styles.buttons}>
                <button type="button" className={`${ui.btn} ${ui.small}`} disabled={lineup.busy || full} title={full ? `這條線已經有 ${LIMITS.seatsPerLine} 席` : undefined} onClick={() => void lineup.seatBack(a.id)}>
                  回座
                </button>
                {!a.builtin &&
                  (confirming === a.id ? (
                    <>
                      <button type="button" className={`${ui.btn} ${ui.small} ${ui.danger}`} disabled={lineup.busy} onClick={() => { setConfirming(null); void lineup.deleteAgent(a.id); }}>
                        確定刪除
                      </button>
                      <button type="button" className={`${ui.btn} ${ui.small}`} onClick={() => setConfirming(null)}>
                        不刪了
                      </button>
                    </>
                  ) : (
                    <button type="button" className={`${ui.btn} ${ui.small} ${ui.danger}`} aria-label={`刪除 ${a.display_name}`} onClick={() => setConfirming(a.id)}>
                      刪除
                    </button>
                  ))}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
