/* Investment Assistant – WebSocket Chat Client */

const SESSION_ID = localStorage.getItem('session_id') || crypto.randomUUID();
localStorage.setItem('session_id', SESSION_ID);

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
  document.getElementById('btn-recommend').classList.toggle('active', mode === 'recommend');
  document.getElementById('btn-auto').classList.toggle('active', mode === 'auto');
  const statusEl = document.getElementById('mode-status');
  statusEl.textContent = mode === 'auto'
    ? '⚡ Auto mode — agent will execute trades within safety limits.'
    : '✋ Recommend mode — agent proposes, you confirm.';

  if (ws?.readyState === WebSocket.OPEN) {
    setSendEnabled(false);
    appendUserMessage(`Switch trading mode to ${mode}`);
    startAssistantMessage();
    ws.send(JSON.stringify({ message: `Switch trading mode to ${mode}` }));
  }
}

// ── Market Snapshot ────────────────────────────────────────────────────────────

async function loadSnapshot() {
  const el = document.getElementById('market-snapshot');
  el.textContent = 'Loading…';
  try {
    const resp = await fetch('/api/market/snapshot');
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

async function loadReports() {
  const el = document.getElementById('reports-list');
  try {
    const resp = await fetch('/api/reports');
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

globalThis.addEventListener('DOMContentLoaded', () => {
  connect();
  loadSnapshot();
  loadReports();
  setInterval(loadSnapshot, 5 * 60 * 1000); // auto-refresh every 5 min
  setSendEnabled(true);

  // Welcome message
  const welcome = document.createElement('div');
  welcome.className = 'msg assistant';
  welcome.innerHTML = `
    <div class="msg-bubble">
      <strong>Welcome to your Investment Assistant! 📈</strong><br><br>
      I have access to real-time market data, news sentiment analysis, and your connected brokerage accounts
      (Alpaca, Interactive Brokers, Coinbase, Binance).<br><br>
      I can analyse markets, run simulations, and — depending on your trading mode — <em>execute trades</em> on your behalf.<br><br>
      Use the quick-prompt buttons on the left, or just ask me anything.
    </div>
    <div class="msg-time">${timeNow()}</div>
  `;
  messagesEl().appendChild(welcome);
});
