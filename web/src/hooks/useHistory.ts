// Pages of the note wall (`GET /history`, plan §7.1). Changing the filter starts over; "load more"
// appends the next page; a finished task re-fetches the first page without blanking the list.

import { useCallback, useEffect, useRef, useState } from "react";
import { PAGE_SIZE, queryHistory } from "../lib/historyFilter";
import { ApiRequestError } from "../net/api";
import { useFactoryDispatch, useFactoryEnv, useFactoryState } from "../state/FactoryProvider";
import type { HistoryItem, HistoryResponse } from "../types/api";
import type { HistoryCounts, TaskStatus } from "../types/events";

export interface HistoryView {
  items: HistoryItem[];
  total: number;
  counts: HistoryCounts | null;
  loading: boolean;
  error: string | null;
  hasMore: boolean;
  loadMore(): void;
  retry(): void;
}

/**
 * Append a page, skipping notes already listed. Offset paging shifts when a task is created
 * between two requests, so the next page can repeat the previous page's last note.
 */
export function mergePage(current: HistoryItem[], page: HistoryItem[]): HistoryItem[] {
  const seen = new Set(current.map((i) => i.task_id));
  const fresh = page.filter((i) => {
    if (seen.has(i.task_id)) return false;
    seen.add(i.task_id);
    return true;
  });
  return fresh.length === 0 ? current : [...current, ...fresh];
}

export function useHistory(statuses: TaskStatus[], pipeline: string | null): HistoryView {
  const env = useFactoryEnv();
  const state = useFactoryState();
  const dispatch = useFactoryDispatch();
  const project = env.projectId ?? state.projectId;
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<HistoryCounts | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const request = useRef(0);
  const recorded = useRef<HistoryResponse | null>(null);
  const key = `${statuses.join(",")}|${pipeline ?? ""}`;
  // A finished or created task changes what the wall should list.
  const refreshKey = state.counters.completed + state.counters.failed + state.counters.created;
  const lastKey = useRef(key);

  const fetchPage = useCallback(
    async (offset: number): Promise<HistoryResponse> => {
      const q = { status: statuses, pipeline: pipeline ?? undefined, limit: PAGE_SIZE, offset };
      let page: HistoryResponse;
      if (env.mode === "replay") {
        recorded.current ??= await env.api.replayHistory();
        page = queryHistory(recorded.current, q);
      } else {
        if (!project) throw new ApiRequestError(0, "還沒有連上專案。");
        page = await env.api.history(project, q);
      }
      // §6.3a point 7: the wall's counts correct the reducer's (trays, stats). A pipeline filter
      // may scope the counts to one floor, so only project-wide answers are used.
      if (!pipeline) dispatch({ type: "history_counts", counts: page.counts });
      return page;
    },
    [env.api, env.mode, project, key, dispatch],
  );

  const reload = useCallback(
    async (blank: boolean) => {
      const id = ++request.current;
      setLoading(true);
      setError(null);
      if (blank) {
        setItems([]);
        setTotal(0);
      }
      try {
        const page = await fetchPage(0);
        if (id !== request.current) return;
        setItems(page.items);
        setTotal(page.total);
        setCounts(page.counts);
      } catch (err) {
        if (id !== request.current) return;
        setError(err instanceof ApiRequestError ? err.detail : "載入失敗，請再試一次。");
      } finally {
        if (id === request.current) setLoading(false);
      }
    },
    [fetchPage],
  );

  useEffect(() => {
    const changed = lastKey.current !== key;
    lastKey.current = key;
    void reload(changed || items.length === 0);
  }, [reload, refreshKey]);

  const loadMore = useCallback(async () => {
    const id = ++request.current;
    setLoading(true);
    setError(null);
    try {
      const page = await fetchPage(items.length);
      if (id !== request.current) return;
      setItems((cur) => mergePage(cur, page.items));
      setTotal(page.total);
      setCounts(page.counts);
    } catch (err) {
      if (id !== request.current) return;
      setError(err instanceof ApiRequestError ? err.detail : "載入失敗，請再試一次。");
    } finally {
      if (id === request.current) setLoading(false);
    }
  }, [fetchPage, items.length]);

  return {
    items,
    total,
    counts,
    loading,
    error,
    hasMore: items.length < total,
    loadMore: () => void loadMore(),
    retry: () => void reload(items.length === 0),
  };
}
