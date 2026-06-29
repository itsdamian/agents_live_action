// utils.js — Helpers (random ID, formatting, DOM utils)

let _idCounter = 0;

export function uid() {
  return `t-${Date.now().toString(36)}-${(++_idCounter).toString(36)}`;
}

export function randomInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

export function pick(arr) {
  return arr[randomInt(0, arr.length - 1)];
}

export function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

export function $(sel, root = document) {
  return root.querySelector(sel);
}

export function $$(sel, root = document) {
  return [...root.querySelectorAll(sel)];
}

export function el(tag, attrs = {}, children = []) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'className') e.className = v;
    else if (k === 'textContent') e.textContent = v;
    else if (k === 'innerHTML') e.innerHTML = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2).toLowerCase(), v);
    else e.setAttribute(k, v);
  }
  for (const c of children) {
    if (typeof c === 'string') e.appendChild(document.createTextNode(c));
    else if (c) e.appendChild(c);
  }
  return e;
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

const TASK_NAMES = [
  'Auth Flow Refactor', 'Cache Layer', 'DB Migration', 'API Rate Limiter',
  'Search Indexer', 'Webhook Handler', 'SSO Integration', 'Log Aggregator',
  'Config Validator', 'Health Check EP', 'Session Manager', 'File Uploader',
  'Email Templates', 'Role Permissions', 'Audit Trail', 'Data Export',
  'Batch Processor', 'Error Boundary', 'Feature Flags', 'Retry Logic',
  'Token Refresh', 'Input Sanitizer', 'Queue Worker', 'Schema Validator',
  'Notification Svc', 'Payment Gateway', 'Analytics Hook', 'Dark Mode',
];

export function randomTaskName() {
  return pick(TASK_NAMES);
}

export const PRIORITIES = ['critical', 'high', 'medium', 'low'];

export function randomPriority() {
  const r = Math.random();
  if (r < 0.08) return 'critical';
  if (r < 0.28) return 'high';
  if (r < 0.68) return 'medium';
  return 'low';
}

export const PRIORITY_COLORS = {
  critical: '#e53e3e',
  high: '#dd6b20',
  medium: '#3182ce',
  low: '#38a169',
};
