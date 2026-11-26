// Lineup actions shared by the stage (×), the bench and the add-agent form.
// Live mode calls the REST API (plan §7.1); replay mode plays the events the server would send.

import { useCallback, useState } from "react";
import { ApiRequestError } from "../net/api";
import { useToasts } from "../components/panels/Toasts";
import { fakeCreateAgent, fakeDeleteAgent, fakeSetSeats, finalSeats, MAIN_PIPELINE, pendingEvent, type NewAgentInput } from "../lib/fakeServer";
import { deferUntilTaskEnds } from "../lib/replayDeferred";
import { selectCurrentTaskId } from "../state/selectors";
import { validateSeats } from "../lib/validation";
import { useFactoryEnv, useFactoryState } from "../state/FactoryProvider";
import type { Effective } from "../types/api";
import type { ServerEvent } from "../types/events";

export interface ActionResult {
  ok: boolean;
  /** Readable failure (server `detail` or a local reason). */
  message?: string;
  /** Server's `errors` list (strings only), shown under the form. */
  errors?: string[];
  effective?: Effective;
}

export interface NewAgentRequestInput extends NewAgentInput {
  prompt: string;
}

const NEXT_TASK_TEXT = "編制已排好，下一張任務開始時生效。";

/** One server `errors` item as a line: a string, or the backend's `{field?, message}`. */
function errorLine(e: unknown): string | null {
  if (typeof e === "string") return e;
  if (typeof e === "object" && e !== null && typeof (e as { message?: unknown }).message === "string") {
    return (e as { message: string }).message;
  }
  return null;
}

function failure(err: unknown): ActionResult {
  if (err instanceof ApiRequestError) {
    return { ok: false, message: err.detail, errors: err.errors.map(errorLine).filter((e): e is string => e !== null && e !== "") };
  }
  return { ok: false, message: "送出失敗，請再試一次。" };
}

export function useLineup() {
  const env = useFactoryEnv();
  const state = useFactoryState();
  const toasts = useToasts();
  const [busy, setBusy] = useState(false);
  const project = env.projectId ?? state.projectId;
  const mainSeats = state.pipelines.find((p) => p.id === MAIN_PIPELINE)?.seats ?? state.pipelines[0]?.seats ?? [];

  const play = (events: Parameters<NonNullable<typeof env.replay>["emit"]>[0][]): boolean => {
    if (!env.replay) {
      toasts.push({ kind: "error", text: "回放還沒準備好。" });
      return false;
    }
    events.forEach((e) => env.replay!.emit(e));
    return true;
  };

  /** Replay's answer to a lineup change: at once when idle, else 202 next_task like the server. */
  const replayAnswer = (events: ServerEvent[]): ActionResult => {
    const taskId = selectCurrentTaskId(state);
    if (!taskId || !env.replay) {
      return play(events) ? { ok: true, effective: "now" } : { ok: false, message: "回放還沒準備好。" };
    }
    play([pendingEvent(finalSeats(events), taskId)]);
    deferUntilTaskEnds(env.replay, taskId, events);
    return { ok: true, effective: "next_task" };
  };

  const run = useCallback(
    async (call: () => Promise<ActionResult>): Promise<ActionResult> => {
      setBusy(true);
      try {
        return await call();
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  /** `onStage`: the change makes a desk arrive or leave now, which LiveAnnouncer already speaks. */
  const notify = (r: ActionResult, doneText: string, onStage = true) => {
    if (!r.ok) toasts.push({ kind: "error", text: r.message ?? "操作失敗。" });
    else if (r.effective === "next_task") toasts.push({ kind: "info", text: NEXT_TASK_TEXT });
    else toasts.push({ kind: "success", text: doneText, silent: onStage });
  };

  /** Create an agent and seat it (`POST /agents`). The caller shows field errors. */
  const createAgent = (input: NewAgentRequestInput): Promise<ActionResult> =>
    run(async () => {
      let result: ActionResult;
      if (env.mode === "replay") {
        try {
          result = replayAnswer(fakeCreateAgent(state, input).events);
        } catch (err) {
          result = failure(err);
        }
      } else if (!project) {
        result = { ok: false, message: "還沒有連上專案。" };
      } else {
        try {
          const res = await env.api.createAgent(project, {
            display_name: input.displayName,
            duty: input.duty,
            prompt: input.prompt,
            kind: input.kind,
            look: input.look,
            llm: input.llm,
            after: input.after,
          });
          result = { ok: true, effective: res.effective };
        } catch (err) {
          result = failure(err);
        }
      }
      if (result.ok) notify(result, `${input.displayName} 報到了。`);
      return result;
    });

  /** Replace the main lineup (`PUT /pipelines/main`), after checking it locally. */
  const setSeats = (seats: string[], doneText: string): Promise<ActionResult> =>
    run(async () => {
      const problem = validateSeats(seats, state.agents);
      let result: ActionResult;
      if (problem) {
        result = { ok: false, message: problem };
      } else if (env.mode === "replay") {
        result = replayAnswer(fakeSetSeats(state, seats));
      } else if (!project) {
        result = { ok: false, message: "還沒有連上專案。" };
      } else {
        try {
          const res = await env.api.putPipeline(project, MAIN_PIPELINE, { seats });
          result = { ok: true, effective: res.effective };
        } catch (err) {
          result = failure(err);
        }
      }
      notify(result, doneText);
      return result;
    });

  const removeSeat = (id: string) => {
    const name = state.agents[id]?.display_name ?? id;
    return setSeats(mainSeats.filter((s) => s !== id), `${name} 離開座位了。`);
  };

  const seatBack = (id: string) => {
    const name = state.agents[id]?.display_name ?? id;
    return setSeats([...mainSeats.filter((s) => s !== id), id], `${name} 回座了。`);
  };

  /** Delete a custom agent (`DELETE /agents/{id}`). */
  const deleteAgent = (id: string): Promise<ActionResult> =>
    run(async () => {
      const agent = state.agents[id];
      const name = agent?.display_name ?? id;
      let result: ActionResult;
      if (agent?.builtin) {
        result = { ok: false, message: "內建同事不能刪除。" };
      } else if (env.mode === "replay") {
        result = replayAnswer(fakeDeleteAgent(state, id));
      } else if (!project) {
        result = { ok: false, message: "還沒有連上專案。" };
      } else {
        try {
          const res = await env.api.deleteAgent(project, id);
          result = { ok: true, effective: res.effective };
        } catch (err) {
          result = failure(err);
        }
      }
      // Deleting someone on the bench changes no desk, so only the toast can say it.
      notify(result, `${name} 已刪除。`, id in state.desks);
      return result;
    });

  return { busy, mainSeats, createAgent, removeSeat, seatBack, deleteAgent };
}
