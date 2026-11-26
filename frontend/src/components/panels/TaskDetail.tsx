// One task in full (plan §11.7 "任務詳情"): overview with the approval controls, agent logs,
// deliverable downloads, each agent's output and the execution history (D20).
// Server text (logs, outputs, notes) is always rendered as text nodes.

import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { approvalLabel, priorityLabel, STATUS_LABELS } from "../../lib/approval";
import { formatBytes, formatCost, formatTime } from "../../lib/format";
import { logAgentName, synthesizeTaskDetail } from "../../lib/taskDetail";
import { ApiRequestError } from "../../net/api";
import { useFactoryEnv, useFactoryState } from "../../state/FactoryProvider";
import ui from "../../styles/ui.module.css";
import type { TaskDetailResponse, TaskRun } from "../../types/api";
import type { AgentView } from "../../types/events";
import { GateControls } from "../stage/GateControls";
import styles from "./TaskDetail.module.css";

export const TABS = [
  { id: "overview", label: "概況" },
  { id: "logs", label: "日誌" },
  { id: "files", label: "交付檔" },
  { id: "outputs", label: "各 agent 產出" },
  { id: "runs", label: "執行紀錄" },
] as const;
type TabId = (typeof TABS)[number]["id"];

const OUTCOME = { done: "完成", failed: "失敗" } as const;
const STEP_KIND = { station: "站", rework: "重做", gate: "閘門" } as const;

export interface TaskDetailProps {
  taskId: string;
}

export function TaskDetail({ taskId }: TaskDetailProps) {
  const env = useFactoryEnv();
  const state = useFactoryState();
  const [tab, setTab] = useState<TabId>("overview");
  const [detail, setDetail] = useState<TaskDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const uid = useId();
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const project = env.projectId ?? state.projectId;
  const status = state.tasks[taskId]?.status;
  const replay = env.mode === "replay";

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (replay) {
        setDetail(synthesizeTaskDetail(state, taskId));
      } else if (project) {
        setDetail(await env.api.taskDetail(project, taskId));
      }
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.detail : "載入失敗，請再試一次。");
    } finally {
      setLoading(false);
    }
  }, [env.api, project, taskId, replay]);

  // Load on open, and again whenever the task moves to another status.
  useEffect(() => {
    void load();
  }, [load, status]);

  function onTabKey(e: KeyboardEvent, index: number) {
    const next = e.key === "ArrowRight" ? index + 1 : e.key === "ArrowLeft" ? index - 1 : e.key === "Home" ? 0 : e.key === "End" ? TABS.length - 1 : null;
    if (next === null) return;
    e.preventDefault();
    const i = (next + TABS.length) % TABS.length;
    setTab(TABS[i]!.id);
    tabRefs.current[i]?.focus();
  }

  const gate = state.awaiting[taskId];
  const title = detail?.task.title ?? state.tasks[taskId]?.title ?? taskId;

  return (
    <div className={styles.detail} data-task={taskId}>
      <h2 className={styles.title}>{title}</h2>
      <div role="tablist" aria-label="任務詳情" className={styles.tabs}>
        {TABS.map((t, i) => (
          <button
            key={t.id}
            ref={(el) => { tabRefs.current[i] = el; }}
            type="button"
            role="tab"
            id={`${uid}-tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`${uid}-panel-${t.id}`}
            tabIndex={tab === t.id ? 0 : -1}
            className={styles.tab}
            onClick={() => setTab(t.id)}
            onKeyDown={(e) => onTabKey(e, i)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`${uid}-panel-${tab}`} aria-labelledby={`${uid}-tab-${tab}`} className={styles.panel} tabIndex={0}>
        {error ? (
          <div role="alert">
            <p className={ui.err}>{error}</p>
            <button type="button" className={ui.btn} onClick={() => void load()}>重試</button>
          </div>
        ) : loading && !detail ? (
          <p className={ui.hint}>載入中…</p>
        ) : !detail ? (
          <p className={ui.hint}>找不到這張任務。</p>
        ) : (
          <>
            {tab === "overview" && <Overview detail={detail} replay={replay} gate={gate ? <GateControls gate={gate} /> : null} liveStatus={status} />}
            {tab === "logs" && <Logs detail={detail} agents={state.agents} />}
            {tab === "files" && <Files detail={detail} />}
            {tab === "outputs" && <Outputs detail={detail} />}
            {tab === "runs" && <Runs runs={detail.runs} />}
          </>
        )}
      </div>
    </div>
  );
}

function Overview({ detail, replay, gate, liveStatus }: { detail: TaskDetailResponse; replay: boolean; gate: React.ReactNode; liveStatus: keyof typeof STATUS_LABELS | undefined }) {
  const t = detail.task;
  const status = liveStatus ?? t.status;
  return (
    <div className={styles.overview}>
      <dl className={styles.facts}>
        <dt>狀態</dt><dd data-testid="detail-status">{STATUS_LABELS[status]}</dd>
        <dt>優先序</dt><dd>{priorityLabel(t.priority)}</dd>
        <dt>核准時機</dt><dd>{approvalLabel(t.approval)}</dd>
        <dt>重做次數</dt><dd>{t.rework_count}</dd>
        <dt>用量</dt>
        <dd>{detail.usage.input_tokens} 輸入 / {detail.usage.output_tokens} 輸出 tokens，{formatCost(detail.usage.cost)}（{detail.usage.call_count} 次呼叫）</dd>
        <dt>建立</dt><dd>{formatTime(t.created_at)}</dd>
      </dl>
      {t.description !== "" && <p className={styles.desc}>{t.description}</p>}
      {replay && <p className={ui.hint}>回放模式沒有後端，這裡只顯示摘要。</p>}
      {gate && (
        <div className={styles.gate} aria-label="核准">
          <p>這張在等你點頭：</p>
          {gate}
        </div>
      )}
      {detail.status_history.length > 0 && (
        <>
          <h3>狀態紀錄</h3>
          <ol className={styles.history}>
            {detail.status_history.map((h, i) => (
              <li key={i}>{formatTime(h.timestamp)}　{h.display_name ?? h.agent}　{h.status}</li>
            ))}
          </ol>
        </>
      )}
    </div>
  );
}

function Logs({ detail, agents }: { detail: TaskDetailResponse; agents: Record<string, AgentView> }) {
  if (detail.logs.length === 0) return <p className={ui.hint}>還沒有日誌。</p>;
  return (
    <ul className={styles.list}>
      {detail.logs.map((l) => (
        <li key={l.name} className={styles.card}>
          <div className={styles.cardHead}>
            <b>{l.display_name ?? logAgentName(agents, l.agent)}</b> <span>{l.status}</span> <span>{formatTime(l.timestamp)}</span> <span>{l.execution_time.toFixed(1)} 秒</span> <span>{formatBytes(l.size)}</span>
            <a href={l.content_url} target="_blank" rel="noopener noreferrer">原始檔</a>
          </div>
          <pre className={styles.pre}>{l.content}</pre>
        </li>
      ))}
    </ul>
  );
}

function Files({ detail }: { detail: TaskDetailResponse }) {
  if (detail.deliverables.length === 0) return <p className={ui.hint}>還沒有交付檔。</p>;
  return (
    <ul className={styles.list}>
      {detail.deliverables.map((f) => (
        <li key={f.name} className={styles.file}>
          <a href={f.download_url} download={f.name}>{f.name}</a>
          <span>{formatBytes(f.size)}</span>
        </li>
      ))}
    </ul>
  );
}

function Outputs({ detail }: { detail: TaskDetailResponse }) {
  if (detail.agent_outputs.length === 0) return <p className={ui.hint}>還沒有產出。</p>;
  return (
    <ul className={styles.list}>
      {detail.agent_outputs.map((o) => (
        <li key={o.station} className={styles.card}>
          <div className={styles.cardHead}><b>{o.display_name}</b></div>
          <pre className={styles.pre}>{o.content}</pre>
          {o.truncated && <p className={ui.hint}>內容太長，只顯示前 20,000 字。</p>}
        </li>
      ))}
    </ul>
  );
}

function Runs({ runs }: { runs: TaskRun[] }) {
  if (runs.length === 0) return <p className={ui.hint}>沒有執行紀錄。</p>;
  return (
    <ul className={styles.list}>
      {runs.map((r) => {
        const names = new Map(r.seats.map((s) => [s.id, s.display_name]));
        return (
          <li key={r.run_no} className={styles.card} data-run={r.run_no}>
            <div className={styles.cardHead}>
              <b>第 {r.run_no} 次</b>
              <span>{r.outcome ? OUTCOME[r.outcome] : "進行中"}</span>
              {r.imported && <span>（匯入的歷史）</span>}
              <span>重做 {r.rework_count} 次</span>
              <span>{formatCost(r.cost_usd)}</span>
              <span>{r.input_tokens + r.output_tokens} tokens</span>
            </div>
            <p className={ui.hint}>{formatTime(r.started_at)} → {formatTime(r.finished_at)}　座位：{r.seats.map((s) => s.display_name).join(" → ")}</p>
            {r.reason && <p className={ui.err}>{r.reason}</p>}
            {r.steps.length > 0 && (
              <ol className={styles.history}>
                {r.steps.map((s) => (
                  <li key={s.seq}>
                    {s.seq}. {STEP_KIND[s.kind]}　{names.get(s.station) ?? s.station}　{s.status}
                    {s.note ? `　${s.note}` : ""}　{formatTime(s.at)}
                  </li>
                ))}
              </ol>
            )}
          </li>
        );
      })}
    </ul>
  );
}
