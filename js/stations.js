// stations.js — Station panel UI rendering & updates

import { state, bus, getThroughput } from './state.js';
import { $, formatTime, formatClock, PRIORITY_COLORS } from './utils.js';

// ---------- Header ----------

export function initHeader() {
  const clockEl = $('#header-clock');
  const uptimeEl = $('#header-uptime');
  const throughputEl = $('#header-throughput');

  function tick() {
    clockEl.textContent = formatClock();
    uptimeEl.textContent = formatTime(Date.now() - state.startTime);
    throughputEl.textContent = `${getThroughput()} tasks/min`;
  }
  tick();
  setInterval(tick, 1000);
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
    const total = c.completed + c.failed;
    const quota = 50; // demo quota
    const pct = Math.min((c.completed / quota) * 100, 100);
    quotaFill.style.width = `${pct}%`;
    quotaLabel.textContent = `${c.completed} / ${quota}`;
    metricCompleted.textContent = c.completed;
    metricReworks.textContent = c.reworks;
    metricInFlight.textContent = c.inFlight;

    // priority distribution
    const tasks = [...state.tasks.values()];
    const counts = { critical: 0, high: 0, medium: 0, low: 0 };
    for (const t of tasks) counts[t.priority]++;
    const max = Math.max(1, tasks.length);
    for (const [k, bar] of Object.entries(prioBars)) {
      bar.style.width = `${(counts[k] / max) * 100}%`;
      bar.title = `${k}: ${counts[k]}`;
    }
  }

  bus.on('counters', update);
  bus.on('task:created', update);
  bus.on('task:completed', update);
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

  function updateUI() {
    const station = state.stations[stationId];

    // status dot
    statusDot.className = `status-dot ${station.status}`;

    // toggle working class on card for typing dots
    card.classList.toggle('working', station.status === 'working');

    // queue
    queueCount.textContent = station.queue.length;

    // progress
    const task = station.current ? state.tasks.get(station.current) : null;
    if (task) {
      progressBar.style.width = `${task.progress * 100}%`;
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

  bus.on('station:start', ({ station }) => {
    if (station.id === stationId) updateUI();
  });
  bus.on('station:progress', ({ station }) => {
    if (station.id === stationId) updateUI();
  });
  bus.on('station:finish', ({ station }) => {
    if (station.id === stationId) updateUI();
  });
  bus.on('station:queueChanged', (station) => {
    if (station.id === stationId) updateUI();
  });

  // logs
  bus.on('log', (entry) => {
    if (entry.station !== stationId) return;
    const li = document.createElement('li');
    li.className = `log-entry log-${entry.type}`;
    li.textContent = entry.message;
    logList.prepend(li);
    // keep only recent
    while (logList.children.length > 12) logList.removeChild(logList.lastChild);
  });

  updateUI();
}

// ---------- Footer Counters ----------

export function initFooter() {
  const created = $('#footer-created');
  const inflight = $('#footer-inflight');
  const completed = $('#footer-completed');
  const failed = $('#footer-failed');

  function bump(el) {
    el.classList.remove('bump');
    void el.offsetWidth; // force reflow
    el.classList.add('bump');
  }

  let prev = { ...state.counters };

  function update() {
    const c = state.counters;
    if (c.created !== prev.created)   { created.textContent = c.created;     bump(created); }
    if (c.inFlight !== prev.inFlight) { inflight.textContent = c.inFlight;   bump(inflight); }
    if (c.completed !== prev.completed) { completed.textContent = c.completed; bump(completed); }
    if (c.failed !== prev.failed)     { failed.textContent = c.failed;       bump(failed); }
    prev = { ...c };
  }

  bus.on('counters', update);
  update();
}
