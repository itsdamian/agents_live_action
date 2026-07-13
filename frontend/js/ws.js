// ws.js — WebSocket connection to /ws/{project_id}.
//
// Responsibilities:
//   * Connect to the backend WS endpoint (nginx proxies /ws → backend:8000
//     with Upgrade headers).
//   * Dispatch every received event envelope into the app state store.
//   * Exponential-backoff reconnect on unexpected close: 2s → 5s → 10s (cap),
//     satisfying Requirement 7.7 (auto-reconnect on disconnect).
//   * Emit connection-status changes so the UI can show a warning banner.
//
// Envelope pushed by the backend (see core/ws_manager.make_event):
//   { "event": "snapshot|station:update|task:created|task:completed|
//               task:failed|devops:alert", "project_id": "...", "data": {...} }

import { bus, applyServerEvent, resetState } from './state.js';

// Reconnect backoff schedule in milliseconds (2s → 5s → 10s, then hold at 10s).
const BACKOFF_SCHEDULE = [2000, 5000, 10000];

// Application-defined close code the backend uses for an unknown project.
const WS_CLOSE_UNKNOWN_PROJECT = 4004;

let socket = null;
let currentProjectId = null;
let reconnectAttempt = 0;
let reconnectTimer = null;
let manualClose = false;

function wsUrl(projectId) {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${window.location.host}/ws/${encodeURIComponent(projectId)}`;
}

function backoffDelay(attempt) {
  const idx = Math.min(attempt, BACKOFF_SCHEDULE.length - 1);
  return BACKOFF_SCHEDULE[idx];
}

function clearReconnect() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}

function scheduleReconnect() {
  clearReconnect();
  const delay = backoffDelay(reconnectAttempt);
  reconnectAttempt += 1;
  bus.emit('connection', { status: 'reconnecting', attempt: reconnectAttempt, delay });
  reconnectTimer = setTimeout(() => {
    if (!manualClose && currentProjectId) {
      openSocket(currentProjectId);
    }
  }, delay);
}

function openSocket(projectId) {
  bus.emit('connection', { status: 'connecting' });
  const ws = new WebSocket(wsUrl(projectId));
  socket = ws;

  ws.addEventListener('open', () => {
    reconnectAttempt = 0;
    clearReconnect();
    bus.emit('connection', { status: 'open' });
  });

  ws.addEventListener('message', (evt) => {
    let envelope;
    try {
      envelope = JSON.parse(evt.data);
    } catch {
      return; // ignore malformed frames
    }
    if (!envelope || typeof envelope.event !== 'string') return;
    applyServerEvent(envelope.event, envelope.data || {});
  });

  ws.addEventListener('close', (evt) => {
    if (manualClose) {
      bus.emit('connection', { status: 'closed' });
      return;
    }
    if (evt.code === WS_CLOSE_UNKNOWN_PROJECT) {
      // Project no longer exists; do not hammer the server with reconnects.
      bus.emit('connection', { status: 'error', reason: evt.reason || 'Unknown project' });
      return;
    }
    scheduleReconnect();
  });

  ws.addEventListener('error', () => {
    // The 'close' handler drives reconnect; just surface the state here.
    bus.emit('connection', { status: 'error' });
  });
}

/**
 * Connect (or reconnect) to a project's WebSocket. Switching projects tears
 * down the previous connection, resets pipeline state, and opens a fresh one
 * (Requirements 1.3, 7.4).
 */
export function connectProject(projectId) {
  // Tear down any existing connection without triggering auto-reconnect.
  manualClose = true;
  clearReconnect();
  if (socket) {
    try { socket.close(); } catch { /* noop */ }
    socket = null;
  }

  currentProjectId = projectId;
  reconnectAttempt = 0;
  manualClose = false;

  // Reset the pipeline display before the new snapshot arrives (Req 7.4).
  resetState();
  openSocket(projectId);
}

/** Permanently close the current connection (e.g. page teardown). */
export function disconnect() {
  manualClose = true;
  clearReconnect();
  if (socket) {
    try { socket.close(); } catch { /* noop */ }
    socket = null;
  }
  currentProjectId = null;
}
