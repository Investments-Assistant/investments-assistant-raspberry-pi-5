/* Investment Assistant – WebSocket Chat Client */

// Keep conversations tab-scoped. A shared localStorage ID made two tabs from
// one browser append to the same in-memory model context and chat history.
const SESSION_ID = sessionStorage.getItem('session_id') || crypto.randomUUID();
sessionStorage.setItem('session_id', SESSION_ID);

let ws = null;
let currentAssistantBubble = null;
let currentAssistantText = '';
let reconnectTimer = null;
let reconnectDelay = 1000;
const MAX_RECONNECT = 30000;

// ── WebSocket ──────────────────────────────────────────────────────────────────

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const url = `${proto}://${location.host}/ws/chat/${SESSION_ID}`;

  setStatus('connecting');
  ws = new WebSocket(url);

  ws.onopen = () => {
    setStatus('online');
    reconnectDelay = 1000;
    clearTimeout(reconnectTimer);
    console.log('WebSocket connected');
  };

  ws.onclose = (e) => {
    setStatus('offline');
    if (e.code === 4001 || e.code === 4003) {
      window.location.assign('/login');
      return;
    }
    if (e.code !== 1000) {
      reconnectTimer = setTimeout(() => {
        reconnectDelay = Math.min(reconnectDelay * 1.5, MAX_RECONNECT);
        connect();
      }, reconnectDelay);
    }
  };

  ws.onerror = (e) => console.error('WS error', e);

  ws.onmessage = (e) => {
    const event = JSON.parse(e.data);
    handleEvent(event);
  };
}

function handleEvent(event) {
  switch (event.type) {
    case 'text_delta':
      appendAssistantDelta(event.text);
      break;
    case 'tool_call':
      appendToolCall(event.name, event.input);
      break;
    case 'tool_result':
      appendToolResult(event.name, event.result);
      break;
    case 'done':
      finaliseAssistantMessage();
      setSendEnabled(true);
      break;
    case 'error':
      appendErrorMessage(event.message);
      setSendEnabled(true);
      break;
  }
}

// ── Message Rendering ─────────────────────────────────────────────────────────

function appendUserMessage(text) {
  const div = document.createElement('div');
  div.className = 'msg user';
  div.innerHTML = `
    <div class="msg-bubble">${escapeHtml(text)}</div>
    <div class="msg-time">${timeNow()}</div>
  `;
  messagesEl().appendChild(div);
  scrollBottom();
}

function appendAssistantMessage(text, createdAt = null) {
  const div = document.createElement('div');
  div.className = 'msg assistant';
  div.innerHTML = `
    <div class="msg-bubble">${markdownToHtml(text)}</div>
    <div class="msg-time">${formatMessageTime(createdAt)}</div>
  `;
  messagesEl().appendChild(div);
  scrollBottom();
}

function startAssistantMessage() {
  // Typing indicator first
  currentAssistantText = '';
  const div = document.createElement('div');
  div.className = 'msg assistant';
  div.innerHTML = `
    <div class="msg-bubble" id="streaming-bubble">
      <span class="typing-dot"></span>
      <span class="typing-dot"></span>
      <span class="typing-dot"></span>
    </div>
    <div class="msg-time">${timeNow()}</div>
  `;
  messagesEl().appendChild(div);
  currentAssistantBubble = div.querySelector('#streaming-bubble');
  currentAssistantBubble.removeAttribute('id');
  scrollBottom();
}

function appendAssistantDelta(text) {
  if (!currentAssistantBubble) startAssistantMessage();
  currentAssistantText += text;
  currentAssistantBubble.innerHTML = markdownToHtml(currentAssistantText);
  scrollBottom();
}

function finaliseAssistantMessage() {
  if (currentAssistantBubble && currentAssistantText) {
    currentAssistantBubble.innerHTML = markdownToHtml(currentAssistantText);
  }
  currentAssistantBubble = null;
  currentAssistantText = '';
  scrollBottom();
}

function appendToolCall(name, input) {
  const el = document.createElement('div');
  el.className = 'tool-call';
  el.innerHTML = `<span class="tool-icon">🔧</span> Calling <strong>${escapeHtml(name)}</strong>&hellip;`;
  messagesEl().appendChild(el);
  scrollBottom();
}

function appendToolResult(name, resultStr) {
  let preview = resultStr;
  try {
    const obj = JSON.parse(resultStr);
    preview = JSON.stringify(obj, null, 0).slice(0, 120) + (resultStr.length > 120 ? '…' : '');
  } catch (_) { /* resultStr is not valid JSON — use the raw string as preview */ }
  const el = document.createElement('div');
  el.className = 'tool-call result';
  el.innerHTML = `<span class="tool-icon">✅</span> <strong>${escapeHtml(name)}</strong> → ${escapeHtml(preview)}`;
  messagesEl().appendChild(el);
  scrollBottom();
}

function appendErrorMessage(msg) {
  const div = document.createElement('div');
  div.className = 'msg assistant';
  div.innerHTML = `<div class="msg-bubble" style="border-color:#ef4444;color:#ef4444;">⚠️ Error: ${escapeHtml(msg)}</div>`;
  messagesEl().appendChild(div);
  scrollBottom();
}

// ── Send ──────────────────────────────────────────────────────────────────────

function sendMessage() {
  const input = document.getElementById('user-input');
  const text = input.value.trim();
  if (!text || ws?.readyState !== WebSocket.OPEN) return;

  input.value = '';
  input.style.height = '';
  setSendEnabled(false);
  appendUserMessage(text);
  startAssistantMessage();
  ws.send(JSON.stringify({ message: text }));
}

function sendQuick(text) {
  document.getElementById('user-input').value = text;
  sendMessage();
}

function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
  // Auto-resize textarea
  const ta = e.target;
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 160) + 'px';
}

// ── Trading Mode ──────────────────────────────────────────────────────────────

async function setMode(mode) {
  const statusEl = document.getElementById('mode-status');
  statusEl.textContent = 'Saving trading mode…';
  try {
    const resp = await fetch('/api/profile/trading-mode', {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrfToken(),
      },
      body: JSON.stringify({ mode }),
    });
    if (resp.status === 401) { window.location.assign('/login'); return; }
    if (!resp.ok) {
      const detail = await resp.json().catch(() => ({}));
      throw new Error(detail.detail || 'Trading mode could not be saved.');
    }
    document.getElementById('btn-recommend').classList.toggle('active', mode === 'recommend');
    document.getElementById('btn-auto').classList.toggle('active', mode === 'auto');
    statusEl.textContent = mode === 'auto'
      ? '⚡ Auto mode — only bounded, configured trades can execute.'
      : '✋ Recommend mode — agent proposes, you confirm.';
  } catch (error) {
    statusEl.textContent = error.message;
    appendErrorMessage(error.message);
  }
}

// ── Market Snapshot ────────────────────────────────────────────────────────────

async function loadSnapshot() {
  const el = document.getElementById('market-snapshot');
  el.textContent = 'Loading…';
  try {
    const resp = await fetch('/api/market/snapshot');
    if (resp.status === 401) { window.location.assign('/login'); return; }
    const data = await resp.json();
    if (data.message) { el.textContent = data.message; return; }
    const markets = data.market_overview?.markets || {};
    let html = '';
    for (const [name, info] of Object.entries(markets)) {
      const price = info.price ? info.price.toLocaleString(undefined, { maximumFractionDigits: 2 }) : 'N/A';
      const chg = info.change_pct;
      let cls = '';
      if (chg > 0) cls = 'up';
      else if (chg < 0) cls = 'down';
      const sign = chg > 0 ? '+' : '';
      const chgStr = chg == null ? '' : ` (${sign}${chg}%)`;
      html += `<div class="market-row"><span class="name">${name}</span><span class="price ${cls}">${price}${chgStr}</span></div>`;
    }
    el.innerHTML = html || 'No data available';
  } catch (e) {
    console.error('Snapshot load failed', e);
    el.textContent = 'Failed to load snapshot.';
  }
}

async function loadSafety() {
  try {
    const resp = await fetch('/api/safety');
    if (resp.status === 401) { window.location.assign('/login'); return; }
    if (!resp.ok) return;
    const policy = await resp.json();
    const mode = policy.trading_mode;
    document.getElementById('btn-recommend').classList.toggle('active', mode === 'recommend');
    document.getElementById('btn-auto').classList.toggle('active', mode === 'auto');
    document.getElementById('mode-status').textContent = policy.daily_halted
      ? '🛑 Auto-trading is halted for today.'
      : mode === 'auto'
        ? `Auto mode · cap $${policy.auto_max_trade_usd} · live ${policy.live_trading_enabled ? 'enabled' : 'disabled'}`
        : 'Recommend mode — every order needs explicit confirmation.';
  } catch (e) {
    console.error('Safety policy load failed', e);
  }
}

function csrfToken() {
  const match = document.cookie.match(/(?:^|; )ia_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}

async function activateKillSwitch() {
  if (!window.confirm('Block all future autonomous orders for the rest of today?')) return;
  try {
    const resp = await fetch('/api/safety/kill-switch', {
      method: 'POST',
      headers: {'X-CSRF-Token': csrfToken()},
    });
    if (resp.status === 401) { window.location.assign('/login'); return; }
    if (!resp.ok) throw new Error('Kill switch could not be persisted.');
    const data = await resp.json();
    document.getElementById('mode-status').textContent = `🛑 ${data.message}`;
  } catch (error) {
    appendErrorMessage(error.message);
  }
}

// ── Durable user experience ─────────────────────────────────────────────────

async function loadProfile() {
  const statusEl = document.getElementById('profile-status');
  try {
    const resp = await fetch('/api/profile');
    if (resp.status === 401) { window.location.assign('/login'); return; }
    if (!resp.ok) throw new Error('Profile unavailable');
    const profile = await resp.json();
    document.getElementById('profile-display-name').value = profile.display_name || '';
    document.getElementById('profile-description').value = profile.description || '';
    document.getElementById('profile-preferences').value = JSON.stringify(
      profile.preferences || {}, null, 2
    );
    statusEl.textContent = 'Profile loaded';
    statusEl.className = 'profile-status saved';
  } catch (error) {
    console.error('Profile load failed', error);
    statusEl.textContent = 'Profile could not be loaded.';
    statusEl.className = 'profile-status error';
  }
}

async function saveProfile() {
  const statusEl = document.getElementById('profile-status');
  let preferences;
  try {
    preferences = JSON.parse(document.getElementById('profile-preferences').value || '{}');
  } catch (_) {
    statusEl.textContent = 'Preferences must be valid JSON.';
    statusEl.className = 'profile-status error';
    return;
  }
  if (!preferences || Array.isArray(preferences) || typeof preferences !== 'object') {
    statusEl.textContent = 'Preferences must be a JSON object.';
    statusEl.className = 'profile-status error';
    return;
  }
  statusEl.textContent = 'Saving…';
  statusEl.className = 'profile-status';
  try {
    const resp = await fetch('/api/profile', {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrfToken(),
      },
      body: JSON.stringify({
        display_name: document.getElementById('profile-display-name').value,
        description: document.getElementById('profile-description').value,
        preferences,
      }),
    });
    if (resp.status === 401) { window.location.assign('/login'); return; }
    if (!resp.ok) {
      const detail = await resp.json().catch(() => ({}));
      throw new Error(detail.detail || 'Profile could not be saved.');
    }
    const saved = await resp.json();
    document.getElementById('profile-preferences').value = JSON.stringify(
      saved.preferences || {}, null, 2
    );
    statusEl.textContent = 'Profile saved';
    statusEl.className = 'profile-status saved';
  } catch (error) {
    console.error('Profile save failed', error);
    statusEl.textContent = error.message;
    statusEl.className = 'profile-status error';
  }
}

async function loadHistory() {
  try {
    const resp = await fetch(
      `/api/chat/history?session_id=${encodeURIComponent(SESSION_ID)}&limit=200`
    );
    if (resp.status === 401) { window.location.assign('/login'); return false; }
    if (!resp.ok) throw new Error('History unavailable');
    const history = await resp.json();
    if (!Array.isArray(history) || history.length === 0) return false;
    messagesEl().replaceChildren();
    for (const message of history) {
      if (message.role === 'user') appendUserMessage(message.content);
      if (message.role === 'assistant') appendAssistantMessage(message.content, message.created_at);
    }
    return true;
  } catch (error) {
    console.error('Chat history load failed', error);
    return false;
  }
}

async function loadReports() {
  const el = document.getElementById('reports-list');
  try {
    const resp = await fetch('/api/reports');
    if (resp.status === 401) { window.location.assign('/login'); return; }
    const reports = await resp.json();
    if (!reports.length) { el.textContent = 'No reports yet.'; return; }
    el.innerHTML = reports.slice(0, 5).map(r => `
      <div class="report-item">
        <span>${r.period_start.slice(0, 10)} → ${r.period_end.slice(0, 10)}</span>
        ${r.pdf_available ? `<a href="/api/reports/${r.id}/pdf" target="_blank">PDF ↗</a>` : ''}
      </div>
    `).join('');
  } catch (e) {
    console.error('Reports load failed', e);
    el.textContent = 'Could not load reports.';
  }
}

// ── Utilities ──────────────────────────────────────────────────────────────────

function messagesEl() { return document.getElementById('messages'); }
function scrollBottom() {
  const el = messagesEl();
  el.scrollTop = el.scrollHeight;
}
function setSendEnabled(enabled) {
  document.getElementById('send-btn').disabled = !enabled;
  document.getElementById('user-input').disabled = !enabled;
}
function setStatus(state) {
  const el = document.getElementById('connection-status');
  el.className = 'conn-status ' + state;
  if (state === 'online') el.textContent = 'Connected';
  else if (state === 'connecting') el.textContent = 'Connecting…';
  else el.textContent = 'Disconnected';
}
function timeNow() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
function formatMessageTime(value) {
  const date = value ? new Date(value) : new Date();
  return Number.isNaN(date.getTime())
    ? timeNow()
    : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
function escapeHtml(str) {
  return String(str).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
}

/**
 * Replace fenced code blocks (``` … ```) using a linear indexOf scan.
 * Regex-based approaches require backtracking over the block content and are
 * vulnerable to super-linear runtime when there is no closing fence.
 */
function replaceCodeBlocks(html) {
  const FENCE = '```';
  let result = '';
  let pos = 0;
  while (pos < html.length) {
    const open = html.indexOf(FENCE, pos);
    if (open === -1) { result += html.slice(pos); break; }
    result += html.slice(pos, open);
    const bodyStart = open + FENCE.length;
    const close = html.indexOf(FENCE, bodyStart);
    if (close === -1) { result += html.slice(open); break; } // unclosed fence — leave as-is
    const body = html.slice(bodyStart, close);
    const nl = body.indexOf('\n');
    const code = (nl >= 0 ? body.slice(nl + 1) : body).trim(); // strip optional language hint
    result += `<pre><code>${code}</code></pre>`;
    pos = close + FENCE.length;
  }
  return result;
}

/** Very minimal Markdown → HTML for chat messages. */
function markdownToHtml(md) {
  let html = escapeHtml(md);
  // Code blocks — handled by linear scan above (no regex backtracking)
  html = replaceCodeBlocks(html);
  // Inline code
  html = html.replaceAll(/`([^`]+)`/g, '<code>$1</code>');
  // Bold
  html = html.replaceAll(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  // Italic
  html = html.replaceAll(/\*(.+?)\*/g, '<em>$1</em>');
  // Headers
  html = html.replaceAll(/^### (.+)$/gm, '<h3>$1</h3>');
  html = html.replaceAll(/^## (.+)$/gm, '<h2>$1</h2>');
  html = html.replaceAll(/^# (.+)$/gm, '<h1>$1</h1>');
  // Unordered list
  html = html.replaceAll(/^[-*] (.+)$/gm, '<li>$1</li>');
  html = html.replace(/(<li>.*<\/li>)+/s, '<ul>$&</ul>');
  // Ordered list
  html = html.replaceAll(/^\d+\. (.+)$/gm, '<li>$1</li>');
  // Horizontal rule
  html = html.replaceAll(/^---$/gm, '<hr>');
  // Paragraphs (blank lines)
  html = html.replaceAll(/\n\n+/g, '</p><p>');
  html = html.replaceAll('\n', '<br>');
  return `<p>${html}</p>`;
}

function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('hidden');
}

// ── Init ──────────────────────────────────────────────────────────────────────

globalThis.addEventListener('DOMContentLoaded', async () => {
  const hadHistory = await loadHistory();
  await loadProfile();
  connect();
  loadSnapshot();
  loadSafety();
  loadReports();
  setInterval(loadSnapshot, 5 * 60 * 1000); // auto-refresh every 5 min
  setSendEnabled(true);

  if (!hadHistory) {
    appendAssistantMessage(
      '**Welcome to your Investment Assistant! 📈**\n\n'
      + 'I can analyse markets, news, simulations, and — depending on your trading mode — execute bounded trades on your behalf.\n\n'
      + 'Use the quick prompts on the left, or ask me anything.'
    );
  }
});
