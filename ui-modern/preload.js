const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('aura', {
  platform: process.platform,
  sendMessage: text => ipcRenderer.send('aura-send-message', text),
  startListening: () => ipcRenderer.send('aura-start-listening'),
  stopListening: () => ipcRenderer.send('aura-stop-listening'),
  confirm: value => ipcRenderer.send('aura-confirm', Boolean(value)),
  getSystemStats: () => ipcRenderer.invoke('aura-system-stats'),
  onStateChange: cb => ipcRenderer.on('aura-state', (_e, data) => cb(data)),
  onMessage: cb => ipcRenderer.on('aura-message', (_e, data) => cb(data)),
  onAction: cb => ipcRenderer.on('aura-action', (_e, data) => cb(data)),
  onBackendStatus: cb => ipcRenderer.on('backend-status', (_e, data) => cb(data)),
  onBackendError: cb => ipcRenderer.on('backend-error', (_e, data) => cb(data)),
  onBackendLog: cb => ipcRenderer.on('backend-log', (_e, data) => cb(data))
});
