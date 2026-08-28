// Preload script - exposes safe IPC to the renderer
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('clipperAPI', {
  selectVideo: () => ipcRenderer.invoke('select-video'),
  getServerUrl: () => ipcRenderer.invoke('server-url'),
});