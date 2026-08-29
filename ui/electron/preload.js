// Preload script - exposes safe IPC to the renderer
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('clipperAPI', {
  selectVideo: () => ipcRenderer.invoke('select-video'),
  getServerUrl: () => ipcRenderer.invoke('server-url'),
  selectCameraClip: () => ipcRenderer.invoke('select-camera-file'),
  selectOutputFolder: () => ipcRenderer.invoke('select-output-folder'),
  revealInFolder: (filePath) => ipcRenderer.invoke('reveal-in-folder', filePath),
});