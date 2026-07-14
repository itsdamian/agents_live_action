// api.js — REST fetch helpers for the FastAPI backend.
//
// nginx reverse-proxies /api to backend:8000, so relative paths work both in
// Docker (served by nginx) and against a locally proxied dev setup.

async function getJSON(path) {
  const res = await fetch(path, {
    headers: { Accept: 'application/json' },
  });
  if (!res.ok) {
    throw new Error(`GET ${path} failed: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

/**
 * GET /api/projects
 * @returns {Promise<{projects: Array, mock_mode: boolean, providers: string[]}>}
 */
export function fetchProjects() {
  return getJSON('/api/projects');
}

/**
 * GET /api/projects/{project_id}
 * @returns {Promise<Object>} project detail incl. agents + pipeline snapshot
 */
export function fetchProject(projectId) {
  return getJSON(`/api/projects/${encodeURIComponent(projectId)}`);
}

/**
 * GET /api/projects/{project_id}/tasks
 * @returns {Promise<{tasks: Array}>}
 */
export function fetchTasks(projectId) {
  return getJSON(`/api/projects/${encodeURIComponent(projectId)}/tasks`);
}

/**
 * GET /api/projects/{project_id}/tasks/{task_id}
 * Full task detail: stored fields + per-station outputs (pm/swe/qa), status
 * history, log links, and deliverables (Task 15).
 * @returns {Promise<{task: Object, status_history: Array, logs: Array, deliverables: Array}>}
 */
export function fetchTaskDetail(projectId, taskId) {
  return getJSON(
    `/api/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}`
  );
}

/**
 * GET /api/projects/{project_id}/tasks/{task_id}/logs
 * @returns {Promise<{task_id: string, logs: Array}>}
 */
export function fetchTaskLogs(projectId, taskId) {
  return getJSON(
    `/api/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}/logs`
  );
}

/**
 * Turn a FastAPI error body into a human-readable message.
 * `detail` can be a plain string (our HTTPException) or a list of validation
 * error objects (Pydantic 422). Falls back to the HTTP status text.
 */
function formatError(body, res) {
  const detail = body && body.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const msg = detail
      .map((e) => {
        const field = Array.isArray(e.loc) ? e.loc[e.loc.length - 1] : null;
        return field ? `${field}: ${e.msg}` : e.msg;
      })
      .filter(Boolean)
      .join('; ');
    if (msg) return msg;
  }
  return `${res.status} ${res.statusText}`;
}

/**
 * POST /api/projects/{project_id}/tasks
 * Creates a task from the UI. The backend appends it to tasks.yaml and the
 * running TaskWatcher enqueues it into the pipeline (no frontend polling).
 *
 * @param {string} projectId
 * @param {{title: string, description?: string, priority?: string}} payload
 * @returns {Promise<{task: Object}>}
 * @throws {Error} with a readable message on validation / server failure
 */
export async function createTask(projectId, payload) {
  const res = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/tasks`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
      body: JSON.stringify(payload),
    }
  );

  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }

  if (!res.ok) {
    throw new Error(formatError(body, res));
  }
  return body;
}

/**
 * POST a JSON body to `path`, returning the parsed body and throwing a readable
 * Error on non-2xx responses. Shared by the approval-gate helpers (Task 17).
 */
async function postJSON(path, payload) {
  const res = await fetch(path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: JSON.stringify(payload ?? {}),
  });

  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }

  if (!res.ok) {
    throw new Error(formatError(body, res));
  }
  return body;
}

/**
 * POST /api/projects/{project_id}/tasks/{task_id}/approve
 * Resumes a task paused at a human approval gate (Task 17).
 * @returns {Promise<{task_id: string, decision: string}>}
 */
export function approveTask(projectId, taskId) {
  return postJSON(
    `/api/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}/approve`
  );
}

/**
 * POST /api/projects/{project_id}/tasks/{task_id}/reject
 * Rejects a task paused at a gate; the note is fed to SWE on the rework pass.
 * @param {string} note reviewer feedback
 * @returns {Promise<{task_id: string, decision: string, note: string}>}
 */
export function rejectTask(projectId, taskId, note = '') {
  return postJSON(
    `/api/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}/reject`,
    { note }
  );
}
