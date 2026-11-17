// Replay mode serves the note wall from the recorded history.json, applying the same
// filtering and paging the server would.

import type { HistoryQuery, HistoryResponse } from "../types/api";

export const PAGE_SIZE = 50;

export function queryHistory(all: HistoryResponse, q: HistoryQuery): HistoryResponse {
  const statuses = q.status && q.status.length > 0 ? new Set<string>(q.status) : null;
  const matching = all.items.filter((i) => (!statuses || statuses.has(i.status)) && (!q.pipeline || i.pipeline_id === q.pipeline));
  const offset = Math.max(0, q.offset ?? 0);
  const limit = Math.min(200, Math.max(1, q.limit ?? PAGE_SIZE));
  return { items: matching.slice(offset, offset + limit), total: matching.length, counts: all.counts };
}
