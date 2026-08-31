// Preload script - exposes safe IPC to the renderer.
// The renderer has no Node access; everything it needs comes through here.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('clipperAPI', {
  selectVideo: () => ipcRenderer.invoke('select-video'),
  getServerUrl: () => ipcRenderer.invoke('server-url'),
  // Shared secret for the local API. See server/auth.py for why it exists.
  getApiToken: () => ipcRenderer.invoke('api-token'),
  selectCameraClip: () => ipcRenderer.invoke('select-camera-file'),
  selectOutputFolder: () => ipcRenderer.invoke('select-output-folder'),
  revealInFolder: (filePath) => ipcRenderer.invoke('reveal-in-folder', filePath),
  openLogsFolder: () => ipcRenderer.invoke('open-logs-folder'),
  // Backend lifecycle updates (starting / ready / failed / crashed) so the UI
  // can tell the user what is happening instead of failing silently.
  onServerStatus: (callback) => {
    ipcRenderer.on('server-status', (_event, payload) => callback(payload));
  },
});
