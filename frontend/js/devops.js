// devops.js — DevOps monitoring panel (Requirements 7.3, 7.8).
//
// Renders the standalone DevOps monitor: latest metric readings plus an alert
// feed. Fed by `devops:alert` events, each carrying { level, message, metric,
// value } (see agents/devops_agent.DevOpsAlert.to_event_data).

import { bus } from './state.js';
import { $, formatClock } from './utils.js';

const METRIC_LABELS = {
  cpu: 'CPU',
  memory: 'MEM',
  disk: 'DISK',
  failed_tasks: 'FAILED',
};

export function initDevOps() {
  const alertsList = $('#devops-alerts');
  const statusDot = $('#devops-status-dot');

  function metricValueEl(metric) {
    const wrap = $(`#devops-metric-${metric}`);
    return wrap ? $('.dm-value', wrap) : null;
  }

  function formatValue(metric, value) {
    if (metric === 'failed_tasks') return String(Math.round(value));
    return `${Math.round(value)}%`;
  }

  bus.on('reset', () => {
    if (alertsList) alertsList.innerHTML = '';
    if (statusDot) statusDot.className = 'status-dot idle';
    for (const metric of Object.keys(METRIC_LABELS)) {
      const el = metricValueEl(metric);
      if (el) {
        el.textContent = '--';
        const wrap = $(`#devops-metric-${metric}`);
        if (wrap) wrap.className = 'devops-metric';
      }
    }
  });

  bus.on('devops:alert', (alert) => {
    if (!alert) return;
    const { level = 'info', message = '', metric, value } = alert;

    // Update the metric readout.
    if (metric && metricValueEl(metric)) {
      metricValueEl(metric).textContent = formatValue(metric, value ?? 0);
      const wrap = $(`#devops-metric-${metric}`);
      if (wrap) wrap.className = `devops-metric level-${level}`;
    }

    // Highlight the panel status dot at the highest current severity.
    if (statusDot) {
      const dotLevel = level === 'critical' ? 'error' : level === 'warning' ? 'working' : 'idle';
      statusDot.className = `status-dot ${dotLevel}`;
    }

    // Prepend to the alert feed.
    if (alertsList) {
      const li = document.createElement('li');
      li.className = `devops-alert-entry level-${level}`;
      const time = document.createElement('span');
      time.className = 'alert-time';
      time.textContent = formatClock();
      const label = METRIC_LABELS[metric] || (metric || 'SYS');
      const text = document.createElement('span');
      text.className = 'alert-text';
      text.textContent = `[${String(level).toUpperCase()}] ${label}: ${message}`;
      li.appendChild(time);
      li.appendChild(text);
      alertsList.prepend(li);
      while (alertsList.children.length > 20) alertsList.removeChild(alertsList.lastChild);
    }
  });
}
