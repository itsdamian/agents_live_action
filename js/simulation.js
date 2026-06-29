// simulation.js — Mock data engine (task generation, processing timers)

import { uid, randomInt, randomTaskName, randomPriority } from './utils.js';
import {
  state, bus, addTask, updateTask, completeTask, reworkTask,
  addLog,
} from './state.js';

let genTimer = null;

function createTask() {
  const priority = randomPriority();
  const task = {
    id: uid(),
    name: randomTaskName(),
    priority,
    stage: 'queue',
    createdAt: Date.now(),
    completedAt: null,
    reworks: 0,
    progress: 0,
  };
  addTask(task);
  enqueueToStation('pm', task);
  addLog('system', `Task "${task.name}" [${priority}] created`, 'create');
}

function enqueueToStation(stationId, task) {
  const station = state.stations[stationId];
  task.stage = stationId === 'pm' ? 'queue' : stationId;
  updateTask(task.id, { stage: task.stage });
  station.queue.push(task.id);
  bus.emit('station:queueChanged', station);
  tryProcess(stationId);
}

function tryProcess(stationId) {
  const station = state.stations[stationId];
  if (station.current || station.queue.length === 0) return;

  const taskId = station.queue.shift();
  const task = state.tasks.get(taskId);
  if (!task) return;

  station.current = taskId;
  station.status = 'working';
  task.stage = stationId;
  task.progress = 0;
  updateTask(taskId, { stage: stationId, progress: 0 });
  bus.emit('station:start', { station, task });
  addLog(stationId, `Processing "${task.name}"`, 'start');

  const duration = getProcessDuration(stationId);
  const steps = 20;
  const stepTime = duration / steps;
  let step = 0;

  const interval = setInterval(() => {
    if (state.paused) return;
    step++;
    task.progress = Math.min(step / steps, 1);
    updateTask(taskId, { progress: task.progress });
    bus.emit('station:progress', { station, task });

    if (step >= steps) {
      clearInterval(interval);
      finishProcessing(stationId, task);
    }
  }, stepTime);
}

function getProcessDuration(stationId) {
  switch (stationId) {
    case 'pm':  return randomInt(4000, 10000);
    case 'swe': return randomInt(8000, 20000);
    case 'qa':  return randomInt(5000, 12000);
    default:    return 5000;
  }
}

function finishProcessing(stationId, task) {
  const station = state.stations[stationId];
  station.current = null;
  station.status = 'idle';
  station.processed++;

  bus.emit('station:finish', { station, task });

  switch (stationId) {
    case 'pm':
      addLog('pm', `Triaged "${task.name}" → SWE`, 'done');
      bus.emit('conveyor:move', { task, from: 'pm', to: 'swe' });
      setTimeout(() => enqueueToStation('swe', task), 600);
      break;

    case 'swe':
      addLog('swe', `Developed "${task.name}" → QA`, 'done');
      bus.emit('conveyor:move', { task, from: 'swe', to: 'qa' });
      setTimeout(() => enqueueToStation('qa', task), 600);
      break;

    case 'qa': {
      const pass = Math.random() < 0.82;
      if (pass) {
        addLog('qa', `"${task.name}" PASSED`, 'success');
        bus.emit('conveyor:move', { task, from: 'qa', to: 'done' });
        setTimeout(() => completeTask(task.id), 400);
      } else {
        addLog('qa', `"${task.name}" FAILED → rework`, 'error');
        bus.emit('conveyor:reject', { task, from: 'qa', to: 'swe' });
        reworkTask(task.id);
        setTimeout(() => enqueueToStation('swe', task), 800);
      }
      break;
    }
  }

  // try next in queue
  setTimeout(() => tryProcess(stationId), 300);
}

function scheduleNextTask() {
  if (state.paused) {
    genTimer = setTimeout(scheduleNextTask, 1000);
    return;
  }
  const delay = randomInt(3000, 8000);
  genTimer = setTimeout(() => {
    createTask();
    scheduleNextTask();
  }, delay);
}

export function startSimulation() {
  // Create a few initial tasks quickly
  createTask();
  setTimeout(createTask, 800);
  setTimeout(createTask, 1800);
  scheduleNextTask();
}

export function stopSimulation() {
  clearTimeout(genTimer);
}

export function togglePause() {
  state.paused = !state.paused;
  bus.emit('paused', state.paused);
}
