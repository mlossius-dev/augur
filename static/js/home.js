/* Augur home view — level 1 (trajectory), level 2 (rate), level 3 (changes) */

'use strict';

// ── State ─────────────────────────────────────────────────────────────────────

const state = {
  asOf: null,        // null = now; ISO string = historical
  data: null,        // last loaded home payload
  loading: false,
};

// ── Bootstrap ─────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  initScrubber();
  loadHome();
});

// ── Data loading ──────────────────────────────────────────────────────────────

async function loadHome() {
  if (state.loading) return;
  state.loading = true;

  const params = state.asOf ? `?as_of=${encodeURIComponent(state.asOf)}` : '';
  try {
    const res = await fetch(`/api/home${params}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.data = await res.json();
    renderHome(state.data);
  } catch (err) {
    renderError('dimensionGrid', `Could not load home view: ${err.message}`);
    renderError('changesList', '');
  } finally {
    state.loading = false;
  }
}

// ── Rendering ─────────────────────────────────────────────────────────────────

function renderHome(data) {
  renderDimensions(data.dimensions);
  renderChanges(data.changes);
  updateAsOfLabel(data.as_of);
}

function renderDimensions(dimensions) {
  const grid = document.getElementById('dimensionGrid');
  if (!grid) return;

  if (!dimensions || dimensions.length === 0) {
    grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1">
      Graph is empty — no dimension data yet.
    </div>`;
    return;
  }

  grid.innerHTML = dimensions.map(d => `
    <div class="dimension-card" onclick="drillDimension('${d.dimension}')" title="${d.label}">
      <div class="dim-label">${d.label}</div>
      <div class="dim-state state-${d.state}">${formatState(d.state)}</div>
      <div class="dim-direction dir-${d.direction}">
        <span class="arrow">${dirArrow(d.direction)}</span>${formatDirection(d.direction)}
      </div>
      <div class="sparkline-container">${renderSparkline(d.sparkline)}</div>
      <div class="mt-4 muted" style="font-family:var(--font-ui);font-size:0.7rem">
        ${d.active_conditions}/${d.total_conditions} conditions active
      </div>
    </div>
  `).join('');
}

function renderSparkline(sparkline) {
  if (!sparkline || sparkline.length < 2) {
    return `<svg><line x1="0" y1="16" x2="100%" y2="16" stroke="var(--border)" stroke-width="1"/></svg>`;
  }

  const W = 200, H = 32;
  const maxTotal = Math.max(...sparkline.map(s => s.total_count), 1);
  const pts = sparkline.map((s, i) => {
    const x = (i / (sparkline.length - 1)) * W;
    const ratio = s.total_count > 0 ? s.active_count / s.total_count : 0;
    const y = H - ratio * (H - 4) - 2;
    return `${x},${y}`;
  });

  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <polyline points="${pts.join(' ')}"
      fill="none" stroke="var(--text-muted)" stroke-width="1.5"
      stroke-linejoin="round" stroke-linecap="round"/>
  </svg>`;
}

function renderChanges(changes) {
  const list = document.getElementById('changesList');
  if (!list) return;

  if (!changes || changes.length === 0) {
    list.innerHTML = `<div class="empty-state">No significant changes in the last 24 hours.</div>`;
    return;
  }

  list.innerHTML = changes.map(c => `
    <div class="change-item" onclick="drillChange('${c.target_type}', '${c.target_id}')">
      <span class="change-type-icon ${changeIconClass(c.change_type)}">${changeIconLabel(c.change_type)}</span>
      <div>
        <div class="change-summary">${escHtml(c.summary)}</div>
        <div class="change-dim-tag">${escHtml(c.dimension_label)}</div>
      </div>
      <div class="change-time">${formatRelTime(c.occurred_at)}</div>
    </div>
  `).join('');
}

function renderError(elementId, msg) {
  const el = document.getElementById(elementId);
  if (el && msg) el.innerHTML = `<div class="error-msg">${escHtml(msg)}</div>`;
}

function updateAsOfLabel(asOf) {
  const label = document.getElementById('scrubber-date');
  if (label) {
    label.textContent = asOf
      ? new Date(asOf).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' })
      : 'Now';
  }
}

// ── Drill-in navigation ───────────────────────────────────────────────────────

function drillChange(targetType, targetId) {
  if (targetType === 'node') {
    loadReasoning('node', targetId);
  } else {
    loadReasoning('edge', targetId);
  }
}

function drillDimension(dimension) {
  // Filter changes by dimension inline; use Topics nav for full topic view
  if (state.data) {
    const filtered = state.data.changes.filter(c => c.dimension === dimension);
    renderChanges(filtered.length > 0 ? filtered : state.data.changes);
  }
}

// ── Formatting helpers ────────────────────────────────────────────────────────

function formatState(s) {
  const labels = {
    improving: 'Improving',
    stable: 'Stable',
    strained: 'Strained',
    deteriorating: 'Deteriorating',
    crisis: 'Crisis',
    unknown: 'No data',
  };
  return labels[s] || s;
}

function formatDirection(d) {
  const labels = { improving: 'improving', steady: 'steady', worsening: 'worsening', unknown: '' };
  return labels[d] || d;
}

function dirArrow(d) {
  return { improving: '↓', steady: '→', worsening: '↑', unknown: '' }[d] || '';
}

function changeIconClass(t) {
  const map = {
    edge_strengthened: 'strengthened',
    edge_weakened: 'weakened',
    condition_activated: 'activated',
    condition_deactivated: 'deactivated',
    disconfirmation_weakened: 'disconf',
    edge_created: 'created',
  };
  return map[t] || '';
}

function changeIconLabel(t) {
  const map = {
    edge_strengthened: '↑ strength',
    edge_weakened: '↓ weakened',
    condition_activated: '● activated',
    condition_deactivated: '○ inactive',
    disconfirmation_weakened: '✗ disconf.',
    edge_created: '+ new link',
  };
  return map[t] || t.replace(/_/g, ' ');
}

function formatRelTime(iso) {
  const now = new Date();
  const then = new Date(iso);
  const diffMs = now - then;
  const diffH = Math.floor(diffMs / 3600000);
  const diffM = Math.floor(diffMs / 60000);
  if (diffM < 60) return `${diffM}m ago`;
  if (diffH < 24) return `${diffH}h ago`;
  return then.toLocaleDateString('en-GB', { month: 'short', day: 'numeric' });
}

function escHtml(str) {
  if (!str) return '';
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Time scrubber ─────────────────────────────────────────────────────────────

function initScrubber() {
  const range = document.getElementById('scrubber-range');
  const nowBtn = document.getElementById('scrubber-now-btn');

  if (!range) return;

  // Scrubber spans from 12 months ago to now
  const nowMs = Date.now();
  const oldestMs = nowMs - (365 * 24 * 3600 * 1000);
  range.min = oldestMs;
  range.max = nowMs;
  range.value = nowMs;

  range.addEventListener('input', () => {
    const ms = parseInt(range.value, 10);
    if (ms >= nowMs - 60000) {  // within 1 minute of "now"
      state.asOf = null;
    } else {
      state.asOf = new Date(ms).toISOString();
    }
    updateAsOfLabel(state.asOf ? new Date(ms).toISOString() : null);
  });

  range.addEventListener('change', () => {
    loadHome();
  });

  if (nowBtn) {
    nowBtn.addEventListener('click', () => {
      state.asOf = null;
      range.value = nowMs;
      updateAsOfLabel(null);
      loadHome();
    });
  }
}

// Expose globally for inline onclick
window.drillChange = drillChange;
window.drillDimension = drillDimension;
