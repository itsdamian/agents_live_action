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
