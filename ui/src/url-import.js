// Direct-media imports use the authenticated local API, never a renderer downloader.
let importPollTimer = null;
let importPollBusy = false;
function importMessage(text) { document.getElementById('import-message').textContent = text; }
async function startUrlImport() {
  const input = document.getElementById('import-url');
  const consent = document.getElementById('import-rights');
  const button = document.getElementById('import-start');
  if (!input.value.trim() || !consent.checked) { importMessage('Paste a link and confirm download/edit permissions first.'); return; }
  button.disabled = true;
  try {
    const response = await fetch(`${serverUrl}/imports`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url:input.value.trim(), rights_confirmed:true, policy_version:'2026-09-18', project_id:currentProjectId || ''})});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Check the link and import permissions.');
    input.value = ''; consent.checked = false;
    importMessage('Import queued. You can keep working; return here to add the downloaded video to your project.');
    await refreshUrlImports();
  } catch (error) { importMessage(error.message); }
  finally { button.disabled = false; }
}
async function refreshUrlImports() {
  if (importPollBusy) return;
  importPollBusy = true;
  clearTimeout(importPollTimer);
  try {
    const response = await fetch(`${serverUrl}/imports`);
    if (!response.ok) throw new Error('Could not check downloads. Reopen this panel to retry.');
    const jobs = await response.json();
    const root = document.getElementById('import-jobs');
    root.replaceChildren();
    for (const job of jobs.slice().reverse()) {
      const row = document.createElement('div'); row.className = 'import-job';
      const text = document.createElement('p');
      const mb = n => (n / 1048576).toFixed(1);
      text.textContent = `${job.host} · ${job.status} · ${mb(job.bytes)} MB${job.total ? ' / ' + mb(job.total) + ' MB' : ''}${job.speed ? ' · ' + mb(job.speed) + ' MB/s' : ''}${job.error ? ' — ' + job.error : ''}`;
      row.append(text);
      if (['queued','downloading','verifying'].includes(job.status)) {
        const progress = document.createElement('progress'); progress.max = 1;
        progress.setAttribute('aria-label','Import progress');
        if (job.total && job.status === 'downloading') progress.value = Math.min(1, job.bytes/job.total);
        row.append(progress);
        const cancel = document.createElement('button'); cancel.className = 'btn btn-small'; cancel.textContent = 'Cancel';
        cancel.onclick = async () => { cancel.disabled = true; try { const res = await fetch(`${serverUrl}/imports/${encodeURIComponent(job.id)}/cancel`, {method:'POST'}); if (!res.ok) throw new Error('Could not cancel import.'); await refreshUrlImports(); } catch (error) { importMessage(error.message); cancel.disabled = false; } };
        row.append(cancel);
      }
      if (job.status === 'ready') {
        const add = document.createElement('button'); add.className = 'btn btn-small';
        add.textContent = 'Add to current project';
        add.onclick = () => { sourceSelectionMode = 'add'; addSourceFiles([{path:job.path, name:'Imported video · ' + job.host}]); importMessage('Video added to the current project.'); };
        row.append(add);
      }
      root.append(row);
    }
    if (jobs.some(job => ['queued','downloading','verifying'].includes(job.status))) importPollTimer = setTimeout(refreshUrlImports, 1000);
  } catch (error) { importMessage(error.message); }
  finally { importPollBusy = false; }
}
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('import-start')?.addEventListener('click', startUrlImport);
  document.querySelector('.url-import-panel')?.addEventListener('toggle', event => { if (event.target.open) refreshUrlImports(); });
});
