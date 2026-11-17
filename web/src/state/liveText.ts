// High-frequency text (token stream, typewriter progress) lives outside the reducer (D12):
// each desk subscribes to its own station with useSyncExternalStore, so a token arriving for
// one desk does not re-render the whole tree.

import { useSyncExternalStore } from "react";
import type { AgentTokenData } from "../types/events";

type Listener = () => void;

export interface LiveTextStore {
  /** Current text of a station ("" when none). The string is stable between changes. */
  get(stationId: string): string;
  set(stationId: string, text: string): void;
  append(stationId: string, text: string): void;
  /** Apply an `agent:token` event: a new `attempt` starts the text over; stale `seq` is dropped. */
  applyToken(token: AgentTokenData): void;
  clear(stationId: string): void;
  clearAll(): void;
  subscribe(listener: Listener): () => void;
}

export function createLiveText(): LiveTextStore {
  const texts = new Map<string, string>();
  const meta = new Map<string, { attempt: number; seq: number }>();
  const listeners = new Set<Listener>();
  const emit = () => listeners.forEach((l) => l());

  const write = (id: string, text: string) => {
    if ((texts.get(id) ?? "") === text) return;
    if (text === "") texts.delete(id);
    else texts.set(id, text);
    emit();
  };

  return {
    get: (id) => texts.get(id) ?? "",
    set: (id, text) => {
      meta.delete(id);
      write(id, text);
    },
    append: (id, text) => write(id, (texts.get(id) ?? "") + text),
    applyToken: (t) => {
      const m = meta.get(t.station);
      if (m && t.attempt === m.attempt && t.seq <= m.seq) return; // duplicate or late
      const restart = !m || t.attempt !== m.attempt;
      meta.set(t.station, { attempt: t.attempt, seq: t.seq });
      write(t.station, (restart ? "" : (texts.get(t.station) ?? "")) + t.text);
    },
    clear: (id) => {
      meta.delete(id);
      write(id, "");
    },
    clearAll: () => {
      meta.clear();
      if (texts.size === 0) return;
      texts.clear();
      emit();
    },
    subscribe: (l) => {
      listeners.add(l);
      return () => {
        listeners.delete(l);
      };
    },
  };
}

/** Subscribe one component to one station's live text. */
export function useLiveText(store: LiveTextStore, stationId: string): string {
  return useSyncExternalStore(store.subscribe, () => store.get(stationId), () => "");
}
