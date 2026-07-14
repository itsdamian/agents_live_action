// task-detail.js — Task result side panel (Task 15).
//
// Clicking a task on the conveyor opens a slide-in side panel that shows, in
// one place, everything the pipeline produced for that task so users never
// have to dig through the projects/ folder:
//   * each station's output (PM / SWE / QA), rebuilt from deliverables
//   * a status history derived from the agent log files
//   * the raw log file contents
//   * the deliverables list, with download links
//
// Data comes from GET /api/projects/{id}/tasks/{task_id} (+ /logs), added in
// this task. Loading and error states are handled inline in the panel body.

import { fetchTaskDetail, approveTask, rejectTask } from './api.js';
import { bus, state } from './state.js';
import { $, showToast } from './utils.js';

let panel;
let backdrop;
let titleEl;
let bodyEl;
let currentTaskId = null;

export function initTaskDetail() {
  panel = $('#task-detail-panel');
  backdrop = $('#task-detail-backdrop');
  titleEl = $('#task-detail-title');
  bodyEl = $('#task-detail-body');
  const closeBtn = $('#task-detail-close');

  if (!panel) return;

  if (closeBtn) closeBtn.addEventListener('click', closePanel);
  if (backdrop) backdrop.addEventListener('click', closePanel);
  document.addEventListener('keydown', (evt) => {
    if (evt.key === 'Escape' && !panel.hidden) closePanel();
  });

  // A task was clicked somewhere in the UI (conveyor item).
  bus.on('task:selected', ({ id }) => {
    if (id) openPanel(id);
  });

  // Keep an open panel in sync when the task it shows crosses an approval gate
  // or a decision resolves it (Task 17), so the approve/reject controls appear
  // and disappear live without the user reopening the panel.
  const refreshIfCurrent = (payload) => {
    const id = payload && payload.task && payload.task.id;
    if (id && currentTaskId === id && panel && !panel.hidden) {
      openPanel(id);
    }
  };
  bus.on('task:awaiting_approval', refreshIfCurrent);
  bus.on('task:approval-resolved', refreshIfCurrent);
}

function openPanel(taskId) {
  currentTaskId = taskId;
  if (titleEl) titleEl.textContent = `> TASK ${taskId}`;
  if (bodyEl) bodyEl.innerHTML = '<div class="td-loading">LOADING&hellip;</div>';
  panel.hidden = false;
  if (backdrop) backdrop.hidden = false;
  requestAnimationFrame(() => panel.classList.add('open'));

  const projectId = state.projectId;
  if (!projectId) {
    renderError('No project selected.');
    return;
  }

  fetchTaskDetail(projectId, taskId)
    .then((detail) => {
      // Guard against a race where the user opened another task meanwhile.
      if (currentTaskId === taskId) render(detail);
    })
    .catch((err) => {
      if (currentTaskId === taskId) {
        renderError(err && err.message ? err.message : 'Failed to load task.');
      }
    });
}

function closePanel() {
  currentTaskId = null;
  panel.classList.remove('open');
  if (backdrop) backdrop.hidden = true;
  // Hide after the slide-out transition so it doesn't flash on next open.
  setTimeout(() => {
    if (!panel.classList.contains('open')) panel.hidden = true;
  }, 300);
}

function renderError(message) {
  if (bodyEl) bodyEl.innerHTML = `<div class="td-error">${escapeHtml(message)}</div>`;
}

function render(detail) {
  const task = detail.task || {};
  const sections = [];

  sections.push(renderMeta(task));
  // Approval gate controls appear only while the task is paused at a gate.
  const awaiting = String(task.status || '') === 'awaiting_approval';
  if (awaiting) sections.push(renderApproval(task));
  sections.push(renderStatusHistory(detail.status_history || []));
  sections.push(
    renderOutput('PM OUTPUT', task.pm_output),
    renderOutput('SWE OUTPUT', task.swe_output),
    renderOutput('QA OUTPUT', task.qa_output)
  );
  sections.push(renderDeliverables(detail.deliverables || []));
  sections.push(renderLogs(detail.logs || []));

  bodyEl.innerHTML = sections.join('');

  if (awaiting) wireApproval(task.id);
}

function renderApproval(task) {
  const inner = `
    <div class="td-approval">
      <p class="td-approval-hint">This task is paused for your review. Approve to continue, or reject with a note to send it back for rework.</p>
      <textarea id="td-approval-note" class="td-approval-note" rows="3"
        placeholder="Rejection note (sent to SWE on rework)"></textarea>
      <div class="td-approval-actions">
        <button type="button" id="td-approve-btn" class="td-btn td-btn-approve">APPROVE</button>
        <button type="button" id="td-reject-btn" class="td-btn td-btn-reject">REJECT</button>
      </div>
      <div id="td-approval-error" class="td-error" hidden></div>
    </div>`;
  return section('APPROVAL REQUIRED', inner);
}

function wireApproval(taskId) {
  const approveBtn = $('#td-approve-btn', bodyEl);
  const rejectBtn = $('#td-reject-btn', bodyEl);
  const noteEl = $('#td-approval-note', bodyEl);
  const errorEl = $('#td-approval-error', bodyEl);
  const projectId = state.projectId;

  const setBusy = (busy) => {
    if (approveBtn) approveBtn.disabled = busy;
    if (rejectBtn) rejectBtn.disabled = busy;
  };
  const showError = (msg) => {
    if (!errorEl) return;
    errorEl.textContent = msg;
    errorEl.hidden = false;
  };

  if (approveBtn) {
    approveBtn.addEventListener('click', async () => {
      if (!projectId) return showError('No project selected.');
      setBusy(true);
      try {
        await approveTask(projectId, taskId);
        showToast(`Task ${taskId} approved`, 'success');
        // The task:approved WS event will refresh the panel.
      } catch (err) {
        showError(err && err.message ? err.message : 'Approve failed.');
        setBusy(false);
      }
    });
  }

  if (rejectBtn) {
    rejectBtn.addEventListener('click', async () => {
      if (!projectId) return showError('No project selected.');
      const note = noteEl ? noteEl.value.trim() : '';
      setBusy(true);
      try {
        await rejectTask(projectId, taskId, note);
        showToast(`Task ${taskId} rejected — sent to rework`, 'info');
      } catch (err) {
        showError(err && err.message ? err.message : 'Reject failed.');
        setBusy(false);
      }
    });
  }
}

function renderMeta(task) {
  const rows = [
    ['STATUS', badge(task.status)],
    ['PRIORITY', escapeHtml(task.priority)],
    ['REWORKS', String(task.rework_count ?? 0)],
    ['CREATED', formatTs(task.created_at)],
    ['UPDATED', formatTs(task.updated_at)],
  ];
  const meta = rows
    .map(
      ([k, v]) =>
        `<div class="td-meta-row"><span class="td-meta-key">${k}</span><span class="td-meta-val">${v}</span></div>`
    )
    .join('');
  return `
    <div class="td-section">
      <div class="td-task-title">${escapeHtml(task.title || task.id || '')}</div>
      ${task.description ? `<div class="td-task-desc">${escapeHtml(task.description)}</div>` : ''}
      <div class="td-meta">${meta}</div>
    </div>`;
}

function renderStatusHistory(history) {
  if (!history.length) {
    return section('STATUS HISTORY', '<div class="td-empty">No history yet.</div>');
  }
  const items = history
    .map(
      (h) => `
      <li class="td-hist-item">
        <span class="td-hist-status ${statusClass(h.status)}">${escapeHtml(h.status || '?')}</span>
        <span class="td-hist-agent">${escapeHtml(h.agent || '')}</span>
        <span class="td-hist-time">${formatTs(h.timestamp)}</span>
      </li>`
    )
    .join('');
  return section('STATUS HISTORY', `<ul class="td-hist">${items}</ul>`);
}

function renderOutput(label, text) {
  if (!text) {
    return section(label, '<div class="td-empty">No output.</div>');
  }
  return section(label, `<pre class="td-pre">${escapeHtml(text)}</pre>`);
}

function renderDeliverables(deliverables) {
  if (!deliverables.length) {
    return section('DELIVERABLES', '<div class="td-empty">No deliverables.</div>');
  }
  const items = deliverables
    .map(
      (d) => `
      <li class="td-file">
        <a href="${escapeAttr(d.download_url)}" download class="td-file-link">${escapeHtml(d.name)}</a>
        <span class="td-file-size">${formatSize(d.size)}</span>
      </li>`
    )
    .join('');
  return section('DELIVERABLES', `<ul class="td-files">${items}</ul>`);
}

function renderLogs(logs) {
  if (!logs.length) {
    return section('LOGS', '<div class="td-empty">No logs.</div>');
  }
  const items = logs
    .map((log) => {
      const header = `${escapeHtml(log.agent || log.name)} · ${escapeHtml(log.status || '')} · ${formatTs(log.timestamp)}`;
      const body = log.content
        ? `<pre class="td-pre td-log-content">${escapeHtml(log.content)}</pre>`
        : `<a href="${escapeAttr(log.content_url)}" target="_blank" rel="noopener" class="td-file-link">view log</a>`;
      return `
        <details class="td-log">
          <summary class="td-log-summary">${header}</summary>
          ${body}
        </details>`;
    })
    .join('');
  return section('LOGS', items);
}

// --- small render helpers --------------------------------------------------

function section(label, inner) {
  return `<div class="td-section"><div class="td-section-title">&gt; ${label}</div>${inner}</div>`;
}

function badge(status) {
  return `<span class="td-badge ${statusClass(status)}">${escapeHtml(status || 'unknown')}</span>`;
}

function statusClass(status) {
  const s = String(status || '').toLowerCase();
  if (s.includes('done') || s === 'success') return 'st-done';
  if (s.includes('fail')) return 'st-failed';
  if (s.includes('progress')) return 'st-progress';
  return 'st-pending';
}

function formatTs(value) {
  if (!value) return '--';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return escapeHtml(String(value));
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function formatSize(bytes) {
  if (typeof bytes !== 'number') return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function escapeAttr(value) {
  return escapeHtml(value);
}
