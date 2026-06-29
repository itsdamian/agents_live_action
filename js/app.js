// app.js — Entry point, bootstraps everything

import { initHeader, initManagerOffice, initStations, initFooter } from './stations.js';
import { initConveyor } from './conveyor.js';
import { startSimulation, togglePause } from './simulation.js';
import { $ } from './utils.js';
import { bus } from './state.js';

document.addEventListener('DOMContentLoaded', () => {
  initHeader();
  initManagerOffice();
  initConveyor();
  initStations();
  initFooter();

  // Pause/resume button
  const pauseBtn = $('#pause-btn');
  if (pauseBtn) {
    pauseBtn.addEventListener('click', () => {
      togglePause();
    });
    bus.on('paused', (paused) => {
      pauseBtn.textContent = paused ? 'Resume' : 'Pause';
      pauseBtn.classList.toggle('paused', paused);
      document.body.classList.toggle('sim-paused', paused);
    });
  }

  startSimulation();
});
