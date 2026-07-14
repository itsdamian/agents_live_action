// utils.js — DOM + formatting helpers.

export function $(sel, root = document) {
  return root.querySelector(sel);
}

export function $$(sel, root = document) {
  return [...root.querySelectorAll(sel)];
}

export function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

export function formatTime(ms) {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(h)}:${pad(m % 60)}:${pad(s % 60)}`;
}

export function formatClock() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export const PRIORITY_COLORS = {
  critical: '#e53e3e',
  high: '#dd6b20',
  medium: '#3182ce',
  low: '#38a169',
};

/**
 * Show a transient toast notification in #toast-container.
 * @param {string} message
 * @param {'success'|'error'|'info'} [type='info']
 * @param {number} [duration=4000] ms before auto-dismiss
 */
export function showToast(message, type = 'info', duration = 4000) {
  const container = $('#toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.setAttribute('role', 'status');
  toast.textContent = message;

  container.appendChild(toast);
  // Trigger enter transition on next frame.
  requestAnimationFrame(() => toast.classList.add('toast-show'));

  const remove = () => {
    toast.classList.remove('toast-show');
    toast.addEventListener('transitionend', () => toast.remove(), { once: true });
    // Fallback removal in case the transition never fires.
    setTimeout(() => toast.remove(), 400);
  };

  setTimeout(remove, duration);
}
