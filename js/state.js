// state.js — Central state store + event emitter

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

// Task stages
export const STAGES = ['queue', 'pm', 'swe', 'qa', 'done', 'failed'];

export const state = {
  startTime: Date.now(),
  tasks: new Map(),       // id -> task object
  counters: { created: 0, inFlight: 0, completed: 0, failed: 0, reworks: 0 },
  stations: {
    pm:  { id: 'pm',  label: 'PM Triage',   status: 'idle', queue: [], current: null, processed: 0 },
    swe: { id: 'swe', label: 'SWE Coding',  status: 'idle', queue: [], current: null, processed: 0 },
    qa:  { id: 'qa',  label: 'QA Testing',  status: 'idle', queue: [], current: null, processed: 0 },
  },
  logs: [],               // { time, station, message, type }
  throughputHistory: [],   // last N completed timestamps for throughput calc
  paused: false,
};

export function addTask(task) {
  state.tasks.set(task.id, task);
  state.counters.created++;
  state.counters.inFlight++;
  bus.emit('task:created', task);
  bus.emit('counters', state.counters);
}

export function updateTask(id, patch) {
  const t = state.tasks.get(id);
  if (!t) return;
  Object.assign(t, patch);
  bus.emit('task:updated', t);
}

export function completeTask(id) {
  const t = state.tasks.get(id);
  if (!t) return;
  t.stage = 'done';
  t.completedAt = Date.now();
  state.counters.inFlight--;
  state.counters.completed++;
  state.throughputHistory.push(Date.now());
  // keep only last 60s of history
  const cutoff = Date.now() - 60000;
  state.throughputHistory = state.throughputHistory.filter((ts) => ts > cutoff);
  bus.emit('task:completed', t);
  bus.emit('counters', state.counters);
}

export function failTask(id) {
  const t = state.tasks.get(id);
  if (!t) return;
  t.stage = 'failed';
  state.counters.inFlight--;
  state.counters.failed++;
  bus.emit('task:failed', t);
  bus.emit('counters', state.counters);
}

export function reworkTask(id) {
  const t = state.tasks.get(id);
  if (!t) return;
  t.stage = 'swe';
  t.reworks = (t.reworks || 0) + 1;
  state.counters.reworks++;
  bus.emit('task:rework', t);
}

export function addLog(station, message, type = 'info') {
  const entry = { time: Date.now(), station, message, type };
  state.logs.unshift(entry);
  if (state.logs.length > 80) state.logs.length = 80;
  bus.emit('log', entry);
}

export function getThroughput() {
  const cutoff = Date.now() - 60000;
  const recent = state.throughputHistory.filter((ts) => ts > cutoff);
  return recent.length; // tasks per minute
}
