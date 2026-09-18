// Electron main process
// Spawns the Python FastAPI server, creates the desktop window.
// Cross-platform: Windows, macOS, Linux.

const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const net = require('net');
const { decideQuit } = require('./ollama-shutdown');
const { compatibleBackend } = require('./backend-identity');
let quitApproved = false, quitPromptPending = false;
const crypto = require('crypto');

let SERVER_PORT = 8765;
let mainWindow = null;
let serverProcess = null;
let serverStatus = { state: "starting", detail: "Starting local engine..." };

// Shared secret for this launch. The Python server requires it in the
// X-Klipzy-Token header on every request, which stops arbitrary web pages from
// driving the local API (it can trigger installers and touch the filesystem).
// Generated here, handed to Python via env, and given to the renderer through
// the preload bridge. The backend stores it locally for authenticated reconnects.
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
  const fs = require('fs');
  const isWin = process.platform === 'win32';
  // Only consider venv interpreters for THIS OS. Previously the Windows
  // python.exe paths were tried first on macOS/Linux, spawning and failing with
  // a scary "EACCES" line before falling back — build the right list per-OS.
  const winVenv = [
    path.join(__dirname, '..', '..', '.venv', 'Scripts', 'python.exe'),   // Windows dev venv
    path.join(__dirname, '..', '..', 'venv', 'Scripts', 'python.exe'),    // Legacy Windows dev venv
    path.join(process.resourcesPath || '', 'venv', 'Scripts', 'python.exe'), // Windows packaged
  ];
  const nixVenv = [
    path.join(__dirname, '..', '..', '.venv', 'bin', 'python'),           // macOS/Linux dev venv
    path.join(__dirname, '..', '..', 'venv', 'bin', 'python'),            // Legacy macOS/Linux dev venv
    path.join(process.resourcesPath || '', 'venv', 'bin', 'python'),      // macOS/Linux packaged
  ];
  // Only keep venv paths that actually exist, so we never spawn a wrong/missing
  // interpreter just to log an error.
  const venv = (isWin ? winVenv : nixVenv).filter((p) => {
    try { return fs.existsSync(p); } catch { return false; }
  });
  // Never switch environments just because the project environment starts slowly.
  return venv.length ? [venv[0]] : [isWin ? 'python' : 'python3', isWin ? 'python3' : 'python'];
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
  serverStatus = { state, detail: detail || "" };
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('server-status', { state, detail: detail || '' });
  }
  console.log(`[server-status] ${state}${detail ? ': ' + detail : ''}`);
}

async function startServer() {
  const serverDir = app.isPackaged ? process.resourcesPath : path.join(__dirname, '..', '..');
  const pythons = findPython();
  if (!(await isPortFree(SERVER_PORT))) {
    reusedExistingServer = true;
    try {
      const token = await getEffectiveApiToken();
      const response = await fetch(`http://127.0.0.1:${SERVER_PORT}/ready`, {
        headers: {'X-Klipzy-Token': token}, signal: AbortSignal.timeout(5000),
      });
      const identity = response.ok ? await response.json() : null;
      if (compatibleBackend(identity, pythons[0], serverDir)) {
        reportServerStatus('ready', 'Connected to the matching local engine.');
        return;
      }
    } catch (_) { /* Older or unrelated server: leave it untouched. */ }
    reusedExistingServer = false;
    let available = false;
    for (let port = 8766; port <= 8785; port++) {
      if (await isPortFree(port)) { SERVER_PORT = port; available = true; break; }
    }
    if (!available) {
      reportServerStatus('failed', 'No free local engine port was found (8765–8785). Close unused Klipzy instances and reopen.');
      return;
    }
    reportServerStatus('starting', `An older or different engine occupies port 8765. Starting the project environment on port ${SERVER_PORT}.`);
  }

  const attempted = [];

  for (const py of pythons) {
    if (app.isQuitting) return;
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

      const ready = await waitForServer(180000, () => app.isQuitting || spawnError !== null || exited !== null);
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

      if (app.isQuitting) return;
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
    'Could not start the Python backend in the selected environment. Reopen Klipzy; if this repeats, '
      + 'check logs/server.log and repair dependencies in the project .venv using the launcher. '
      + 'A slow startup does not mean PyTorch or a GPU is missing. Details: ' + attempted.join(' | ')
  );
}

function waitForServer(timeoutMs = 30000, shouldAbort = () => false) {
  return new Promise((resolve) => {
    const start = Date.now();
    const check = async () => {
      if (shouldAbort()) return resolve(false);
      try {
        // Readiness must not import AI libraries or run hardware probes.
        const res = await fetch(`http://127.0.0.1:${SERVER_PORT}/ready`, {
          headers: { "X-Klipzy-Token": API_TOKEN }, signal: AbortSignal.timeout(2000),
        });
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

  mainWindow.on('close', (event) => {
    if (!quitApproved) { event.preventDefault(); app.quit(); }
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

ipcMain.handle('restart-app', async () => {
  if (reusedExistingServer) return {error:'This engine was started outside this window. Close Klipzy and restart that backend before reopening.'};
  try {
    const headers = {'X-Klipzy-Token': await getEffectiveApiToken()};
    const response = await fetch(`http://127.0.0.1:${SERVER_PORT}/api/setup/install-jobs`, {headers, signal:AbortSignal.timeout(5000)});
    if (!response.ok) throw new Error('Could not verify installer status');
    const data = await response.json();
    if (data.jobs.some(job => !['complete','failed'].includes(job.state))) return {error:'Wait for dependency installation to finish before restarting.'};
    const jobsResponse = await fetch(`http://127.0.0.1:${SERVER_PORT}/jobs`, {headers, signal:AbortSignal.timeout(5000)});
    if (!jobsResponse.ok) throw new Error('Could not verify processing status');
    const jobsData = await jobsResponse.json();
    const jobs = Array.isArray(jobsData) ? jobsData : jobsData.jobs || [];
    if (jobs.some(job => ['queued','running','processing'].includes(job.status))) return {error:'Finish or cancel queued and running jobs before restarting.'};
    const editorResponse = await fetch(`http://127.0.0.1:${SERVER_PORT}/editor/exports`, {headers, signal:AbortSignal.timeout(5000)});
    if (!editorResponse.ok) throw new Error('Could not verify editor exports; finish exports before restarting.');
    const editorData = await editorResponse.json();
    if (editorData.jobs.some(job => !job.done)) return {error:'Finish or cancel editor exports before restarting.'};
    app.isQuitting = true;
    if (serverProcess && serverProcess.exitCode === null) {
      const stopped = await new Promise(resolve => {
        const timer = setTimeout(() => resolve(false),10000);
        serverProcess.once('exit', () => {clearTimeout(timer); resolve(true);});
        serverProcess.kill();
      });
      if (!stopped) {app.isQuitting = false; return {error:'The backend did not stop. Close and reopen Klipzy manually.'};}
    }
    quitApproved = true;
    app.relaunch();
    app.quit();
    return {ok:true};
  } catch(error) {app.isQuitting = false; return {error:error.message};}
});

ipcMain.handle('server-status', () => serverStatus);
ipcMain.handle('server-url', () => `http://127.0.0.1:${SERVER_PORT}`);

async function getEffectiveApiToken() {
  if (!reusedExistingServer) return API_TOKEN;
  const fs = require('fs');
  const root = app.isPackaged ? process.resourcesPath : path.join(__dirname, '..', '..');
  try {
    return fs.readFileSync(path.join(root, 'logs', 'api_token.txt'), 'utf8').trim();
  } catch (_) { return ''; }
}
ipcMain.handle('api-token', getEffectiveApiToken);

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

ipcMain.handle('select-music-file', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Select a background music track',
    properties: ['openFile'],
    filters: [
      { name: 'Audio', extensions: ['mp3', 'wav', 'm4a', 'aac', 'flac', 'ogg'] },
      { name: 'All files', extensions: ['*'] },
    ],
  });
  if (result.canceled || result.filePaths.length === 0) return null;
  return result.filePaths[0];
});

ipcMain.handle('select-videos-multi', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Select videos to batch process',
    properties: ['openFile', 'multiSelections'],
    filters: [
      { name: 'Videos', extensions: ['mp4', 'mov', 'mkv', 'webm', 'avi', 'm4v'] },
      { name: 'All files', extensions: ['*'] },
    ],
  });
  if (result.canceled || result.filePaths.length === 0) return [];
  return result.filePaths;
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
app.on('before-quit', (event) => {
  if (quitApproved) { app.isQuitting = true; return; }
  event.preventDefault();
  if (quitPromptPending) return;
  quitPromptPending = true;
  const ask = options => mainWindow && !mainWindow.isDestroyed()
    ? dialog.showMessageBox(mainWindow, options) : dialog.showMessageBox(options);
  decideQuit({ ask })
    .then(approved => { if (approved) { quitApproved = true; app.isQuitting = true; app.quit(); } })
    .catch(error => {
      console.error('Quit prompt failed; leaving Ollama unchanged:', error.message);
      quitApproved = true; app.isQuitting = true; app.quit();
    })
    .finally(() => { quitPromptPending = false; });
});

app.on('quit', () => {
  // Only stop a backend we started ourselves; leave a pre-existing one alone.
  if (serverProcess && !reusedExistingServer) {
    serverProcess.kill();
  }
});