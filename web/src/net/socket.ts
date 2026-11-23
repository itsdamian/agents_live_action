// WebSocket connection to /ws/{project_id}, with exponential-backoff reconnect.
// Same schedule as the old frontend/js/ws.js: 2 s → 5 s → 10 s, then hold at 10 s.
// A fresh snapshot arrives after every (re)connect, so no state is kept here.

import { parseServerFrameText } from "./guards";
import type { ServerEvent } from "../types/events";

export const BACKOFF_MS = [2000, 5000, 10000] as const;
/** Application-defined close code the backend uses for an unknown project. */
export const WS_CLOSE_UNKNOWN_PROJECT = 4004;

export function backoffDelay(attempt: number): number {
  return BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)]!;
}

export function wsUrl(projectId: string, loc: Pick<Location, "protocol" | "host"> = window.location): string {
  const proto = loc.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${loc.host}/ws/${encodeURIComponent(projectId)}`;
}

export type SocketStatus = "connecting" | "open" | "reconnecting" | "closed" | "error";

export interface SocketStatusInfo {
  status: SocketStatus;
  attempt: number;
  delay: number | null;
  reason: string | null;
}

/** The slice of the WebSocket API we use (lets tests inject a fake). */
export interface WebSocketLike {
  addEventListener(type: "open", cb: () => void): void;
  addEventListener(type: "message", cb: (e: { data: unknown }) => void): void;
  addEventListener(type: "close", cb: (e: { code: number; reason: string }) => void): void;
  addEventListener(type: "error", cb: () => void): void;
  close(): void;
}
export type WebSocketFactory = (url: string) => WebSocketLike;

export interface SocketOptions {
  projectId: string;
  onEvent: (event: ServerEvent) => void;
  onStatus: (info: SocketStatusInfo) => void;
  /** Frames that fail validation (default: console.warn). Unknown event names are dropped silently. */
  onInvalid?: (reason: string) => void;
  url?: string;
  createSocket?: WebSocketFactory;
}

export interface SocketHandle {
  close(): void;
}

export function connectSocket(opts: SocketOptions): SocketHandle {
  const url = opts.url ?? wsUrl(opts.projectId);
  const create: WebSocketFactory = opts.createSocket ?? ((u) => new WebSocket(u) as unknown as WebSocketLike);
  const onInvalid = opts.onInvalid ?? ((reason) => console.warn("Dropped invalid WS frame:", reason));

  let socket: WebSocketLike | null = null;
  let attempt = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let closedByUs = false;

  const status = (s: SocketStatus, delay: number | null = null, reason: string | null = null) =>
    opts.onStatus({ status: s, attempt, delay, reason });

  const open = () => {
    status("connecting");
    const ws = create(url);
    socket = ws;

    ws.addEventListener("open", () => {
      if (ws !== socket) return;
      attempt = 0;
      status("open");
    });

    ws.addEventListener("message", (e) => {
      if (ws !== socket || typeof e.data !== "string") return;
      const result = parseServerFrameText(e.data);
      if (result.ok) opts.onEvent(result.event);
      else if (!result.unknown) onInvalid(result.reason);
    });

    ws.addEventListener("close", (e) => {
      if (ws !== socket) return;
      socket = null;
      if (closedByUs) {
        status("closed");
        return;
      }
      if (e.code === WS_CLOSE_UNKNOWN_PROJECT) {
        status("error", null, e.reason || "Unknown project");
        return; // the project is gone: do not hammer the server
      }
      const delay = backoffDelay(attempt);
      attempt += 1;
      status("reconnecting", delay);
      timer = setTimeout(() => {
        timer = null;
        if (!closedByUs) open();
      }, delay);
    });

    ws.addEventListener("error", () => {
      // `close` follows and drives the reconnect; nothing to do here.
    });
  };

  open();

  return {
    close() {
      closedByUs = true;
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
      const ws = socket;
      socket = null;
      if (ws) {
        try {
          ws.close();
        } catch {
          /* already closed */
        }
      }
      status("closed");
    },
  };
}
