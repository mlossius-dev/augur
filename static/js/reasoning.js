/* Augur reasoning view — level 5 (the "why") */

'use strict';

// ── Entry point ───────────────────────────────────────────────────────────────

async function loadReasoning(type, id) {
  showReasoningView();

  const container = document.getElementById('reasoning-content');
  container.innerHTML = `<div class="loading">Loading reasoning…</div>`;

  try {
    const asOfParam = window._augurAsOf ? `?as_of=${encodeURIComponent(window._augurAsOf)}` : '';
    const url = `/api/reasoning/${type}/${id}${asOfParam}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    if (type === 'node') renderNodeReasoning(container, data);
    else renderEdgeReasoning(container, data);
  } catch (err) {
    container.innerHTML = `<div class="error-msg">Failed to load reasoning: ${escHtml(err.message)}</div>`;
  }
}

function showReasoningView() {
  const views = ['home-view', 'topic-view', 'topic-list-view'];
  views.forEach(id => { const el = document.getElementById(id); if (el) el.style.display = 'none'; });
  document.getElementById('reasoning-view').style.display = 'block';
  window.scrollTo(0, 0);
}

function showHomeView() {
  document.getElementById('home-view').style.display = 'block';
  ['reasoning-view', 'topic-view', 'topic-list-view'].forEach(id => {
    const el = document.getElementById(id); if (el) el.style.display = 'none';
  });
  window.scrollTo(0, 0);
}

// ── Node reasoning ────────────────────────────────────────────────────────────

function renderNodeReasoning(container, data) {
  const { node, edges, signals, state_history } = data;

  const typeData = node.type_data || {};
  const currentState = typeData.current_state;
  const isCondition = node.node_type === 'condition';

  container.innerHTML = `
    <div class="reasoning-header">
      <div class="back-link" onclick="showHomeView()">← Back to home</div>
      <div class="reasoning-node-name">${escHtml(node.name)}</div>
      <div class="reasoning-node-type">${escHtml(node.node_type)}${isCondition && currentState ? ` · <span class="state-${currentState}">${currentState}</span>` : ''}</div>
      ${node.description ? `<p class="reasoning-text mt-8">${escHtml(node.description)}</p>` : ''}
    </div>

    ${isCondition && state_history.length > 0 ? renderStateHistory(state_history) : ''}

    ${edges.length > 0 ? renderEdges(edges, node.node_id) : ''}

    ${signals.length > 0 ? renderSignals(signals, 'Signals that established this node') : ''}

    <div class="reasoning-section">
      <div class="reasoning-label">Provenance</div>
      <div class="muted" style="font-size:0.8rem;font-family:var(--font-ui)">
        Node ID: <span class="mono">${node.node_id}</span><br>
        Created: ${node.created_at ? new Date(node.created_at).toLocaleString('en-GB') : 'unknown'}
      </div>
    </div>
  `;
}

function renderStateHistory(history) {
  const items = history.map(h => `
    <li>
      <span class="timeline-date">${formatDate(h.content_timestamp)}</span>
      <span class="timeline-change ${stateChangeClass(h.new_state)}">${h.new_state}</span>
      <span class="timeline-reasoning">${escHtml(h.reasoning || '')}</span>
    </li>
  `).join('');
  return `
    <div class="reasoning-section">
      <div class="reasoning-label">State history</div>
      <ul class="weight-timeline">${items}</ul>
    </div>
  `;
}

function renderEdges(edges, currentNodeId) {
  const items = edges.map(e => {
    const isSource = e.source.id === currentNodeId;
    const neighbor = isSource ? e.target : e.source;
    const verb = edgeVerb(e.edge_type);
    const desc = isSource
      ? `This node <em>${verb}</em> <span class="edge-neighbor">${escHtml(neighbor.name)}</span>`
      : `<span class="edge-neighbor">${escHtml(neighbor.name)}</span> <em>${verb}</em> this node`;
    return `
      <li class="edge-item" onclick="loadReasoning('edge','${e.edge_id}')">
        <span class="edge-type-tag">${e.edge_type.replace(/_/g,' ')}</span>
        <div class="edge-desc">${desc}</div>
        <span class="weight-badge weight-${e.weight_band}">${e.weight_band}</span>
      </li>
    `;
  }).join('');
  return `
    <div class="reasoning-section">
      <div class="reasoning-label">Connected edges (${edges.length})</div>
      <ul class="edge-list">${items}</ul>
    </div>
  `;
}

// ── Edge reasoning ────────────────────────────────────────────────────────────

function renderEdgeReasoning(container, data) {
  const { edge, source_node, target_node, weight_history,
          supporting_signals, disconfirming_signals, disconfirmation_events } = data;

  container.innerHTML = `
    <div class="reasoning-header">
      <div class="back-link" onclick="showHomeView()">← Back to home</div>
      <div class="reasoning-node-name">
        ${escHtml(source_node.name)}
        <span style="color:var(--text-muted);font-weight:400;font-size:0.9em"> ${edgeVerb(edge.edge_type)} </span>
        ${escHtml(target_node.name)}
      </div>
      <div class="reasoning-node-type">
        edge · ${edge.edge_type.replace(/_/g,' ')} · <span class="weight-badge weight-${edge.weight_band}">${edge.weight_band}</span>
        ${edge.deprecated ? '<span style="color:var(--crisis);margin-left:8px">deprecated</span>' : ''}
      </div>
    </div>

    <div class="reasoning-section">
      <div class="reasoning-label">Reasoning</div>
      <p class="reasoning-text">${escHtml(edge.reasoning)}</p>
    </div>

    <div class="reasoning-section">
      <div class="reasoning-label">Falsification criteria</div>
      <div class="falsification-box">${escHtml(edge.falsification_criteria)}</div>
    </div>

    ${renderWeightHistory(weight_history)}

    ${supporting_signals.length > 0 ? renderSignals(supporting_signals, `Supporting signals (${supporting_signals.length})`) : ''}

    ${disconfirming_signals.length > 0 ? renderSignals(disconfirming_signals, `Disconfirming signals (${disconfirming_signals.length})`, true) : ''}

    ${disconfirmation_events.length > 0 ? renderDisconfEvents(disconfirmation_events) : ''}

    <div class="reasoning-section">
      <div class="reasoning-label">Connected nodes</div>
      <div style="display:grid;grid-template-columns:1fr auto 1fr;gap:10px;align-items:center;font-size:0.9rem">
        <div style="padding:8px;border:1px solid var(--border);cursor:pointer"
             onclick="loadReasoning('node','${source_node.node_id}')">
          <div style="font-family:var(--font-ui);font-size:0.7rem;color:var(--text-muted);text-transform:uppercase">Source</div>
          <div style="color:var(--link)">${escHtml(source_node.name)}</div>
          <div style="font-family:var(--font-ui);font-size:0.72rem;color:var(--text-faint)">${source_node.node_type}</div>
        </div>
        <div style="font-family:var(--font-ui);font-size:0.75rem;color:var(--text-muted);text-align:center;padding:0 4px">
          ${edge.edge_type.replace(/_/g,' ')} →
        </div>
        <div style="padding:8px;border:1px solid var(--border);cursor:pointer"
             onclick="loadReasoning('node','${target_node.node_id}')">
          <div style="font-family:var(--font-ui);font-size:0.7rem;color:var(--text-muted);text-transform:uppercase">Target</div>
          <div style="color:var(--link)">${escHtml(target_node.name)}</div>
          <div style="font-family:var(--font-ui);font-size:0.72rem;color:var(--text-faint)">${target_node.node_type}</div>
        </div>
      </div>
    </div>

    <div class="reasoning-section">
      <div class="reasoning-label">Provenance</div>
      <div class="muted" style="font-size:0.8rem;font-family:var(--font-ui)">
        Edge ID: <span class="mono">${edge.edge_id}</span><br>
        Created: ${edge.created_at ? new Date(edge.created_at).toLocaleString('en-GB') : 'unknown'}<br>
        ${edge.last_disconfirmation_pass ? `Last challenged: ${new Date(edge.last_disconfirmation_pass).toLocaleDateString('en-GB')}` : 'Never challenged'}
      </div>
    </div>
  `;
}

function renderWeightHistory(history) {
  if (!history || history.length === 0) return '';
  const items = history.map(h => `
    <li>
      <span class="timeline-date">${formatDate(h.content_timestamp)}</span>
      <span class="timeline-change ${weightChangeClass(h.change_type)}">${h.change_type}</span>
      <span class="weight-badge weight-${h.weight_band}">${h.weight_band}</span>
      <span class="timeline-reasoning">${escHtml(h.reasoning || '')}</span>
    </li>
  `).join('');
  return `
    <div class="reasoning-section">
      <div class="reasoning-label">Weight history</div>
      <ul class="weight-timeline">${items}</ul>
    </div>
  `;
}

function renderSignals(signals, label, isDisconf = false) {
  const items = signals.map(s => `
    <li class="signal-item">
      <div>${escHtml(s.claim_text || '')}</div>
      <div class="signal-meta">
        ${escHtml(s.source_id || 'unknown source')} ·
        ${escHtml(s.lens_id)} ·
        ${escHtml(s.confidence_band || '')} ·
        ${s.content_timestamp ? new Date(s.content_timestamp).toLocaleDateString('en-GB') : ''}
      </div>
    </li>
  `).join('');
  return `
    <div class="reasoning-section">
      <div class="reasoning-label">${label}</div>
      <ul class="signal-list">${items}</ul>
    </div>
  `;
}

function renderDisconfEvents(events) {
  const items = events.map(e => `
    <li class="signal-item">
      <div>
        <span style="font-family:var(--font-ui);font-size:0.78rem;font-weight:600;color:${e.outcome === 'found' ? 'var(--crisis)' : 'var(--improving)'}">
          ${e.outcome === 'found' ? 'Evidence found' : 'No disconfirmation found'}
        </span>
        ${e.reasoning ? ` — ${escHtml(e.reasoning)}` : ''}
      </div>
      <div class="signal-meta">
        Challenged ${e.challenged_at ? new Date(e.challenged_at).toLocaleDateString('en-GB') : ''} ·
        Weight at challenge: ${escHtml(e.weight_band_at_challenge || '')}
      </div>
    </li>
  `).join('');
  return `
    <div class="reasoning-section">
      <div class="reasoning-label">Disconfirmation history</div>
      <ul class="signal-list">${items}</ul>
    </div>
  `;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function edgeVerb(t) {
  const verbs = {
    causes: 'causes', enables: 'enables', constrains: 'constrains',
    accelerates: 'accelerates', correlates_with: 'correlates with',
    contradicts: 'contradicts', refines: 'refines',
    part_of: 'is part of', produces: 'produces',
  };
  return verbs[t] || t.replace(/_/g, ' ');
}

function weightChangeClass(t) {
  return { strengthened: 'change-str', weakened: 'change-wk',
           disconfirmation: 'change-dc', initial: 'change-in' }[t] || '';
}

function stateChangeClass(s) {
  return { active: 'change-str', inactive: 'change-wk' }[s] || '';
}

function formatDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString('en-GB', { year: 'numeric', month: 'short', day: 'numeric' });
}

function escHtml(str) {
  if (!str) return '';
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// Expose globally
window.loadReasoning = loadReasoning;
window.showHomeView = showHomeView;
