/* Augur scenarios view — level 4 (plausible futures) */

'use strict';

// ── Loading ───────────────────────────────────────────────────────────────────

async function loadScenarios(dimension) {
  const container = document.getElementById('scenariosList');
  if (!container) return;

  const dimParam = dimension ? `&dimension=${encodeURIComponent(dimension)}` : '';
  const asOfParam = window._augurAsOf ? `&as_of=${encodeURIComponent(window._augurAsOf)}` : '';

  try {
    const res = await fetch(`/api/scenarios?limit=20${dimParam}${asOfParam}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderScenarios(container, data.scenarios || []);
  } catch (err) {
    container.innerHTML = `<div class="error-msg">Failed to load scenarios: ${escHtml(err.message)}</div>`;
  }
}

// ── Rendering ─────────────────────────────────────────────────────────────────

function renderScenarios(container, scenarios) {
  if (!scenarios || scenarios.length === 0) {
    container.innerHTML = `<div class="empty-state">No scenarios yet — run <code>augur project --all</code> to generate.</div>`;
    return;
  }

  // Group by dimension
  const byDim = {};
  for (const s of scenarios) {
    const key = s.dimension || 'global';
    if (!byDim[key]) byDim[key] = [];
    byDim[key].push(s);
  }

  container.innerHTML = Object.entries(byDim).map(([dim, items]) => `
    <div class="scenario-group">
      <div class="scenario-dim-header">${escHtml(dimLabel(dim))}</div>
      ${items.map(s => renderScenarioCard(s)).join('')}
    </div>
  `).join('');
}

function renderScenarioCard(s) {
  const bandClass = `prob-${s.probability_band}`;
  return `
    <div class="scenario-card">
      <div class="scenario-header">
        <span class="scenario-prob ${bandClass}">${s.probability_band}</span>
        <span class="scenario-title">${escHtml(s.title)}</span>
        <span class="scenario-horizon muted">${escHtml(s.time_horizon)}</span>
      </div>
      <p class="scenario-summary">${escHtml(s.summary)}</p>
    </div>
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
    global: 'Global / Cross-cutting',
  };
  return labels[dim] || dim.replace(/_/g, ' ');
}

function escHtml(str) {
  if (!str) return '';
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// Expose globally
window.loadScenarios = loadScenarios;
