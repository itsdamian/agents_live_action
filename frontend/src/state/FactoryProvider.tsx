// FactoryProvider (plan D12): useReducer + two contexts (state, dispatch) plus an env context
// with the pieces that do not change per event (api, liveText store, replay handle, project list).
//
// Data source, decided from the URL:
//   ?replay=<name>   plays public/replay/<name>.jsonl, no backend (AC9)
//   ?project=<id>    connects the WebSocket for that project
//   neither          asks /api/projects and takes the first project

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  type Dispatch,
  type ReactNode,
} from "react";
import { createApi, type Api, type ApiFetch } from "../net/api";
import { loadReplay, startReplay, type FetchLike, type ReplayHandle } from "../net/replay";
import { connectSocket, type WebSocketFactory } from "../net/socket";
import { readUrlParams, withProject } from "../lib/urlParams";
import type { ProjectInfo } from "../types/api";
import type { ServerEvent } from "../types/events";
import { createLiveText, type LiveTextStore } from "./liveText";
import { factoryReducer, initialState, type FactoryAction, type FactoryState } from "./reducer";

export type DataMode = "live" | "replay";

export interface FactoryEnv {
  mode: DataMode;
  /** The project being watched (null while it is still being resolved). */
  projectId: string | null;
  projects: ProjectInfo[];
  selectProject(id: string): void;
  api: Api;
  liveText: LiveTextStore;
  /** Present in replay mode: lets the UI fake server reactions (e.g. the approval gate). */
  replay: ReplayHandle | null;
  clock(): number;
}

const StateContext = createContext<FactoryState | null>(null);
const DispatchContext = createContext<Dispatch<FactoryAction> | null>(null);
const EnvContext = createContext<FactoryEnv | null>(null);

function need<T>(value: T | null, name: string): T {
  if (value === null) throw new Error(`${name} must be used inside <FactoryProvider>`);
  return value;
}
export const useFactoryState = () => need(useContext(StateContext), "useFactoryState");
export const useFactoryDispatch = () => need(useContext(DispatchContext), "useFactoryDispatch");
export const useFactoryEnv = () => need(useContext(EnvContext), "useFactoryEnv");

export interface FactoryProviderProps {
  children: ReactNode;
  /** Defaults to `window.location.search`. */
  search?: string;
  fetch?: ApiFetch;
  createSocket?: WebSocketFactory;
  clock?: () => number;
  liveText?: LiveTextStore;
  /** Interval for expiring cheer / hello / leaving states. */
  tickMs?: number;
}

export function FactoryProvider({
  children,
  search,
  fetch: fetchProp,
  createSocket,
  clock = Date.now,
  liveText: liveTextProp,
  tickMs = 200,
}: FactoryProviderProps) {
  const params = useMemo(() => readUrlParams(search ?? window.location.search), [search]);
  const mode: DataMode = params.replay ? "replay" : "live";
  const [projectId, setProjectId] = useState<string | null>(params.project);
  const [projects, setProjects] = useState<ProjectInfo[]>([]);
  const [replay, setReplay] = useState<ReplayHandle | null>(null);
  const [state, dispatch] = useReducer(factoryReducer, projectId, initialState);

  const fetchRef = useRef<ApiFetch | undefined>(fetchProp);
  fetchRef.current = fetchProp;
  const api = useMemo(() => createApi((input, init) => (fetchRef.current ?? globalThis.fetch)(input, init)), []);
  const liveText = useMemo(() => liveTextProp ?? createLiveText(), [liveTextProp]);
  const clockRef = useRef(clock);
  clockRef.current = clock;

  // Project list, and the first project when none was named.
  useEffect(() => {
    if (mode === "replay") return;
    let cancelled = false;
    api
      .projects()
      .then((res) => {
        if (cancelled) return;
        setProjects(res.projects);
        setProjectId((current) => current ?? res.projects[0]?.id ?? null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const reason = err instanceof Error ? err.message : "無法載入專案列表";
        dispatch({ type: "connection", status: "error", reason });
      });
    return () => {
      cancelled = true;
    };
  }, [api, mode]);

  // Event source: replay file or WebSocket.
  useEffect(() => {
    if (mode === "live" && projectId === null) return;
    dispatch({ type: "reset", projectId: mode === "live" ? projectId : null });
    liveText.clearAll();

    const handle = (ev: ServerEvent) => {
      if (ev.event === "agent:token") liveText.applyToken(ev.data);
      else if (ev.event === "snapshot") liveText.clearAll();
      dispatch({ ...ev, now: clockRef.current() });
    };

    if (mode === "replay" && params.replay) {
      let cancelled = false;
      let handleRef: ReplayHandle | null = null;
      const fetchLike: FetchLike = (input, init) => (fetchRef.current ?? globalThis.fetch)(input, init);
      loadReplay(params.replay, fetchLike)
        .then((lines) => {
          if (cancelled) return;
          dispatch({ type: "connection", status: "open" });
          handleRef = startReplay({ lines, onEvent: handle, speed: params.speed });
          setReplay(handleRef);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          dispatch({ type: "connection", status: "error", reason: err instanceof Error ? err.message : "replay failed" });
        });
      return () => {
        cancelled = true;
        handleRef?.stop();
        setReplay(null);
      };
    }

    const socket = connectSocket({
      projectId: projectId as string,
      createSocket,
      onEvent: handle,
      onStatus: (info) =>
        dispatch({ type: "connection", status: info.status, attempt: info.attempt, delay: info.delay, reason: info.reason }),
    });
    return () => socket.close();
  }, [mode, projectId, params.replay, params.speed, liveText, createSocket]);

  // Expire cheer / hello / leaving. The reducer returns the same state when nothing expired.
  useEffect(() => {
    const id = setInterval(() => dispatch({ type: "tick", now: clockRef.current() }), tickMs);
    return () => clearInterval(id);
  }, [tickMs]);

  const selectProject = useCallback((id: string) => {
    window.history.replaceState(null, "", withProject(window.location.search, id));
    setProjectId(id);
  }, []);

  const env = useMemo<FactoryEnv>(
    () => ({
      mode,
      projectId: mode === "live" ? projectId : state.projectId,
      projects,
      selectProject,
      api,
      liveText,
      replay,
      clock: () => clockRef.current(),
    }),
    [mode, projectId, state.projectId, projects, selectProject, api, liveText, replay],
  );

  return (
    <EnvContext.Provider value={env}>
      <DispatchContext.Provider value={dispatch}>
        <StateContext.Provider value={state}>{children}</StateContext.Provider>
      </DispatchContext.Provider>
    </EnvContext.Provider>
  );
}

/** Provides a fixed state (and no data source). For component tests and previews. */
export function StaticFactoryProvider({
  state,
  dispatch = () => {},
  env,
  children,
}: {
  state: FactoryState;
  dispatch?: Dispatch<FactoryAction>;
  env?: Partial<FactoryEnv>;
  children: ReactNode;
}) {
  const full = useMemo<FactoryEnv>(
    () => ({
      mode: "live",
      projectId: state.projectId,
      projects: [],
      selectProject: () => {},
      api: createApi(() => Promise.reject(new Error("no network in StaticFactoryProvider"))),
      liveText: createLiveText(),
      replay: null,
      clock: () => 0,
      ...env,
    }),
    [state.projectId, env],
  );
  return (
    <EnvContext.Provider value={full}>
      <DispatchContext.Provider value={dispatch}>
        <StateContext.Provider value={state}>{children}</StateContext.Provider>
      </DispatchContext.Provider>
    </EnvContext.Provider>
  );
}
