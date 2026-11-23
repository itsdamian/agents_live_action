// Replay mode: lineup changes made while a task is in flight answer `next_task` (plan §7.1, D21)
// and only take effect once that task has finished, like the real server (the recorded
// custom-lineup replay shows `pipeline:pending` first, `agent:joined` + `pipeline:updated` after
// `task:completed`). The events to play later are kept per replay handle.

import type { ReplayHandle } from "../net/replay";
import type { ServerEvent } from "../types/events";

interface Deferred {
  taskId: string;
  events: ServerEvent[];
}

const store = new WeakMap<ReplayHandle, Deferred[]>();

export function deferUntilTaskEnds(handle: ReplayHandle, taskId: string, events: ServerEvent[]): void {
  store.set(handle, [...(store.get(handle) ?? []), { taskId, events }]);
}

export function hasDeferred(handle: ReplayHandle): boolean {
  return (store.get(handle)?.length ?? 0) > 0;
}

/** Remove and return, in order, the events waiting on tasks for which `ended(taskId)` is true. */
export function takeDeferred(handle: ReplayHandle, ended: (taskId: string) => boolean): ServerEvent[] {
  const list = store.get(handle) ?? [];
  const ready = list.filter((d) => ended(d.taskId));
  if (ready.length === 0) return [];
  store.set(handle, list.filter((d) => !ended(d.taskId)));
  return ready.flatMap((d) => d.events);
}
