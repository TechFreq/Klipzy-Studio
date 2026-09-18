let setupRecommendations = null;
let selectedOllamaModel = "";
let localModelSaving = false, localModelRevision = 0;
function syncSidebarModels(models) {
  const sidebar = document.getElementById('sidebar-ai-model');
  if (sidebar) {
    if (models) sidebar.innerHTML = models.map(model => `<option value="${escapeHtml(model)}">${escapeHtml(model)}</option>`).join('');
    if (selectedOllamaModel && ![...sidebar.options].some(option => option.value === selectedOllamaModel)) sidebar.add(new Option(selectedOllamaModel + ' · download if needed', selectedOllamaModel));
    sidebar.value = selectedOllamaModel;
    sidebar.disabled = localModelSaving || !sidebar.options.length;
    sidebar.onchange = () => chooseLocalModel(sidebar.value).catch(error => showToast(error.message,'error'));
  }
  const quality = document.getElementById('sidebar-whisper-model');
  const source = document.getElementById('whisper-model');
  if (quality && source) {
    quality.innerHTML = source.innerHTML;
    quality.value = source.value;
    quality.onchange = () => { source.value = quality.value; source.dispatchEvent(new Event('change',{bubbles:true})); };
    if (!source.dataset.sidebarBound) {
      source.dataset.sidebarBound = '1';
      source.addEventListener('change', () => {quality.value = source.value;});
      document.getElementById('ai-whisper-model')?.addEventListener('change', () => {quality.value = source.value;});
    }
  }
}
async function chooseLocalModel(model) {
  if (localModelSaving) return;
  const previous = selectedOllamaModel;
  localModelSaving = true; localModelRevision++;
  selectedOllamaModel = model;
  renderRecommendations(setupRecommendations);
  syncSidebarModels();
  const status = document.getElementById('sidebar-model-status');
  if (status) status.textContent = 'Saving model selection…';
  document.querySelectorAll('[data-model-kind="ollama"], [data-use], #ai-ollama-model').forEach(el => {el.disabled=true;});
  try {
    const response = await fetch(`${serverUrl}/api/setup/ai-model`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'ollama',model})});
    if (!response.ok) throw new Error('Could not save model selection');
    if (status) status.textContent = 'Selected for the next local AI request. Loading occurs when used; running processing keeps its current model.';
    showToast(`${model} selected. Download it in Settings if needed.`, 'success');
  } catch(error) {
    selectedOllamaModel = previous;
    if (status) status.textContent = 'Selection failed; previous model restored.';
    throw error;
  } finally {
    localModelSaving = false;
    renderRecommendations(setupRecommendations);
    syncSidebarModels();
    document.querySelectorAll('[data-use], #ai-ollama-model').forEach(el => {el.disabled=false;});
    loadAiModels();
    renderModelCatalog();
  }
}

/*
 * Setup / System panel: dependency rows, model catalog, AI models + engine, GPU acceleration and the render estimator.
 *
 * Split out of renderer.js, which had grown past 5,800 lines and made bugs easy
 * to hide. This is a CLASSIC script (not an ES module), loaded after
 * renderer.js in index.html, so it shares one global scope with it: top-level
 * functions and state declared there are available here and vice versa. Nothing
 * here runs work at load time beyond registering listeners, so load order only
 * needs renderer.js to come first.
 */

// ------------------------------------------------------------------
// Setup / System panel
// ------------------------------------------------------------------
async function loadSetupPanel() {
  if (typeof backendState !== 'undefined' && backendState === 'starting') {
    document.getElementById('setup-hardware').textContent = 'Starting local engine. Hardware checks will run automatically when it is ready...';
    return;
  }
  startInstallActivity();
  await refreshInstallActivity().catch(() => {});
  loadSupportLinks();
  try {
    const res = await fetch(`${serverUrl}/api/setup/status`);
    if (!res.ok) throw new Error(`Setup check failed (${res.status})`);
    const data = await res.json();
    renderHardware(data);
    loadGpuAcceleration();
    setupRecommendations = data.recommendations;
    renderRecommendations(data.recommendations);
    renderDeps(data);
    bindInstallAll();
    bindClearCache();
    loadAiModels(data);
    loadLlmEndpoint();
    loadAsrEndpoint();
    renderModelCatalog();
    loadOptionalAddons();
  } catch (e) {
    document.getElementById('setup-hardware').innerHTML = '<span class="muted">⚠️ Could not reach the server.</span>';
  }
}

// Optional AI add-ons (currently: speaker diarization via pyannote + HF token).
async function loadOptionalAddons() {
  const statusEl = document.getElementById('diarization-status');
  const installBtn = document.getElementById('install-diarization-btn');
  // Diarization availability.
  try {
    const r = await fetch(`${serverUrl}/tools/diarization-available`);
    const d = await r.json();
    if (statusEl) statusEl.textContent = d.available ? '✓ Installed' : 'Not installed';
    if (installBtn) {
      installBtn.textContent = d.available ? '✓ Installed' : '⬇️ Install pyannote';
      installBtn.disabled = !!d.available;
    }
  } catch (_) { if (statusEl) statusEl.textContent = ''; }
  // HF token status.
  try {
    const r = await fetch(`${serverUrl}/api/setup/hf-token`);
    const d = await r.json();
    const ts = document.getElementById('hf-token-status');
    if (ts) ts.textContent = d.set ? '· saved ✓' : '· not set';
  } catch (_) {}

  if (installBtn && !installBtn.dataset.bound) {
    installBtn.dataset.bound = '1';
    installBtn.addEventListener('click', async () => {
      const orig = installBtn.textContent;
      installBtn.disabled = true;
      installBtn.textContent = '⏳ Installing… (a few min)';
      showToast('Installing pyannote.audio — this is a large download', 'info');
      try {
        const r = await runDependencyInstall('diarization');
        const d = await r.json();
        if (d.ok) { showToast('✅ pyannote installed — restart the app to load it', 'success'); }
        else throw new Error(d.error || d.stderr || 'install failed');
      } catch (e) {
        showToast(`Install failed: ${e.message || e}`, 'error');
      } finally {
        installBtn.disabled = false;
        installBtn.textContent = orig;
        await loadOptionalAddons();
        renderInstallActivity();
      }
    });
  }
  const saveBtn = document.getElementById('hf-token-save');
  if (saveBtn && !saveBtn.dataset.bound) {
    saveBtn.dataset.bound = '1';
    saveBtn.addEventListener('click', async () => {
      const input = document.getElementById('hf-token-input');
      const token = (input && input.value || '').trim();
      try {
        const r = await fetch(`${serverUrl}/api/setup/hf-token`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token }),
        });
        if (!r.ok) throw new Error(`Server returned ${r.status}`);
        if (input) input.value = '';
        showToast(token ? 'Hugging Face token saved ✓' : 'Token cleared', 'success');
        loadOptionalAddons();
      } catch (e) {
        showToast(`Could not save token: ${e.message || e}`, 'error');
      }
    });
  }
}

// Clips-Kitty-style local-LLM catalog: browse curated models (small → large),
// download the ones you want, and set the active model. The ⭐ pick matches
// your hardware. Downloads use Ollama, so the row is disabled if it's absent.
async function renderModelCatalog() {
  const grid = document.getElementById('model-catalog');
  if (!grid) return;
  let data;
  try {
    // Pass the active caption preset so high-energy presets get a punchier pick.
    const preset = document.getElementById('generated-caption-preset')?.value || '';
    const res = await fetch(`${serverUrl}/api/setup/model-catalog?preset=${encodeURIComponent(preset)}`);
    if (!res.ok) throw new Error();
    data = await res.json();
  } catch (_) {
    grid.innerHTML = '<span class="muted">⚠️ Could not load the model catalog.</span>';
    return;
  }
  const ollamaReady = !!(data.ollama && data.ollama.installed);
  const active = data.active;
  grid.innerHTML = (data.models || []).map((m) => {
    const isActive = m.name === active;
    const badges = [];
    if (m.recommended) badges.push('<span class="model-badge rec">⭐ Recommended</span>');
    if (isActive) badges.push(`<span class="model-badge active">● ${m.installed ? 'In use' : 'Selected · download needed'}</span>`);
    else if (m.installed) badges.push('<span class="model-badge dl">✓ Downloaded</span>');
    if (m.tested) badges.push('<span class="model-badge tested" title="Has local test coverage; not a clip-quality certification">🧪 Tested</span>');
    if (m.license) badges.push('<span class="model-badge license" title="Model license">' + escapeHtml(m.license) + '</span>');
    if (!m.fits_ram) badges.push('<span class="model-badge warn">Needs ' + m.min_ram_gb + 'GB+ RAM</span>');

    let actions;
    if (!m.installed) {
      actions = `<button class="btn btn-small btn-secondary" data-pull="${escapeHtml(m.name)}" ${ollamaReady ? '' : 'disabled'}>⬇️ Download (${m.size_gb}GB)</button>`;
    } else if (isActive) {
      actions = `<button class="btn btn-small" disabled>● In use</button>` +
                `<button class="btn btn-small btn-ghost" data-remove="${escapeHtml(m.name)}" title="Delete this model from disk">🗑</button>`;
    } else {
      actions = `<button class="btn btn-small btn-primary" data-use="${escapeHtml(m.name)}">Use</button>` +
                `<button class="btn btn-small btn-ghost" data-remove="${escapeHtml(m.name)}" title="Delete this model from disk">🗑</button>`;
    }
    return `
      <div class="model-card${isActive ? ' is-active' : ''}">
        <div class="model-card-top">
          <span class="model-name">${escapeHtml(m.label)}</span>
          <span class="model-tier">${escapeHtml(m.tier)}</span>
        </div>
        <div class="model-badges">${badges.join('')}</div>
        <div class="model-note muted small">${escapeHtml(m.note)}</div>
        <div class="model-meta muted small">${escapeHtml(m.params)} · ~${m.size_gb}GB${m.installed ? ' · downloaded' : ' download'}${m.fits_vram ? ' · ⚡ fits your GPU' : ''}</div>
        <div class="model-card-actions">${actions}</div>
      </div>`;
  }).join('');

  if (!ollamaReady) {
    grid.insertAdjacentHTML('afterbegin',
      '<p class="muted small" style="grid-column:1/-1;">Ollama isn\'t installed yet — install it from the dependencies above to download and run these models.</p>');
  }

  // "Currently in use" banner at the very top so the active model is obvious.
  if (active) {
    const am = (data.models || []).find((m) => m.name === active);
    grid.insertAdjacentHTML('afterbegin',
      `<div class="model-inuse-banner" style="grid-column:1/-1;">${am?.installed ? '🟢 <strong>In use:</strong>' : '<strong>Selected:</strong>'} ${escapeHtml(am ? am.label : active)} <span class="muted small">${am?.installed ? '— used for local AI hooks, titles &amp; chat.' : '— download this model below to use it locally.'}</span></div>`);
  }

  // Transparency footnote (Clips-Kitty-style honesty): be clear about which
  // models we've actually measured vs. ones that just "should work".
  grid.insertAdjacentHTML('beforeend',
    '<p class="muted small" style="grid-column:1/-1; margin-top:6px;">🧪 <strong>Tested</strong> models were benchmarked for clip-selection quality on real footage (RTX 3060). The rest run the same way but haven\'t been measured for pick quality. License badges are best-effort — verify terms before commercial use.</p>');

  grid.querySelectorAll('[data-pull]').forEach((btn) => {
    btn.addEventListener('click', () => startModelPull(btn.dataset.pull, btn));
  });

  grid.querySelectorAll('[data-use]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      chooseLocalModel(btn.dataset.use).catch(error => showToast(error.message,'error'));
    });
  });

  grid.querySelectorAll('[data-remove]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const model = btn.dataset.remove;
      const ok = await showConfirm(`Delete the model "${model}" from disk? You can re-download it later.`);
      if (!ok) return;
      btn.disabled = true;
      try {
        const r = await fetch(`${serverUrl}/api/setup/ollama/remove?model=${encodeURIComponent(model)}`, { method: 'POST' });
        const d = await r.json().catch(() => ({}));
        if (!r.ok || d.ok === false) throw new Error(d.error || d.stderr || `Server returned ${r.status}`);
        showToast(`🗑 Removed ${model}`, 'success');
        renderModelCatalog();
        loadAiModels();
      } catch (e) {
        showToast(`Could not remove model: ${e.message || e}`, 'error');
        btn.disabled = false;
      }
    });
  });
}

// Start a streaming model download with a live progress bar + cancel button.
// Cancel aborts the pull and asks the server to remove the partial model.
const _pullPollTimers = {};
async function startModelPull(model, btn) {
  const card = btn.closest('.model-card');
  const actions = card ? card.querySelector('.model-card-actions') : null;
  try {
    const r = await fetch(`${serverUrl}/api/setup/ollama/pull-start?model=${encodeURIComponent(model)}`, { method: 'POST' });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `Server returned ${r.status}`);
  } catch (e) {
    showToast(`Couldn't start download: ${e.message || e}`, 'error');
    return;
  }
  if (actions) {
    actions.innerHTML = `
      <div class="pull-progress">
        <div class="pull-bar"><div class="pull-fill" style="width:0%"></div></div>
        <div class="pull-row"><span class="pull-pct muted small">Starting…</span>
          <button class="btn btn-small btn-ghost pull-cancel">Cancel</button></div>
      </div>`;
    actions.querySelector('.pull-cancel')?.addEventListener('click', async () => {
      try { await fetch(`${serverUrl}/api/setup/ollama/pull-cancel?model=${encodeURIComponent(model)}`, { method: 'POST' }); } catch (_) {}
      const pct = actions.querySelector('.pull-pct');
      if (pct) pct.textContent = 'Cancelling…';
    });
  }
  // Poll progress.
  if (_pullPollTimers[model]) clearInterval(_pullPollTimers[model]);
  _pullPollTimers[model] = setInterval(async () => {
    let p;
    try {
      const res = await fetch(`${serverUrl}/api/setup/ollama/pull-progress?model=${encodeURIComponent(model)}`);
      p = await res.json();
    } catch (_) { return; }
    const fill = actions && actions.querySelector('.pull-fill');
    const pct = actions && actions.querySelector('.pull-pct');
    if (fill && typeof p.percent === 'number') fill.style.width = `${p.percent}%`;
    if (pct) pct.textContent = p.state === 'downloading'
      ? `${p.status || 'downloading'} · ${Math.round(p.percent || 0)}%`
      : (p.status || p.state || '');
    if (p.done || ['success', 'cancelled', 'error', 'idle'].includes(p.state)) {
      clearInterval(_pullPollTimers[model]);
      delete _pullPollTimers[model];
      if (p.state === 'success') showToast(`✅ ${model} downloaded`, 'success');
      else if (p.state === 'cancelled') showToast(`Cancelled ${model} (partial download removed)`, 'info');
      else if (p.state === 'error') showToast(`Download failed: ${p.error || 'unknown error'}`, 'error');
      renderModelCatalog();
      loadAiModels();
    }
  }, 1000);
}

// Clear the transcript cache (frees space; next run re-transcribes).
function bindClearCache() {
  const btn = document.getElementById('clear-cache-btn');
  if (!btn || btn.dataset.bound) return;
  btn.dataset.bound = '1';
  btn.addEventListener('click', async () => {
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = '⏳ Clearing…';
    try {
      const res = await fetch(`${serverUrl}/api/setup/clear-cache`, { method: 'POST' });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(d.detail || `Server returned ${res.status}`);
      showToast(`🧹 Cleared ${d.cleared || 0} cached transcript(s) · ${d.mb_freed || 0} MB freed`, 'success');
    } catch (e) {
      showToast(`Could not clear cache: ${e.message}`, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  });
}

// One-click: install every missing dependency, one at a time, with a real
// progress bar. Installing sequentially via the per-component endpoint lets us
// show "Installing X (2/5)" and advance the bar as each finishes — no server
// streaming needed.
function bindInstallAll() {
  const btn = document.getElementById('install-all-btn');
  if (!btn || btn.dataset.bound) return;
  btn.dataset.bound = '1';
  btn.addEventListener('click', async () => {
    const statusEl = document.getElementById('install-all-status');
    const progWrap = document.getElementById('install-all-progress');
    const bar = progWrap ? progWrap.querySelector('.progress-bar') : null;
    const fill = document.getElementById('install-all-fill');
    const original = btn.textContent;

    const setProgress = (done, total, label) => {
      const pct = total ? Math.round((done / total) * 100) : 0;
      if (fill) fill.style.width = `${pct}%`;
      if (bar) bar.setAttribute('aria-valuenow', String(pct));
      if (statusEl) statusEl.textContent = label;
    };

    btn.disabled = true;
    btn.textContent = '⏳ Checking what is missing…';
    if (progWrap) progWrap.classList.remove('hidden');
    setProgress(0, 1, 'Checking what needs installing…');

    try {
      const missRes = await fetch(`${serverUrl}/api/setup/missing`);
      const missData = await missRes.json().catch(() => ({}));
      if (!missRes.ok) throw new Error(missData.detail || `Server returned ${missRes.status}`);
      const missing = missData.missing || [];

      if (!missing.length) {
        setProgress(1, 1, '');
        showAlert('✅ Everything recommended is already installed — nothing to do.');
        return;
      }

      const total = missing.length;
      const installed = [];
      const failed = [];
      for (let i = 0; i < total; i++) {
        const key = missing[i];
        btn.textContent = `⏳ Installing ${key} (${i + 1}/${total})…`;
        setProgress(i, total, `Installing ${key} (${i + 1} of ${total})… this can take a few minutes.`);
        try {
          const res = await runDependencyInstall(key);
          const d = await res.json().catch(() => ({}));
          if (res.ok && !d.error && (d.returncode === 0 || d.returncode === undefined)) installed.push(key);
          else failed.push(key);
        } catch (_) {
          failed.push(key);
        }
        setProgress(i + 1, total, `Finished ${i + 1} of ${total}.`);
      }

      showAlert(`Install All finished.\n\n✅ Installed: ${installed.join(', ') || 'none'}\n❌ Failed: ${failed.join(', ') || 'none'}`);
      loadSetupPanel();
    } catch (e) {
      showAlert(`Install All failed: ${e.message}`);
    } finally {
      btn.disabled = false;
      btn.textContent = original;
      if (statusEl) statusEl.textContent = '';
      if (progWrap) progWrap.classList.add('hidden');
      if (fill) fill.style.width = '0%';
    }
  });
}

// Single source of truth for the Whisper model size.
//
// There are two selects for the same setting: #whisper-model (Clipping Options,
// the one buildProcessPayload actually sends) and #ai-whisper-model (Setup
// panel). Historically only the Setup select was restored from localStorage,
// and the sync ran one way (Setup → clip) on change only — so on startup the
// clip select stayed at the HTML default `base` and every run used `base` no
// matter what the UI showed, until the user re-selected. This wires both selects
// to one saved value with two-way mirroring so they can never drift apart.
//
// Idempotent: safe to call multiple times (startup + each Setup panel load).
// The actual logic lives in the dependency-injected, unit-tested module
// src/whisper-sync.js (KlipzyWhisperSync); this just supplies the real document,
// localStorage and toast.
function initWhisperModelSync() {
  if (typeof KlipzyWhisperSync === 'undefined') return;  // module failed to load
  KlipzyWhisperSync.syncWhisperModelSelects({
    doc: document,
    storage: localStorage,
    announce: (message) => showToast(message, 'success'),
  });
}

// Populate + wire the "Change AI models" selectors.
async function loadAiModels(statusData) {
  // Keep both Whisper selects and the saved preference in lockstep.
  initWhisperModelSync();

  // Ollama model: list what's installed locally, let the user pick the active one.
  const ollamaSel = document.getElementById('ai-ollama-model');
  if (!ollamaSel) return;
  try {
    const revision = localModelRevision;
    const res = await fetch(`${serverUrl}/api/setup/ai-models`);
    if (!res.ok) throw new Error();
    const data = await res.json();
    if (localModelSaving || revision !== localModelRevision) return;
    const models = data.installed_ollama_models || [];
    const active = data.ollama;
    selectedOllamaModel = active || "";
    syncSidebarModels(models);
    renderRecommendations(setupRecommendations);
    if (!models.length) {
      const installed = statusData && statusData.ollama && statusData.ollama.installed;
      ollamaSel.innerHTML = active
        ? `<option value="${escapeHtml(active)}" selected>${escapeHtml(active)} (selected · download needed)</option>`
        : `<option value="">${installed ? 'No models pulled yet — use “Pull model”' : 'Ollama not installed'}</option>`;
      ollamaSel.disabled = true;
    } else {
      ollamaSel.disabled = false;
      ollamaSel.innerHTML = models.map((m) =>
        `<option value="${escapeHtml(m)}"${m === active ? ' selected' : ''}>${escapeHtml(m)}</option>`
      ).join('');
      if (active && !models.includes(active)) {
        ollamaSel.insertAdjacentHTML('afterbegin', `<option value="${escapeHtml(active)}" selected>${escapeHtml(active)} (selected · download needed)</option>`);
      }
    }
    if (!ollamaSel.dataset.bound) {
      ollamaSel.dataset.bound = '1';
      ollamaSel.addEventListener('change', async () => {
        if (!ollamaSel.value) return;
        chooseLocalModel(ollamaSel.value).catch(error => showToast(error.message,'error'));
      });
    }
  } catch (_) {
    ollamaSel.innerHTML = '<option value="">Could not reach Ollama</option>';
    ollamaSel.disabled = true;
  }
}

let supportLinks = {};

function loadSupportLinks() {
  fetch(`${serverUrl}/api/setup/support`)
    .then((r) => r.json())
    .then((s) => {
      supportLinks = s || {};
      const map = { 'support-paypal': s.paypal, 'support-beacons': s.beacons, 'support-star': s.star, 'support-issues': s.issues };
      Object.entries(map).forEach(([id, url]) => {
        const el = document.getElementById(id);
        if (el && url) el.setAttribute('href', url);
      });
    })
    .catch(() => {});
}

// Single entry point for "Settings" — the Setup view already consolidates
// hardware, dependencies, model management and support, so route there.
function openSettings() {
  const setupNav = document.querySelector('.nav-item[data-view="setup"]');
  if (setupNav) setupNav.click();   // reuse the normal nav switch

  document.getElementById('view-setup')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// Support TechFreq: open the best available external link (my links hub, else
// donate). If we don't have the URLs yet, fall back to the Setup support card.
function openSupport() {
  const url = supportLinks.beacons || supportLinks.paypal || supportLinks.star;
  if (url && window.open) {
    window.open(url, '_blank', 'noopener');
    return;
  }
  // Links not loaded yet — make sure they get fetched, then land on Setup.
  openSettings();
  loadSupportLinks();
  showToast('💙 Thanks for supporting TechFreq! Support links are in Setup.', 'info');
}

function renderHardware(data) {
  const gpu = data.gpu || {};
  const cpu = data.cpu || {};
  const torch = data.torch || {};
  const py = data.python || {};
  const gpuName = gpu.name ? `${gpu.name}${gpu.vram_gb ? ` (${gpu.vram_gb} GB VRAM)` : ''}` : 'No discrete GPU detected';
  const accel = torch.cuda ? 'CUDA ✓' : (torch.mps ? 'Apple Silicon (MPS) ✓' : 'CPU mode — supported');
  const accelHint = torch.cuda ? '(RTX-class GPU — full GPU speed)'
    : (torch.mps ? '(Apple Silicon Metal)'
    : (gpu.name ? `Detected ${gpu.name}. CPU processing is available; compatible GPU acceleration is optional (see Setup).` : 'CPU processing is supported. GPU acceleration is optional.'));
  document.getElementById('setup-hardware').innerHTML = `
    <div class="hw-grid">
      <div class="hw-item"><span class="hw-label">OS</span><strong>${escapeHtml(data.os || 'unknown')}</strong></div>
      <div class="hw-item"><span class="hw-label">Python</span><strong>${escapeHtml(py.version || '?')}</strong></div>
      <div class="hw-item"><span class="hw-label">CPU</span><strong>${escapeHtml(String(cpu.cores || '?'))} cores</strong></div>
      <div class="hw-item"><span class="hw-label">RAM</span><strong>${escapeHtml(String(cpu.ram_gb || '?'))} GB</strong></div>
      <div class="hw-item"><span class="hw-label">GPU</span><strong>${escapeHtml(gpuName)}</strong></div>
      <div class="hw-item"><span class="hw-label">Acceleration</span><strong>${escapeHtml(accel)}</strong></div>
    </div>
    <div class="hw-note">${escapeHtml(accelHint)}</div>`;
}

// Hardware-aware GPU-acceleration card: detects a dormant GPU (a CUDA/Metal-
// capable machine running CPU-only PyTorch) and guides the user to enable it,
// greys out when already active, and always shows copyable install/uninstall
// commands so it works on any machine a fork/copy lands on.
async function loadGpuAcceleration() {
  const el = document.getElementById('gpu-accel');
  if (!el) return;
  let g;
  try {
    const res = await fetch(`${serverUrl}/api/setup/gpu`);
    g = await res.json();
  } catch (_) {
    el.innerHTML = '<span class="muted">⚠️ Could not read GPU status.</span>';
    return;
  }

  const needsRestart = installJobs.some(j => j.restart_required && ["gpu", "pytorch"].includes(j.component));
  const dotClass = g.state === 'active' ? 'ok' : (g.state === 'cpu_only' ? '' : 'warn');
  const cmdBlock = (label, cmd) => (cmd ? `
    <div class="gpu-cmd">
      <span class="gpu-cmd-label">${escapeHtml(label)}</span>
      <code class="gpu-cmd-text">${escapeHtml(cmd)}</code>
      <button class="btn btn-small btn-ghost gpu-copy" data-cmd="${escapeHtml(cmd)}" title="Copy command">📋</button>
    </div>` : '');

  let actions = '';
  let guide = '';
  if (needsRestart) {
    actions = '<button class="btn btn-primary btn-small" id="gpu-restart-btn">Restart Klipzy to activate</button>';
    guide = '<p>Installation verified in a fresh Python process. Restart to replace the libraries loaded by this session.</p>';
  } else if (g.state === 'active') {
    actions = `<button class="btn btn-small" disabled>✓ Acceleration active (${escapeHtml(g.engine || 'GPU')})</button>
               <button class="btn btn-small btn-ghost" id="gpu-revert-btn" title="Remove PyTorch from this environment">Uninstall PyTorch…</button>`;
  } else if (g.state === 'dormant' && g.cuda_build) {
    actions = '<span class="muted">CUDA build installed · GPU initialization needs attention</span>';
    guide = '<p>CUDA-enabled PyTorch is installed, but this process cannot use the GPU. Restart Klipzy and check the NVIDIA driver if this persists. Downloading the same build again will not enable a driver that is unavailable.</p>';
  } else if (g.state === 'dormant' || g.state === 'not_installed') {
    const verb = g.state === 'not_installed' ? 'Install PyTorch' : `Enable ${g.plan_label || 'GPU acceleration'}`;
    actions = `<button class="btn btn-primary btn-small" id="gpu-enable-btn">⚡ ${escapeHtml(verb)}</button>`;
    guide = `<ol class="gpu-guide">
      <li>Click <strong>${escapeHtml(verb)}</strong> (or copy the command below and run it yourself), then wait for the download (~2.5GB).</li>
      <li>When it finishes, <strong>restart the app</strong> so it loads the new build.</li>
      <li>Return here — this card should then read <strong>"Acceleration active"</strong>.</li>
    </ol>`;
  }

  const expWarn = (g.experimental && (g.state === 'dormant' || g.state === 'not_installed'))
    ? '<p class="gpu-exp small">⚠️ Community-supported path — AMD/Intel acceleration on this OS isn\'t officially tested here. It may not speed up every feature, and CPU stays a reliable fallback. If it works great (or not at all), please <a href="#" class="gpu-feedback">report feedback</a> so we can improve it for your hardware.</p>'
    : '';

  // Transcription accelerator (MLX on Apple / faster-whisper elsewhere).
  const t = g.transcription || {};
  const transLine = t.active ? `Active: ${escapeHtml(t.active)}` : 'Not detected';
  const transBlock = `
    <div class="gpu-trans">
      <span class="gpu-cmd-label">Transcription</span>
      <span class="gpu-trans-info muted small">${transLine}${t.recommend ? ' — ' + escapeHtml(t.recommend) : ' (accelerated)'}</span>
      ${t.command ? `<button class="btn btn-small btn-ghost gpu-copy" data-cmd="${escapeHtml(t.command)}" title="Copy install command">📋 Copy</button>` : ''}
    </div>`;

  el.innerHTML = `
    <div class="gpu-status">
      <span class="dot ${dotClass}"></span>
      <div>
        <div class="gpu-headline">${escapeHtml(g.headline || '')}</div>
        <div class="gpu-detail muted small">${escapeHtml(g.detail || '')}</div>
      </div>
    </div>
    ${guide}${expWarn}
    <div class="gpu-actions">${actions}</div>
    <div class="gpu-cmds">
      ${g.state !== 'cpu_only' ? cmdBlock('Enable (' + (g.plan_label || 'accelerated') + ')', g.install_command) : ''}
      ${cmdBlock('Uninstall PyTorch', g.uninstall_command)}
      ${g.state === 'active' ? cmdBlock('Revert to CPU build', g.cpu_command) : ''}
    </div>
    ${transBlock}
    <p class="muted small">These commands are generated for this computer and target Klipzy’s Python environment. On Windows, paste into PowerShell. Restart after changing PyTorch.</p>`;

  el.querySelectorAll('.gpu-copy').forEach((b) => b.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(b.dataset.cmd); showToast('Command copied', 'success'); }
    catch (_) { showToast('Copy failed — select the text manually', 'error'); }
  }));

  // "Report feedback" — open the project's issues/links (resolved at click time
  // so it works even if the support links loaded after this card rendered).
  el.querySelector('.gpu-feedback')?.addEventListener('click', (e) => {
    e.preventDefault();
    const url = supportLinks.issues || supportLinks.github || supportLinks.beacons;
    if (url && window.open) { window.open(url, '_blank', 'noopener'); }
    else { openSettings(); showToast('Feedback & issue links are in the Support section.', 'info'); }
  });

  document.getElementById('gpu-restart-btn')?.addEventListener('click', restartAfterInstall);
  renderInstallActivity();
  if (g.python_executable) {
    const runtime = document.createElement('p');
    runtime.className = 'muted small';
    runtime.style.overflowWrap = 'anywhere';
    runtime.textContent = `Engine Python: ${g.python_executable} · PyTorch ${g.torch_version || 'not loaded'}${g.cuda_build ? ' · CUDA build ' + g.cuda_build : ''}`;
    el.append(runtime);
  }
  const enableBtn = document.getElementById('gpu-enable-btn');
  if (enableBtn) enableBtn.addEventListener('click', async () => {
    const ok = await showConfirm(
      'Check and install the recommended PyTorch build? Klipzy will verify the environment first and skip downloading if that build is already installed. A new installation can be several GB and requires a restart.',
      'Enable GPU acceleration');
    if (!ok) return;
    const orig = enableBtn.textContent;
    enableBtn.disabled = true;
    enableBtn.textContent = '⏳ Installing… (~2.5GB, several min)';
    showToast('Installing the GPU build of PyTorch — large download, please wait', 'info');
    try {
      const r = await runDependencyInstall('gpu');
      const d = await r.json();
      if (d.ok) showToast(d.already_installed ? 'PyTorch build verified — no download needed. Restart to refresh the active engine.' : 'GPU build verified — restart to activate it', 'success');
      else throw new Error(d.error || d.stderr || 'install failed');
    } catch (e) {
      showToast(`Install failed: ${e.message || e}. You can copy the command and run it manually.`, 'error');
    } finally {
      enableBtn.disabled = false;
      enableBtn.textContent = orig;
      await loadSetupPanel();
      renderInstallActivity();
    }
  });

  const revertBtn = document.getElementById('gpu-revert-btn');
  if (revertBtn) revertBtn.addEventListener('click', async () => {
    const ok = await showConfirm(
      'Remove the current PyTorch build? Face-tracking will be unavailable until you reinstall PyTorch (the CPU command is shown for that).',
      'Uninstall PyTorch');
    if (!ok) return;
    const orig = revertBtn.textContent;
    revertBtn.disabled = true;
    revertBtn.textContent = '⏳ Removing…';
    try {
      const r = await fetch(`${serverUrl}/api/setup/uninstall`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ component: 'pytorch' }),
      });
      const d = await r.json();
      if (d.ok) showToast('PyTorch removed. Reinstall with the CPU or GPU command, then restart.', 'success');
      else throw new Error(d.stderr || d.error || 'uninstall failed');
    } catch (e) {
      showToast(`Uninstall failed: ${e.message || e}`, 'error');
    } finally {
      revertBtn.disabled = false;
      revertBtn.textContent = orig;
      await loadGpuAcceleration();
      renderInstallActivity();
    }
  });
}

function renderRecommendations(recs) {
  if (!recs) return;
  const items = [
    { key: 'whisper', name: '🎤 Whisper (transcription)', ...recs.whisper },
    { key: 'yolo', name: '👁️ YOLO (face tracking)', ...recs.yolo },
    { key: 'ollama', name: '🤖 Ollama (AI edit chat)', ...recs.ollama },
  ];
  // Which choice is currently selected per kind, so we can highlight it:
  //  - whisper reflects the actual dropdown value used for the next job
  //  - others highlight the "Best fit" (first) recommendation for this hardware
  const whisperSel = document.getElementById('whisper-model')?.value || '';
  document.getElementById('setup-recommendations').innerHTML = items.map((it) => `
    <div class="rec-card">
      <div class="rec-name">${escapeHtml(it.name)}</div>
      <div class="rec-model">${escapeHtml(it.model)}</div>
      <div class="rec-meta">
        ${it.engine ? `<span class="rec-chip">⚡ ${escapeHtml(it.engine)}</span>` : ''}
        ${it.realtime_factor ? `<span class="rec-chip">${escapeHtml(it.realtime_factor)}</span>` : ''}
      </div>
      <div class="rec-note muted">${escapeHtml(it.note || '')}</div>
      ${it.key === 'yolo' ? '<p class="muted small">Face tracking currently uses yolov8n.pt automatically. Alternate YOLO model selection is not supported yet.</p>' : it.choices ? `<div class="rec-choices">${it.choices.map((choice, i) => {
        const selected = it.key === 'whisper' ? (choice.model === whisperSel) : (it.key === 'ollama' && choice.model === selectedOllamaModel);
        return `<button class="btn btn-small rec-choice${selected ? ' is-selected' : ''}" aria-busy="${it.key === 'ollama' && localModelSaving}" ${it.key === 'ollama' && localModelSaving ? 'disabled' : ''} data-model-kind="${it.key}" data-model="${escapeHtml(choice.model)}">
          ${escapeHtml(choice.tier)}: ${escapeHtml(choice.model)}
        </button>`;
      }).join('')}</div>` : ''}
    </div>`).join('');
  document.querySelectorAll('.rec-choice').forEach((button) => {
    button.addEventListener('click', async () => {
      if (button.dataset.modelKind === 'whisper') {
        const select = document.getElementById('whisper-model');
        if (select) {
          select.value = button.dataset.model;
          // Funnel through the shared sync: persists + mirrors #ai-whisper-model.
          select.dispatchEvent(new Event('change', { bubbles: true }));
        }
        // Move the highlight to the clicked whisper choice.
        button.parentElement.querySelectorAll('.rec-choice').forEach((b) => b.classList.remove('is-selected'));
        button.classList.add('is-selected');
        showToast(`Whisper model set to ${button.dataset.model}`, 'success');
      } else {
        chooseLocalModel(button.dataset.model).catch(error => showToast(error.message,'error'));
      }
    });
  });
}

const DEP_DEF = {
  ffmpeg: {
    label: 'FFmpeg',
    desc: 'Required for video trimming, cropping, and audio extraction',
    check: (d) => d.ffmpeg && d.ffmpeg.ffmpeg,
    statusText: (d) => (d.ffmpeg && d.ffmpeg.ffmpeg ? 'Installed' : 'Missing'),
  },
  whisper: {
    label: 'OpenAI Whisper',
    desc: 'Speech-to-text engine — the universal transcription fallback',
    check: (d) => d.whisper && d.whisper.installed,
    statusText: (d) => (d.whisper && d.whisper.installed ? 'Installed' : 'Missing'),
  },
  'faster-whisper': {
    label: 'Faster-Whisper',
    desc: 'CTranslate2 backend — 3-5x faster transcription on CPU/GPU',
    check: (d) => d.faster_whisper && d.faster_whisper.installed,
    statusText: (d) => (d.faster_whisper && d.faster_whisper.installed ? 'Installed' : 'Missing'),
  },
  'mlx-whisper': {
    label: 'MLX-Whisper (Apple Silicon)',
    desc: 'Native MLX acceleration — fastest transcription on M-series Macs',
    check: (d) => d.mlx_whisper && d.mlx_whisper.installed,
    statusText: (d) => (d.mlx_whisper && d.mlx_whisper.installed ? 'Installed' : 'Missing'),
  },
  librosa: {
    label: 'librosa',
    desc: 'Audio-energy analysis for excitement/loudness highlight detection',
    check: (d) => d.librosa && d.librosa.installed,
    statusText: (d) => (d.librosa && d.librosa.installed ? 'Installed' : 'Missing'),
  },
  ollama: {
    label: 'Ollama (Required)',
    desc: 'Install Ollama once; Klipzy connects to its local service automatically when AI analysis or chat is enabled.',
    check: (d) => d.ollama && d.ollama.installed,
    statusText: (d) => {
      if (!d.ollama || !d.ollama.installed) return 'Missing - required';
      return d.ollama.running ? '✅ Running' : 'Installed (offline · launch app)';
    },
  },
  lmstudio: {
    label: 'LM Studio (Optional)',
    desc: 'Alternative local LLM engine (OpenAI-compatible server on port 1234)',
    check: (d) => d.lmstudio && d.lmstudio.installed,
    statusText: (d) => {
      if (!d.lmstudio || !d.lmstudio.installed) return 'Not found';
      return d.lmstudio.running ? '✅ Running' : 'Installed (offline · start server)';
    },
  },
  pytorch: {
    label: 'PyTorch',
    desc: 'ML backend for GPU-accelerated Whisper & YOLO',
    check: (d) => d.torch && d.torch.installed,
    statusText: (d) => (d.torch && d.torch.installed ? `v${d.torch.version || 'installed'}` : 'Missing'),
  },
  ultralytics: {
    label: 'Ultralytics (YOLO)',
    desc: 'Face tracking for smart auto-reframing',
    check: (d) => !!d.ultralytics && d.ultralytics.installed,
    statusText: (d) => (d.ultralytics && d.ultralytics.installed ? 'Installed' : 'Missing'),
  },
};
function renderDeps(data) {
  const cmds = data.install_commands || {};
  const uninstallCmds = data.uninstall_commands || {};
  // Show every component the server knows how to install, plus the always-relevant
  // core ones. mlx-whisper only appears when the server offers it (Apple Silicon).
  const base = ['ollama', 'lmstudio', 'ffmpeg', 'pytorch', 'whisper', 'faster-whisper', 'librosa', 'ultralytics'];
  if (cmds['mlx-whisper'] || (data.mlx_whisper && data.mlx_whisper.installed)) base.push('mlx-whisper');
  const renderable = base.filter((key) => DEP_DEF[key]);
  const rows = renderable.map((key) => {
    const def = DEP_DEF[key];
    const pending = installJobs.some(job => job.restart_required && (job.component === key || key === 'pytorch' && job.component === 'gpu'));
    const ok = pending || def.check(data);
    const cmdAvailable = !!cmds[key];
    const canUninstall = ok && !pending && !!uninstallCmds[key];
    const statusMark = ok ? '✅' : '❌';
    const tag = pending ? 'Verified · Restart required' : def.statusText ? def.statusText(data) : (ok ? 'Installed' : 'Missing');
    return `
      <div class="dep-row ${ok ? 'ok' : 'missing'}" data-key="${key}">
        <div class="dep-info">
          <span class="dep-status">${statusMark}</span>
          <div>
            <strong>${escapeHtml(def.label)}</strong>
            <span class="muted small">${escapeHtml(def.desc)}</span>
          </div>
        </div>
        <div class="dep-actions">
          ${ok
            ? `<span class="dep-installed">${escapeHtml(tag)}</span>`
              + (canUninstall ? ` <button class="btn btn-small btn-ghost" data-uninstall="${escapeHtml(key)}" title="Remove this package">🗑 Uninstall</button>` : '')
            : (cmdAvailable
              ? `<button class="btn btn-small btn-primary" data-install="${escapeHtml(key)}">⬇️ Install</button>`
              : '<span class="muted small">Manual install needed</span>')}
        </div>
      </div>`;
  }).join('');
  document.getElementById('setup-deps').innerHTML = rows;

  renderInstallActivity();

  // Uninstall buttons (pip packages only)
  document.querySelectorAll('[data-uninstall]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const key = btn.dataset.uninstall;
      const confirmed = await showConfirm(`Uninstall ${key}? You can reinstall it later from this panel.`);
      if (!confirmed) return;
      btn.disabled = true;
      btn.textContent = '⏳ Removing…';
      try {
        const res = await fetch(`${serverUrl}/api/setup/uninstall`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ component: key }),
        });
        const d = await res.json().catch(() => ({}));
        if (!res.ok || d.error) throw new Error(d.error || d.detail || `Server returned ${res.status}`);
        showToast(d.ok ? `${key} uninstalled` : `${key}: exit ${d.returncode}`, d.ok ? 'success' : 'error');
        loadSetupPanel();
      } catch (e) {
        showAlert(`Uninstall failed: ${e.message}`);
        btn.disabled = false;
        btn.textContent = '🗑 Uninstall';
      }
    });
  });

  document.querySelectorAll('[data-install]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const key = btn.dataset.install;
      btn.disabled = true;
      btn.textContent = '⏳ Installing... (may take a while)';
      try {
        const res = await runDependencyInstall(key);
        const data = await res.json();
        if (data.error) {
          showAlert(`Install failed: ${data.error}`);
          btn.disabled = false;
          btn.textContent = '⬇️ Install';
        } else if (data.returncode === 0) {
          showAlert(`✅ ${key} verified. See Install activity for restart status.\n\nCommand: ${data.command}`);
          loadSetupPanel();
        } else {
          showAlert(`Install may have failed (code ${data.returncode}).\n\nCommand: ${data.command}\n\n${data.stderr || data.stdout || ''}`);
          btn.disabled = false;
          btn.textContent = '⬇️ Install';
        }
      } catch (e) {
        showAlert(`Install error: ${e.message}`);
        btn.disabled = false;
        btn.textContent = '⬇️ Install';
      }
    });
  });

  // One-click Ollama model pull (auto-downloads a GGUF model in the app)
  if (data.ollama && data.ollama.installed) {
    const row = document.querySelector('.dep-row[data-key="ollama"]');
    if (row) row.querySelector('.dep-actions').insertAdjacentHTML('beforeend', '<button class="btn btn-small" id="ollama-pull-btn">🔄 Pull model</button>');
  }
  const pullBtn = document.getElementById('ollama-pull-btn');
  if (pullBtn) pullBtn.addEventListener('click', async () => {
    pullBtn.disabled = true;
    pullBtn.textContent = '⏳ Downloading model...';
    try {
      const res = await fetch(`${serverUrl}/api/setup/ollama/pull?model=gemma2:2b`, { method: 'POST' });
      const d = await res.json();
      if (d.error) showAlert(`Pull failed: ${d.error}`);
      else if (d.returncode === 0) showAlert('✅ gemma2:2b downloaded and ready!');
      else showAlert(`Pull may have failed (code ${d.returncode}).\n\n${d.stderr || d.stdout || ''}`);
    } catch (e) {
      showAlert('Pull error: ' + e.message);
    }
    pullBtn.disabled = false;
    pullBtn.textContent = '🔄 Pull model';
  });
}

// Render time estimator
function bindEstimator() {
  const btn = document.getElementById('estimate-btn');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const duration = parseFloat(document.getElementById('estimate-duration').value) || 30;
    const layout = document.getElementById('estimate-layout').value;
    const resultEl = document.getElementById('estimate-result');
    resultEl.classList.remove('hidden');
    try {
      const res = await fetch(`${serverUrl}/api/setup/estimate?duration=${duration}&layout=${layout}`);
      const e = await res.json();
      resultEl.innerHTML =
        `<strong>${escapeHtml(e.estimated_text)}</strong> &nbsp;·&nbsp; <span class="muted">${escapeHtml(e.engine)} — ~${escapeHtml(e.realtime_factor)} realtime</span>`;
    } catch (err) {
      resultEl.innerHTML = '<span class="muted">⚠️ Could not estimate.</span>';
    }
  });
}

