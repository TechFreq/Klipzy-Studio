// Install history is held by the backend, so navigating away never loses progress.
let installJobs = [], installPollTimer = null;
function renderInstallActivity() {
  const host = document.getElementById('install-activity');
  if (!host) return;
  const expanded = new Set([...host.querySelectorAll('article[data-job] details[open]')].map(el => el.parentElement.dataset.job));
  host.innerHTML = installJobs.length ? installJobs.slice().reverse().map(job => {
    const busy = !['complete','failed'].includes(job.state);
    const label = job.restart_required ? 'Verified · Restart required' : job.state === 'complete' ? 'Verified · Already installed' : job.state;
    return `<article class="install-job" data-job="${escapeHtml(job.id)}" aria-label="${escapeHtml(job.component)} installation">
      <strong>${escapeHtml(job.component)}</strong> <span role="status">${escapeHtml(label)}</span>
      ${busy ? '<progress aria-label="Installation in progress"></progress>' : ''}
      ${job.restart_required ? '<button class="btn btn-primary btn-small" data-restart-app>Restart Klipzy</button>' : ''}
      <details ${busy || job.state === 'failed' || expanded.has(job.id) ? 'open' : ''}><summary>Installer output</summary><pre>${escapeHtml(job.output || 'Checking environment…')}</pre></details>
    </article>`;
  }).join('') : '<p class="muted">Downloads, verification results, and installer output will appear here.</p>';
  host.querySelectorAll('[data-restart-app]').forEach(button => button.onclick = restartAfterInstall);
  document.querySelectorAll('[data-install], #gpu-enable-btn, #install-diarization-btn, #install-all-btn, [data-uninstall], #gpu-revert-btn').forEach(button => {
    const component = button.dataset.install || (button.id === 'gpu-enable-btn' ? 'gpu' : button.id === 'install-diarization-btn' ? 'diarization' : '');
    const pending = installJobs.find(j => j.restart_required && (j.component === component || ['gpu','pytorch'].includes(j.component) && ['gpu','pytorch'].includes(component)));
    const busy = installJobs.some(j => !['complete','failed'].includes(j.state));
    if (pending && component) { button.disabled = true; button.textContent = '✓ Installed · Restart below'; }
    else if (busy) {
      if (!button.dataset.installLocked) button.dataset.wasDisabled = String(button.disabled);
      button.disabled = true; button.dataset.installLocked = '1';
    }
    else if (button.dataset.installLocked) { button.disabled = button.dataset.wasDisabled === 'true'; delete button.dataset.installLocked; delete button.dataset.wasDisabled; }
  });
}
async function refreshInstallActivity() {
  const response = await fetch(`${serverUrl}/api/setup/install-jobs`);
  if (!response.ok) throw new Error('Could not read install progress');
  const data = await response.json();
  const changed = JSON.stringify(installJobs) !== JSON.stringify(data.jobs || []);
  installJobs = data.jobs || [];
  if (changed || !document.getElementById('install-activity')?.children.length) renderInstallActivity();
  return installJobs;
}
function startInstallActivity() {
  refreshInstallActivity().catch(() => {});
  if (!installPollTimer) installPollTimer = setInterval(() => refreshInstallActivity().catch(() => {}), 1500);
}
async function runDependencyInstall(component) {
  const response = await fetch(`${serverUrl}/api/setup/install-jobs`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({component})});
  const started = await response.json();
  if (!response.ok) throw new Error(started.detail || 'Could not start install');
  document.getElementById('install-activity')?.scrollIntoView({behavior:'smooth',block:'nearest'});
  for (;;) {
    const jobs = await refreshInstallActivity();
    const job = jobs.find(j => j.id === started.id);
    if (!job) throw new Error('Install record unavailable. Check Settings before retrying.');
    if (['complete','failed'].includes(job.state)) {
      const result = {...job, returncode:job.ok ? 0 : 1, stderr:job.output};
      return {ok:job.ok, json:async()=>result};
    }
    await new Promise(resolve => setTimeout(resolve,1000));
  }
}
async function restartAfterInstall() {
  if (!window.clipperAPI?.restartApp) { showAlert('Close and reopen Klipzy to load the updated dependencies.'); return; }
  if (!await showConfirm('Restart Klipzy to load updated dependencies? Finish or cancel any processing jobs first.', 'Restart Klipzy')) return;
  try {
    const response = await window.clipperAPI.restartApp();
    if (response?.error) throw new Error(response.error);
  } catch(error) { showAlert(error.message); }
}
