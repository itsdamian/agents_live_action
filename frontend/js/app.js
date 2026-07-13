// app.js — Entry point. Bootstraps UI modules, loads the project list from the
// backend, wires the project selector, and manages the WebSocket connection +
// disconnect warning banner.

import { initHeader, initManagerOffice, initStations, initFooter } from './stations.js';
import { initConveyor } from './conveyor.js';
import { initDevOps } from './devops.js';
import { fetchProjects } from './api.js';
import { connectProject } from './ws.js';
import { bus, state } from './state.js';
import { $ } from './utils.js';

document.addEventListener('DOMContentLoaded', async () => {
  initHeader();
  initManagerOffice();
  initConveyor();
  initStations();
  initFooter();
  initDevOps();

  initConnectionIndicator();

  const select = $('#project-select');
  if (select) {
    select.addEventListener('change', () => {
      const projectId = select.value;
      if (projectId) {
        state.projectId = projectId;
        connectProject(projectId);
      }
    });
  }

  await loadProjects(select);
});

async function loadProjects(select) {
  try {
    const data = await fetchProjects();
    const projects = data.projects || [];

    // [MOCK MODE] indicator reflects the /api/projects flag (Req 7.6).
    state.mockMode = Boolean(data.mock_mode);
    bus.emit('mock', state.mockMode);

    if (!select) return;
    select.innerHTML = '';

    if (projects.length === 0) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = '-- no projects found --';
      select.appendChild(opt);
      return;
    }

    for (const p of projects) {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = p.name || p.id;
      select.appendChild(opt);
    }

    // Auto-select and connect to the first project (Req 1.2, 1.3).
    select.value = projects[0].id;
    state.projectId = projects[0].id;
    connectProject(projects[0].id);
  } catch (err) {
    // eslint-disable-next-line no-console
    console.error('Failed to load projects:', err);
    if (select) {
      select.innerHTML = '';
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = '-- backend unreachable --';
      select.appendChild(opt);
    }
    showBanner('BACKEND UNREACHABLE — RETRYING&hellip;');
  }
}

// ---------- Connection status indicator + warning banner (Req 7.7) ----------

function initConnectionIndicator() {
  const statusEl = $('#conn-status');
  const statusText = statusEl ? $('.conn-text', statusEl) : null;

  bus.on('connection', ({ status, attempt }) => {
    if (statusEl) {
      statusEl.dataset.state = status;
      if (statusText) statusText.textContent = status.toUpperCase();
    }
    switch (status) {
      case 'open':
        hideBanner();
        break;
      case 'connecting':
        showBanner('CONNECTING&hellip;');
        break;
      case 'reconnecting':
        showBanner(`CONNECTION LOST — RECONNECTING (attempt ${attempt})&hellip;`);
        break;
      case 'error':
        showBanner('CONNECTION ERROR — RETRYING&hellip;');
        break;
      case 'closed':
        hideBanner();
        break;
      default:
        break;
    }
  });
}

function showBanner(html) {
  const banner = $('#ws-banner');
  const text = $('#ws-banner-text');
  if (text) text.innerHTML = html;
  if (banner) banner.hidden = false;
}

function hideBanner() {
  const banner = $('#ws-banner');
  if (banner) banner.hidden = true;
}
