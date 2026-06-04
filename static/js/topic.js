/* Augur topic view — level 4 (topic clusters) */

'use strict';

// ── Entry point ───────────────────────────────────────────────────────────────

async function loadTopicView(topicId) {
  showTopicView();

  const container = document.getElementById('topic-content');
  container.innerHTML = `<div class="loading">Loading topic…</div>`;

  try {
    const asOfParam = window._augurAsOf ? `?as_of=${encodeURIComponent(window._augurAsOf)}` : '';
    const res = await fetch(`/api/topics/${topicId}${asOfParam}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderTopicDetail(container, data);
  } catch (err) {
    container.innerHTML = `<div class="error-msg">Failed to load topic: ${escHtml(err.message)}</div>`;
  }
}

async function loadTopicList() {
  const container = document.getElementById('topic-list-content');
  if (!container) return;

  container.innerHTML = `<div class="loading">Loading topics…</div>`;

  try {
    const res = await fetch('/api/topics');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderTopicList(container, data.topics || []);
  } catch (err) {
    container.innerHTML = `<div class="error-msg">Failed to load topics: ${escHtml(err.message)}</div>`;
  }
}

function showTopicView() {
  document.getElementById('home-view').style.display = 'none';
  const rv = document.getElementById('topic-view');
  if (rv) rv.style.display = 'block';
  window.scrollTo(0, 0);
}

function showTopicListView() {
  document.getElementById('home-view').style.display = 'none';
  const rv = document.getElementById('topic-list-view');
  if (rv) {
    rv.style.display = 'block';
    loadTopicList();
  }
  window.scrollTo(0, 0);
}

// ── Rendering ─────────────────────────────────────────────────────────────────

function renderTopicList(container, topics) {
  if (!topics || topics.length === 0) {
    container.innerHTML = `<div class="empty-state">No topics defined yet. Use the CLI to create topics.</div>`;
    return;
  }

  const items = topics.map(t => `
    <div class="topic-card" onclick="loadTopicView('${t.topic_id}')">
      <div class="topic-name">${escHtml(t.name)}</div>
      ${t.dimension ? `<div class="topic-dimension">${escHtml(dimLabel(t.dimension))}</div>` : ''}
      <div class="topic-state state-${t.state}">${formatState(t.state)}</div>
      <div class="topic-stats muted">
        ${t.active_condition_count}/${t.node_count} conditions active
      </div>
      ${t.description ? `<div class="topic-desc muted">${escHtml(t.description)}</div>` : ''}
    </div>
  `).join('');

  container.innerHTML = `<div class="topic-grid">${items}</div>`;
}

function renderTopicDetail(container, data) {
  const { nodes, state, name, description, dimension, node_count, active_condition_count } = data;

  const conditionNodes = (nodes || []).filter(n => n.node_type === 'condition');
  const otherNodes = (nodes || []).filter(n => n.node_type !== 'condition');

  container.innerHTML = `
    <div class="reasoning-header">
      <div class="back-link" onclick="showTopicListView()">← Back to topics</div>
      <div class="reasoning-node-name">${escHtml(name)}</div>
      <div class="reasoning-node-type">
        topic${dimension ? ` · ${escHtml(dimLabel(dimension))}` : ''}
        · <span class="state-${state}">${formatState(state)}</span>
      </div>
      ${description ? `<p class="reasoning-text mt-8">${escHtml(description)}</p>` : ''}
    </div>

    <div class="reasoning-section">
      <div class="reasoning-label">Overview</div>
      <div class="muted" style="font-family:var(--font-ui);font-size:0.85rem">
        ${active_condition_count} of ${node_count} conditions active
      </div>
    </div>

    ${conditionNodes.length > 0 ? renderTopicNodeGroup('Condition nodes', conditionNodes) : ''}
    ${otherNodes.length > 0 ? renderTopicNodeGroup('Other nodes', otherNodes) : ''}
    ${nodes && nodes.length === 0 ? `<div class="reasoning-section"><div class="empty-state">No nodes assigned to this topic yet.</div></div>` : ''}
  `;
}

function renderTopicNodeGroup(label, nodes) {
  const items = nodes.map(n => {
    const stateClass = n.current_state ? `state-${n.current_state}` : '';
    const stateBadge = n.current_state
      ? `<span class="${stateClass}" style="font-size:0.75rem;margin-left:8px">${n.current_state}</span>`
      : '';
    return `
      <li class="edge-item" onclick="loadReasoning('node','${n.node_id}')">
        <span class="edge-type-tag">${escHtml(n.node_type)}</span>
        <div class="edge-desc">
          ${escHtml(n.name)}${stateBadge}
        </div>
        ${n.notes ? `<div class="muted" style="font-size:0.75rem;margin-top:3px">${escHtml(n.notes)}</div>` : ''}
      </li>
    `;
  }).join('');
  return `
    <div class="reasoning-section">
      <div class="reasoning-label">${label} (${nodes.length})</div>
      <ul class="edge-list">${items}</ul>
    </div>
  `;
}

// ── Geo scope view ────────────────────────────────────────────────────────────

async function loadGeoScope(lat, lon) {
  const container = document.getElementById('geo-content');
  if (!container) return;

  container.innerHTML = `<div class="loading">Detecting regional scope…</div>`;

  try {
    const asOfParam = window._augurAsOf ? `&as_of=${encodeURIComponent(window._augurAsOf)}` : '';
    const res = await fetch(`/api/geo/scope?lat=${lat}&lon=${lon}${asOfParam}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderGeoScope(container, data);
  } catch (err) {
    container.innerHTML = `<div class="error-msg">Geo scope failed: ${escHtml(err.message)}</div>`;
  }
}

function renderGeoScope(container, data) {
  const { region, dimensions, changes } = data;

  const dimCards = (dimensions || []).map(d => `
    <div class="dimension-card" style="cursor:default">
      <div class="dim-label">${escHtml(d.label)}</div>
      <div class="dim-state state-${d.state}">${formatState(d.state)}</div>
    </div>
  `).join('');

  const changeItems = (changes || []).map(c => `
    <div class="change-item" onclick="drillChange('${c.target_type}', '${c.target_id}')">
      <span class="change-type-icon ${changeIconClass(c.change_type)}">${changeIconLabel(c.change_type)}</span>
      <div>
        <div class="change-summary">${escHtml(c.summary)}</div>
        <div class="change-dim-tag">${escHtml(c.dimension_label)}</div>
      </div>
    </div>
  `).join('');

  container.innerHTML = `
    <div class="reasoning-header">
      <div class="reasoning-node-name">${escHtml(region.display_name)}</div>
      <div class="reasoning-node-type">geographic scope · ${escHtml(region.perspectives.join(', '))}</div>
    </div>
    <div class="dimension-grid" style="margin-top:12px">${dimCards || '<div class="empty-state">No dimension data.</div>'}</div>
    ${changeItems ? `
      <div class="changes-section" style="margin-top:16px">
        <div class="section-label">Recent changes in this region</div>
        <div class="change-list">${changeItems}</div>
      </div>
    ` : `<div class="empty-state" style="margin-top:16px">No recent changes for this region.</div>`}
  `;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function dimLabel(dim) {
  const labels = {
    economic_stability: 'Economic Stability',
    geopolitical_tension: 'Geopolitical Tension',
    resource_availability: 'Resource Availability',
    environmental_stress: 'Environmental Stress',
    structural_change: 'Structural Change',
  };
  return labels[dim] || dim.replace(/_/g, ' ');
}

function formatState(s) {
  const labels = {
    improving: 'Improving', stable: 'Stable', strained: 'Strained',
    deteriorating: 'Deteriorating', crisis: 'Crisis', unknown: 'No data',
  };
  return labels[s] || s;
}

function changeIconClass(t) {
  const map = {
    edge_strengthened: 'strengthened', edge_weakened: 'weakened',
    condition_activated: 'activated', condition_deactivated: 'deactivated',
    disconfirmation_weakened: 'disconf', edge_created: 'created',
  };
  return map[t] || '';
}

function changeIconLabel(t) {
  const map = {
    edge_strengthened: '↑ strength', edge_weakened: '↓ weakened',
    condition_activated: '● activated', condition_deactivated: '○ inactive',
    disconfirmation_weakened: '✗ disconf.', edge_created: '+ new link',
  };
  return map[t] || t.replace(/_/g, ' ');
}

function escHtml(str) {
  if (!str) return '';
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// Expose globally
window.loadTopicView = loadTopicView;
window.loadTopicList = loadTopicList;
window.showTopicView = showTopicView;
window.showTopicListView = showTopicListView;
window.loadGeoScope = loadGeoScope;
