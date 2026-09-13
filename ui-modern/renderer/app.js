const state = {
  currentState: 'STARTING',
  listening: true,
  sessionTimeout: 120,
  sessionRemaining: 120,
  sessionActiveSince: null,
  actions: [],
  backendConnected: false,
  commandStartedAt: null,
  lastState: null
};

const $ = id => document.getElementById(id);
const conversation = $('conversation');
const messageInput = $('messageInput');
const sendButton = $('sendButton');
const listenToggle = $('listenToggle');
const listenButtonText = $('listenButtonText');
const stateLabel = $('stateLabel');
const stateDescription = $('stateDescription');
const sessionTimer = $('sessionTimer');
const sessionProgress = $('sessionProgress');
const sessionStatus = $('sessionStatus');
const recentActions = $('recentActions');
const actionCount = $('actionCount');
const currentTime = $('currentTime');
const systemTime = $('systemTime');
const aiEngine = $('aiEngine');

const stateDescriptions = {
  STARTING: 'Initializing AURA systems', ACTIVE: 'Listening for your command', SLEEPING: 'Say AURA to wake me',
  THINKING: 'Processing your request', SPEAKING: 'AURA is responding', EXECUTING: 'Executing your request',
  WAITING_FOR_CONFIRMATION: 'Waiting for your confirmation', ERROR: 'System requires attention', STOPPED: 'Voice listening is paused'
};

function nowText() { return new Date().toLocaleTimeString('en-IN', { hour12: false }); }
function safeText(v) { return v == null ? '' : String(v); }

function addMessage(speaker, text, metaText) {
  if (!conversation || !safeText(text).trim()) return;
  const isAura = String(speaker).toLowerCase() === 'aura';
  const message = document.createElement('div');
  message.className = isAura ? 'message aura-message' : 'message user-message';
  const avatar = document.createElement('div'); avatar.className = 'message-avatar'; avatar.textContent = isAura ? 'A' : 'Y';
  const content = document.createElement('div'); content.className = 'message-content';
  const meta = document.createElement('div'); meta.className = 'message-meta';
  const name = document.createElement('strong'); name.textContent = speaker || 'AURA';
  const tag = document.createElement('span'); tag.textContent = metaText || (isAura ? 'AURA' : 'YOU');
  meta.append(name, tag);
  const body = document.createElement('div'); body.className = 'message-text'; body.textContent = text;
  content.append(meta, body); message.append(avatar, content); conversation.appendChild(message);
  conversation.scrollTop = conversation.scrollHeight;
}

function setState(next) {
  if (!next) return;
  next = String(next).toUpperCase();
  const previous = state.currentState;
  state.currentState = next;
  if (stateLabel) stateLabel.textContent = next;
  if (stateDescription) stateDescription.textContent = stateDescriptions[next] || 'AURA is ready';
  document.body.dataset.state = next.toLowerCase();

  // Start the visual session countdown only when a NEW active session begins.
  // Do not reset it on every ACTIVE event; that was the reason the old timer stayed at 02:00.
  if (next === 'ACTIVE' && previous !== 'ACTIVE') {
    state.sessionActiveSince = Date.now();
    state.sessionRemaining = state.sessionTimeout;
  }
  if (next === 'SLEEPING' || next === 'STOPPED') state.sessionActiveSince = null;
  updateSessionUI();
  updateListeningButton(next);

  if (next === 'THINKING') state.commandStartedAt = state.commandStartedAt || Date.now();
  if (next === 'SPEAKING' && state.commandStartedAt) {
    const seconds = (Date.now() - state.commandStartedAt) / 1000;
    addLog(`Response path: ${seconds.toFixed(1)}s before speaking`, 'INFO');
  }
  if (next === 'ACTIVE' && previous === 'SPEAKING') state.commandStartedAt = null;
}

function updateListeningButton(s) {
  if (!listenButtonText) return;
  const stopped = s === 'STOPPED';
  listenButtonText.textContent = stopped ? 'START LISTENING' : 'STOP LISTENING';
  state.listening = !stopped;
}

function updateSessionUI() {
  const remaining = Math.max(0, Math.ceil(state.sessionRemaining));
  const m = Math.floor(remaining / 60), s = remaining % 60;
  if (sessionTimer) sessionTimer.textContent = `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  if (sessionProgress) sessionProgress.style.width = `${Math.max(0, Math.min(100, remaining / state.sessionTimeout * 100))}%`;
  if (sessionStatus) {
    const sleep = state.currentState === 'SLEEPING' || state.currentState === 'STOPPED';
    sessionStatus.textContent = sleep ? state.currentState : 'ACTIVE';
    sessionStatus.style.color = sleep ? '#8c96a5' : '';
  }
}

setInterval(() => {
  if (state.currentState === 'ACTIVE' && state.sessionActiveSince) {
    const elapsed = (Date.now() - state.sessionActiveSince) / 1000;
    state.sessionRemaining = Math.max(0, state.sessionTimeout - elapsed);
    updateSessionUI();
  }
}, 250);

function updateClock() {
  const t = new Date().toLocaleTimeString('en-IN', { hour12: false });
  if (currentTime) currentTime.textContent = t;
  if (systemTime) systemTime.textContent = t;
}
setInterval(updateClock, 1000); updateClock();

function addRecentAction(action, message, success = true) {
  if (!recentActions) return;
  const item = { action: safeText(action) || 'ACTION', message: safeText(message) || 'Action completed.', success: success !== false, time: nowText() };
  state.actions.unshift(item); state.actions = state.actions.slice(0, 12);
  renderRecentActions();
}

function renderRecentActions() {
  if (!recentActions) return;
  recentActions.innerHTML = '';
  if (actionCount) actionCount.textContent = String(state.actions.length);
  if (!state.actions.length) {
    const empty = document.createElement('div'); empty.className = 'empty-actions'; empty.innerHTML = '<div class="empty-icon">—</div><span>No recent actions</span>'; recentActions.appendChild(empty); return;
  }
  state.actions.forEach(item => {
    const el = document.createElement('div'); el.className = 'action-item';
    const top = document.createElement('div'); top.className = 'action-top';
    const dot = document.createElement('span'); dot.className = 'action-check'; dot.style.background = item.success ? '' : '#d85b5b';
    const name = document.createElement('span'); name.className = 'action-name'; name.textContent = item.action;
    top.append(dot, name);
    const msg = document.createElement('div'); msg.className = 'action-message'; msg.textContent = item.message;
    const tm = document.createElement('div'); tm.className = 'action-time'; tm.textContent = item.time;
    el.append(top, msg, tm); recentActions.appendChild(el);
  });
}

// Logs are intentionally created below Recent Actions without requiring a second HTML panel.
function ensureLogPanel() {
  let panel = $('systemLogs');
  if (panel) return panel;
  if (!recentActions || !recentActions.parentElement) return null;
  panel = document.createElement('section'); panel.id = 'systemLogs'; panel.className = 'system-logs';
  panel.innerHTML = '<div class="panel-heading"><span>SYSTEM LOG</span><span id="logCount" class="action-count">0</span></div><div id="logEntries" class="log-entries"></div>';
  recentActions.parentElement.insertBefore(panel, recentActions.nextSibling);
  return panel;
}

function addLog(text, level='INFO') {
  const panel = ensureLogPanel(); if (!panel) return;
  const entries = $('logEntries'); if (!entries) return;
  const row = document.createElement('div'); row.className = `log-entry log-${String(level).toLowerCase()}`;
  const time = document.createElement('span'); time.className = 'log-time'; time.textContent = nowText();
  const msg = document.createElement('span'); msg.className = 'log-text'; msg.textContent = safeText(text);
  row.append(time, msg); entries.appendChild(row);
  while (entries.children.length > 80) entries.removeChild(entries.firstChild);
  const count = $('logCount'); if (count) count.textContent = String(entries.children.length);
  entries.scrollTop = entries.scrollHeight;
}

function showActionResult(data) {
  const action = data.action || data.name || 'ACTION';
  const message = data.message || data.result || 'Action completed.';
  const success = data.success !== false;
  addRecentAction(action, message, success);
  // Make the result visible in the conversation too, including conversions and system actions.
  const looksLikeConversion = /convert|conversion|exchange|currency|equals|₹|\$|€|£/i.test(`${action} ${message}`);
  addMessage('AURA', message, looksLikeConversion ? 'RESULT' : 'ACTION');
  addLog(`${action}: ${message}`, success ? 'INFO' : 'ERROR');
}

function sendText() {
  const text = messageInput?.value.trim(); if (!text) return;
  addMessage('YOU', text, 'YOU');
  messageInput.value = '';
  state.commandStartedAt = Date.now();
  addLog(`Command sent: ${text}`, 'INFO');
  window.aura?.sendMessage(text);
}

sendButton?.addEventListener('click', sendText);
messageInput?.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendText(); } });
listenToggle?.addEventListener('click', () => {
  if (state.currentState === 'STOPPED') { addLog('Starting voice listening...', 'INFO'); window.aura?.startListening(); }
  else { addLog('Stopping voice listening...', 'INFO'); window.aura?.stopListening(); }
});

window.aura?.onStateChange(data => setState(typeof data === 'object' ? data.state : data));
window.aura?.onMessage(data => {
  if (!data) return;
  const text = data.text || data.message || ''; if (!text) return;
  addMessage(data.speaker || 'AURA', text);
});
window.aura?.onAction(showActionResult);
window.aura?.onBackendStatus(data => {
  state.backendConnected = Boolean(data?.connected);
  document.body.dataset.backend = state.backendConnected ? 'connected' : 'disconnected';
  document.querySelectorAll('.conversation-status').forEach(el => {
    const spans = el.querySelectorAll('span'); if (spans.length > 1) spans[spans.length - 1].textContent = state.backendConnected ? 'CONNECTED' : 'CONNECTING';
  });
  addLog(state.backendConnected ? 'Python backend connected.' : 'Python backend disconnected.', state.backendConnected ? 'INFO' : 'WARN');
});
window.aura?.onBackendError(data => { const msg = data?.message || 'Backend error.'; addLog(msg, 'ERROR'); addMessage('AURA', msg, 'ERROR'); setState('ERROR'); });
window.aura?.onBackendLog(data => addLog(data?.text || '', data?.level || 'INFO'));

function updateSystemMetrics() {
  if (!window.aura?.getSystemStats) return;
  window.aura.getSystemStats().then(stats => {
    const cpu = Math.max(0, Math.min(100, Number(stats?.cpu) || 0));
    const ram = Math.max(0, Math.min(100, Number(stats?.ram) || 0));
    if ($('cpuValue')) $('cpuValue').textContent = `${Math.round(cpu)}%`;
    if ($('cpuBar')) $('cpuBar').style.width = `${cpu}%`;
    if ($('ramValue')) $('ramValue').textContent = `${Math.round(ram)}%`;
    if ($('ramBar')) $('ramBar').style.width = `${ram}%`;
  }).catch(() => {});
}
setInterval(updateSystemMetrics, 2000);

function updateAIEngine(name) { if (aiEngine && name) aiEngine.textContent = safeText(name).toUpperCase(); }

setState('STARTING');
state.sessionActiveSince = null;
state.sessionRemaining = state.sessionTimeout;
updateSessionUI(); renderRecentActions(); ensureLogPanel(); updateSystemMetrics();
addLog('AURA UI initialized.', 'INFO');
messageInput?.focus();
