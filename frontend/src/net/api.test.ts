import { vi } from "vitest";
import { ApiRequestError, createApi, normalizeError, UNAVAILABLE_TEXT } from "./api";

function jsonResponse(body: unknown, status = 200, statusText = "") {
  return new Response(JSON.stringify(body), { status, statusText, headers: { "Content-Type": "application/json" } });
}

function setup(res: Response | (() => Promise<Response>)) {
  const fetchImpl = vi.fn(async () => (typeof res === "function" ? res() : res.clone()));
  return { fetchImpl, api: createApi(fetchImpl) };
}
const call = (f: ReturnType<typeof vi.fn>) => {
  const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
  return { url, init, headers: init.headers as Record<string, string> };
};

describe("requests", () => {
  it("GETs with an Accept header and no body", async () => {
    const { api, fetchImpl } = setup(jsonResponse({ projects: [], mock_mode: true, providers: [] }));
    await expect(api.projects()).resolves.toMatchObject({ mock_mode: true });
    const c = call(fetchImpl);
    expect(c.url).toBe("/api/projects");
    expect(c.init.method).toBe("GET");
    expect(c.init.body).toBeUndefined();
    expect(c.headers.Accept).toBe("application/json");
  });

  it("encodes ids in paths", async () => {
    const { api, fetchImpl } = setup(jsonResponse({}));
    await api.taskDetail("my project", "task/1");
    expect(call(fetchImpl).url).toBe("/api/projects/my%20project/tasks/task%2F1");
  });

  it("creates a task with the approval field", async () => {
    const { api, fetchImpl } = setup(jsonResponse({ task: {} }, 201));
    await api.createTask("p", { title: "x", description: "d", priority: "high", approval: "after_qa" });
    const c = call(fetchImpl);
    expect(c.url).toBe("/api/projects/p/tasks");
    expect(c.init.method).toBe("POST");
    expect(c.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(c.init.body as string)).toEqual({ title: "x", description: "d", priority: "high", approval: "after_qa" });
  });

  it("approve posts an empty object, reject posts the note", async () => {
    const a = setup(jsonResponse({ task_id: "t", decision: "approved" }));
    await a.api.approve("p", "t");
    expect(call(a.fetchImpl).url).toBe("/api/projects/p/tasks/t/approve");
    expect(call(a.fetchImpl).init.body).toBe("{}");
    const r = setup(jsonResponse({ task_id: "t", decision: "rejected", note: "n" }));
    await r.api.reject("p", "t", "再改一下");
    expect(JSON.parse(call(r.fetchImpl).init.body as string)).toEqual({ note: "再改一下" });
  });

  it("creates and updates agents including the llm field", async () => {
    const c = setup(jsonResponse({}, 201));
    await c.api.createAgent("p", { display_name: "設計師", duty: "d", prompt: "p", kind: "make", look: "beret", llm: { provider: "anthropic", model: "claude-opus-5-5" }, after: "pm" });
    expect(JSON.parse(call(c.fetchImpl).init.body as string).llm).toEqual({ provider: "anthropic", model: "claude-opus-5-5" });
    const u = setup(jsonResponse({}));
    await u.api.updateAgent("p", "designer", { llm: null });
    expect(call(u.fetchImpl)).toMatchObject({ url: "/api/projects/p/agents/designer" });
    expect(call(u.fetchImpl).init.method).toBe("PUT");
  });

  it("PUT pipelines and settings send their bodies", async () => {
    const a = setup(jsonResponse({}));
    await a.api.putPipeline("p", "main", { seats: ["pm", "qa"] });
    expect(call(a.fetchImpl).url).toBe("/api/projects/p/pipelines/main");
    expect(JSON.parse(call(a.fetchImpl).init.body as string)).toEqual({ seats: ["pm", "qa"] });
    const b = setup(jsonResponse({}));
    await b.api.putSettings("p", { llm: "mock" });
    expect(call(b.fetchImpl).url).toBe("/api/projects/p/settings");
    expect(JSON.parse(call(b.fetchImpl).init.body as string)).toEqual({ llm: "mock" });
  });

  it("DELETE agent has no body; import sends YAML text", async () => {
    const d = setup(jsonResponse({ agent_id: "a", effective: "now" }));
    await d.api.deleteAgent("p", "a");
    expect(call(d.fetchImpl).init).toMatchObject({ method: "DELETE", body: undefined });
    const i = setup(jsonResponse({}));
    await i.api.importAgents("p", "schema_version: 1\n");
    expect(call(i.fetchImpl).headers["Content-Type"]).toBe("application/yaml");
    expect(call(i.fetchImpl).init.body).toBe("schema_version: 1\n");
  });

  it("history builds the query string from the filters", async () => {
    const a = setup(jsonResponse({ items: [], total: 0, counts: {} }));
    await a.api.history("p", { status: ["done", "failed"], pipeline: "main", limit: 50, offset: 100 });
    expect(call(a.fetchImpl).url).toBe("/api/projects/p/history?status=done%2Cfailed&pipeline=main&limit=50&offset=100");
    const b = setup(jsonResponse({}));
    await b.api.history("p");
    expect(call(b.fetchImpl).url).toBe("/api/projects/p/history");
  });

  it("exposes download URLs for export endpoints", () => {
    const api = createApi(vi.fn());
    expect(api.exportAgentsUrl("a b")).toBe("/api/projects/a%20b/agents/export");
    expect(api.tasksExportUrl("p")).toBe("/api/projects/p/tasks/export");
  });

  it("returns plain text for non-JSON success bodies", async () => {
    const { api } = setup(new Response("tasks: []\n", { status: 200, headers: { "Content-Type": "text/plain" } }));
    await expect(api.health()).resolves.toBe("tasks: []\n");
  });
});

describe("errors", () => {
  it("string detail → {detail, errors: []}", async () => {
    const { api } = setup(jsonResponse({ detail: "Task t is not awaiting approval" }, 409, "Conflict"));
    const err = await api.approve("p", "t").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiRequestError);
    expect(err).toMatchObject({ status: 409, detail: "Task t is not awaiting approval", errors: [] });
  });

  it("our own `errors` list is carried along", async () => {
    const { api } = setup(jsonResponse({ detail: "編制不合格", errors: ["第一席不能是 check"] }, 422));
    await expect(api.putPipeline("p", "main", { seats: [] })).rejects.toMatchObject({
      status: 422, detail: "編制不合格", errors: ["第一席不能是 check"],
    });
  });

  it("Pydantic 422 list becomes a readable detail and keeps the raw errors", async () => {
    const list = [
      { loc: ["body", "title"], msg: "String should have at least 1 character" },
      { loc: ["body", "priority"], msg: "Input should be 'critical'" },
    ];
    const { api } = setup(jsonResponse({ detail: list }, 422, "Unprocessable Entity"));
    await expect(api.createTask("p", { title: "" })).rejects.toMatchObject({
      status: 422,
      detail: "title: String should have at least 1 character; priority: Input should be 'critical'",
      errors: list,
    });
  });

  it("non-JSON error bodies fall back to the status line", async () => {
    const { api } = setup(new Response("<html>", { status: 502, statusText: "Bad Gateway" }));
    await expect(api.projects()).rejects.toMatchObject({ status: 502, detail: "502 Bad Gateway", errors: [] });
  });

  it("network failures become status 0 with a Chinese message", async () => {
    const { api } = setup(() => Promise.reject(new TypeError("Failed to fetch")));
    await expect(api.projects()).rejects.toMatchObject({ status: 0, detail: "無法連線到伺服器", errors: [] });
  });

  it("503 (project failed to initialise) gets a readable Chinese text; the server's text is kept", async () => {
    const server = "Project p failed to initialise in the database, so it can not run tasks; see the server log, then restart the server to retry";
    const { api } = setup(jsonResponse({ detail: server }, 503, "Service Unavailable"));
    const err = await api.createAgent("p", { display_name: "x", duty: "d", prompt: "p", kind: "make", look: "cap", llm: null, after: null }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiRequestError);
    expect(err).toMatchObject({ status: 503, detail: UNAVAILABLE_TEXT, serverDetail: server, errors: [] });
  });

  it("the backend's `{field, message}` error items are carried along as they are", async () => {
    const errors = [{ field: "after", message: "「ghost」不在這條線上" }];
    const { api } = setup(jsonResponse({ detail: "新增 agent 的內容不合格", errors }, 422));
    await expect(api.createAgent("p", { display_name: "x", duty: "d", prompt: "p", kind: "make", look: "cap", llm: null, after: "ghost" })).rejects.toMatchObject({
      status: 422, detail: "新增 agent 的內容不合格", errors,
    });
  });

  it("normalizeError handles odd bodies", () => {
    expect(normalizeError(null, 500, "Err")).toEqual({ detail: "500 Err", errors: [] });
    expect(normalizeError({}, 400, "")).toEqual({ detail: "400", errors: [] });
    expect(normalizeError({ detail: [{ msg: "x" }] }, 422, "")).toMatchObject({ detail: "x" });
  });
});
