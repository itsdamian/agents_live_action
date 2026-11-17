// Test-only: render a component tree with a fixed factory state, toasts and a stubbed API.
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";
import { ToastProvider } from "../components/panels/Toasts";
import type { Api } from "../net/api";
import { StaticFactoryProvider, type FactoryEnv } from "../state/FactoryProvider";
import type { FactoryState } from "../state/reducer";
import type { ServerEvent } from "../types/events";
import type { ReplayHandle } from "../net/replay";

/** An API whose methods are all vi.fn(); override the ones a test cares about. */
export function stubApi(over: Record<string, unknown> = {}): Api & Record<string, ReturnType<typeof vi.fn>> {
  const names = ["health", "projects", "tasks", "createTask", "taskDetail", "approve", "reject", "rerun", "agents", "createAgent",
    "agent", "updateAgent", "deleteAgent", "importAgents", "pipelines", "putPipeline", "settings", "putSettings", "history"];
  const api: Record<string, unknown> = {
    exportAgentsUrl: (p: string) => `/api/projects/${p}/agents/export`,
    tasksExportUrl: (p: string) => `/api/projects/${p}/tasks/export`,
  };
  for (const n of names) api[n] = vi.fn().mockResolvedValue({});
  return Object.assign(api, over) as never;
}

export function fakeReplay() {
  const emitted: ServerEvent[] = [];
  const handle: ReplayHandle = { stop: () => {}, emit: (e) => emitted.push(e), finished: false };
  return { emitted, handle };
}

export function renderWith(ui: ReactElement, state: FactoryState, env: Partial<FactoryEnv> = {}) {
  const wrap = (s: FactoryState, el: ReactElement) => (
    <StaticFactoryProvider state={s} env={env}>
      <ToastProvider>{el}</ToastProvider>
    </StaticFactoryProvider>
  );
  const r = render(wrap(state, ui));
  return { ...r, update: (s: FactoryState, el: ReactElement = ui) => r.rerender(wrap(s, el)) };
}
