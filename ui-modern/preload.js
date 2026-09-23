const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('aura', {
  platform: process.platform,
  sendMessage: text => ipcRenderer.send('aura-send-message', text),
  startListening: () => ipcRenderer.send('aura-start-listening'),
  stopListening: () => ipcRenderer.send('aura-stop-listening'),
  confirm: value => ipcRenderer.send('aura-confirm', Boolean(value)),
  mute: () => ipcRenderer.send('aura-mute'),
  getSystemStats: () => ipcRenderer.invoke('aura-system-stats'),
  onStateChange: cb => ipcRenderer.on('aura-state', (_e, data) => cb(data)),
  onMessage: cb => ipcRenderer.on('aura-message', (_e, data) => cb(data)),
  onAction: cb => ipcRenderer.on('aura-action', (_e, data) => cb(data)),
  onBackendStatus: cb => ipcRenderer.on('backend-status', (_e, data) => cb(data)),
  onBackendError: cb => ipcRenderer.on('backend-error', (_e, data) => cb(data)),
  onBackendLog: cb => ipcRenderer.on('backend-log', (_e, data) => cb(data)),
  onProviderStatus: cb => ipcRenderer.on('provider-status', (_e, data) => cb(data)),
  onSttStatus: cb => ipcRenderer.on('stt-status', (_e, data) => cb(data)),
  onTtsStatus: cb => ipcRenderer.on('tts-status', (_e, data) => cb(data)),
  // Per-utterance TTS status (Task 2/8) — distinct from onTtsStatus, which
  // is the one-time "is the engine available at all" check at startup.
  // Fires with {state: "speaking"|"completed"|"failed", emotion, ...}
  // around every spoken reply.
  onTtsUtterance: cb => ipcRenderer.on('tts-utterance', (_e, data) => cb(data))
});
