// Typed fetch wrapper for the REST API (plan §7, §7.1a). No data-fetching library (D11).
// Every non-2xx response (and every network failure) becomes an `ApiRequestError`
// carrying `{status, detail, errors}`; `detail` is already a readable string.

import type {
  AgentDetailResponse,
  AgentsResponse,
  ApiError,
  ApiErrorBody,
  ApproveResponse,
  DeleteAgentResponse,
  HealthResponse,
  HistoryQuery,
  HistoryResponse,
  ImportAgentsResponse,
  NewAgentRequest,
  NewAgentResponse,
  NewTaskRequest,
  NewTaskResponse,
  PipelinesResponse,
  ProjectsResponse,
  PutPipelineRequest,
  PutPipelineResponse,
  PutSettingsRequest,
  PutSettingsResponse,
  RejectResponse,
  RerunResponse,
  SettingsResponse,
  TaskDetailResponse,
  TasksResponse,
  UpdateAgentRequest,
  UpdateAgentResponse,
} from "../types/api";

export type ApiFetch = (input: string, init?: RequestInit) => Promise<Response>;

export class ApiRequestError extends Error implements ApiError {
  readonly status: number;
  readonly detail: string;
  readonly errors: unknown[];
  constructor(status: number, detail: string, errors: unknown[] = []) {
    super(detail);
    this.name = "ApiRequestError";
    this.status = status;
    this.detail = detail;
    this.errors = errors;
  }
}

/** Turn a FastAPI error body into `{detail, errors}` (string detail, or Pydantic's 422 list). */
export function normalizeError(
  body: unknown,
  status: number,
  statusText: string,
): { detail: string; errors: unknown[] } {
  const fallback = `${status} ${statusText}`.trim();
  if (typeof body !== "object" || body === null) return { detail: fallback, errors: [] };
  const { detail, errors } = body as ApiErrorBody;
  const extra = Array.isArray(errors) ? errors : [];
  if (typeof detail === "string" && detail !== "") return { detail, errors: extra };
  if (Array.isArray(detail)) {
    const msg = detail
      .map((e) => {
        const field = Array.isArray(e.loc) ? e.loc[e.loc.length - 1] : undefined;
        return field !== undefined && field !== "body" ? `${field}: ${e.msg ?? ""}` : (e.msg ?? "");
      })
      .filter((m) => m !== "")
      .join("; ");
    return { detail: msg || fallback, errors: extra.length > 0 ? extra : detail };
  }
  return { detail: fallback, errors: extra };
}

const enc = encodeURIComponent;
const JSON_HEADERS = { Accept: "application/json" };

export function createApi(fetchImpl?: ApiFetch) {
  // Resolved lazily so a fetch installed after module load (tests, polyfills) is honoured.
  const doFetch: ApiFetch = fetchImpl ?? ((input, init) => globalThis.fetch(input, init));

  async function request<T>(method: string, path: string, body?: unknown, raw?: { text: string; type: string }): Promise<T> {
    const headers: Record<string, string> = { ...JSON_HEADERS };
    let payload: string | undefined;
    if (raw) {
      headers["Content-Type"] = raw.type;
      payload = raw.text;
    } else if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    let res: Response;
    try {
      res = await doFetch(path, { method, headers, body: payload });
    } catch {
      throw new ApiRequestError(0, "無法連線到伺服器");
    }
    if (!res.ok) {
      let parsed: unknown = null;
      try {
        parsed = await res.json();
      } catch {
        parsed = null;
      }
      const { detail, errors } = normalizeError(parsed, res.status, res.statusText);
      throw new ApiRequestError(res.status, detail, errors);
    }
    if (res.status === 204) return undefined as T;
    const type = res.headers.get("Content-Type") ?? "";
    if (!type.includes("json")) return (await res.text()) as T;
    return (await res.json()) as T;
  }

  const p = (project: string) => `/api/projects/${enc(project)}`;
  const get = <T>(path: string) => request<T>("GET", path);
  const send = <T>(method: string, path: string, body?: unknown) => request<T>(method, path, body ?? {});

  return {
    health: () => get<HealthResponse>("/api/health"),
    projects: () => get<ProjectsResponse>("/api/projects"),

    tasks: (project: string) => get<TasksResponse>(`${p(project)}/tasks`),
    createTask: (project: string, req: NewTaskRequest) => send<NewTaskResponse>("POST", `${p(project)}/tasks`, req),
    taskDetail: (project: string, taskId: string) => get<TaskDetailResponse>(`${p(project)}/tasks/${enc(taskId)}`),
    approve: (project: string, taskId: string) => send<ApproveResponse>("POST", `${p(project)}/tasks/${enc(taskId)}/approve`),
    reject: (project: string, taskId: string, note = "") =>
      send<RejectResponse>("POST", `${p(project)}/tasks/${enc(taskId)}/reject`, { note }),
    rerun: (project: string, taskId: string) => send<RerunResponse>("POST", `${p(project)}/tasks/${enc(taskId)}/rerun`),
    /** Absolute-path helpers for links (logs, deliverables come with their own URLs in the detail). */
    tasksExportUrl: (project: string) => `${p(project)}/tasks/export`,

    agents: (project: string) => get<AgentsResponse>(`${p(project)}/agents`),
    createAgent: (project: string, req: NewAgentRequest) => send<NewAgentResponse>("POST", `${p(project)}/agents`, req),
    agent: (project: string, id: string) => get<AgentDetailResponse>(`${p(project)}/agents/${enc(id)}`),
    updateAgent: (project: string, id: string, req: UpdateAgentRequest) =>
      send<UpdateAgentResponse>("PUT", `${p(project)}/agents/${enc(id)}`, req),
    deleteAgent: (project: string, id: string) => request<DeleteAgentResponse>("DELETE", `${p(project)}/agents/${enc(id)}`),
    exportAgentsUrl: (project: string) => `${p(project)}/agents/export`,
    importAgents: (project: string, yaml: string) =>
      request<ImportAgentsResponse>("POST", `${p(project)}/agents/import`, undefined, { text: yaml, type: "application/yaml" }),

    pipelines: (project: string) => get<PipelinesResponse>(`${p(project)}/pipelines`),
    putPipeline: (project: string, pipelineId: string, req: PutPipelineRequest) =>
      send<PutPipelineResponse>("PUT", `${p(project)}/pipelines/${enc(pipelineId)}`, req),

    settings: (project: string) => get<SettingsResponse>(`${p(project)}/settings`),
    putSettings: (project: string, req: PutSettingsRequest) =>
      send<PutSettingsResponse>("PUT", `${p(project)}/settings`, req),

    history: (project: string, q: HistoryQuery = {}) => {
      const params = new URLSearchParams();
      if (q.status && q.status.length > 0) params.set("status", q.status.join(","));
      if (q.pipeline) params.set("pipeline", q.pipeline);
      if (q.limit !== undefined) params.set("limit", String(q.limit));
      if (q.offset !== undefined) params.set("offset", String(q.offset));
      const qs = params.toString();
      return get<HistoryResponse>(`${p(project)}/history${qs ? `?${qs}` : ""}`);
    },
  };
}

export type Api = ReturnType<typeof createApi>;
