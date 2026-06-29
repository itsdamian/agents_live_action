// conveyor.js — Conveyor belt rendering, task items, SVG oversight lines

import { state, bus } from './state.js';
import { $, PRIORITY_COLORS } from './utils.js';

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

  bus.on('task:created', (task) => createItemEl(task));

  bus.on('conveyor:move', ({ task, from, to }) => moveItem(task.id, to));

  bus.on('conveyor:reject', ({ task }) => rejectItem(task.id));

  bus.on('task:completed', (task) => completeItem(task.id));

  bus.on('station:start', ({ station, task }) => {
    const el = itemEls.get(task.id);
    if (el) {
      el.classList.add('processing');
      moveItem(task.id, station.id);
    }
  });

  bus.on('station:finish', ({ station, task }) => {
    const el = itemEls.get(task.id);
    if (el) el.classList.remove('processing');
  });

  bus.on('task:rework', (task) => {
    const el = itemEls.get(task.id);
    if (!el) return;
    // add/update rework badge
    let badge = el.querySelector('.rework-badge');
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'rework-badge';
      el.appendChild(badge);
    }
    badge.textContent = task.reworks;
  });

  drawOversightLines();
}

function createItemEl(task) {
  const el = document.createElement('div');
  el.className = `conveyor-item priority-${task.priority}`;
  el.style.left = `${STATION_POSITIONS.queue}%`;
  el.dataset.taskId = task.id;

  // priority circle
  const prioTag = document.createElement('span');
  prioTag.className = `item-prio prio-${task.priority}`;
  prioTag.textContent = task.priority[0].toUpperCase();
  el.appendChild(prioTag);

  // label
  const label = document.createElement('span');
  label.className = 'item-label';
  label.textContent = task.name.length > 16 ? task.name.substring(0, 15) + '\u2026' : task.name;
  el.appendChild(label);

  // check overlay (hidden until completed)
  const check = document.createElement('div');
  check.className = 'item-check';
  check.textContent = '\u2713';
  el.appendChild(check);

  beltContainer.appendChild(el);
  itemEls.set(task.id, el);

  // animate entrance
  requestAnimationFrame(() => {
    el.classList.add('entered');
    moveItem(task.id, 'pm');
  });
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
  // spawn red particles
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
  // spawn success particles
  spawnParticles(el, 'var(--success)', 10);
  el.classList.add('completed');
  setTimeout(() => {
    el.remove();
    itemEls.delete(taskId);
  }, 1400);
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

      // Curved path
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
