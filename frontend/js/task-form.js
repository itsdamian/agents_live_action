// task-form.js — "NEW TASK" button + modal form (Task 14).
//
// Lets the user create a task from the UI instead of hand-editing tasks.yaml.
// On submit it POSTs to /api/projects/{id}/tasks via api.js. The backend
// appends the task to tasks.yaml; the running TaskWatcher then enqueues it and
// the pipeline broadcasts a `task:created` WS event — so the new task surfaces
// in the queue automatically, with no frontend polling.

import { createTask } from './api.js';
import { state } from './state.js';
import { $, showToast } from './utils.js';

export function initTaskForm() {
  const openBtn = $('#new-task-btn');
  const modal = $('#new-task-modal');
  const closeBtn = $('#new-task-close');
  const cancelBtn = $('#task-cancel');
  const form = $('#new-task-form');
  const titleInput = $('#task-title');
  const descInput = $('#task-description');
  const prioInput = $('#task-priority');
  const errorBox = $('#task-form-error');
  const submitBtn = $('#task-submit');

  if (!openBtn || !modal || !form) return;

  function showError(message) {
    if (!errorBox) return;
    errorBox.textContent = message;
    errorBox.hidden = false;
  }

  function clearError() {
    if (!errorBox) return;
    errorBox.textContent = '';
    errorBox.hidden = true;
  }

  function openModal() {
    clearError();
    modal.hidden = false;
    // Focus the title after the modal is visible.
    requestAnimationFrame(() => titleInput && titleInput.focus());
  }

  function closeModal() {
    modal.hidden = true;
    form.reset();
    clearError();
  }

  openBtn.addEventListener('click', openModal);
  if (closeBtn) closeBtn.addEventListener('click', closeModal);
  if (cancelBtn) cancelBtn.addEventListener('click', closeModal);

  // Click on the dimmed backdrop (not the dialog) closes the modal.
  modal.addEventListener('click', (evt) => {
    if (evt.target === modal) closeModal();
  });

  // Escape closes the modal while it is open.
  document.addEventListener('keydown', (evt) => {
    if (evt.key === 'Escape' && !modal.hidden) closeModal();
  });

  form.addEventListener('submit', async (evt) => {
    evt.preventDefault();
    clearError();

    const projectId = state.projectId;
    if (!projectId) {
      showError('No project selected.');
      return;
    }

    const title = (titleInput ? titleInput.value : '').trim();
    if (!title) {
      showError('Title is required.');
      if (titleInput) titleInput.focus();
      return;
    }

    const payload = {
      title,
      description: descInput ? descInput.value.trim() : '',
      priority: prioInput ? prioInput.value : 'medium',
    };

    if (submitBtn) submitBtn.disabled = true;
    try {
      const { task } = await createTask(projectId, payload);
      // Success: the task:created WS event (via TaskWatcher) will surface it in
      // the pipeline queue — we only confirm the submission here.
      const label = task && task.title ? task.title : title;
      showToast(`Task created: ${label}`, 'success');
      closeModal();
    } catch (err) {
      // Backend validation failure (or unreachable backend) — show the message.
      showError(err && err.message ? err.message : 'Failed to create task.');
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  });
}
