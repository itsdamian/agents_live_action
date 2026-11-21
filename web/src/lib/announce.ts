// Screen-reader announcements for the stage (aria-live). The stage is mostly animation, so the
// moments that need a person get a sentence: a gate waiting, a rejection, a task done or failed,
// someone arriving or leaving. Routine "working" changes stay silent.

import type { DeskState } from "../state/deskMachine";
import type { FactoryState } from "../state/reducer";

export interface AnnounceSnapshot {
  desks: Record<string, DeskState["state"]>;
  completed: number;
  failed: number;
}

export function announceSnapshot(state: FactoryState): AnnounceSnapshot {
  const desks: Record<string, DeskState["state"]> = {};
  for (const [id, d] of Object.entries(state.desks)) desks[id] = d.state;
  return { desks, completed: state.counters.completed, failed: state.counters.failed };
}

/** Sentences for what changed between two snapshots (empty when nothing worth saying). */
export function announcements(prev: AnnounceSnapshot, next: AnnounceSnapshot, state: FactoryState): string[] {
  const out: string[] = [];
  const name = (id: string) => state.agents[id]?.display_name ?? id;
  for (const [id, now] of Object.entries(next.desks)) {
    const before = prev.desks[id];
    if (before === now) continue;
    if (now === "waiting") out.push(`${name(id)} 等你點頭`);
    else if (now === "reject") out.push(`${name(id)} 被退件了`);
    else if (now === "hello") out.push(`${name(id)} 報到了`);
  }
  for (const id of Object.keys(prev.desks)) if (!(id in next.desks)) out.push(`${name(id)} 離開了`);
  if (next.completed > prev.completed) out.push("一張任務完成了");
  if (next.failed > prev.failed) out.push("一張任務失敗了");
  return out;
}
