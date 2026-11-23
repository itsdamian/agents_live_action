// Replay mode: plays the lineup changes that were waiting for a task (see lib/replayDeferred.ts)
// once that task is done or failed.

import { useEffect } from "react";
import { hasDeferred, takeDeferred } from "../lib/replayDeferred";
import { useFactoryEnv, useFactoryState } from "../state/FactoryProvider";

export function useReplayDeferred(): void {
  const env = useFactoryEnv();
  const state = useFactoryState();
  useEffect(() => {
    const handle = env.replay;
    if (env.mode !== "replay" || !handle || !hasDeferred(handle)) return;
    const ended = (taskId: string) => {
      const status = state.tasks[taskId]?.status;
      return status === undefined || status === "done" || status === "failed";
    };
    takeDeferred(handle, ended).forEach((e) => handle.emit(e));
  }, [env.mode, env.replay, state.tasks]);
}
