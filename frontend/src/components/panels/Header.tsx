// The page header (plan §11.7): title, project select, [MOCK MODE] with its reason, connection
// state and the project-level model menu (D21).

import { useEffect, useRef, useState } from "react";
import { formatBudget } from "../../lib/format";
import { fakeSetProjectLlm } from "../../lib/fakeServer";
import { choiceLabel, fromSelectValue, llmOptions, toSelectValue } from "../../lib/llmChoice";
import { mockHint, mockReasonText } from "../../lib/bubble";
import { ApiRequestError } from "../../net/api";
import { useFactoryEnv, useFactoryState } from "../../state/FactoryProvider";
import { selectCurrentTaskId } from "../../state/selectors";
import ui from "../../styles/ui.module.css";
import type { LlmChoice } from "../../types/events";
import { ConfirmDialog } from "./ConfirmDialog";
import styles from "./Header.module.css";
import { useToasts } from "./Toasts";

export function connectionLabel(status: string, attempt: number, delay: number | null, reason: string | null, mode: "live" | "replay"): string {
  switch (status) {
    case "connecting":
      return "連線中…";
    case "open":
      return mode === "replay" ? "回放中" : "已連線";
    case "reconnecting":
      return `重新連線中…（第 ${attempt} 次${delay ? `，約 ${Math.round(delay / 1000)} 秒後` : ""}）`;
    case "closed":
      return "已中斷";
    default:
      return reason ? `連線失敗：${reason}` : "連線失敗";
  }
}

function ConnectionStatus() {
  const { connection } = useFactoryState();
  const env = useFactoryEnv();
  const text = connectionLabel(connection.status, connection.attempt, connection.delay, connection.reason, env.mode);
  // Not a live region: ConnectionBanner is the one place that tells a screen reader about the
  // connection (once per outage), so retries do not chatter.
  return (
    <span className={styles.conn} data-status={connection.status} data-testid="connection">
      <span className={styles.dot} aria-hidden="true" />
      {text}
    </span>
  );
}

function ProjectSelect() {
  const state = useFactoryState();
  const env = useFactoryEnv();
  const current = env.projectId ?? state.projectId;
  if (env.mode === "live" && env.projects.length > 0) {
    return (
      <label className={styles.inline}>
        <span>專案</span>
        <select className={`${ui.input} ${styles.select}`} value={current ?? ""} onChange={(e) => env.selectProject(e.target.value)}>
          {env.projects.map((p) => (
            <option key={p.id} value={p.id}>{p.name || p.id}</option>
          ))}
        </select>
      </label>
    );
  }
  return (
    <span className={styles.inline} data-testid="project-name">
      <span>專案</span>
      <b>{current ?? "—"}</b>
    </span>
  );
}

/** "[MOCK MODE]" plus the reason when the server is the cause (FORCE_MOCK or no API key). */
export function MockIndicator() {
  const state = useFactoryState();
  if (!state.mockMode) return null;
  const reason = state.forceMock ? "FORCE_MOCK 強制" : state.llm && !state.llm.anthropic_key_present ? "沒有 API 金鑰" : null;
  return (
    <span className={styles.mock} data-testid="mock-indicator" title="所有 agent 都用 Mock，不會產生費用">
      [MOCK MODE]{reason ? `（${reason}）` : ""}
    </span>
  );
}

function ModelMenu() {
  const state = useFactoryState();
  const env = useFactoryEnv();
  const toasts = useToasts();
  const [pendingChoice, setPendingChoice] = useState<LlmChoice | null>(null);
  const [nextTaskMark, setNextTaskMark] = useState(false);
  const finished = state.counters.completed + state.counters.failed;
  const markedAt = useRef(0);

  // The mark ends when the next task has run, or when the project model changes again.
  useEffect(() => {
    if (nextTaskMark && finished !== markedAt.current) setNextTaskMark(false);
  }, [finished, nextTaskMark]);

  const llm = state.llm;
  if (!llm) return null;
  const project = env.projectId ?? state.projectId;
  const options = llmOptions(llm.models, { withDefault: false });
  const current = toSelectValue(llm.project);
  // Why a Claude choice would still run as Mock on this server (null: it really would call Claude).
  const claudeFallback = llm.force_mock ? "force_mock" : !llm.anthropic_key_present ? "missing_key" : null;
  const realMockReason = llm.project !== "mock" ? claudeFallback : null;

  function applied(effective: "now" | "next_task") {
    if (effective === "next_task") {
      markedAt.current = finished;
      setNextTaskMark(true);
      toasts.push({ kind: "info", text: "模型已換，下一張任務開始時生效。" });
    } else {
      setNextTaskMark(false);
    }
  }

  async function apply(choice: LlmChoice) {
    if (env.mode === "replay") {
      if (!env.replay) {
        toasts.push({ kind: "error", text: "回放還沒準備好。" });
        return;
      }
      // Like the server: 202 next_task while a task is in flight (plan §7.1 PUT /settings).
      const effective = selectCurrentTaskId(state) ? "next_task" : "now";
      fakeSetProjectLlm(state, choice, effective).forEach((e) => env.replay!.emit(e));
      applied(effective);
      return;
    }
    if (!project) {
      toasts.push({ kind: "error", text: "還沒有連上專案。" });
      return;
    }
    try {
      const res = await env.api.putSettings(project, { llm: choice });
      applied(res.effective);
    } catch (err) {
      toasts.push({ kind: "error", text: err instanceof ApiRequestError ? err.detail : "切換模型失敗，請再試一次。" });
    }
  }

  function onChange(value: string) {
    const choice = fromSelectValue(value) ?? "mock";
    if (choice === "mock") void apply(choice);
    else setPendingChoice(choice);
  }

  const budget = formatBudget(state.usage.dailyBudgetUsd, state.usage.projectBudgetUsd);
  return (
    <>
      <label className={styles.inline}>
        <span>模型</span>
        <select className={`${ui.input} ${styles.select}`} value={current} onChange={(e) => onChange(e.target.value)}>
          {options.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </label>
      {nextTaskMark && <span className={styles.pending} data-testid="llm-pending">下一張開始</span>}
      {realMockReason && (
        <span className={styles.mock} data-testid="llm-mock-note">{mockHint(realMockReason)}</span>
      )}
      {pendingChoice && (
        <ConfirmDialog
          title={`切換到 ${choiceLabel(llm.models, pendingChoice)}？`}
          confirmLabel="確定切換"
          onCancel={() => setPendingChoice(null)}
          onConfirm={() => {
            const choice = pendingChoice;
            setPendingChoice(null);
            void apply(choice);
          }}
        >
          <p>{budget ? `會產生費用，目前預算上限：${budget}。` : "會產生費用。目前沒有設定預算上限，建議在後端設定 PROJECT_BUDGET_USD。"}</p>
          {claudeFallback && <p>不過伺服器目前會改用 Mock（{mockReasonText(claudeFallback)}），實際不會呼叫 Claude。</p>}
        </ConfirmDialog>
      )}
    </>
  );
}

export function Header() {
  return (
    <header className={styles.header}>
      <div className={styles.titleRow}>
        <h1>Agent 工廠現場</h1>
        <ConnectionStatus />
      </div>
      <div className={styles.bar}>
        <ProjectSelect />
        <MockIndicator />
        <ModelMenu />
      </div>
    </header>
  );
}
