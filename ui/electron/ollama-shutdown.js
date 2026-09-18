// Local Ollama is shared with other apps. Never stop it without a quit-time choice.
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const run = promisify(execFile);
async function isOllamaRunning(platform = process.platform, execute = run) {
  if (platform === 'win32') {
    const { stdout } = await execute('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
      "@(Get-Process -ErrorAction Stop | Where-Object { $_.ProcessName -in @('ollama','ollama app','ollama_llama_server') }).Count; exit 0"], { windowsHide: true, timeout: 5000 });
    return Number(stdout.trim()) > 0;
  }
  try { await execute('pgrep', ['-x', 'ollama'], { timeout: 5000 }); return true; }
  catch (error) { if (error.code === 1) return false; throw error; }
}
async function stopOllama(platform = process.platform, execute = run) {
  if (platform === 'win32') {
    await execute('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
      "$ErrorActionPreference='Stop'; Get-Process -ErrorAction Stop | Where-Object { $_.ProcessName -in @('ollama','ollama app','ollama_llama_server') } | ForEach-Object { $ollamaProcessId = $_.Id; try { Stop-Process -Id $ollamaProcessId -Force -ErrorAction Stop } catch { if (Get-Process -Id $ollamaProcessId -ErrorAction SilentlyContinue) { throw } } }; exit 0"], { windowsHide: true, timeout: 10000 });
  } else {
    if (platform === 'darwin') {
      await execute('osascript', ['-e', 'tell application "Ollama" to quit'], { timeout: 10000 }).catch(() => {});
    }
    try { await execute('pkill', ['-x', 'ollama'], { timeout: 10000 }); }
    catch (error) { if (error.code !== 1) throw error; }
  }
}
async function decideQuit({ detect = isOllamaRunning, stop = stopOllama, ask }) {
  let running;
  try { running = await detect(); }
  catch (_) {
    return (await ask({ type:'warning', title:'Quit Klipzy Studio?', message:'Could not check whether Ollama is running.', detail:'Quit Klipzy and leave any Ollama processes unchanged?', buttons:['Quit Klipzy','Cancel'], defaultId:0, cancelId:1 })).response === 0;
  }
  if (!running) return true;
  const { response } = await ask({ type:'question', title:'Quit Klipzy Studio', message:'Ollama is still running in the background.',
    detail:'Keep it running for other apps, or stop local Ollama to release its memory. Stopping it interrupts other apps using this local Ollama instance. Klipzy will close its own backend either way.',
    buttons:['Keep Ollama running and quit','Stop Ollama and quit','Cancel'], defaultId:0, cancelId:2, noLink:true });
  if (response === 2) return false;
  if (response === 0) return true;
  try { await stop(); return true; }
  catch (error) {
    return (await ask({ type:'warning', title:'Quit Klipzy Studio?',
      message:'Ollama could not be stopped. You can still quit Klipzy.',
      detail:'Any remaining Ollama process will be left running. You can close it from its tray menu.\n\n' + error.message,
      buttons:['Quit Klipzy anyway','Cancel'], defaultId:0, cancelId:1, noLink:true })).response === 0;
  }
}
module.exports = { isOllamaRunning, stopOllama, decideQuit };
