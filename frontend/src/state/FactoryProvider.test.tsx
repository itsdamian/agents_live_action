import { act, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, vi } from "vitest";
import defaultRun from "../../public/replay/default-run.jsonl?raw";
import App from "../App";
import type { WebSocketLike } from "../net/socket";
import { FactoryProvider, useFactoryEnv, useFactoryState } from "./FactoryProvider";
import { createLiveText } from "./liveText";

class FakeSocket implements WebSocketLike {
  static all: FakeSocket[] = [];
  handlers: Record<string, Array<(e: never) => void>> = {};
  closed = false;
  constructor(readonly url: string) { FakeSocket.all.push(this); }
  addEventListener(type: string, cb: (e: never) => void) { (this.handlers[type] ??= []).push(cb); }
  close() { this.closed = true; }
  fire(type: string, e: unknown = {}) { (this.handlers[type] ?? []).forEach((h) => h(e as never)); }
}

const textResponse = (text: string) => new Response(text, { status: 200 });
const jsonResponse = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } });

let errors: ReturnType<typeof vi.spyOn>;
let warns: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  vi.useFakeTimers();
  FakeSocket.all = [];
  errors = vi.spyOn(console, "error").mockImplementation(() => {});
  warns = vi.spyOn(console, "warn").mockImplementation(() => {});
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0); });

describe("replay mode", () => {
  const replayFetch = vi.fn(async (url: string) => {
    if (url.endsWith("/replay/default-run.jsonl")) return textResponse(defaultRun);
    return new Response("", { status: 404 });
  });

  it("plays default-run end to end without console errors or warnings", async () => {
    render(
      <StrictMode>
        <FactoryProvider search="?replay=default-run" fetch={replayFetch}>
          <App />
        </FactoryProvider>
      </StrictMode>,
    );
    await flush();
    expect(screen.getByTestId("connection")).toHaveTextContent("回放中");

    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(screen.getByTestId("project-name")).toHaveTextContent("example-project");
    expect(screen.getByTestId("stat-completed")).toHaveTextContent("3"); // 2 in the snapshot + the task that just completed
    expect(screen.getByTestId("stat-reworks")).toHaveTextContent("5");
    expect(errors).not.toHaveBeenCalled();
    expect(warns).not.toHaveBeenCalled();
  });

  it("speed=20 finishes much sooner", async () => {
    render(
      <FactoryProvider search="?replay=default-run&speed=20" fetch={replayFetch}>
        <App />
      </FactoryProvider>,
    );
    await flush();
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(screen.getByTestId("stat-completed")).toHaveTextContent("3");
  });

  it("shows an error status when the replay file is missing", async () => {
    render(
      <FactoryProvider search="?replay=missing" fetch={replayFetch}>
        <App />
      </FactoryProvider>,
    );
    await flush();
    expect(screen.getByTestId("connection")).toHaveTextContent("連線失敗");
  });

  it("a dev server answering index.html for a missing replay: 找不到 replay, not stuck on 連線中", async () => {
    const spa = vi.fn(async () => new Response("<!doctype html><div id=root></div>", { status: 200, headers: { "Content-Type": "text/html" } }));
    render(
      <FactoryProvider search="?replay=ghost" fetch={spa}>
        <App />
      </FactoryProvider>,
    );
    await flush();
    expect(document.querySelector("[data-banner]")).toHaveTextContent("找不到 replay：ghost");
    expect(screen.getByTestId("connection-announcer")).toHaveTextContent("找不到 replay：ghost");
    expect(screen.getByTestId("connection")).not.toHaveTextContent("連線中");
    expect(screen.queryByText("連線中…")).toBeNull();
  });

  it("exposes a replay handle for faking server events", async () => {
    let env: ReturnType<typeof useFactoryEnv> | null = null;
    function Probe() { env = useFactoryEnv(); return null; }
    render(
      <FactoryProvider search="?replay=default-run" fetch={replayFetch}>
        <Probe />
      </FactoryProvider>,
    );
    await flush();
    expect(env!.mode).toBe("replay");
    expect(env!.replay).not.toBeNull();
  });

  it("does not call the REST API in replay mode", async () => {
    const f = vi.fn(replayFetch);
    render(
      <FactoryProvider search="?replay=default-run" fetch={f}>
        <App />
      </FactoryProvider>,
    );
    await flush();
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    for (const [url] of f.mock.calls) expect(url).toMatch(/\/replay\//);
  });
});

describe("live mode", () => {
  const fetchProjects = vi.fn(async () =>
    jsonResponse({ projects: [{ id: "alpha", name: "alpha", path: "/p", agents: [], task_count: 0, active_task_count: 0, status: "idle" }, { id: "beta", name: "beta", path: "/q", agents: [], task_count: 0, active_task_count: 0, status: "idle" }], mock_mode: true, providers: [] }),
  );

  it("connects the socket for ?project= without needing the project list first", async () => {
    render(
      <FactoryProvider search="?project=beta" fetch={fetchProjects} createSocket={(u) => new FakeSocket(u)}>
        <App />
      </FactoryProvider>,
    );
    await flush();
    expect(FakeSocket.all).toHaveLength(1);
    expect(FakeSocket.all[0]!.url).toMatch(/\/ws\/beta$/);
    act(() => FakeSocket.all[0]!.fire("open"));
    expect(screen.getByTestId("connection")).toHaveTextContent("已連線");
  });

  it("picks the first project when none is named", async () => {
    render(
      <FactoryProvider search="" fetch={fetchProjects} createSocket={(u) => new FakeSocket(u)}>
        <App />
      </FactoryProvider>,
    );
    await flush();
    expect(FakeSocket.all.at(-1)!.url).toMatch(/\/ws\/alpha$/);
  });

  it("reports a project-list failure as a connection error", async () => {
    const failing = vi.fn(async () => new Response(JSON.stringify({ detail: "boom" }), { status: 500, statusText: "Err", headers: { "Content-Type": "application/json" } }));
    render(
      <FactoryProvider search="" fetch={failing} createSocket={(u) => new FakeSocket(u)}>
        <App />
      </FactoryProvider>,
    );
    await flush();
    expect(screen.getByTestId("connection")).toHaveTextContent("連線失敗");
    expect(FakeSocket.all).toHaveLength(0);
  });

  it("feeds frames into the reducer and agent:token into liveText", async () => {
    const live = createLiveText();
    function Probe() { const s = useFactoryState(); return <p data-testid="gate">{Object.keys(s.awaiting).length}</p>; }
    render(
      <FactoryProvider search="?project=alpha" fetch={fetchProjects} createSocket={(u) => new FakeSocket(u)} liveText={live}>
        <Probe />
      </FactoryProvider>,
    );
    await flush();
    const sock = FakeSocket.all[0]!;
    const task = { id: "t1", title: "x", priority: "high", status: "awaiting_approval", rework_count: 0 };
    act(() => {
      sock.fire("message", { data: JSON.stringify({ event: "task:awaiting_approval", data: { task, station: "qa", approval: "after_qa" } }) });
      sock.fire("message", { data: JSON.stringify({ event: "agent:token", data: { task_id: "t1", station: "swe", attempt: 1, seq: 1, text: "Hi", final: false, truncated: false } }) });
    });
    expect(screen.getByTestId("gate").textContent).toBe("1");
    expect(live.get("swe")).toBe("Hi");
  });

  it("closes the socket on unmount", async () => {
    const { unmount } = render(
      <FactoryProvider search="?project=alpha" fetch={fetchProjects} createSocket={(u) => new FakeSocket(u)}>
        <App />
      </FactoryProvider>,
    );
    await flush();
    unmount();
    expect(FakeSocket.all[0]!.closed).toBe(true);
  });
});
