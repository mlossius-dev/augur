/* Augur conversation layer — graph-grounded Q&A */

'use strict';

// ── State ─────────────────────────────────────────────────────────────────────

const convState = {
  sessionId: null,
  loading: false,
};

// ── Bootstrap ─────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('conv-form');
  const input = document.getElementById('conv-input');
  if (!form || !input) return;

  form.addEventListener('submit', e => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q || convState.loading) return;
    input.value = '';
    submitQuestion(q);
  });
});

// ── Submit ────────────────────────────────────────────────────────────────────

async function submitQuestion(question) {
  if (convState.loading) return;
  convState.loading = true;

  const log = document.getElementById('conv-log');
  if (!log) return;

  appendMessage(log, 'user', question);

  const thinkingEl = appendMessage(log, 'assistant', '…');

  try {
    const body = {
      question,
      session_id: convState.sessionId || null,
      as_of: window._augurAsOf || null,
    };
    const res = await fetch('/api/conversation/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    convState.sessionId = data.session_id;
    thinkingEl.querySelector('.conv-content').textContent = data.answer;

    const meta = thinkingEl.querySelector('.conv-meta');
    if (meta) {
      meta.textContent = `${data.context.n_nodes} nodes · ${data.context.n_edges} edges · ${data.model_used}`;
    }
  } catch (err) {
    thinkingEl.querySelector('.conv-content').textContent =
      `Error: ${err.message}`;
    thinkingEl.classList.add('conv-error');
  } finally {
    convState.loading = false;
  }
}

function appendMessage(log, role, text) {
  const el = document.createElement('div');
  el.className = `conv-message conv-${role}`;
  el.innerHTML = `
    <div class="conv-role">${role === 'user' ? 'You' : 'Augur'}</div>
    <div class="conv-content">${escHtml(text)}</div>
    ${role === 'assistant' ? '<div class="conv-meta muted"></div>' : ''}
  `;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return el;
}

function clearConversation() {
  convState.sessionId = null;
  const log = document.getElementById('conv-log');
  if (log) log.innerHTML = '';
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function escHtml(str) {
  if (!str) return '';
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

window.clearConversation = clearConversation;
