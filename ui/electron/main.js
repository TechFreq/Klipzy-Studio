// Electron main process
// Spawns the Python FastAPI server, creates the desktop window.
// Cross-platform: Windows, macOS, Linux.

const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const net = require('net');
const crypto = require('crypto');

const SERVER_PORT = 8765;
let mainWindow = null;
let serverProcess = null;

// Shared secret for this launch. The Python server requires it in the
// X-Klipzy-Token header on every request, which stops arbitrary web pages from
// driving the local API (it can trigger installers and touch the filesystem).
// Generated here, handed to Python via env, and given to the renderer through
// the preload bridge. It never touches disk.
const API_TOKEN = crypto.randomBytes(32).toString('hex');

// Set when the backend was already running before we launched, so we know the
// token we generated is not the one that server is using.
let reusedExistingServer = false;

// ------------------------------------------------------------------
// Python server management
// ------------------------------------------------------------------
function findPython() {
  // Dev: venv at repo root (same location as `npm run dev` uses).
  // Packaged: venv + server copied to resourcesPath via electron-builder extraResources.
  const candidates = [
    path.join(__dirname, '..', '..', '.venv', 'Scripts', 'python.exe'),  // Windows dev venv
    path.join(__dirname, '..', '..', '.venv', 'bin', 'python'),          // macOS/Linux dev venv
    path.join(__dirname, '..', '..', 'venv', 'Scripts', 'python.exe'),   // Legacy Windows dev venv
    path.join(__dirname, '..', '..', 'venv', 'bin', 'python'),           // Legacy macOS/Linux dev venv
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

// Tell the renderer how backend startup is going. The window is created before
// the server is up, so it needs to hear about progress and failures rather than
// the user staring at a dead UI.
function reportServerStatus(state, detail) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('server-status', { state, detail: detail || '' });
  }
  console.log(`[server-status] ${state}${detail ? ': ' + detail : ''}`);
}

async function startServer() {
  // If a server is already listening, reuse it rather than fighting for the
  // port. Note that it will have its own token, so the renderer reads the
  // effective one from disk in that case.
  if (!(await isPortFree(SERVER_PORT))) {
    console.log('Server already running on port', SERVER_PORT);
    reusedExistingServer = true;
    reportServerStatus('ready', 'Connected to a backend that was already running.');
    return;
  }

  const serverDir = app.isPackaged ? process.resourcesPath : path.join(__dirname, '..', '..');
  const pythons = findPython();
  const attempted = [];

  for (const py of pythons) {
    try {
      reportServerStatus('starting', `Trying ${py}`);

      serverProcess = spawn(py, ['-m', 'server.api.server'], {
        cwd: serverDir,
        stdio: 'pipe',
        env: {
          ...process.env,
          PYTHONUNBUFFERED: '1',
          KLIPZY_API_TOKEN: API_TOKEN,
          KLIPZY_PORT: String(SERVER_PORT),
        },
      });

      // A missing interpreter fires 'error'; an interpreter that starts but
      // cannot import its dependencies just exits. Watch for both, otherwise a
      // broken install burns the full timeout on every candidate.
      let spawnError = null;
      let exited = null;
      let stderrTail = '';

      serverProcess.once('error', (error) => {
        spawnError = error;
        console.error('Failed to start Python with', py, error.message);
      });
      serverProcess.once('exit', (code, signal) => {
        exited = { code, signal };
      });
      serverProcess.stdout.on('data', (d) => console.log('[server]', d.toString().trim()));
      serverProcess.stderr.on('data', (d) => {
        const text = d.toString();
        stderrTail = (stderrTail + text).slice(-2000);
        console.error('[server-err]', text.trim());
      });

      const ready = await waitForServer(30000, () => spawnError !== null || exited !== null);
      if (ready) {
        console.log('Python server started with', py);
        reportServerStatus('ready', '');
        // If it dies later, say so instead of leaving the UI silently broken.
        serverProcess.once('exit', (code, signal) => {
          if (!app.isQuitting) {
            reportServerStatus(
              'crashed',
              `The backend stopped unexpectedly (code ${code ?? 'null'}${signal ? ', signal ' + signal : ''}). Restart Klipzy Studio.`
            );
          }
        });
        return;
      }

      attempted.push(`${py}: ${spawnError ? spawnError.message : exited ? `exited with code ${exited.code}` : 'timed out'}`);
      if (serverProcess && !serverProcess.killed) serverProcess.kill();
      if (stderrTail.trim()) console.error('[server-err last output]', stderrTail.trim());
    } catch (e) {
      attempted.push(`${py}: ${e.message || e}`);
      console.error('Failed to start with', py, e.message || e);
    }
  }

  serverProcess = null;
  reportServerStatus(
    'failed',
    'Could not start the Python backend. Check that Python 3.10+ is installed and that '
      + '"pip install -r requirements.txt" has been run. Details: ' + attempted.join(' | ')
  );
}

function waitForServer(timeoutMs = 30000, shouldAbort = () => false) {
  return new Promise((resolve) => {
    const start = Date.now();
    const check = async () => {
      if (shouldAbort()) return resolve(false);
      try {
        // /health is intentionally exempt from token auth so this poll works.
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
    title: 'Klipzy Studio',
    webPreferences: {
      // The renderer only talks to the preload bridge (window.clipperAPI); it
      // never needs direct Node access, so keep the process isolated.
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
      sandbox: true,
    },
  });

  mainWindow.loadFile(path.join(__dirname, '..', 'index.html'));

  // Open external links (support/donate/GitHub, and anything with target=_blank)
  // in the user's real browser instead of a bare in-app Electron window.
  const { shell } = require('electron');
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) {
      shell.openExternal(url);
      return { action: 'deny' };
    }
    return { action: 'deny' };
  });

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

ipcMain.handle('api-token', () => {
  // Normal path: the token we generated and passed to our own child process.
  if (!reusedExistingServer) return API_TOKEN;

  // We attached to a backend somebody else started, so its token is whatever
  // that process wrote to logs/api_token.txt.
  try {
    const fs = require('fs');
    const root = app.isPackaged ? process.resourcesPath : path.join(__dirname, '..', '..');
    return fs.readFileSync(path.join(root, 'logs', 'api_token.txt'), 'utf8').trim();
  } catch (e) {
    console.error('Could not read the token of the already-running server:', e.message);
    return '';
  }
});

ipcMain.handle('select-output-folder', async (_event, defaultPath) => {
  const opts = {
    title: 'Choose where to save this export',
    properties: ['openDirectory', 'createDirectory'],
    buttonLabel: 'Select Folder',
  };
  // Start the dialog in the previously-configured output folder when we have one.
  if (defaultPath && typeof defaultPath === 'string') opts.defaultPath = defaultPath;
  const result = await dialog.showOpenDialog(mainWindow, opts);
  if (result.canceled || result.filePaths.length === 0) return null;
  return result.filePaths[0];
});

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
  if (!filePath || typeof filePath !== 'string') return null;
  const { shell } = require('electron');
  const fs = require('fs');
  try {
    // A DIRECTORY should be OPENED (show its contents), while a FILE should be
    // REVEALED (open its parent folder with the file highlighted). Calling
    // showItemInFolder on a directory only selects it inside its parent, which
    // is not what "open the export destination" should do — that was the bug.
    if (fs.statSync(filePath).isDirectory()) {
      const err = await shell.openPath(filePath);
      if (err) shell.showItemInFolder(filePath); // fallback if openPath fails
    } else {
      shell.showItemInFolder(filePath);
    }
  } catch {
    // Path may not exist / stat failed — best-effort reveal.
    shell.showItemInFolder(filePath);
  }
  return filePath;
});

// Backs the "Logs" button in the header. Server output lands in logs/server.log
// via server/logging_setup.py.
ipcMain.handle('open-logs-folder', async () => {
  const { shell } = require('electron');
  const fs = require('fs');
  const root = app.isPackaged ? process.resourcesPath : path.join(__dirname, '..', '..');
  const logsDir = path.join(root, 'logs');
  try {
    fs.mkdirSync(logsDir, { recursive: true });
  } catch (e) { /* already there, or not writable */ }
  const error = await shell.openPath(logsDir);
  return error ? { ok: false, error } : { ok: true, path: logsDir };
});

// ------------------------------------------------------------------
// App lifecycle
// ------------------------------------------------------------------
app.whenReady().then(async () => {
  // Show the window FIRST. Backend startup walks a list of Python candidates
  // with a timeout each, which previously meant the user could sit in front of
  // a blank screen for minutes with no explanation.
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });

  // Start the backend once the page can actually receive status updates.
  mainWindow.webContents.once('did-finish-load', () => {
    startServer();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

// Distinguishes an intentional shutdown from a backend crash.
app.on('before-quit', () => {
  app.isQuitting = true;
});

app.on('quit', () => {
  // Only stop a backend we started ourselves; leave a pre-existing one alone.
  if (serverProcess && !reusedExistingServer) {
    serverProcess.kill();
  }
});