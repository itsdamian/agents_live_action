// `?replay=<name>` driver: plays public/replay/<name>.jsonl without a backend.
// Lines are {"t": ms, "event", "data"}; the first line is a snapshot.

import { parseReplayText } from "./guards";
import type { ReplayLine, ServerEvent } from "../types/events";

export type FetchLike = (input: string, init?: RequestInit) => Promise<Pick<Response, "ok" | "status" | "text">>;

export class ReplayLoadError extends Error {}

export function replayUrl(name: string, base = import.meta.env.BASE_URL): string {
  return `${base}replay/${encodeURIComponent(name)}.jsonl`;
}

/** Replay names are file names under public/replay/: letters, digits, `-` and `_` only. */
export function isValidReplayName(name: string): boolean {
  return /^[A-Za-z0-9_-]{1,64}$/.test(name);
}

export async function loadReplay(name: string, fetchImpl: FetchLike): Promise<ReplayLine[]> {
  if (!isValidReplayName(name)) throw new ReplayLoadError(`Invalid replay name: ${name}`);
  let res;
  try {
    res = await fetchImpl(replayUrl(name));
  } catch {
    throw new ReplayLoadError(`Cannot load replay "${name}"`);
  }
  if (!res.ok) throw new ReplayLoadError(`Replay "${name}" not found (${res.status})`);
  const { lines, errors } = parseReplayText(await res.text());
  if (errors.length > 0) {
    const first = errors[0]!;
    throw new ReplayLoadError(`Replay "${name}" line ${first.line}: ${first.reason}`);
  }
  if (lines.length === 0) throw new ReplayLoadError(`Replay "${name}" is empty`);
  return lines;
}

export interface ReplayHandle {
  stop(): void;
  /** Emit an event right now (used to fake server reactions to UI actions, e.g. the approval gate). */
  emit(event: ServerEvent): void;
  readonly finished: boolean;
}

export interface ReplayOptions {
  lines: ReplayLine[];
  onEvent: (event: ServerEvent) => void;
  /** 1 = recorded timing; 3 = three times faster. */
  speed?: number;
  onFinished?: () => void;
}

/** Schedule each line at `t / speed` ms after the start, in file order. */
export function startReplay({ lines, onEvent, speed = 1, onFinished }: ReplayOptions): ReplayHandle {
  const factor = Number.isFinite(speed) && speed > 0 ? speed : 1;
  let index = 0;
  let prevT = 0;
  let stopped = false;
  let finished = false;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const strip = (line: ReplayLine): ServerEvent => {
    const { t: _t, ...event } = line;
    void _t;
    return event as ServerEvent;
  };

  const step = () => {
    timer = null;
    if (stopped) return;
    // Emit this line and every following line that shares its timestamp, in file order.
    const t = lines[index]!.t;
    while (index < lines.length && lines[index]!.t === t && !stopped) {
      onEvent(strip(lines[index]!));
      index += 1;
    }
    prevT = t;
    if (stopped) return;
    if (index >= lines.length) {
      finished = true;
      onFinished?.();
      return;
    }
    timer = setTimeout(step, Math.max(0, (lines[index]!.t - prevT) / factor));
  };

  if (lines.length > 0) timer = setTimeout(step, Math.max(0, lines[0]!.t / factor));
  else finished = true;

  return {
    stop() {
      stopped = true;
      if (timer !== null) clearTimeout(timer);
      timer = null;
    },
    emit(event) {
      if (!stopped) onEvent(event);
    },
    get finished() {
      return finished;
    },
  };
}
