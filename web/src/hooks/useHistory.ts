// Pages of the note wall (`GET /history`, plan §7.1). Changing the filter starts over; "load more"
// appends the next page; a finished task re-fetches the first page without blanking the list.

import { useCallback, useEffect, useRef, useState } from "react";
import { PAGE_SIZE, queryHistory } from "../lib/historyFilter";
import { ApiRequestError } from "../net/api";
import { useFactoryEnv, useFactoryState } from "../state/FactoryProvider";
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

export function useHistory(statuses: TaskStatus[], pipeline: string | null): HistoryView {
  const env = useFactoryEnv();
  const state = useFactoryState();
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
      if (env.mode === "replay") {
        recorded.current ??= await env.api.replayHistory();
        return queryHistory(recorded.current, q);
      }
      if (!project) throw new ApiRequestError(0, "還沒有連上專案。");
      return env.api.history(project, q);
    },
    [env.api, env.mode, project, key],
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
      setItems((cur) => [...cur, ...page.items]);
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
