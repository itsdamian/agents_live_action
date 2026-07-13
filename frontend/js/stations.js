// stations.js — Header, Manager's Office, station cards + footer rendering.
//
// Subscribes to normalized bus events from state.js (real backend data) and
// updates the sci-fi HUD panels. No mock data is generated here.

import { state, bus, getThroughput } from './state.js';
import { $, formatTime, formatClock } from './utils.js';

// ---------- Header ----------

export function initHeader() {
  const clockEl = $('#header-clock');
  const uptimeEl = $('#header-uptime');
  const throughputEl = $('#header-throughput');

  function tick() {
    clockEl.textContent = formatClock();
    uptimeEl.textContent = formatTime(Date.now() - state.startTime);
    throughputEl.textContent = `${getThroughput()} t/min`;
  }
  tick();
  setInterval(tick, 1000);

  // [MOCK MODE] indicator (Req 7.6).
  const mockEl = $('#mock-indicator');
  bus.on('mock', (isMock) => {
    if (mockEl) mockEl.hidden = !isMock;
  });
}

// ---------- Manager's Office ----------

export function initManagerOffice() {
  const quotaFill = $('#quota-fill');
  const quotaLabel = $('#quota-label');
  const metricCompleted = $('#metric-completed');
  const metricReworks = $('#metric-reworks');
  const metricInFlight = $('#metric-inflight');
  const prioBars = {
    critical: $('#prio-critical'),
    high: $('#prio-high'),
    medium: $('#prio-medium'),
    low: $('#prio-low'),
  };

  function update() {
    const c = state.counters;
    const quota = 50;
    const pct = Math.min((c.completed / quota) * 100, 100);
    quotaFill.style.width = `${pct}%`;
    quotaLabel.textContent = `${c.completed} / ${quota}`;
    metricCompleted.textContent = c.completed;
    metricReworks.textContent = c.reworks;
    metricInFlight.textContent = c.inFlight;

    const tasks = [...state.tasks.values()];
    const counts = { critical: 0, high: 0, medium: 0, low: 0 };
    for (const t of tasks) {
      if (counts[t.priority] !== undefined) counts[t.priority]++;
    }
    const max = Math.max(1, tasks.length);
    for (const [k, bar] of Object.entries(prioBars)) {
      if (!bar) continue;
      bar.style.width = `${(counts[k] / max) * 100}%`;
      bar.title = `${k}: ${counts[k]}`;
    }
  }

  bus.on('counters', update);
  bus.on('task:created', update);
  bus.on('task:completed', update);
  bus.on('snapshot', update);
  bus.on('reset', update);
  update();
}

// ---------- Station Cards ----------

export function initStations() {
  for (const id of ['pm', 'swe', 'qa']) {
    initStationCard(id);
  }
}

function initStationCard(stationId) {
  const card = $(`#station-${stationId}`);
  const statusDot = $(`.status-dot`, card);
  const queueCount = $(`.queue-count`, card);
  const progressBar = $(`.progress-fill`, card);
  const currentLabel = $(`.current-task`, card);
  const processedCount = $(`.processed-count`, card);
  const logList = $(`.station-log`, card);

  function render() {
    const station = state.stations[stationId];
    statusDot.className = `status-dot ${station.status}`;
    card.classList.toggle('working', station.status === 'working');
    queueCount.textContent = station.queue ?? 0;

    const task = station.current ? state.tasks.get(station.current) : null;
    if (task) {
      progressBar.style.width = '70%';
      progressBar.classList.add('active');
      currentLabel.textContent = task.name;
      currentLabel.className = 'current-task active';
    } else {
      progressBar.style.width = '0%';
      progressBar.classList.remove('active');
      currentLabel.textContent = '-- IDLE --';
      currentLabel.className = 'current-task';
    }
    processedCount.textContent = station.processed;
  }

  function appendLog(message, type = 'info') {
    const li = document.createElement('li');
    li.className = `log-entry log-${type}`;
    li.textContent = message;
    logList.prepend(li);
    while (logList.children.length > 12) logList.removeChild(logList.lastChild);
  }

  bus.on('station:state', (station) => {
    if (station.id === stationId) render();
  });

  bus.on('reset', () => {
    logList.innerHTML = '';
    render();
  });

  bus.on('station:working', ({ station, task }) => {
    if (station === stationId && task) appendLog(`Processing "${task.name}"`, 'start');
  });

  bus.on('station:finished', ({ station, task, success }) => {
    if (station !== stationId || !task) return;
    appendLog(
      success ? `Finished "${task.name}"` : `Error on "${task.name}"`,
      success ? 'done' : 'error'
    );
  });

  bus.on('station:rework', ({ station, task }) => {
    if (station === stationId && task) {
      appendLog(`"${task.name}" sent back for rework (#${task.reworks})`, 'error');
    }
  });

  bus.on('task:completed', (task) => {
    if (stationId === 'qa' && task) appendLog(`"${task.name}" PASSED`, 'success');
  });

  bus.on('task:failed', ({ task, reason }) => {
    if (stationId === 'qa' && task) {
      appendLog(`"${task.name}" FAILED: ${reason || 'max reworks'}`, 'error');
    }
  });

  render();
}

// ---------- Footer Counters ----------

export function initFooter() {
  const created = $('#footer-created');
  const inflight = $('#footer-inflight');
  const completed = $('#footer-completed');
  const failed = $('#footer-failed');

  function bump(el) {
    el.classList.remove('bump');
    void el.offsetWidth;
    el.classList.add('bump');
  }

  let prev = { ...state.counters };

  function update() {
    const c = state.counters;
    if (c.created !== prev.created) { created.textContent = c.created; bump(created); }
    if (c.inFlight !== prev.inFlight) { inflight.textContent = c.inFlight; bump(inflight); }
    if (c.completed !== prev.completed) { completed.textContent = c.completed; bump(completed); }
    if (c.failed !== prev.failed) { failed.textContent = c.failed; bump(failed); }
    prev = { ...c };
  }

  bus.on('counters', update);
  bus.on('reset', update);
  update();
}
