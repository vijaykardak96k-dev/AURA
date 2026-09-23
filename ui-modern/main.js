const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const os = require('os');

let mainWindow = null;
let pythonProcess = null;
let stdoutBuffer = '';
let backendStarted = false;

const PROJECT_ROOT = path.resolve(__dirname, '..');
const PYTHON_EXECUTABLE = process.env.AURA_PYTHON || path.join(PROJECT_ROOT, '.venv', 'Scripts', 'python.exe');

function sendToRenderer(channel, data = {}) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.webContents.send(channel, data);
}

function sendLog(text, level = 'INFO') {
  const clean = String(text ?? '').replace(/\r/g, '').trim();
  if (!clean) return;
  console.log(`[PYTHON] ${clean}`);
  sendToRenderer('backend-log', { text: clean, level, time: new Date().toLocaleTimeString('en-IN', { hour12: false }) });
}

function handlePythonLine(line) {
  const raw = String(line ?? '').replace(/\r$/, '');
  if (!raw.trim()) return;

  const prefix = 'AURA_UI_EVENT ';
  if (!raw.startsWith(prefix)) {
    let level = 'INFO';
    if (/\bERROR\b|Traceback|failed|exception/i.test(raw)) level = 'ERROR';
    else if (/\bWARNING\b|unavailable|timeout/i.test(raw)) level = 'WARN';
    sendLog(raw, level);
    return;
  }

  try {
    const event = JSON.parse(raw.slice(prefix.length));
    if (!event || !event.type) return;

    // Python uses short event names; preload exposes stable aura-* IPC names.
    // Keep this mapping explicit so state/action/message events actually reach
    // the renderer instead of disappearing silently.
    const channelMap = {
      state: 'aura-state',
      message: 'aura-message',
      action: 'aura-action',
      error: 'backend-error',
      'backend-status': 'backend-status',
      'tts-status': 'tts-status',
      'stt-status': 'stt-status',
      'backend-log': 'backend-log',
      'provider-status': 'provider-status',
      'voice-status': 'voice-status',
      'tts-utterance': 'tts-utterance'
    };

    const channel = channelMap[event.type] || event.type;
    sendToRenderer(channel, event.data || {});
  } catch (error) {
    sendLog(`Invalid backend event: ${error.message}`, 'ERROR');
  }
}

function startPythonBackend() {
  if (backendStarted) return;
  backendStarted = true;

  console.log('[AURA] Starting Python backend...');
  console.log('[AURA] Python:', PYTHON_EXECUTABLE);
  sendToRenderer('backend-status', { connected: false, starting: true });

  pythonProcess = spawn(PYTHON_EXECUTABLE, ['-m', 'app.main', '--electron'], {
    cwd: PROJECT_ROOT,
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    stdio: ['pipe', 'pipe', 'pipe'],
    windowsHide: true
  });

  pythonProcess.stdout.on('data', chunk => {
    stdoutBuffer += chunk.toString();
    const lines = stdoutBuffer.split(/\r?\n/);
    stdoutBuffer = lines.pop() || '';
    lines.forEach(handlePythonLine);
  });

  pythonProcess.stderr.on('data', chunk => {
    const text = chunk.toString();
    text.split(/\r?\n/).forEach(line => {
      if (line.trim()) handlePythonLine(line);
    });
  });

  pythonProcess.on('error', error => {
    console.error('[AURA] Failed to start Python:', error);
    sendToRenderer('backend-error', { message: error.message });
    sendToRenderer('backend-status', { connected: false, starting: false });
  });

  pythonProcess.on('spawn', () => {
    sendToRenderer('backend-status', { connected: true, starting: false });
    sendLog('Python backend process connected.', 'INFO');
  });

  pythonProcess.on('exit', (code, signal) => {
    console.log(`[AURA] Python backend stopped. code=${code}, signal=${signal}`);
    pythonProcess = null;
    backendStarted = false;
    sendToRenderer('backend-status', { connected: false, starting: false });
    if (code && code !== 0) sendToRenderer('backend-error', { message: `Python backend stopped with code ${code}.` });
  });
}

function sendCommand(command) {
  if (!pythonProcess || pythonProcess.killed || !pythonProcess.stdin || !pythonProcess.stdin.writable) {
    sendToRenderer('backend-error', { message: 'AURA backend is not connected yet.' });
    return false;
  }
  try {
    pythonProcess.stdin.write(JSON.stringify(command) + '\n');
    return true;
  } catch (error) {
    sendToRenderer('backend-error', { message: error.message });
    return false;
  }
}

ipcMain.on('aura-send-message', (_event, text) => {
  if (typeof text !== 'string' || !text.trim()) return;
  sendCommand({ type: 'command', text: text.trim() });
});

ipcMain.on('aura-start-listening', () => sendCommand({ type: 'start_listening' }));
ipcMain.on('aura-stop-listening', () => sendCommand({ type: 'stop_listening' }));
ipcMain.on('aura-confirm', (_event, value) => sendCommand({ type: 'confirm', value: Boolean(value) }));
ipcMain.on('aura-mute', () => sendCommand({ type: 'mute' }));

let previousCpuSample = null;
function getCpuUsage() {
  const cpus = os.cpus();
  let idle = 0, total = 0;
  for (const cpu of cpus) {
    idle += cpu.times.idle;
    total += cpu.times.user + cpu.times.nice + cpu.times.sys + cpu.times.irq + cpu.times.idle;
  }
  if (!previousCpuSample) { previousCpuSample = { idle, total }; return 0; }
  const idleDelta = idle - previousCpuSample.idle;
  const totalDelta = total - previousCpuSample.total;
  previousCpuSample = { idle, total };
  return totalDelta > 0 ? (1 - idleDelta / totalDelta) * 100 : 0;
}

ipcMain.handle('aura-system-stats', async () => {
  const total = os.totalmem();
  const free = os.freemem();
  return { cpu: getCpuUsage(), ram: (1 - free / total) * 100 };
});

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1500,
    height: 900,
    fullscreen: true,
    frame: false,
    backgroundColor: '#02050a',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  mainWindow.webContents.on('did-finish-load', () => {
    sendToRenderer('backend-status', { connected: Boolean(pythonProcess), starting: Boolean(!pythonProcess && backendStarted) });
  });
  mainWindow.on('closed', () => { mainWindow = null; });
}

app.whenReady().then(() => {
  createWindow();
  setTimeout(startPythonBackend, 400);
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  if (!pythonProcess || pythonProcess.killed) return;
  try { pythonProcess.stdin.write(JSON.stringify({ type: 'shutdown' }) + '\n'); } catch (_) {}
  setTimeout(() => {
    if (pythonProcess && !pythonProcess.killed) pythonProcess.kill();
  }, 1200);
});
