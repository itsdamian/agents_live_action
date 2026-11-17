// "可以" / "重做" on the desk that holds a note at an approval gate (plan §11.7, AC10 f).
// Live mode calls the REST API and waits for the server's events; replay mode answers with fake
// events through the replay handle. While a request is out both buttons stay locked.

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ApiRequestError } from "../../net/api";
import { useFactoryEnv, useFactoryState } from "../../state/FactoryProvider";
import type { Gate } from "../../state/reducer";
import type { AgentView, ServerEvent, TaskSummary } from "../../types/events";
import styles from "./GateControls.module.css";

export const STALE_GATE_TEXT = "這張已經不在等你了";
/** Unlock fallback if the server accepted the decision but its event never arrives. */
export const UNLOCK_AFTER_MS = 10_000;

/** The nearest make seat before `station` (the backend's `prev_maker`); the first seat as a fallback. */
export function reworkTarget(seatIds: string[], agents: Record<string, AgentView>, station: string): string {
  const i = seatIds.indexOf(station);
  for (let j = i - 1; j >= 0; j--) {
    if (agents[seatIds[j]!]?.kind === "make") return seatIds[j]!;
  }
  return seatIds[0] ?? station;
}

export interface GateControlsProps {
  gate: Gate;
}

export function GateControls({ gate }: GateControlsProps) {
  const env = useFactoryEnv();
  const state = useFactoryState();
  const [mode, setMode] = useState<"choose" | "note">("choose");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const noteRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (mode === "note") noteRef.current?.focus();
  }, [mode]);

  // The gate is a different gate now (or gone): start fresh.
  useEffect(() => {
    setMode("choose");
    setNote("");
    setBusy(false);
    setError(null);
  }, [gate.taskId, gate.station]);

  useEffect(() => {
    if (!busy) return;
    const id = setTimeout(() => setBusy(false), UNLOCK_AFTER_MS);
    return () => clearTimeout(id);
  }, [busy]);

  const task = state.tasks[gate.taskId];
  const summary = (status: TaskSummary["status"], rework = 0): TaskSummary => ({
    id: gate.taskId,
    title: gate.title,
    priority: task?.priority ?? "medium",
    status,
    rework_count: (task?.rework_count ?? 0) + rework,
  });

  const fake = (decision: "approve" | "reject", text: string) => {
    const replay = env.replay;
    if (!replay) return;
    const events: ServerEvent[] =
      decision === "approve"
        ? [{ event: "task:approved", data: { task: summary("in_progress"), station: gate.station } }]
        : [
            { event: "task:rejected", data: { task: summary("in_progress"), station: gate.station, note: text } },
            {
              event: "station:update",
              data: {
                station: reworkTarget(Object.values(state.pipelines).flatMap((p) => p.seats), state.agents, gate.station),
                status: "rework",
                task: summary("in_progress", 1),
              },
            },
          ];
    events.forEach((e) => replay.emit(e));
  };

  const send = async (decision: "approve" | "reject", text = "") => {
    if (busy) return;
    setBusy(true);
    setError(null);
    if (env.mode === "replay") {
      fake(decision, text);
      return;
    }
    const project = env.projectId ?? state.projectId;
    if (!project) {
      setBusy(false);
      setError("還沒有連上專案");
      return;
    }
    try {
      if (decision === "approve") await env.api.approve(project, gate.taskId);
      else await env.api.reject(project, gate.taskId, text);
      // Stay locked: the server's task:approved / task:rejected event removes the gate.
    } catch (err) {
      setBusy(false);
      if (err instanceof ApiRequestError && err.status === 409) setError(STALE_GATE_TEXT);
      else setError(err instanceof ApiRequestError ? err.detail : "送出失敗，請再試一次");
    }
  };

  const submitNote = (e: FormEvent) => {
    e.preventDefault();
    void send("reject", note.trim());
  };
  const onNoteKey = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      setMode("choose");
      setError(null);
    }
  };

  return (
    <div className={styles.gate} data-gate-task={gate.taskId} aria-busy={busy || undefined}>
      {mode === "choose" ? (
        <div className={styles.row}>
          <button type="button" className={`${styles.btn} ${styles.yes}`} disabled={busy} onClick={() => void send("approve")}>
            可以
          </button>
          <button type="button" className={styles.btn} disabled={busy} onClick={() => setMode("note")}>
            重做
          </button>
        </div>
      ) : (
        <form className={styles.noteForm} onSubmit={submitNote} onKeyDown={onNoteKey}>
          <label className={styles.noteLabel}>
            <span>哪裡要改？（選填）</span>
            <input ref={noteRef} value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} disabled={busy} autoComplete="off" />
          </label>
          <div className={styles.row}>
            <button type="submit" className={styles.btn} disabled={busy}>送出重做</button>
            <button type="button" className={styles.btn} disabled={busy} onClick={() => { setMode("choose"); setError(null); }}>
              取消
            </button>
          </div>
        </form>
      )}
      {error && (
        <p role="alert" className={styles.err}>
          {error}
        </p>
      )}
    </div>
  );
}
