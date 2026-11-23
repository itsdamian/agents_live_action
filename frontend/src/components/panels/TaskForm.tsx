// "派一張任務": title, description, priority and when the user wants to look at it (核准時機).

import { useId, useRef, useState, type FormEvent } from "react";
import { APPROVAL_OPTIONS, PRIORITY_OPTIONS } from "../../lib/approval";
import { ApiRequestError } from "../../net/api";
import { useFactoryEnv, useFactoryState } from "../../state/FactoryProvider";
import ui from "../../styles/ui.module.css";
import type { Approval, Priority, TaskSummary } from "../../types/events";
import { usePanels } from "./PanelsContext";
import { useToasts } from "./Toasts";

export const TITLE_MAX = 200;

export function TaskForm() {
  const env = useFactoryEnv();
  const state = useFactoryState();
  const toasts = useToasts();
  const panels = usePanels();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState<Priority>("medium");
  const [approval, setApproval] = useState<Approval>("none");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const titleRef = useRef<HTMLInputElement>(null);
  const uid = useId();
  const project = env.projectId ?? state.projectId;

  async function submit(e: FormEvent) {
    e.preventDefault();
    const name = title.trim();
    if (name === "") {
      setError("標題要填。");
      titleRef.current?.focus();
      return;
    }
    if (Array.from(name).length > TITLE_MAX) {
      setError(`標題最多 ${TITLE_MAX} 個字。`);
      titleRef.current?.focus();
      return;
    }
    setError(null);
    setBusy(true);
    try {
      let created: TaskSummary;
      if (env.mode === "replay") {
        if (!env.replay) throw new ApiRequestError(0, "回放還沒準備好。");
        const n = Object.keys(state.tasks).length + 1;
        created = { id: `task-${String(n).padStart(3, "0")}`, title: name, priority, status: "pending", rework_count: 0 };
        env.replay.emit({ event: "task:created", data: { task: created, queue_size: state.queueSize + 1 } });
      } else {
        if (!project) throw new ApiRequestError(0, "還沒有連上專案。");
        const res = await env.api.createTask(project, { title: name, description: description.trim(), priority, approval });
        created = res.task;
      }
      const id = created.id;
      toasts.push({ kind: "success", text: `任務「${name}」已派出。`, action: { label: "看詳情", onClick: () => panels.open({ kind: "task-detail", taskId: id }) } });
      panels.close();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.detail : "送出失敗，請再試一次。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className={ui.form} onSubmit={submit} noValidate aria-label="派一張任務">
      <label className={`${ui.field} ${ui.wide}`}>
        <span>標題</span>
        <input ref={titleRef} className={ui.input} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="加登入頁" autoComplete="off" aria-invalid={error ? true : undefined} />
      </label>
      <label className={`${ui.field} ${ui.wide}`}>
        <span>描述（選填）</span>
        <textarea className={ui.input} value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>
      <label className={ui.field}>
        <span>優先序</span>
        <select className={ui.input} value={priority} onChange={(e) => setPriority(e.target.value as Priority)}>
          {PRIORITY_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </label>
      <fieldset className={ui.fieldset}>
        <legend className={ui.legend}>核准時機</legend>
        <div className={ui.radios}>
          {APPROVAL_OPTIONS.map((o) => (
            <label key={o.value}>
              <input type="radio" name={`${uid}-approval`} value={o.value} checked={approval === o.value} onChange={() => setApproval(o.value)} /> {o.label}
            </label>
          ))}
        </div>
      </fieldset>
      <div className={`${ui.actions} ${ui.wide}`}>
        <button type="submit" className={`${ui.btn} ${ui.primary}`} disabled={busy}>派出任務</button>
        {error && <p className={ui.err} role="alert">{error}</p>}
      </div>
    </form>
  );
}
