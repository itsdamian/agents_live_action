// state.js — Central state store + event bus, driven by real backend events.
//
// The WebSocket layer (ws.js) calls applyServerEvent(event, data) with the
// backend envelope's event name + data. This module maps those into the
// internal state model and re-emits normalized UI events on `bus`, which the
// rendering modules (stations.js, conveyor.js, devops.js) subscribe to.
//
// Backend event names (see core/pipeline.py + agents/devops_agent.py):
//   snapshot | task:created | station:update | task:completed |
//   task:failed | devops:alert
//
// Task summary shape from the backend:
//   { id, title, priority, status, rework_count }

class EventEmitter {
  constructor() {
    this._handlers = {};
  }
  on(evt, fn) {
    (this._handlers[evt] ||= []).push(fn);
    return () => this.off(evt, fn);
  }
  off(evt, fn) {
    const list = this._handlers[evt];
    if (list) this._handlers[evt] = list.filter((f) => f !== fn);
  }
  emit(evt, data) {
    for (const fn of this._handlers[evt] || []) fn(data);
    for (const fn of this._handlers['*'] || []) fn(evt, data);
  }
}

export const bus = new EventEmitter();

const STATIONS = ['pm', 'swe', 'qa'];

function freshStations() {
  return {
    pm: { id: 'pm', label: 'PM Triage', status: 'idle', current: null, processed: 0, queue: 0 },
    swe: { id: 'swe', label: 'SWE Coding', status: 'idle', current: null, processed: 0, queue: 0 },
    qa: { id: 'qa', label: 'QA Testing', status: 'idle', current: null, processed: 0, queue: 0 },
  };
}

export const state = {
  startTime: Date.now(),
  projectId: null,
  mockMode: false,
  tasks: new Map(), // id -> normalized task
  counters: { created: 0, inFlight: 0, completed: 0, failed: 0, reworks: 0 },
  stations: freshStations(),
  throughputHistory: [], // completion timestamps for t/min calc
};

// --- helpers ---------------------------------------------------------------

function normalizeTask(summary) {
  if (!summary) return null;
  return {
    id: summary.id,
    name: summary.title ?? summary.name ?? summary.id,
    priority: summary.priority ?? 'medium',
    status: summary.status ?? 'pending',
    reworks: summary.rework_count ?? 0,
  };
}

function upsertTask(summary) {
  const task = normalizeTask(summary);
  if (!task) return null;
  const existing = state.tasks.get(task.id);
  if (existing) {
    Object.assign(existing, task);
    return existing;
  }
  state.tasks.set(task.id, task);
  return task;
}

function recordThroughput() {
  const now = Date.now();
  state.throughputHistory.push(now);
  const cutoff = now - 60000;
  state.throughputHistory = state.throughputHistory.filter((ts) => ts > cutoff);
}

export function getThroughput() {
  const cutoff = Date.now() - 60000;
  return state.throughputHistory.filter((ts) => ts > cutoff).length;
}

function emitCounters() {
  bus.emit('counters', state.counters);
}

// --- reset (on project switch / before snapshot) ---------------------------

export function resetState() {
  state.tasks.clear();
  state.counters = { created: 0, inFlight: 0, completed: 0, failed: 0, reworks: 0 };
  state.stations = freshStations();
  state.throughputHistory = [];
  bus.emit('reset');
  emitCounters();
}

// --- server event application ----------------------------------------------

export function applyServerEvent(event, data) {
  switch (event) {
    case 'snapshot':
      applySnapshot(data);
      break;
    case 'task:created':
      applyTaskCreated(data);
      break;
    case 'station:update':
      applyStationUpdate(data);
      break;
    case 'task:completed':
      applyTaskCompleted(data);
      break;
    case 'task:failed':
      applyTaskFailed(data);
      break;
    case 'devops:alert':
      bus.emit('devops:alert', data);
      break;
    default:
      // Unknown event: ignore but keep a breadcrumb for debugging.
      // eslint-disable-next-line no-console
      console.debug('Unhandled WS event:', event, data);
  }
}

function applySnapshot(data) {
  // A snapshot is authoritative: rebuild from scratch.
  resetState();

  const pipeline = data.pipeline || {};
  state.mockMode = Boolean(data.mock_mode);
  bus.emit('mock', state.mockMode);

  const done = pipeline.done_count ?? 0;
  const failed = pipeline.failed_count ?? 0;
  state.counters.completed = done;
  state.counters.failed = failed;

  // Seed the task list from the snapshot (used for the priority mix chart).
  for (const t of data.tasks || []) {
    upsertTask(t);
  }

  const stations = pipeline.stations || {};
  const queueSize = pipeline.queue_size ?? 0;
  for (const sid of STATIONS) {
    const snap = stations[sid];
    const station = state.stations[sid];
    if (snap) {
      station.status = snap.status || 'idle';
      if (snap.task_id) {
        const task = upsertTask({
          id: snap.task_id,
          title: snap.task_title,
          priority: 'medium',
          status: 'in_progress',
        });
        station.current = task ? task.id : null;
        // Surface the in-flight task on the conveyor at its current station.
        if (task) bus.emit('snapshot:active', { station: sid, task });
      } else {
        station.current = null;
      }
    }
    station.queue = sid === 'pm' ? queueSize : 0;
  }

  // In-flight = seeded tasks that are currently in progress.
  state.counters.inFlight = [...state.tasks.values()].filter(
    (t) => t.status === 'in_progress'
  ).length;

  // Replay recent DevOps alerts so the panel is populated on connect.
  for (const alert of data.devops_alerts || []) {
    bus.emit('devops:alert', alert);
  }

  bus.emit('snapshot', { stations: state.stations, queueSize });
  emitStations();
  emitCounters();
}

function emitStations() {
  for (const sid of STATIONS) {
    bus.emit('station:state', state.stations[sid]);
  }
}

function applyTaskCreated(data) {
  const task = upsertTask(data.task);
  if (!task) return;
  state.counters.created += 1;
  state.counters.inFlight += 1;
  if (typeof data.queue_size === 'number') {
    state.stations.pm.queue = data.queue_size;
    bus.emit('station:state', state.stations.pm);
  }
  bus.emit('task:created', task);
  emitCounters();
}

function applyStationUpdate(data) {
  const sid = data.station;
  const station = state.stations[sid];
  if (!station) return;
  const task = upsertTask(data.task);

  if (data.status === 'working') {
    station.status = 'working';
    station.current = task ? task.id : null;
    bus.emit('station:state', station);
    bus.emit('station:working', { station: sid, task });
    return;
  }

  if (data.status === 'rework') {
    // QA bounced the task back to SWE for rework.
    if (task) task.reworks = (task.reworks || 0) + 1;
    state.counters.reworks += 1;
    bus.emit('station:rework', { station: sid, task });
    emitCounters();
    return;
  }

  // 'done' or 'error': the station finished this task and returns to idle.
  station.status = 'idle';
  station.current = null;
  station.processed += 1;
  bus.emit('station:state', station);
  bus.emit('station:finished', { station: sid, task, success: data.success !== false });
}

function applyTaskCompleted(data) {
  const task = upsertTask(data.task);
  if (task) task.status = 'done';
  state.counters.completed = data.done_count ?? state.counters.completed + 1;
  state.counters.inFlight = Math.max(0, state.counters.inFlight - 1);
  recordThroughput();
  bus.emit('task:completed', task);
  emitCounters();
}

function applyTaskFailed(data) {
  const task = upsertTask(data.task);
  if (task) task.status = 'failed';
  state.counters.failed = data.failed_count ?? state.counters.failed + 1;
  state.counters.inFlight = Math.max(0, state.counters.inFlight - 1);
  bus.emit('task:failed', { task, reason: data.reason });
  emitCounters();
}
