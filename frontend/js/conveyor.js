// conveyor.js — Conveyor belt rendering, task items, SVG oversight lines.
//
// Driven entirely by normalized bus events emitted from state.js (which in
// turn are fed by real backend WebSocket events). The belt animation and
// particle effects from the original mock UI are preserved (Requirement 7.5).

import { bus } from './state.js';
import { $ } from './utils.js';

const STATION_POSITIONS = {
  queue: 1,
  pm: 15,
  swe: 48,
  qa: 80,
  done: 98,
};

let beltContainer;
let particlesContainer;
let svgOverlay;
const itemEls = new Map(); // taskId -> DOM element

export function initConveyor() {
  beltContainer = $('#conveyor-items');
  particlesContainer = $('#particles-container');
  svgOverlay = $('#oversight-svg');

  bus.on('reset', clearAll);

  // New task enters the line at intake, then slides to PM.
  bus.on('task:created', (task) => {
    if (!task) return;
    createItemEl(task, 'queue');
    requestAnimationFrame(() => moveItem(task.id, 'pm'));
  });

  // Snapshot: a task already in flight — drop it directly at its station.
  bus.on('snapshot:active', ({ station, task }) => {
    if (!task) return;
    createItemEl(task, station);
    const el = itemEls.get(task.id);
    if (el) el.classList.add('processing');
  });

  // A station started working on a task: slide the item there + pulse it.
  bus.on('station:working', ({ station, task }) => {
    if (!task) return;
    let el = itemEls.get(task.id);
    if (!el) {
      createItemEl(task, station);
      el = itemEls.get(task.id);
    }
    el.classList.add('processing');
    moveItem(task.id, station);
  });

  bus.on('station:finished', ({ task }) => {
    if (!task) return;
    const el = itemEls.get(task.id);
    if (el) el.classList.remove('processing');
  });

  // QA bounced the task back to SWE: reject bounce + rework badge.
  bus.on('station:rework', ({ task }) => {
    if (!task) return;
    rejectItem(task.id);
    const el = itemEls.get(task.id);
    if (!el) return;
    let badge = el.querySelector('.rework-badge');
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'rework-badge';
      el.appendChild(badge);
    }
    badge.textContent = task.reworks ?? 1;
  });

  bus.on('task:completed', (task) => {
    if (task) completeItem(task.id);
  });

  bus.on('task:failed', ({ task }) => {
    if (task) failItem(task.id);
  });

  drawOversightLines();
}

function clearAll() {
  if (beltContainer) beltContainer.innerHTML = '';
  itemEls.clear();
}

function createItemEl(task, atStation = 'queue') {
  if (itemEls.has(task.id)) return;
  const el = document.createElement('div');
  el.className = `conveyor-item priority-${task.priority}`;
  el.style.left = `${STATION_POSITIONS[atStation] ?? STATION_POSITIONS.queue}%`;
  el.dataset.taskId = task.id;

  const prioTag = document.createElement('span');
  prioTag.className = `item-prio prio-${task.priority}`;
  prioTag.textContent = String(task.priority)[0].toUpperCase();
  el.appendChild(prioTag);

  const label = document.createElement('span');
  label.className = 'item-label';
  const name = task.name || task.id;
  label.textContent = name.length > 16 ? name.substring(0, 15) + '\u2026' : name;
  el.appendChild(label);

  const check = document.createElement('div');
  check.className = 'item-check';
  check.textContent = '\u2713';
  el.appendChild(check);

  beltContainer.appendChild(el);
  itemEls.set(task.id, el);

  requestAnimationFrame(() => el.classList.add('entered'));
}

function moveItem(taskId, toStation) {
  const el = itemEls.get(taskId);
  if (!el) return;
  const pct = STATION_POSITIONS[toStation] ?? STATION_POSITIONS.queue;
  el.style.left = `${pct}%`;
}

function rejectItem(taskId) {
  const el = itemEls.get(taskId);
  if (!el) return;
  el.classList.add('rejected');
  spawnParticles(el, 'var(--danger)', 6);
  setTimeout(() => {
    el.classList.remove('rejected');
    moveItem(taskId, 'swe');
  }, 700);
}

function completeItem(taskId) {
  const el = itemEls.get(taskId);
  if (!el) return;
  moveItem(taskId, 'done');
  spawnParticles(el, 'var(--success)', 10);
  el.classList.add('completed');
  setTimeout(() => {
    el.remove();
    itemEls.delete(taskId);
  }, 1400);
}

function failItem(taskId) {
  const el = itemEls.get(taskId);
  if (!el) return;
  el.classList.add('rejected');
  spawnParticles(el, 'var(--danger)', 10);
  setTimeout(() => {
    el.remove();
    itemEls.delete(taskId);
  }, 1000);
}

function spawnParticles(anchorEl, color, count) {
  if (!particlesContainer) return;
  const rect = anchorEl.getBoundingClientRect();
  const containerRect = particlesContainer.getBoundingClientRect();
  const cx = rect.left + rect.width / 2 - containerRect.left;
  const cy = rect.top + rect.height / 2 - containerRect.top;

  for (let i = 0; i < count; i++) {
    const p = document.createElement('div');
    p.className = 'particle';
    const angle = (Math.PI * 2 * i) / count + (Math.random() - 0.5) * 0.5;
    const dist = 20 + Math.random() * 30;
    const px = Math.cos(angle) * dist;
    const py = Math.sin(angle) * dist;
    p.style.cssText = `
      left: ${cx}px; top: ${cy}px;
      background: ${color};
      --px: ${px}px; --py: ${py}px;
      width: ${4 + Math.random() * 4}px;
      height: ${4 + Math.random() * 4}px;
      animation-duration: ${0.5 + Math.random() * 0.4}s;
    `;
    particlesContainer.appendChild(p);
    setTimeout(() => p.remove(), 1000);
  }
}

function drawOversightLines() {
  const svg = svgOverlay;
  if (!svg) return;

  function updateLines() {
    const officeEl = $('#manager-office');
    const svgRect = svg.getBoundingClientRect();
    svg.innerHTML = '';

    if (!officeEl) return;
    const officeRect = officeEl.getBoundingClientRect();
    const ox = officeRect.left + officeRect.width / 2 - svgRect.left;
    const oy = officeRect.bottom - svgRect.top;

    const colors = { pm: 'rgba(124,58,237,0.25)', swe: 'rgba(0,136,221,0.25)', qa: 'rgba(0,168,102,0.25)' };

    for (const sid of ['pm', 'swe', 'qa']) {
      const stEl = $(`#station-${sid}`);
      if (!stEl) continue;
      const stRect = stEl.getBoundingClientRect();
      const sx = stRect.left + stRect.width / 2 - svgRect.left;
      const sy = stRect.top - svgRect.top;

      const midY = (oy + sy) / 2;
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', `M ${ox} ${oy} C ${ox} ${midY}, ${sx} ${midY}, ${sx} ${sy}`);
      path.setAttribute('fill', 'none');
      path.setAttribute('stroke', colors[sid]);
      path.setAttribute('stroke-width', '1.5');
      path.setAttribute('stroke-dasharray', '6 4');
      path.setAttribute('class', 'oversight-line');
      svg.appendChild(path);
    }
  }

  updateLines();
  window.addEventListener('resize', updateLines);
  setInterval(updateLines, 3000);
}
