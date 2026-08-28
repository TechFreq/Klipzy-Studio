// Electron main process
// Spawns the Python FastAPI server, creates the desktop window.
// Cross-platform: Windows, macOS, Linux.

const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const net = require('net');

const SERVER_PORT = 8765;
let mainWindow = null;
let serverProcess = null;

// ------------------------------------------------------------------
// Python server management
// ------------------------------------------------------------------
function findPython() {
  // Dev: venv at repo root (same location as `npm run dev` uses).
  // Packaged: venv + server copied to resourcesPath via electron-builder extraResources.
  const candidates = [
    path.join(__dirname, '..', '..', 'venv', 'Scripts', 'python.exe'),   // Windows dev venv
    path.join(__dirname, '..', '..', 'venv', 'bin', 'python'),           // macOS/Linux dev venv
    path.join(process.resourcesPath, 'venv', 'Scripts', 'python.exe'),   // Windows packaged
    path.join(process.resourcesPath, 'venv', 'bin', 'python'),           // macOS/Linux packaged
    'python',
    'python3',
  ];
  return candidates;
}

function isPortFree(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once('error', () => resolve(false));
    server.once('listening', () => server.close(() => resolve(true)));
    server.listen(port, '127.0.0.1');
  });
}

async function startServer() {
  // If server already running, reuse it
  if (!(await isPortFree(SERVER_PORT))) {
    console.log('Server already running on port', SERVER_PORT);
    return;
  }

  const serverDir = app.isPackaged ? process.resourcesPath : path.join(__dirname, '..', '..');
  const pythons = findPython();

  for (const py of pythons) {
    try {
      serverProcess = spawn(py, ['-m', 'server.api.server'], {
        cwd: serverDir,
        stdio: 'pipe',
        env: { ...process.env, PYTHONUNBUFFERED: '1' },
      });

      serverProcess.stdout.on('data', (d) => console.log('[server]', d.toString().trim()));
      serverProcess.stderr.on('data', (d) => console.error('[server-err]', d.toString().trim()));

      // Wait for server to be ready
      const ready = await waitForServer();
      if (ready) {
        console.log('Python server started.');
        return;
      }
    } catch (e) {
      console.error('Failed to start with', py, e);
    }
  }
  console.error('Could not start Python server. Is Python + deps installed?');
}

function waitForServer(timeoutMs = 30000) {
  return new Promise((resolve) => {
    const start = Date.now();
    const check = async () => {
      try {
        const res = await fetch(`http://127.0.0.1:${SERVER_PORT}/health`);
        if (res.ok) return resolve(true);
      } catch (e) { /* not ready yet */ }
      if (Date.now() - start > timeoutMs) return resolve(false);
      setTimeout(check, 500);
    };
    check();
  });
}

// ------------------------------------------------------------------
// Window creation
// ------------------------------------------------------------------
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    title: 'AI Video Clipper',
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  mainWindow.loadFile(path.join(__dirname, '..', 'index.html'));

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// ------------------------------------------------------------------
// IPC handlers
// ------------------------------------------------------------------
ipcMain.handle('select-video', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Select a video to clip',
    properties: ['openFile'],
    filters: [
      { name: 'Videos', extensions: ['mp4', 'mov', 'mkv', 'avi', 'webm', 'flv', 'ts'] },
      { name: 'All files', extensions: ['*'] },
    ],
  });
  if (result.canceled || result.filePaths.length === 0) return null;
  return result.filePaths[0];
});

ipcMain.handle('server-url', () => `http://127.0.0.1:${SERVER_PORT}`);

ipcMain.handle('select-camera-file', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Select your camera / face-cam recording',
    properties: ['openFile'],
    filters: [
      { name: 'Videos', extensions: ['mp4', 'mov', 'mkv', 'webm'] },
      { name: 'All files', extensions: ['*'] },
    ],
  });
  if (result.canceled || result.filePaths.length === 0) return null;
  return result.filePaths[0];
});

ipcMain.handle('reveal-in-folder', async (_event, filePath) => {
  if (!filePath || typeof filePath !== 'string') return '/';
  const { shell } = require('electron');
  shell.showItemInFolder(filePath);
  return filePath;
});

// ------------------------------------------------------------------
// App lifecycle
// ------------------------------------------------------------------
app.whenReady().then(async () => {
  await startServer();
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('quit', () => {
  if (serverProcess) {
    serverProcess.kill();
  }
});