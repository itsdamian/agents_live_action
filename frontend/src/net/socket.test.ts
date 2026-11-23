import { afterEach, beforeEach, vi } from "vitest";
import { BACKOFF_MS, backoffDelay, connectSocket, wsUrl, type SocketStatusInfo, type WebSocketLike } from "./socket";
import type { ServerEvent } from "../types/events";

class FakeSocket implements WebSocketLike {
  static all: FakeSocket[] = [];
  handlers: Record<string, Array<(e: never) => void>> = {};
  closed = false;
  constructor(readonly url: string) {
    FakeSocket.all.push(this);
  }
  addEventListener(type: string, cb: (e: never) => void) {
    (this.handlers[type] ??= []).push(cb);
  }
  close() {
    this.closed = true;
  }
  fire(type: string, e: unknown = {}) {
    (this.handlers[type] ?? []).forEach((h) => h(e as never));
  }
  open() { this.fire("open"); }
  message(data: unknown) { this.fire("message", { data: typeof data === "string" ? data : JSON.stringify(data) }); }
  drop(code = 1006, reason = "") { this.fire("close", { code, reason }); }
}

const frame = { event: "agent:left", data: { agent_id: "a", pipeline_id: "main" } };

function setup() {
  const events: ServerEvent[] = [];
  const statuses: SocketStatusInfo[] = [];
  const invalid: string[] = [];
  const handle = connectSocket({
    projectId: "my project",
    url: "ws://test/ws/my%20project",
    createSocket: (u) => new FakeSocket(u),
    onEvent: (e) => events.push(e),
    onStatus: (s) => statuses.push(s),
    onInvalid: (r) => invalid.push(r),
  });
  return { handle, events, statuses, invalid, last: () => FakeSocket.all[FakeSocket.all.length - 1]! };
}

beforeEach(() => {
  FakeSocket.all = [];
  vi.useFakeTimers();
});
afterEach(() => vi.useRealTimers());

describe("backoff", () => {
  it("is 2 s, 5 s, 10 s and then holds at 10 s", () => {
    expect(BACKOFF_MS).toEqual([2000, 5000, 10000]);
    expect([0, 1, 2, 3, 9].map(backoffDelay)).toEqual([2000, 5000, 10000, 10000, 10000]);
  });
});

describe("wsUrl", () => {
  it("uses ws/wss by page protocol and encodes the project id", () => {
    expect(wsUrl("a b", { protocol: "http:", host: "h:1" })).toBe("ws://h:1/ws/a%20b");
    expect(wsUrl("x", { protocol: "https:", host: "h" })).toBe("wss://h/ws/x");
  });
});

describe("connectSocket", () => {
  it("reports connecting then open and forwards valid frames", () => {
    const t = setup();
    expect(t.statuses.map((s) => s.status)).toEqual(["connecting"]);
    expect(t.last().url).toBe("ws://test/ws/my%20project");
    t.last().open();
    t.last().message(frame);
    expect(t.statuses.at(-1)).toMatchObject({ status: "open", attempt: 0 });
    expect(t.events).toEqual([frame]);
  });

  it("drops unknown events quietly and reports invalid frames", () => {
    const t = setup();
    t.last().open();
    t.last().message({ event: "staffing:progress", data: {} });
    t.last().message("{broken");
    t.last().message({ event: "agent:left", data: {} });
    t.last().fire("message", { data: new ArrayBuffer(1) });
    expect(t.events).toEqual([]);
    expect(t.invalid).toHaveLength(2);
  });

  it("reconnects after 2 s, 5 s, 10 s, 10 s and resets after a successful open", () => {
    const t = setup();
    t.last().open();
    const delays: number[] = [];
    for (let i = 0; i < 4; i++) {
      t.last().drop();
      const s = t.statuses.at(-1)!;
      expect(s.status).toBe("reconnecting");
      delays.push(s.delay!);
      expect(s.attempt).toBe(i + 1);
      const before = FakeSocket.all.length;
      vi.advanceTimersByTime(s.delay! - 1);
      expect(FakeSocket.all).toHaveLength(before); // not yet
      vi.advanceTimersByTime(1);
      expect(FakeSocket.all).toHaveLength(before + 1);
    }
    expect(delays).toEqual([2000, 5000, 10000, 10000]);
    t.last().open(); // success resets the schedule
    t.last().drop();
    expect(t.statuses.at(-1)).toMatchObject({ status: "reconnecting", delay: 2000, attempt: 1 });
  });

  it("a failed reconnect attempt keeps backing off (close without open)", () => {
    const t = setup();
    t.last().drop();
    vi.advanceTimersByTime(2000);
    t.last().drop();
    expect(t.statuses.at(-1)).toMatchObject({ status: "reconnecting", delay: 5000, attempt: 2 });
  });

  it("does not reconnect when the project is unknown (4004)", () => {
    const t = setup();
    t.last().open();
    t.last().drop(4004, "Unknown project: nope");
    expect(t.statuses.at(-1)).toMatchObject({ status: "error", reason: "Unknown project: nope" });
    vi.advanceTimersByTime(60000);
    expect(FakeSocket.all).toHaveLength(1);
  });

  it("close() stops reconnecting, closes the socket and reports closed", () => {
    const t = setup();
    t.last().open();
    t.last().drop();
    t.handle.close();
    vi.advanceTimersByTime(60000);
    expect(FakeSocket.all).toHaveLength(1);
    expect(t.statuses.at(-1)?.status).toBe("closed");

    const u = setup();
    const sock = u.last();
    u.handle.close();
    expect(sock.closed).toBe(true);
    sock.drop(1000); // the late close event must not trigger a reconnect
    vi.advanceTimersByTime(60000);
    expect(FakeSocket.all).toHaveLength(2);
  });

  it("ignores events from a socket that was already replaced", () => {
    const t = setup();
    const first = t.last();
    first.open();
    first.drop();
    vi.advanceTimersByTime(2000);
    first.message(frame); // stale socket
    expect(t.events).toEqual([]);
    t.last().open();
    t.last().message(frame);
    expect(t.events).toHaveLength(1);
  });
});
