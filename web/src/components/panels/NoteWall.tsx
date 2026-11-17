// The note wall (plan D20): every task as a note, filterable by status, 50 per page.
// Notes of deleted agents still show their name and look, from the run's seat snapshot.

import { useState } from "react";
import { STATUS_LABELS } from "../../lib/approval";
import { agentColor } from "../../lib/colors";
import { formatCost, formatTime } from "../../lib/format";
import { useHistory } from "../../hooks/useHistory";
import { useFactoryState } from "../../state/FactoryProvider";
import { groupHistory } from "../../state/selectors";
import ui from "../../styles/ui.module.css";
import type { HistoryItem, SeatSnapshot } from "../../types/api";
import { TASK_STATUSES, type TaskStatus } from "../../types/events";
import { Avatar } from "../stage/Avatar";
import styles from "./NoteWall.module.css";
import { usePanels } from "./PanelsContext";

export const REASON_MAX = 40;

const DEFAULTS: Record<"inbox" | "outbox", TaskStatus[]> = {
  inbox: ["pending"],
  outbox: ["done", "failed"],
};

export function truncateReason(reason: string, max: number = REASON_MAX): string {
  const chars = Array.from(reason);
  return chars.length <= max ? reason : `${chars.slice(0, max).join("")}…`;
}

/** Stations in visit order, each once (a rework repeats stations in `path`). */
export function uniquePath(path: string[]): string[] {
  return [...new Set(path)];
}

export interface NoteWallProps {
  initial?: "inbox" | "outbox";
}

export function NoteWall({ initial = "outbox" }: NoteWallProps) {
  const state = useFactoryState();
  const panels = usePanels();
  const [statuses, setStatuses] = useState<TaskStatus[]>(DEFAULTS[initial]);
  const [pipeline, setPipeline] = useState<string | null>(null);
  const history = useHistory(statuses, pipeline);
  const counts = history.counts ?? state.historyCounts;
  const many = state.pipelines.length > 1;

  const toggle = (s: TaskStatus) =>
    setStatuses((cur) => {
      const next = cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s];
      return TASK_STATUSES.filter((x) => next.includes(x)); // stable order
    });

  const groups = many ? groupHistory(history.items, "pipeline") : [{ key: "", items: history.items }];

  return (
    <div className={styles.wall}>
      <div role="group" aria-label="狀態篩選" className={styles.filters}>
        {TASK_STATUSES.map((s) => (
          <button key={s} type="button" className={styles.chip} aria-pressed={statuses.includes(s)} onClick={() => toggle(s)}>
            {STATUS_LABELS[s]} <span className={styles.count}>{counts[s]}</span>
          </button>
        ))}
      </div>
      {many && (
        <label className={styles.pipeline}>
          <span>樓層</span>
          <select className={ui.input} value={pipeline ?? ""} onChange={(e) => setPipeline(e.target.value || null)}>
            <option value="">全部</option>
            {state.pipelines.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </label>
      )}
      <p className={ui.hint} role="status" aria-live="polite">
        {history.loading && history.items.length === 0 ? "載入中…" : `已載入 ${history.items.length} / 共 ${history.total} 張`}
      </p>
      {history.error && (
        <div role="alert">
          <p className={ui.err}>{history.error}</p>
          <button type="button" className={ui.btn} onClick={history.retry}>重試</button>
        </div>
      )}
      {!history.loading && !history.error && history.items.length === 0 && <p className={ui.hint}>這個篩選下沒有便條。</p>}
      {groups.map((g) => (
        <section key={g.key} aria-label={g.key ? `樓層 ${state.pipelines.find((p) => p.id === g.key)?.name ?? g.key}` : undefined}>
          {g.key && <h3 className={styles.floor}>{state.pipelines.find((p) => p.id === g.key)?.name ?? g.key}</h3>}
          <ul className={styles.list}>
            {g.items.map((item) => (
              <li key={item.task_id}>
                <NoteCard item={item} onOpen={() => panels.open({ kind: "task-detail", taskId: item.task_id })} />
              </li>
            ))}
          </ul>
        </section>
      ))}
      {history.hasMore && (
        <button type="button" className={ui.btn} disabled={history.loading} onClick={history.loadMore}>
          載入更多
        </button>
      )}
    </div>
  );
}

function NoteCard({ item, onOpen }: { item: HistoryItem; onOpen: () => void }) {
  const state = useFactoryState();
  const seatById = new Map<string, SeatSnapshot>(item.seats.map((s) => [s.id, s]));
  const stations = uniquePath(item.path);
  const tone = item.status === "failed" ? "bad" : item.status === "done" ? "ok" : "normal";
  return (
    <button type="button" className={styles.card} data-status={item.status} data-tone={tone} data-task={item.task_id} onClick={onOpen}>
      <span className={styles.head}>
        <b className={styles.title}>{item.title}</b>
        <span className={styles.status}>{STATUS_LABELS[item.status]}</span>
      </span>
      {stations.length > 0 && (
        <span className={styles.crew} aria-label={`經過：${stations.map((id) => seatById.get(id)?.display_name ?? id).join("、")}`}>
          {stations.map((id) => {
            const seat = seatById.get(id);
            const gone = !state.agents[id];
            return (
              <span key={id} className={styles.mini} data-agent={id} data-gone={gone || undefined} title={`${seat?.display_name ?? id}${gone ? "（已離開）" : ""}`}>
                <Avatar look={seat?.look ?? "cap"} color={agentColor(id)} state="still" />
                <span className={styles.miniName}>{seat?.display_name ?? id}{gone ? "（已離開）" : ""}</span>
              </span>
            );
          })}
        </span>
      )}
      <span className={styles.meta}>
        {item.rework_count > 0 && <span className={styles.rework}>重做 {item.rework_count}</span>}
        {item.cost_usd !== null && item.cost_usd > 0 && <span>{formatCost(item.cost_usd)}</span>}
        {item.finished_at && <span>{formatTime(item.finished_at)}</span>}
        {item.stale && <span className={styles.stale}>狀態可能已過期</span>}
      </span>
      {item.reason && <span className={styles.reason}>{truncateReason(item.reason)}</span>}
    </button>
  );
}
