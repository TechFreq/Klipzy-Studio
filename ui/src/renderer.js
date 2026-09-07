// Renderer - UI logic for Klipzy Studio (client)
// Talks to the local Python FastAPI server

let serverUrl = 'http://127.0.0.1:8765';
let apiToken = '';

// The backend requires a shared secret on every call (see server/auth.py), which
// is what stops a random web page from driving this API. Rather than touch all
// ~30 fetch call sites, wrap fetch once here so the header is attached to any
// request aimed at our own server - including any added later.
const nativeFetch = window.fetch.bind(window);
window.fetch = function klipzyFetch(resource, options) {
  const url = typeof resource === 'string' ? resource : (resource && resource.url) || '';
  if (!apiToken || !url.startsWith(serverUrl)) {
    return nativeFetch(resource, options);
  }
  const opts = { ...(options || {}) };
  const headers = new Headers(opts.headers || {});
  if (!headers.has('X-Klipzy-Token')) headers.set('X-Klipzy-Token', apiToken);
  opts.headers = headers;
  return nativeFetch(resource, opts);
};
let selectedVideo = null;
let pollTimer = null;
let generatedClips = [];
let currentEditingClip = null;
// Rotating intro-hook suggestions for the currently-edited clip.
let hookCandidates = [];
let hookCandidateIdx = -1;
let currentProjectId = null;
let currentWizardStep = 1;
const PROJECTS_KEY = 'klipzy.projects.v1';

function setWizardStep(stepNum) {
  currentWizardStep = stepNum;
  for (let i = 1; i <= 4; i++) {
    const stepBtn = document.getElementById(`wizard-step-btn-${i}`);
    const panel = document.getElementById(`step-panel-${i}`);
    if (stepBtn) {
      stepBtn.classList.toggle('active', i === stepNum);
      stepBtn.classList.toggle('completed', i < stepNum);
      stepBtn.setAttribute('aria-selected', i === stepNum ? 'true' : 'false');
    }
    if (panel) {
      panel.classList.toggle('active', i === stepNum);
    }
  }
  if (stepNum === 2) {
    refreshPortraitCaptionPreview();
  }
  // The step-3 spinner should only animate while a job is actually running.
  // Landing on step 3 with no active job (e.g. navigating back after a finished
  // run) must not show a perpetual spinner.
  if (stepNum === 3 && !currentActiveJobId) {
    setProcessingActive(false);
  }
}

// Show/hide the processing spinner so it never spins when idle. When inactive,
// the step-3 header shows a "done" state instead of an endless spinner.
function setProcessingActive(active) {
  const spinner = document.getElementById('processing-spinner');
  if (spinner) spinner.classList.toggle('hidden', !active);
  const title = document.getElementById('processing-video-title');
  if (title && !active) {
    title.textContent = 'Processing complete. Head to Generated Clips, or start a new project.';
  }
}

function resetWizardToStep1() {
  selectedVideo = null;
  generatedClips = [];
  currentProjectId = null;
  currentEditingClip = null;
  // A running job would otherwise keep polling after the user resets.
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  currentActiveJobId = null;
  lastLoggedStep = '';
  const setText = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
  const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
  const hide = (id) => document.getElementById(id)?.classList.add('hidden');
  setText('file-name', '');
  hide('file-info');
  hide('project-bar');
  setVal('project-name', '');
  hide('trim-panel');
  const grid = document.getElementById('clips-grid');
  if (grid) grid.innerHTML = '';
  const nextBtn = document.getElementById('step1-next-btn');
  if (nextBtn) nextBtn.disabled = true;
  const startBtn = document.getElementById('start-clipping');
  if (startBtn) startBtn.disabled = true;
  updateCurrentProjectUI('');  // no active project after a reset
  renderProjectGrid();         // fresh wizard → recents may show again
  // The wizard lives inside #view-clipper. "New Project" can be triggered from
  // the always-visible sidebar while the user is on another view (Setup/Chat),
  // so switch back to the clipper view — otherwise resetting the wizard step
  // happens off-screen and the click appears to do nothing.
  const clipperView = document.getElementById('view-clipper');
  if (clipperView && !clipperView.classList.contains('active')) {
    document.querySelectorAll('.nav-item').forEach((b) => b.classList.remove('active'));
    document.querySelector('.nav-item[data-view="clipper"]')?.classList.add('active');
    document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
    clipperView.classList.add('active');
  }
  setWizardStep(1);
}

// Manual trim state
let trimState = {
  duration: 0,
  start: 0,
  end: 0,
  camVideo: null,
};

// ------------------------------------------------------------------
// Custom UI Dialogs (Styled Alert & Confirm Modal / Toast)
// ------------------------------------------------------------------
function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `<span>${escapeHtml(message)}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.animation = 'toast-out 0.25s cubic-bezier(0.16, 1, 0.3, 1) forwards';
    setTimeout(() => toast.remove(), 260);
  }, 3200);
}

function showCustomDialog({ title = 'Notification', message = '', isConfirm = false, openPath = null }) {
  return new Promise((resolve) => {
    const modal = document.getElementById('dialog-modal');
    const titleEl = document.getElementById('dialog-title');
    const msgEl = document.getElementById('dialog-message');
    const cancelBtn = document.getElementById('dialog-cancel-btn');
    const confirmBtn = document.getElementById('dialog-confirm-btn');
    const closeBtn = document.getElementById('dialog-close');
    const openBtn = document.getElementById('dialog-open-folder-btn');

    if (!modal) {
      if (isConfirm) resolve(window.confirm(message));
      else { window.alert(message); resolve(true); }
      return;
    }

    titleEl.textContent = title;
    msgEl.textContent = message;

    if (isConfirm) {
      cancelBtn.classList.remove('hidden');
      confirmBtn.textContent = 'Confirm';
    } else {
      cancelBtn.classList.add('hidden');
      confirmBtn.textContent = 'OK';
    }

    // Optional "Open folder" affordance — reveals the exported file/folder in
    // the OS file manager without closing the user out of the dialog.
    const onOpen = () => revealInFolder(openPath);
    if (openBtn) {
      if (openPath) {
        openBtn.classList.remove('hidden');
        openBtn.addEventListener('click', onOpen);
      } else {
        openBtn.classList.add('hidden');
      }
    }

    modal.classList.remove('hidden');

    function cleanup(val) {
      modal.classList.add('hidden');
      confirmBtn.removeEventListener('click', onConfirm);
      cancelBtn.removeEventListener('click', onCancel);
      closeBtn.removeEventListener('click', onCancel);
      if (openBtn) openBtn.removeEventListener('click', onOpen);
      resolve(val);
    }

    function onConfirm() { cleanup(true); }
    function onCancel() { cleanup(false); }

    confirmBtn.addEventListener('click', onConfirm);
    cancelBtn.addEventListener('click', onCancel);
    closeBtn.addEventListener('click', onCancel);
  });
}

function showAlert(message, title = 'Notification', openPath = null) {
  return showCustomDialog({ title, message, isConfirm: false, openPath });
}

function showConfirm(message, title = 'Please Confirm') {
  return showCustomDialog({ title, message, isConfirm: true });
}

// ------------------------------------------------------------------
// Modal accessibility (applies to every .modal generically)
//
// The modals are opened/closed all over the file by toggling .hidden. Rather
// than wire focus handling into each site, observe class changes centrally:
//  - on open: remember what had focus, move focus into the dialog
//  - on close: restore focus to the opener
//  - Escape closes the top-most open modal
//  - Tab is trapped within the open modal
// ------------------------------------------------------------------
let _lastFocusedBeforeModal = null;

function focusableWithin(el) {
  return Array.from(el.querySelectorAll(
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
  )).filter((n) => n.offsetParent !== null);
}

function initModalA11y() {
  document.querySelectorAll('.modal').forEach((modal) => {
    const observer = new MutationObserver(() => {
      const isOpen = !modal.classList.contains('hidden');
      if (isOpen && !modal.dataset.a11yOpen) {
        modal.dataset.a11yOpen = '1';
        _lastFocusedBeforeModal = document.activeElement;
        const focusables = focusableWithin(modal);
        (focusables[0] || modal).focus?.();
      } else if (!isOpen && modal.dataset.a11yOpen) {
        delete modal.dataset.a11yOpen;
        if (_lastFocusedBeforeModal && _lastFocusedBeforeModal.focus) {
          _lastFocusedBeforeModal.focus();
          _lastFocusedBeforeModal = null;
        }
      }
    });
    observer.observe(modal, { attributes: true, attributeFilter: ['class'] });
  });

  document.addEventListener('keydown', (e) => {
    const open = Array.from(document.querySelectorAll('.modal:not(.hidden)')).pop();
    if (!open) return;

    if (e.key === 'Escape') {
      // Prefer a dedicated close button so any per-modal cleanup still runs.
      const closeBtn = open.querySelector('.modal-close, [data-modal-close]');
      if (closeBtn) closeBtn.click();
      else open.classList.add('hidden');
      return;
    }

    if (e.key === 'Tab') {
      const focusables = focusableWithin(open);
      if (!focusables.length) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault(); first.focus();
      }
    }
  });
}

// ------------------------------------------------------------------
// Init
// ------------------------------------------------------------------
async function init() {
  if (window.clipperAPI) {
    serverUrl = await window.clipperAPI.getServerUrl();
    // Must land before any request goes out, or the backend answers 401.
    try {
      apiToken = (await window.clipperAPI.getApiToken()) || '';
    } catch (_) {
      apiToken = '';
    }
    if (window.clipperAPI.onServerStatus) {
      window.clipperAPI.onServerStatus(handleServerStatus);
    }
  }
  bindEvents();
  initModalA11y();
  loadProjectList();
  // These all hit the API, so they wait until a token is in hand.
  checkHealth();
  loadSetupPanel();
  populateCaptionPresets();
  loadOutputFolder();
  startResourcePolling();
}

// Surfaces backend lifecycle events from the Electron main process. The window
// now opens before the server is ready, so the user gets told what is going on
// rather than meeting a UI whose buttons quietly do nothing.
function handleServerStatus({ state, detail }) {
  const el = document.getElementById('health-status');
  const paint = (cls, text) => {
    if (el) el.innerHTML = `<span class="dot ${cls}"></span> ${escapeHtml(text)}`;
  };

  if (state === 'starting') {
    paint('warn', 'Starting AI engine...');
  } else if (state === 'ready') {
    // Re-read the token in case we attached to a pre-existing backend.
    if (window.clipperAPI && window.clipperAPI.getApiToken) {
      window.clipperAPI.getApiToken().then((t) => {
        if (t) apiToken = t;
        checkHealth();
        loadSetupPanel();
        populateCaptionPresets();
        loadOutputFolder();
        startResourcePolling();
      }).catch(() => checkHealth());
    } else {
      checkHealth();
    }
    if (detail) showToast(detail, 'info');
  } else if (state === 'failed' || state === 'crashed') {
    paint('err', state === 'crashed' ? 'AI engine stopped' : 'AI engine unavailable');
    showError(detail || 'The Python backend is not running.');
  }
}

// ------------------------------------------------------------------
// Live resource footer (CPU / RAM / GPU / VRAM / disk). Polls the backend
// every ~2.5s and reassures the user that everything runs on their machine.
// Every field is optional — a chip only renders when its data is present.
// ------------------------------------------------------------------
let resourceTimer = null;

function startResourcePolling() {
  if (resourceTimer) return;
  pollResources();
  resourceTimer = setInterval(pollResources, 2500);
}

async function pollResources() {
  try {
    const res = await fetch(`${serverUrl}/api/setup/resources`);
    if (!res.ok) return;               // server not ready yet; try again next tick
    renderResourceFooter(await res.json());
  } catch (_) {
    /* transient network blip — keep the last render, retry next tick */
  }
}

function _resMeter(pct) {
  const p = Math.max(0, Math.min(100, Math.round(pct || 0)));
  const cls = p >= 90 ? 'hot' : (p >= 70 ? 'warn' : '');
  return `<div class="resource-meter ${cls}"><span style="width:${p}%"></span></div>`;
}

function renderResourceFooter(r) {
  const el = document.getElementById('resource-footer');
  if (!el || !r) return;
  const chips = [];
  if (r.cpu_percent != null) {
    chips.push(`<div class="resource-chip"><span class="resource-label">CPU</span>${_resMeter(r.cpu_percent)}<span class="resource-val">${r.cpu_percent}%</span></div>`);
  }
  if (r.ram_percent != null) {
    const detail = (r.ram_used_gb != null && r.ram_total_gb != null)
      ? `${r.ram_used_gb}/${r.ram_total_gb} GB` : `${r.ram_percent}%`;
    chips.push(`<div class="resource-chip"><span class="resource-label">RAM</span>${_resMeter(r.ram_percent)}<span class="resource-val">${detail}</span></div>`);
  }
  if (r.gpu_percent != null) {
    chips.push(`<div class="resource-chip" title="${escapeHtml(r.gpu_name || 'GPU')}"><span class="resource-label">GPU</span>${_resMeter(r.gpu_percent)}<span class="resource-val">${r.gpu_percent}%</span></div>`);
  }
  if (r.vram_total_gb != null) {
    const used = r.vram_used_gb != null ? r.vram_used_gb : 0;
    const pct = (used / r.vram_total_gb) * 100;
    chips.push(`<div class="resource-chip"><span class="resource-label">VRAM</span>${_resMeter(pct)}<span class="resource-val">${used}/${r.vram_total_gb} GB</span></div>`);
  }
  if (r.disk_free_gb != null) {
    chips.push(`<div class="resource-chip"><span class="resource-label">Disk</span><span class="resource-val">${r.disk_free_gb} GB free</span></div>`);
  }
  if (!chips.length) { el.classList.add('hidden'); return; }
  el.innerHTML = chips.join('');
  el.classList.remove('hidden');
}

// ------------------------------------------------------------------
// Output-mode presets: one pick sets aspect ratio + durations + reframing to
// match a target platform. Purely a convenience over the manual controls; the
// values it writes still flow through buildProcessPayload() unchanged.
// ------------------------------------------------------------------
const OUTPUT_MODE_PRESETS = {
  shorts:   { hint: 'Vertical 9:16, clips up to 60s — TikTok / Reels / Shorts.',
              set: { 'clip-aspect-ratio': '9:16', 'min-duration': 20, 'max-duration': 60 },
              check: { 'vertical-crop': true } },
  x:        { hint: 'Square 1:1, clips up to 140s — X / Twitter.',
              set: { 'clip-aspect-ratio': '1:1', 'min-duration': 30, 'max-duration': 140 },
              check: { 'vertical-crop': true } },
  longform: { hint: 'Landscape 16:9, longer highlights up to 90s — YouTube.',
              set: { 'clip-aspect-ratio': '16:9', 'min-duration': 30, 'max-duration': 90 },
              check: { 'vertical-crop': false } },
  tight:    { hint: 'Vertical 9:16 up to 60s with dead-air removed — punchy edits.',
              set: { 'clip-aspect-ratio': '9:16', 'min-duration': 20, 'max-duration': 60 },
              check: { 'vertical-crop': true, 'remove-silence': true } },
};

function applyOutputPreset() {
  const sel = document.getElementById('output-mode-preset');
  const hintEl = document.getElementById('output-mode-hint');
  if (!sel) return;
  const preset = OUTPUT_MODE_PRESETS[sel.value];
  if (!preset) {
    if (hintEl) hintEl.textContent = 'Or fine-tune everything manually below.';
    return;
  }
  Object.entries(preset.set || {}).forEach(([id, val]) => {
    const node = document.getElementById(id);
    if (node) node.value = val;
  });
  Object.entries(preset.check || {}).forEach(([id, val]) => {
    const node = document.getElementById(id);
    if (node && node.type === 'checkbox') node.checked = !!val;
  });
  if (hintEl) hintEl.textContent = preset.hint;
}

document.getElementById('output-mode-preset')?.addEventListener('change', applyOutputPreset);

// ------------------------------------------------------------------
// Activity log: append a timestamped event to the process feed. Lifecycle
// events (start / done / error) get a colored class so they stand out from the
// per-stage progress lines.
// ------------------------------------------------------------------
function logActivity(text, cls) {
  const logEl = document.getElementById('transcription-log');
  if (!logEl) return;
  // Drop the "Waiting for events…" placeholder on the first real entry.
  const placeholder = logEl.querySelector('p.muted');
  if (placeholder) placeholder.remove();
  const p = document.createElement('p');
  const timeStr = new Date().toLocaleTimeString([], { hour12: false });
  p.className = 'log-entry' + (cls ? ' ' + cls : '');
  p.innerHTML = `<span class="log-time">[${timeStr}]</span> <span>${escapeHtml(text)}</span>`;
  logEl.appendChild(p);
  logEl.scrollTop = logEl.scrollHeight;
}

document.getElementById('clear-activity-log-btn')?.addEventListener('click', () => {
  const logEl = document.getElementById('transcription-log');
  if (logEl) logEl.innerHTML = '<p class="muted">Waiting for events…</p>';
});

// Kept in sync with the server-side default (server/api/server.py) so the
// "Clear / reset" button knows what to restore to when offline.
const DEFAULT_OUTPUT_ROOT = '';

async function loadOutputFolder() {
  const input = document.getElementById('output-folder-input');
  if (!input) return;
  try {
    const res = await fetch(`${serverUrl}/output-folder`);
    if (res.ok) {
      const data = await res.json();
      input.value = data.folder || '';
      const isDefault = !!data.is_default || !data.folder;
      input.dataset.default = data.folder || DEFAULT_OUTPUT_ROOT;
      document.getElementById('output-folder-row')?.classList.toggle('is-default', isDefault);
    }
  } catch (_) { /* server offline: leave path blank; browse will still work */ }
}

async function chooseOutputFolder() {
  let folder = null;
  if (window.clipperAPI && window.clipperAPI.selectOutputFolder) {
    folder = await window.clipperAPI.selectOutputFolder();
  } else {
    // Browser fallback (no Electron): can't pick a folder, prompt for a path.
    folder = window.prompt('Paste the folder path to use as your default export destination:');
  }
  if (!folder) return;
  await saveOutputFolder(folder);
}

async function saveOutputFolder(folder) {
  const input = document.getElementById('output-folder-input');
  const folderValue = (folder || '').trim();
  const body = JSON.stringify({ folder: folderValue });
  try {
    const res = await fetch(`${serverUrl}/output-folder`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || data.message || 'Failed to save output folder');
    if (input) input.value = data.folder || folderValue;
    const isDefault = !data.is_default || data.is_default === true || data.folder === (input?.dataset.default || '');
    document.getElementById('output-folder-row')?.classList.toggle('is-default', !!isDefault);
    showToast('Output folder updated ✅', 'success');
  } catch (err) {
    showToast(`Output folder not saved: ${err.message || err}`, 'error');
  }
}

async function resetOutputFolder() {
  const input = document.getElementById('output-folder-input');
  const defaultPath = input?.dataset.default || DEFAULT_OUTPUT_ROOT;
  await saveOutputFolder(defaultPath);
}

// Caption preset dropdown labels (emoji + display name). Single source of
// truth shared by the main clipper options, the trim panel, and the caption
// editor modal, so new presets only need adding here (backend supplies the
// full list via /caption-presets).
const CAPTION_PRESET_LABELS = {
  viral_yellow:   '✨ Viral Yellow Highlight',
  neon_green:     '🟢 Neon Green Pop',
  bold_white:     '⚪ Bold Clean White',
  cyberpunk_cyan: '💠 Cyberpunk Cyan',
  tiktok_pop:     '🔥 TikTok Pop Red-Yellow',
  fire_red:       '🔴 Fire Red Flame',
  retro_vaporwave:'🌴 Retro Vaporwave',
  mrbeast_impact: '⚡ MrBeast Impact Yellow',
  pastel_pink:    '🌸 Pastel Pink Aesthetic',
  minimalist_dark:'📦 Minimalist Dark Box',
  comic_punch:    '💥 Comic Book Punch',
  golden_hour:    '🌅 Golden Hour Amber',
  electric_purple:'🔮 Electric Purple',
  sunset_orange:  '🌇 Sunset Coral Orange',
  matrix_green:   '💻 Matrix Digital Green',
  deep_blue:      '❄️ Ice Blue Arctic',
  boxed_karaoke:  '🏷️ Boxed Pill Karaoke',
  glitch_shadow:  '👾 Glitch Cyan Shadow',
  elegant_serif:  '🖋️ Editorial Luxury Serif',
  high_contrast:  '👁️ High-Contrast Inverted',
  monochrome_chic:'🎬 Monochrome Chic',
  gaming_rgb:     '🕹️ Gamer RGB Lime/Pink',
};

// Backend fallback order if /caption-presets is unreachable.
const CAPTION_PRESET_IDS = Object.keys(CAPTION_PRESET_LABELS);

async function populateCaptionPresets() {
  // Populate immediately with the static list so the dropdowns are never
  // empty (e.g. while the server fetch is still in flight or offline).
  const renderOptions = (ids) => ids
    .map((id) => `<option value="${escapeHtml(id)}">${escapeHtml(CAPTION_PRESET_LABELS[id] || id)}</option>`)
    .join('');

  const fillSelects = (ids) => {
    const opts = renderOptions(ids);
    ['generated-caption-preset', 'caption-preset'].forEach((selId) => {
      const sel = document.getElementById(selId);
      if (sel && !sel.dataset.populated) {
        const current = sel.value;
        sel.innerHTML = opts;
        if (current && ids.includes(current)) sel.value = current;
        sel.dataset.populated = '1';
      }
    });
    // Options just landed, so refresh the caption editor preview.
    refreshCaptionPreview();
  };

  fillSelects(CAPTION_PRESET_IDS);

  // Then upgrade to the full backend list (with any new presets) if reachable.
  try {
    const res = await fetch(`${serverUrl}/caption-presets`);
    if (res.ok) {
      const presets = await res.json();
      if (presets && presets.length) {
        fillSelects(presets.map((p) => p.id));
      }
    }
  } catch (_) { /* offline -> static list already populated */ }
}

function bindEvents() {
  // Navigation
  document.querySelectorAll('.nav-item').forEach((btn) => {
    btn.addEventListener('click', () => {
      const view = document.getElementById(`view-${btn.dataset.view}`);
      if (!view) return; // guard against a nav button with no matching section
      document.querySelectorAll('.nav-item').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
      view.classList.add('active');
      // Keep the Projects home fresh whenever it becomes visible.
      if (btn.dataset.view === 'projects') renderProjectsHome();
    });
  });

  // Projects home actions
  document.getElementById('projects-new-btn')?.addEventListener('click', goToNewProject);
  document.getElementById('projects-open-output-btn')?.addEventListener('click', openOutputFolder);
  document.getElementById('project-edit-save')?.addEventListener('click', saveEditProject);
  document.getElementById('project-edit-cancel')?.addEventListener('click', closeEditProjectModal);
  document.getElementById('project-edit-close')?.addEventListener('click', closeEditProjectModal);
  document.getElementById('project-new-create')?.addEventListener('click', createNewProject);
  document.getElementById('project-new-cancel')?.addEventListener('click', closeNewProjectModal);
  document.getElementById('project-new-close')?.addEventListener('click', closeNewProjectModal);

  // File selection
  const dropZone = document.getElementById('drop-zone');
  const fileInput = document.getElementById('file-input');

  if (dropZone && fileInput) {
  dropZone.addEventListener('click', () => fileInput.click());
  // Keyboard access: the drop zone is role="button", so Enter/Space open the picker.
  dropZone.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); }
  });
  dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
  dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    const files = Array.from(e.dataTransfer.files || []).filter(f => f.type.startsWith('video/') || /\.(mp4|mov|mkv|webm|avi|m4v)$/i.test(f.name));
    if (files.length === 1) {
      selectVideoFile(files[0]);
    } else if (files.length > 1) {
      handleBatchVideoDrop(files);
    }
  });
  fileInput.addEventListener('change', (e) => {
    const files = Array.from(e.target.files || []);
    if (files.length === 1) {
      selectVideoFile(files[0]);
    } else if (files.length > 1) {
      handleBatchVideoDrop(files);
    }
  });
  }

  document.getElementById('change-file')?.addEventListener('click', () => fileInput && fileInput.click());
  document.getElementById('save-project')?.addEventListener('click', saveCurrentProject);
  document.getElementById('project-list')?.addEventListener('change', (e) => {
    if (e.target.value === '__new__') { e.target.value = ''; goToNewProject(); return; }
    if (e.target.value) openProject(e.target.value);
  });

  // Clickable server-health footer -> jump to Setup & diagnostics so the user
  // can see what's healthy, what's missing, and what model suits their hardware.
  const healthBtn = document.getElementById('health-status-btn');
  if (healthBtn) healthBtn.addEventListener('click', () => openSettings());

  // Sidebar quick-access: Settings (consolidates deps/models/support), Queue,
  // and a dedicated Support TechFreq entry.
  document.getElementById('sidebar-settings-btn')?.addEventListener('click', () => openSettings());
  document.getElementById('sidebar-queue-btn')?.addEventListener('click', openQueueModal);
  document.getElementById('sidebar-support-btn')?.addEventListener('click', openSupport);

  // "📜 Logs" button in the header – opens the logs folder via Electron
  const openLogsBtn = document.getElementById('open-logs-folder');
  if (openLogsBtn) {
    openLogsBtn.addEventListener('click', () => {
      if (window.clipperAPI && window.clipperAPI.openLogsFolder) {
        window.clipperAPI.openLogsFolder();
      } else {
        showAlert('Logs are written to the "logs" folder next to the app. Server logs land in logs/server.log.');
      }
    });
  }

  // Info tooltips ("❓") are nested inside <label> elements, so a click would
  // otherwise toggle the checkbox. Stop that so the bubble is purely informational.
  document.querySelectorAll('.tooltip-toggle').forEach((el) => {
    el.addEventListener('click', (e) => e.preventDefault());
    el.addEventListener('mousedown', (e) => e.stopPropagation());
  });

  // Output folder (CapCut-style save location)
  const browseOutput = document.getElementById('browse-output-folder');
  if (browseOutput) browseOutput.addEventListener('click', chooseOutputFolder);
  const resetOutput = document.getElementById('reset-output-folder');
  if (resetOutput) resetOutput.addEventListener('click', resetOutputFolder);
  document.getElementById('sidebar-project-list')?.addEventListener('change', (e) => {
    if (e.target.value === '__new__') { e.target.value = ''; goToNewProject(); return; }
    if (e.target.value) openProject(e.target.value);
  });
  document.getElementById('delete-project')?.addEventListener('click', deleteCurrentProject);

  // Start clipping
  document.getElementById('start-clipping')?.addEventListener('click', startClipping);

  // Manual trim
  document.getElementById('add-trim-btn')?.addEventListener('click', addTrimmedClip);
  document.getElementById('trim-preview-btn')?.addEventListener('click', previewTrimSelection);
  document.getElementById('trim-layout')?.addEventListener('change', (e) => {
    const isReaction = e.target.value === 'game_reaction';
    document.getElementById('cam-options')?.classList.toggle('hidden', !isReaction);
    if (isReaction) updateCamPositionUI();
    // Suggested action moments are a gaming-focused affordance.
    document.getElementById('suggested-moments')?.classList.toggle('hidden', !isReaction);
    // Reaction PiP is full-frame, so no smart-crop offset needed.
    applyTrimPreviewRatio(e.target.value);
    const tv = document.getElementById('trim-video');
    if (tv) tv.style.display = '';
  });
  document.getElementById('cam-position')?.addEventListener('change', updateCamPositionUI);
  document.getElementById('pick-cam-btn')?.addEventListener('click', pickCameraClip);
  document.getElementById('suggest-moments-btn')?.addEventListener('click', loadSuggestedMoments);
  document.getElementById('trim-video')?.addEventListener('loadedmetadata', () => {
    const tv = document.getElementById('trim-video');
    trimState.duration = (tv && tv.duration) || 0;
    if (trimState.duration) {
      trimState.start = 0;
      trimState.end = trimState.duration;
      applyTrimPreviewRatio();
      updateTrimUI();
    }
  });
  setupTrimTimeline();

  // Chat
  document.getElementById('chat-send')?.addEventListener('click', sendChat);
  document.getElementById('chat-input')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') sendChat();
  });

  // Export-as format actions
  document.getElementById('export-compile')?.addEventListener('click', exportCompileReel);
  document.getElementById('cancel-active-job-btn')?.addEventListener('click', cancelActiveJob);

  // Global font size used by the initial Generate Clip render.
  // Generated clips own the caption controls; keep the legacy generation
  // preset synchronized so an initial render and later edits use one setting.
  const generatedPreset = document.getElementById('generated-caption-preset');
  generatedPreset?.addEventListener('change', refreshCaptionPreview);
  const generatedFontSize = document.getElementById('generated-caption-font-size');
  if (generatedFontSize) {
    const label = document.getElementById('generated-caption-font-size-label');
    const syncGenerated = () => {
      if (label) label.textContent = generatedFontSize.value;
      const previewFont = document.getElementById('caption-preview-font-size');
      const previewLabel = document.getElementById('caption-preview-font-size-label');
      if (previewFont) previewFont.value = generatedFontSize.value;
      if (previewLabel) previewLabel.textContent = generatedFontSize.value;
      applyCaptionPreviewStyle();
    };
    generatedFontSize.addEventListener('input', syncGenerated);
    generatedFontSize.addEventListener('change', syncGenerated);
  }
  const previewFontSize = document.getElementById('caption-preview-font-size');
  if (previewFontSize) {
    const previewLabel = document.getElementById('caption-preview-font-size-label');
    const syncPreview = () => {
      const generatedFont = document.getElementById('generated-caption-font-size');
      if (generatedFont) generatedFont.value = previewFontSize.value;
      if (previewLabel) previewLabel.textContent = previewFontSize.value;
      const generatedLabel = document.getElementById('generated-caption-font-size-label');
      if (generatedLabel) generatedLabel.textContent = previewFontSize.value;
      applyCaptionPreviewStyle();
    };
    previewFontSize.addEventListener('input', syncPreview);
    previewFontSize.addEventListener('change', syncPreview);
  }
  // Caption preset select inside the caption editor modal refreshes the preview.
  const captionPresetSelect = document.getElementById('caption-preset');
  if (captionPresetSelect) {
    captionPresetSelect.addEventListener('change', () => {
      const generatedPreset = document.getElementById('generated-caption-preset');
      if (generatedPreset) generatedPreset.value = captionPresetSelect.value;
      refreshCaptionPreview();
    });
  }

  // CapCut-style caption customization live updates
  document.getElementById('caption-font-name')?.addEventListener('change', applyCaptionPreviewStyle);
  document.getElementById('caption-primary-color')?.addEventListener('input', applyCaptionPreviewStyle);
  document.getElementById('caption-highlight-color')?.addEventListener('input', applyCaptionPreviewStyle);
  document.getElementById('caption-outline-color')?.addEventListener('input', applyCaptionPreviewStyle);

  const outlineSlider = document.getElementById('caption-outline-width');
  if (outlineSlider) {
    const outlineLabel = document.getElementById('caption-outline-width-label');
    const updateOutline = () => {
      if (outlineLabel) outlineLabel.textContent = outlineSlider.value;
      applyCaptionPreviewStyle();
    };
    outlineSlider.addEventListener('input', updateOutline);
    outlineSlider.addEventListener('change', updateOutline);
  }

  document.getElementById('caption-uppercase')?.addEventListener('change', refreshCaptionPreview);
  document.getElementById('caption-bold')?.addEventListener('change', applyCaptionPreviewStyle);
  document.getElementById('caption-italic')?.addEventListener('change', applyCaptionPreviewStyle);
  document.getElementById('caption-position')?.addEventListener('change', applyPortraitCaptionPreviewStyle);
  document.getElementById('caption-chunk-size')?.addEventListener('change', refreshPortraitCaptionPreview);

  // Brand Kits (save/apply/delete the whole caption look + layout).
  loadBrandKitList();
  document.getElementById('brand-kit-select')?.addEventListener('change', (e) => {
    const id = e.target.value;
    if (!id) return;
    const kit = readBrandKits().find((k) => k.id === id);
    if (kit) applyBrandKit(kit.settings);
  });
  document.getElementById('brand-kit-delete')?.addEventListener('click', () => {
    const sel = document.getElementById('brand-kit-select');
    const id = sel && sel.value;
    if (!id) { showToast('Pick a kit to delete first', 'info'); return; }
    writeBrandKits(readBrandKits().filter((k) => k.id !== id));
    loadBrandKitList();
    showToast('Brand kit deleted', 'info');
  });
  document.getElementById('brand-kit-save')?.addEventListener('click', () => {
    const bar = document.querySelector('.brand-kit-bar');
    if (!bar || bar.querySelector('.brand-kit-nameform')) return;
    const form = document.createElement('span');
    form.className = 'brand-kit-nameform';
    form.innerHTML = `<input type="text" id="brand-kit-name" placeholder="Kit name" maxlength="40" />` +
      `<button class="btn btn-small btn-primary" id="brand-kit-name-ok">Save</button>` +
      `<button class="btn btn-small btn-ghost" id="brand-kit-name-cancel">Cancel</button>`;
    bar.appendChild(form);
    const input = form.querySelector('#brand-kit-name');
    input.focus();
    form.querySelector('#brand-kit-name-cancel').onclick = () => form.remove();
    form.querySelector('#brand-kit-name-ok').onclick = () => {
      const kits = readBrandKits();
      const name = (input.value || '').trim() || `Kit ${kits.length + 1}`;
      const kit = { id: `kit-${Date.now()}`, name, settings: collectBrandKit() };
      kits.push(kit);
      writeBrandKits(kits);
      loadBrandKitList();
      const sel = document.getElementById('brand-kit-select');
      if (sel) sel.value = kit.id;
      form.remove();
      showToast(`Saved brand kit “${name}”`, 'success');
    };
  });

  // A/B hook variants: show several options at once to compare + pick.
  document.getElementById('hook-variants-btn')?.addEventListener('click', showHookVariants);

  // Intro-hook live preview: update as the user types or resizes the hook.
  document.getElementById('edit-intro-hook')?.addEventListener('input', updateHookPreview);
  const hookSizeSlider = document.getElementById('intro-hook-font-size');
  if (hookSizeSlider) {
    hookSizeSlider.addEventListener('input', () => {
      const lbl = document.getElementById('intro-hook-font-size-label');
      if (lbl) lbl.textContent = hookSizeSlider.value;
      updateHookPreview();
    });
  }

  // Remove hook: clear the intro-hook field and any rotating candidates so no
  // hook is burned on the next Save & Apply (the save flow keys intro_enabled
  // off whether this field has text). Also hides the live overlay immediately.
  document.getElementById('remove-hook-btn')?.addEventListener('click', () => {
    const hookInput = document.getElementById('edit-intro-hook');
    if (hookInput) hookInput.value = '';
    hookCandidates = [];
    hookCandidateIdx = -1;
    const vbox = document.getElementById('hook-variants');
    if (vbox) { vbox.classList.add('hidden'); vbox.innerHTML = ''; }
    updateHookPreview();
    showToast('Intro hook removed — Save & Apply to re-render without it', 'info');
  });

  // Wizard Stepper & Nav Actions
  for (let i = 1; i <= 4; i++) {
    const stepBtn = document.getElementById(`wizard-step-btn-${i}`);
    if (stepBtn) {
      stepBtn.addEventListener('click', () => {
        if (i === 1) {
          setWizardStep(1);
        } else if (i === 2 && selectedVideo) {
          setWizardStep(2);
        } else if (i === 3 && selectedVideo) {
          setWizardStep(3);
        } else if (i === 4 && generatedClips && generatedClips.length > 0) {
          setWizardStep(4);
        }
      });
    }
  }

  const step1Next = document.getElementById('step1-next-btn');
  if (step1Next) {
    step1Next.addEventListener('click', () => {
      if (selectedVideo) setWizardStep(2);
    });
  }

  const step2Back = document.getElementById('step2-back-btn');
  if (step2Back) {
    step2Back.addEventListener('click', () => setWizardStep(1));
  }

  const captionToggleBtn = document.getElementById('caption-style-toggle-btn');
  const captionWrap = document.getElementById('generated-caption-settings-wrap');
  if (captionToggleBtn && captionWrap) {
    captionToggleBtn.addEventListener('click', () => {
      const isOpen = !captionWrap.classList.contains('hidden');
      if (isOpen) {
        captionWrap.classList.add('hidden');
        captionToggleBtn.classList.remove('open');
      } else {
        captionWrap.classList.remove('hidden');
        captionToggleBtn.classList.add('open');
        refreshPortraitCaptionPreview();
      }
    });
  }

  const step4Restart = document.getElementById('step4-restart-btn');
  if (step4Restart) {
    step4Restart.addEventListener('click', resetWizardToStep1);
  }
}

// ------------------------------------------------------------------
// Server health
// ------------------------------------------------------------------
async function checkHealth() {
  const statusEl = document.getElementById('health-status');
  if (!statusEl) return;
  try {
    const res = await fetch(`${serverUrl}/health`);
    // A 4xx/5xx still resolves the promise, so the old code painted "ready"
    // for a 500. Only a genuine 200 means the backend is actually up.
    if (!res.ok) {
      statusEl.innerHTML = `<span class="dot error"></span> Server error (${res.status})`;
      return;
    }
    const data = await res.json();
    // Show the active transcription backend (mlx / faster-whisper / openai-whisper)
    // so it's obvious at a glance which acceleration path is live.
    const backend = data.transcription_backend;
    const backendLabel = backend ? ` · ${escapeHtml(backend)}` : '';
    if (data.ffmpeg_available === false) {
      statusEl.innerHTML = `<span class="dot warn"></span> Running — FFmpeg missing${backendLabel}`;
    } else {
      statusEl.innerHTML = `<span class="dot ok"></span> Server ready${backendLabel}`;
    }
  } catch (e) {
    statusEl.innerHTML = `<span class="dot error"></span> Server offline`;
  }
}

// ------------------------------------------------------------------
// File selection
// ------------------------------------------------------------------
function selectVideoFile(file) {
  selectedVideo = file.path;
  generatedClips = [];
  // Keep the current project id when adding a video to a freshly created
  // project draft (New Project flow); otherwise start a fresh unsaved session.
  if (!pendingNewProject) currentProjectId = null;
  emojiSuggestionCache.clear();
  document.getElementById('file-name').textContent = file.name;
  document.getElementById('file-info').classList.remove('hidden');
  document.getElementById('project-bar').classList.remove('hidden');
  // Use the name from the New Project modal if one is pending; otherwise fall
  // back to the video's filename.
  const projName = pendingNewProject?.name || file.name.replace(/\.[^.]+$/, '');
  document.getElementById('project-name').value = projName;
  updateCurrentProjectUI(projName);
  // If this video is being added to a project draft (or a re-opened project),
  // persist the source now so the entry stops being an empty stub.
  if (currentProjectId) saveProjectManifest(false);
  renderProjectGrid();  // a video is loaded now — hide the step-1 recents
  document.getElementById('start-clipping').disabled = false;
  const nextBtn = document.getElementById('step1-next-btn');
  if (nextBtn) nextBtn.disabled = false;
  document.getElementById('clips-grid').innerHTML = '';
  document.getElementById('results').classList.add('hidden');

  // Initialize the manual trimmer with this source.
  const video = document.getElementById('trim-video');
  video.src = fileUrl(selectedVideo);
  trimState.camVideo = null;
  document.getElementById('trim-panel').classList.remove('hidden');
  document.getElementById('cam-path').value = '';
  setWizardStep(1);

  // Auto-detect gameplay + facecam and switch to the reaction layout for the user.
  autoDetectLayout(selectedVideo);
}

// Ask the backend whether this looks like gameplay with a corner webcam. If so,
// flip the layout to Gaming Reaction and preset the camera corner — the user can
// still change it. Best-effort: silent if the server is offline or unsure.
async function autoDetectLayout(videoPath) {
  try {
    const res = await fetch(`${serverUrl}/tools/detect-layout`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_path: videoPath, start_time: 0, end_time: 120 }),
    });
    if (!res.ok) return;
    const d = await res.json();
    if (!d.is_gaming) return;
    const layout = document.getElementById('trim-layout');
    if (layout) {
      layout.value = 'game_reaction';
      layout.dispatchEvent(new Event('change'));  // reveal cam options + ratio
    }
    const camPos = document.getElementById('cam-position');
    if (camPos && d.cam_position) camPos.value = d.cam_position;
    document.getElementById('suggested-moments')?.classList.remove('hidden');
    showToast(`🎮 Gaming + facecam detected (${Math.round((d.confidence || 0) * 100)}%) — switched to Reaction layout`, 'info');
  } catch (_) { /* detection is optional */ }
}

async function handleBatchVideoDrop(files) {
  showToast(`📁 Batch mode: Enqueueing ${files.length} long-form videos...`, 'info');
  // First video is opened in the editor view
  selectVideoFile(files[0]);
  
  // Submit all files sequentially to the backend queue. Read the option
  // controls ONCE, with null-safe access and the same element IDs the single
  // clip path uses (the old code read a non-existent #aspect-ratio and threw).
  const val = (id, fallback) => document.getElementById(id)?.value ?? fallback;
  const checked = (id) => !!document.getElementById(id)?.checked;
  const payloadBase = {
    aspect_ratio: val('clip-aspect-ratio', '9:16'),
    whisper_model: val('whisper-model', 'base'),
    use_audio_energy: checked('audio-energy'),
    use_llm: checked('use-llm'),
    burn_captions: checked('burn-captions'),
    remove_silence: checked('remove-silence'),
    bleep_profanity: checked('censor-profanity'),
    caption_style: val('generated-caption-preset', 'viral_yellow'),
  };

  let queuedCount = 0;
  for (const file of files) {
    try {
      const res = await fetch(`${serverUrl}/process`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payloadBase, video_path: file.path }),
      });
      if (res.ok) queuedCount++;
    } catch (_) { /* keep going; report the tally at the end */ }
  }
  if (queuedCount === files.length) {
    showToast(`🚀 Queued all ${queuedCount} videos into the background processor.`, 'success');
  } else {
    showToast(`Queued ${queuedCount}/${files.length} videos. The rest failed — is the server running?`, queuedCount ? 'info' : 'error');
  }
  setWizardStep(3);
}

function readProjects() {
  try { return JSON.parse(localStorage.getItem(PROJECTS_KEY) || '[]'); }
  catch (_) { return []; }
}

function writeProjects(projects) {
  localStorage.setItem(PROJECTS_KEY, JSON.stringify(projects));
}

function loadProjectList() {
  const select = document.getElementById('project-list');
  if (!select) return;
  select.innerHTML = '<option value="">Open saved project…</option><option value="__new__">➕ Start New Project</option>';
  readProjects().sort((a, b) => (b.updatedAt || '').localeCompare(a.updatedAt || '')).forEach((project) => {
    const option = document.createElement('option');
    option.value = project.id;
    option.textContent = project.name || 'Untitled project';
    select.appendChild(option);
  });
  const generatedPreset = document.getElementById('generated-caption-preset');
  if (generatedPreset && currentProjectId) {
    const project = readProjects().find((item) => item.id === currentProjectId);
    if (project?.captionStyle) generatedPreset.value = project.captionStyle;
    if (project?.fontSize) {
      const font = document.getElementById('generated-caption-font-size');
      if (font) font.value = project.fontSize;
      const label = document.getElementById('generated-caption-font-size-label');
      if (label && font) label.textContent = font.value;
    }
  }
  const sidebarSelect = document.getElementById('sidebar-project-list');
  if (sidebarSelect) {
    sidebarSelect.innerHTML = select.innerHTML;
    if (currentProjectId) sidebarSelect.value = currentProjectId;
  }
  renderProjectGrid();
  renderProjectsHome();
}

// CapCut-style saved-projects grid: thumbnail + name + clip count/duration + Open.
function renderProjectGrid() {
  const grid = document.getElementById('project-grid');
  const wrap = document.getElementById('project-grid-wrap');
  if (!grid) return;
  const projects = readProjects().sort((a, b) => (b.updatedAt || '').localeCompare(a.updatedAt || ''));
  // Recent projects belong on the Projects home (the "main menu"). Once the
  // user is in a project (draft or opened) or has a video loaded, hide the
  // step-1 recents so the wizard isn't cluttered with them.
  const inProject = !!currentProjectId || !!selectedVideo;
  if (!projects.length || inProject) {
    if (wrap) wrap.classList.add('hidden');
    grid.innerHTML = '';
    return;
  }
  if (wrap) wrap.classList.remove('hidden');
  grid.innerHTML = '';
  projects.forEach((project) => {
    const clips = project.clips || [];
    const totalSecs = clips.reduce((a, c) => a + (Number(c && c.duration) || 0), 0);
    const thumb = clips.find((c) => c && c.thumbnail_path);
    const when = project.updatedAt ? new Date(project.updatedAt).toLocaleDateString() : '';
    const card = document.createElement('div');
    card.className = 'project-card';
    card.innerHTML = `
      <div class="project-thumb">${thumb
        ? `<img alt="" src="${escapeHtml(fileUrl(thumb.thumbnail_path))}" loading="lazy" />`
        : '<span class="project-thumb-fallback" aria-hidden="true">🎬</span>'}
        <span class="project-thumb-badge">${clips.length} clip${clips.length === 1 ? '' : 's'}</span>
      </div>
      <div class="project-card-body">
        <div class="project-card-name">${escapeHtml(project.name || 'Untitled project')}</div>
        <div class="project-card-meta muted small">${Math.round(totalSecs)}s · ${escapeHtml(when)}${clips.length ? ' · ✅ Ready' : ''}</div>
        <div class="project-card-actions">
          <button class="btn btn-small btn-primary" data-open="${escapeHtml(project.id)}">Open</button>
          <button class="btn btn-small btn-ghost" data-del="${escapeHtml(project.id)}">🗑</button>
        </div>
      </div>`;
    card.querySelector('[data-open]').addEventListener('click', () => openProject(project.id));
    card.querySelector('[data-del]').addEventListener('click', async () => {
      const ok = await showConfirm(`Delete "${project.name || 'Untitled project'}" and its generated clips? The rendered files are removed too.`);
      if (!ok) return;
      await deleteProjectFootage(project);   // remove rendered clips, not just the manifest
      writeProjects(readProjects().filter((p) => p.id !== project.id));
      if (currentProjectId === project.id) currentProjectId = null;
      loadProjectList();
    });
    grid.appendChild(card);
  });
}

// ------------------------------------------------------------------
// Projects Home (landing view): create a new project or reopen a saved
// one. A standalone screen (OpenClipper/CapCut-style) that mirrors the
// step-1 recent grid but adds an Edit action (rename + description).
// ------------------------------------------------------------------
function activateView(viewName) {
  const view = document.getElementById(`view-${viewName}`);
  if (!view) return;
  document.querySelectorAll('.nav-item').forEach((b) => b.classList.remove('active'));
  document.querySelector(`.nav-item[data-view="${viewName}"]`)?.classList.add('active');
  document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
  view.classList.add('active');
}

// Holds the name/description entered in the New Project modal until the first
// save writes them onto the manifest.
let pendingNewProject = null;

// Reflect the active project's name in the clipper header + sidebar so the user
// can see which project they're working in. Pass '' to clear.
function updateCurrentProjectUI(name) {
  const trimmed = (name || '').trim();
  const label = document.getElementById('active-project-label');
  const nameEl = document.getElementById('active-project-name');
  const sideEl = document.getElementById('sidebar-current-project');
  if (nameEl) nameEl.textContent = trimmed;
  if (label) label.classList.toggle('hidden', !trimmed);
  if (sideEl) {
    sideEl.textContent = trimmed ? `▶ ${trimmed}` : '';
    sideEl.classList.toggle('hidden', !trimmed);
  }
}

// ------------------------------------------------------------------
// Brand Kits: save the current caption look + layout as a reusable template.
// ------------------------------------------------------------------
const BRAND_KITS_KEY = 'klipzy.brandkits.v1';
const BRAND_KIT_FIELDS = [
  'generated-caption-preset', 'generated-caption-font-size', 'caption-font-name',
  'caption-primary-color', 'caption-highlight-color', 'caption-outline-color', 'caption-outline-width',
  'caption-position', 'caption-chunk-size', 'caption-uppercase', 'caption-bold', 'caption-italic',
  'caption-intro-enabled', 'caption-intro-duration', 'clip-aspect-ratio', 'trim-layout', 'vertical-crop',
];
function readBrandKits() { try { return JSON.parse(localStorage.getItem(BRAND_KITS_KEY)) || []; } catch (_) { return []; } }
function writeBrandKits(k) { localStorage.setItem(BRAND_KITS_KEY, JSON.stringify(k)); }
function collectBrandKit() {
  const data = {};
  BRAND_KIT_FIELDS.forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    data[id] = el.type === 'checkbox' ? el.checked : el.value;
  });
  return data;
}
function applyBrandKit(settings) {
  Object.entries(settings || {}).forEach(([id, val]) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (el.type === 'checkbox') el.checked = !!val; else el.value = val;
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  if (typeof refreshCaptionPreview === 'function') refreshCaptionPreview();
  showToast('Brand kit applied ✅', 'success');
}
function loadBrandKitList() {
  const sel = document.getElementById('brand-kit-select');
  if (!sel) return;
  const kits = readBrandKits();
  sel.innerHTML = '<option value="">Choose a saved kit…</option>' +
    kits.map((k) => `<option value="${escapeHtml(k.id)}">${escapeHtml(k.name)}</option>`).join('');
}

function goToNewProject() {
  // Ask for a name + optional description first (OpenClipper-style), then drop
  // into the wizard. The drop zone in step 1 is where the video gets added.
  openNewProjectModal();
}

function openNewProjectModal() {
  const nameEl = document.getElementById('project-new-name');
  const descEl = document.getElementById('project-new-desc');
  if (nameEl) nameEl.value = '';
  if (descEl) descEl.value = '';
  document.getElementById('project-new-modal')?.classList.remove('hidden');
  if (nameEl) setTimeout(() => nameEl.focus(), 30);
}
function closeNewProjectModal() {
  document.getElementById('project-new-modal')?.classList.add('hidden');
}
function createNewProject() {
  const name = (document.getElementById('project-new-name')?.value || '').trim() || 'Untitled project';
  const description = (document.getElementById('project-new-desc')?.value || '').trim();
  closeNewProjectModal();
  // Persist a stub immediately so the project shows up in the lists right away
  // (source video + clips get filled in when the user adds media). This matches
  // the reference apps: create the project first, add footage after.
  const project = {
    id: `project-${Date.now()}`,
    name,
    description,
    source: '',
    sourceName: '',
    clips: [],
    updatedAt: new Date().toISOString(),
  };
  const projects = readProjects();
  projects.push(project);
  writeProjects(projects);
  enterProjectDraft(project);
  showToast(`Project “${name}” created — now add your video`, 'info');
}

// Enter a project that has no source video yet (a freshly created draft, or a
// stub reopened from the list): reset the wizard to step 1, mark it current,
// prefill its name, and select it in the lists so it's clearly active.
function enterProjectDraft(project) {
  resetWizardToStep1();               // clears state + switches to the clipper view
  currentProjectId = project.id;
  pendingNewProject = { name: project.name || 'Untitled project', description: project.description || '' };
  const nameInput = document.getElementById('project-name');
  if (nameInput) nameInput.value = project.name || '';
  updateCurrentProjectUI(project.name || '');
  loadProjectList();
  const pl = document.getElementById('project-list'); if (pl) pl.value = project.id;
  const spl = document.getElementById('sidebar-project-list'); if (spl) spl.value = project.id;
}

function openProjectFromHome(id) {
  activateView('clipper');
  openProject(id);
}

async function openOutputFolder() {
  let folder = document.getElementById('output-folder-input')?.value?.trim();
  if (!folder) {
    try {
      const r = await fetch(`${serverUrl}/output-folder`);
      if (r.ok) { const d = await r.json(); folder = d.output_dir || d.path || d.folder || ''; }
    } catch (_) { /* offline — fall through */ }
  }
  if (folder) revealInFolder(folder);
  else showAlert('No output folder is set yet. Pick one in step 1 (“Save generated clips to”).');
}

function renderProjectsHome() {
  const grid = document.getElementById('projects-home-grid');
  if (!grid) return;
  const projects = readProjects().sort((a, b) => (b.updatedAt || '').localeCompare(a.updatedAt || ''));
  const countEl = document.getElementById('projects-home-count');
  if (countEl) countEl.textContent = projects.length ? `${projects.length} project${projects.length === 1 ? '' : 's'}` : '';
  grid.innerHTML = '';
  if (!projects.length) {
    grid.innerHTML = `
      <div class="projects-empty">
        <span class="projects-empty-icon" aria-hidden="true">🎬</span>
        <h3>No projects yet</h3>
        <p>Start your first one — drop in a long video and Klipzy turns it into shorts.</p>
        <button class="btn btn-primary btn-large" id="projects-empty-new">➕ New Project</button>
      </div>`;
    grid.querySelector('#projects-empty-new')?.addEventListener('click', goToNewProject);
    return;
  }
  projects.forEach((project) => {
    const clips = project.clips || [];
    const totalSecs = clips.reduce((a, c) => a + (Number(c && c.duration) || 0), 0);
    const thumb = clips.find((c) => c && c.thumbnail_path);
    const when = project.updatedAt ? new Date(project.updatedAt).toLocaleDateString() : '';
    const card = document.createElement('div');
    card.className = 'project-card';
    card.innerHTML = `
      <div class="project-thumb">${thumb
        ? `<img alt="" src="${escapeHtml(fileUrl(thumb.thumbnail_path))}" loading="lazy" />`
        : '<span class="project-thumb-fallback" aria-hidden="true">🎬</span>'}
        <span class="project-thumb-badge">${clips.length} clip${clips.length === 1 ? '' : 's'}</span>
      </div>
      <div class="project-card-body">
        <div class="project-card-name">${escapeHtml(project.name || 'Untitled project')}</div>
        <div class="project-card-meta muted small">${Math.round(totalSecs)}s · ${escapeHtml(when)}${clips.length ? ' · ✅ Ready' : ''}</div>
        <div class="project-card-actions">
          <button class="btn btn-small btn-primary" data-open>Open</button>
          <button class="btn btn-small btn-secondary" data-edit>✏️ Edit</button>
          <button class="btn btn-small btn-ghost" data-del title="Delete project">🗑</button>
        </div>
      </div>`;
    card.querySelector('[data-open]').addEventListener('click', () => openProjectFromHome(project.id));
    card.querySelector('[data-edit]').addEventListener('click', () => openEditProjectModal(project.id));
    card.querySelector('[data-del]').addEventListener('click', async () => {
      const ok = await showConfirm(`Delete "${project.name || 'Untitled project'}" and its generated clips? The rendered files are removed too.`);
      if (!ok) return;
      await deleteProjectFootage(project);
      writeProjects(readProjects().filter((p) => p.id !== project.id));
      if (currentProjectId === project.id) currentProjectId = null;
      loadProjectList();
    });
    grid.appendChild(card);
  });
}

// Edit Project modal (rename + optional description). Description is stored on
// the manifest so it round-trips.
let editingProjectId = null;
function openEditProjectModal(id) {
  const project = readProjects().find((p) => p.id === id);
  if (!project) { showAlert('That project could no longer be found.'); return; }
  editingProjectId = id;
  const nameEl = document.getElementById('project-edit-name');
  const descEl = document.getElementById('project-edit-desc');
  if (nameEl) nameEl.value = project.name || '';
  if (descEl) descEl.value = project.description || '';
  document.getElementById('project-edit-modal')?.classList.remove('hidden');
  if (nameEl) setTimeout(() => nameEl.focus(), 30);
}
function closeEditProjectModal() {
  editingProjectId = null;
  document.getElementById('project-edit-modal')?.classList.add('hidden');
}
function saveEditProject() {
  if (!editingProjectId) return closeEditProjectModal();
  const projects = readProjects();
  const project = projects.find((p) => p.id === editingProjectId);
  if (!project) { closeEditProjectModal(); return; }
  project.name = (document.getElementById('project-edit-name')?.value || '').trim() || 'Untitled project';
  project.description = (document.getElementById('project-edit-desc')?.value || '').trim();
  project.updatedAt = new Date().toISOString();
  writeProjects(projects);
  closeEditProjectModal();
  loadProjectList();
  showToast('Project updated', 'success');
}

function saveProjectManifest(showMessage = false) {
  if (!selectedVideo) return;
  const name = document.getElementById('project-name').value.trim() || 'Untitled project';
  const projects = readProjects();
  // Preserve fields the wizard doesn't own (e.g. a description set via the
  // Projects-home Edit modal) so re-saving here doesn't wipe them.
  const existing = projects.find((item) => item.id === currentProjectId);
  const project = {
    id: currentProjectId || `project-${Date.now()}`,
    name,
    // Prefer an existing description, then one entered in the New Project modal.
    description: existing?.description || pendingNewProject?.description || '',
    source: selectedVideo,
    sourceName: document.getElementById('file-name').textContent,
    clips: generatedClips,
    captionStyle: document.getElementById('generated-caption-preset')?.value || 'viral_yellow',
    fontSize: globalCaptionFontSize(),
    captionOptions: collectCaptionOptions(),
    outputFolder: document.getElementById('output-folder-input')?.value || '',
    // Output layout/aspect so reopening a project restores the same 9:16 /
    // gaming / aspect setup it was created with, not the defaults.
    aspectRatio: document.getElementById('clip-aspect-ratio')?.value || '9:16',
    layout: document.getElementById('trim-layout')?.value || 'vertical',
    camPosition: document.getElementById('cam-position')?.value || 'bottom-right',
    camScale: document.getElementById('cam-scale')?.value || '0.3',
    updatedAt: new Date().toISOString(),
  };
  currentProjectId = project.id;
  const index = projects.findIndex((item) => item.id === project.id);
  if (index >= 0) projects[index] = project; else projects.push(project);
  writeProjects(projects);
  pendingNewProject = null;  // consumed — name/description now live on the manifest
  updateCurrentProjectUI(name);
  loadProjectList();
  document.getElementById('project-list').value = project.id;
  if (showMessage) showAlert(`✅ Project saved: ${name}`);
}

function saveCurrentProject() {
  if (!selectedVideo) return showAlert('Choose a video before saving a project.');
  saveProjectManifest(true);
}

function saveCurrentProjectSilently() {
  saveProjectManifest(false);
}

function openProject(id) {
  const project = readProjects().find((item) => item.id === id);
  if (!project) return;
  // A stub project (created via New Project, no video added yet): resume it at
  // step 1 so the user can add a video, rather than trying to load an empty src.
  if (!project.source) {
    enterProjectDraft(project);
    return;
  }
  pendingNewProject = null;  // opening an existing project cancels any pending "new project"
  currentProjectId = project.id;
  selectedVideo = project.source;
  if (project.outputFolder) saveOutputFolder(project.outputFolder);
  // Restore the output layout/aspect the project was created with.
  const aspectSel = document.getElementById('clip-aspect-ratio');
  if (aspectSel && project.aspectRatio) aspectSel.value = project.aspectRatio;
  const camScaleSel = document.getElementById('cam-scale');
  if (camScaleSel && project.camScale) camScaleSel.value = project.camScale;
  const layoutSel = document.getElementById('trim-layout');
  if (layoutSel && project.layout) {
    layoutSel.value = project.layout;
    layoutSel.dispatchEvent(new Event('change'));  // reveal/hide cam-options
  }
  const camPosSel = document.getElementById('cam-position');
  if (camPosSel && project.camPosition) {
    camPosSel.value = project.camPosition;
    camPosSel.dispatchEvent(new Event('change'));  // sync cam-clip/scale rows
  }
  generatedClips = Array.isArray(project.clips) ? project.clips : [];
  document.getElementById('file-name').textContent = project.sourceName || project.source;
  document.getElementById('file-info').classList.remove('hidden');
  document.getElementById('project-bar').classList.remove('hidden');
  document.getElementById('project-name').value = project.name || '';
  updateCurrentProjectUI(project.name || '');
  document.getElementById('start-clipping').disabled = !selectedVideo;
  const video = document.getElementById('trim-video');
  video.src = fileUrl(selectedVideo);
  document.getElementById('trim-panel').classList.remove('hidden');

  // Restore caption options if present
  if (project.captionOptions) {
    const o = project.captionOptions;
    if (o.caption_style) {
      const preset = document.getElementById('generated-caption-preset');
      if (preset) preset.value = o.caption_style;
    }
    if (o.font_size) {
      const font = document.getElementById('generated-caption-font-size');
      if (font) font.value = o.font_size;
      const label = document.getElementById('generated-caption-font-size-label');
      if (label) label.textContent = o.font_size;
    }
    if (o.font_name !== undefined) {
      const el = document.getElementById('caption-font-name');
      if (el) el.value = o.font_name || '';
    }
    if (o.primary_color) {
      const el = document.getElementById('caption-primary-color');
      if (el) el.value = o.primary_color;
    }
    if (o.highlight_color) {
      const el = document.getElementById('caption-highlight-color');
      if (el) el.value = o.highlight_color;
    }
    if (o.outline_color) {
      const el = document.getElementById('caption-outline-color');
      if (el) el.value = o.outline_color;
    }
    if (o.outline_width !== undefined) {
      const el = document.getElementById('caption-outline-width');
      if (el) el.value = o.outline_width;
      const label = document.getElementById('caption-outline-width-label');
      if (label) label.textContent = o.outline_width;
    }
    if (o.position !== undefined) {
      const el = document.getElementById('caption-position');
      if (el) el.value = String(o.position);
    }
    if (o.chunk_size !== undefined) {
      const el = document.getElementById('caption-chunk-size');
      if (el) el.value = String(o.chunk_size || 0);
    }
    if (o.uppercase !== undefined) {
      const el = document.getElementById('caption-uppercase');
      if (el) el.checked = !!o.uppercase;
    }
    if (o.bold !== undefined) {
      const el = document.getElementById('caption-bold');
      if (el) el.checked = !!o.bold;
    }
    if (o.italic !== undefined) {
      const el = document.getElementById('caption-italic');
      if (el) el.checked = !!o.italic;
    }
    refreshCaptionPreview();
  }

  showResults(generatedClips);
  document.getElementById('project-list').value = id;
  const sidebarSelect = document.getElementById('sidebar-project-list');
  if (sidebarSelect) sidebarSelect.value = id;
  const nextBtn = document.getElementById('step1-next-btn');
  if (nextBtn) nextBtn.disabled = !selectedVideo;
  if (generatedClips && generatedClips.length > 0) {
    setWizardStep(4);
  } else {
    setWizardStep(1);
  }
}

async function deleteCurrentProject() {
  if (!currentProjectId) return showAlert('Save this project first, then it can be deleted.');
  const projects = readProjects();
  const project = projects.find((item) => item.id === currentProjectId);
  // Check existence BEFORE reading project.name — the old order dereferenced a
  // possibly-undefined project when currentProjectId pointed at a stale entry.
  if (!project) {
    showAlert('That project could no longer be found.');
    return;
  }
  const confirmed = await showConfirm(`Delete project “${project.name}” and its generated files?`);
  if (!confirmed) return;
  await deleteProjectFootage(project);
  writeProjects(projects.filter((item) => item.id !== currentProjectId));
  resetWizardToStep1();
  loadProjectList();
}

// Collect every generated artifact for a project and ask the server to delete
// the footage. Shared by the "Delete Project" button and the project-grid trash
// icon so removing a project always removes its rendered clips too — the server
// only deletes inside the output folder, never the original source video.
function projectArtifactPaths(project) {
  const paths = [];
  (project.clips || []).forEach((clip) => {
    if (clip?.output_file) paths.push(clip.output_file);
    if (clip?.srt_path) paths.push(clip.srt_path);
    if (clip?.vtt_path) paths.push(clip.vtt_path);
    if (clip?.ass_path) paths.push(clip.ass_path);
    if (clip?.output_file) {
      const baseDir = clip.output_file.replace(/\\/g, '/').split('/').slice(0, -1).join('/');
      if (baseDir) paths.push(baseDir);
    }
  });
  return paths;
}

async function deleteProjectFootage(project) {
  const paths = projectArtifactPaths(project);
  if (!paths.length) return;
  try {
    await fetch(`${serverUrl}/project/delete`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths }),
    });
  } catch (_) { /* local manifest removal still proceeds */ }
}

// ------------------------------------------------------------------
// Manual trim / reaction layout
// ------------------------------------------------------------------
function setupTrimTimeline() {
  const timeline = document.getElementById('trim-timeline');
  const handleIn = document.getElementById('trim-handle-in');
  const handleOut = document.getElementById('trim-handle-out');
  const video = document.getElementById('trim-video');

  function xToTime(clientX) {
    const rect = timeline.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    return frac * trimState.duration;
  }

  function timeToPct(t) {
    if (!trimState.duration) return 0;
    return (t / trimState.duration) * 100;
  }

  function paint(sel, startPct, endPct) {
    sel.style.left = `${startPct}%`;
    sel.style.width = `${Math.max(0, endPct - startPct)}%`;
    handleIn.style.left = `${startPct}%`;
    handleOut.style.left = `${endPct}%`;
    updateTrimLabels();
  }

  function updateTrimLabels() {
    document.getElementById('trim-start-label').textContent = `Start: ${fmtTrimTime(trimState.start)}`;
    document.getElementById('trim-end-label').textContent = `End: ${fmtTrimTime(trimState.end)}`;
    document.getElementById('trim-duration-label').textContent = `${(trimState.end - trimState.start).toFixed(1)}s`;
  }

  window.updateTrimUI = function () {
    paint(document.getElementById('trim-selection'), timeToPct(trimState.start), timeToPct(trimState.end));
  };

  // Coalesce seeks to a single update per animation frame: seeking on every
  // mousemove is what made the preview feel laggy during a drag.
  function createSeeker() {
    let raf = 0;
    let pending = null;
    const apply = () => {
      raf = 0;
      if (pending != null) {
        const t = pending;
        pending = null;
        video.currentTime = t;
      }
    };
    return (t) => {
      pending = t;
      if (!raf) raf = requestAnimationFrame(() => apply());
    };
  }
  const seekTo = createSeeker();

  // Click on empty timeline seeks the video.
  timeline.addEventListener('click', (e) => {
    if (e.target === timeline) {
      seekTo(xToTime(e.clientX));
    }
  });

  // Drag anywhere non-handle seeks playhead.
  timeline.addEventListener('mousedown', (e) => {
    if (e.target !== handleIn && e.target !== handleOut) {
      const move = (ev) => { ev.preventDefault(); seekTo(xToTime(ev.clientX)); };
      move(e);
      const up = () => {
        window.removeEventListener('mousemove', move);
        window.removeEventListener('mouseup', up);
      };
      window.addEventListener('mousemove', move);
      window.addEventListener('mouseup', up);
    }
  });

  // Drag handles with pointer capture. DOM updates and preview seeks are both
  // coalesced, avoiding layout/decoder work for every raw mouse event.
  const handleDrag = (which) => (e) => {
    e.preventDefault();
    e.stopPropagation();
    const handle = e.currentTarget;
    const move = (ev) => {
      const t = xToTime(ev.clientX);
      if (which === 'in') trimState.start = Math.min(t, trimState.end - 0.1);
      else trimState.end = Math.max(t, trimState.start + 0.1);
      seekTo(which === 'in' ? trimState.start : trimState.end);
      if (!handleDrag.paintRaf) {
        handleDrag.paintRaf = requestAnimationFrame(() => {
          handleDrag.paintRaf = 0;
          paint(document.getElementById('trim-selection'), timeToPct(trimState.start), timeToPct(trimState.end));
        });
      }
    };
    const up = () => {
      handle.releasePointerCapture?.(e.pointerId);
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', up);
      handle.removeEventListener('pointercancel', up);
    };
    handle.setPointerCapture?.(e.pointerId);
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', up);
    handle.addEventListener('pointercancel', up);
    move(e);
  };

  handleIn.addEventListener('pointerdown', handleDrag('in'));
  handleOut.addEventListener('pointerdown', handleDrag('out'));

  // Keep playhead in sync while video plays.
  video.addEventListener('timeupdate', () => {
    const pct = timeToPct(video.currentTime);
    document.getElementById('trim-playhead').style.left = `${pct}%`;
  });
}

function fmtTrimTime(seconds) {
  if (!isFinite(seconds)) seconds = 0;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  const tenths = Math.floor((seconds % 1) * 10);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${tenths}`;
}

// Mirror the selected output ratio in the preview box.
function applyTrimPreviewRatio() {
  const layout = document.getElementById('trim-layout').value;
  const video = document.getElementById('trim-video');
  video.classList.toggle('trim-vertical', layout === 'vertical');
  video.classList.toggle('trim-wide', layout === 'full');
}

function previewTrimSelection() {
  const video = document.getElementById('trim-video');
  if (!trimState.duration) return;
  video.currentTime = trimState.start;
  video.play();
  const stopAt = () => {
    if (video.currentTime >= trimState.end) {
      video.pause();
      video.removeEventListener('timeupdate', stopAt);
    }
  };
  video.addEventListener('timeupdate', stopAt);
}

// Ask the backend for suggested action moments (loud + high-motion) and render
// them as one-click buttons that load the moment into the trim selection.
async function loadSuggestedMoments() {
  if (!selectedVideo) {
    showToast('Load a video first to detect action moments.', 'info');
    return;
  }
  const btn = document.getElementById('suggest-moments-btn');
  const list = document.getElementById('suggested-moments-list');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Detecting…'; }
  if (list) list.innerHTML = '<p class="muted small">Scanning for gunfights, big plays & high-motion moments…</p>';
  try {
    const minD = parseFloat(document.getElementById('min-duration')?.value) || 15;
    const maxD = parseFloat(document.getElementById('max-duration')?.value) || 45;
    const res = await fetch(`${serverUrl}/tools/suggest-moments`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_path: selectedVideo, min_duration: minD, max_duration: maxD, max_moments: 6 }),
    });
    const data = await res.json().catch(() => ({}));
    const moments = data.moments || [];
    if (!moments.length) {
      if (list) list.innerHTML = '<p class="muted small">No standout action moments found. Try the manual trimmer, or lower the minimum duration.</p>';
      return;
    }
    if (list) {
      list.innerHTML = '';
      moments.forEach((m, i) => {
        const b = document.createElement('button');
        b.className = 'btn btn-small moment-chip';
        b.type = 'button';
        b.title = m.reason || 'Suggested moment';
        b.textContent = `${m.title || `Moment ${i + 1}`} · ${fmtTrimTime(m.start)}–${fmtTrimTime(m.end)} (${Math.round(m.duration)}s)`;
        b.addEventListener('click', () => applySuggestedMoment(m.start, m.end));
        list.appendChild(b);
      });
    }
    showToast(`🎯 Found ${moments.length} suggested moment${moments.length === 1 ? '' : 's'} — click one to load it.`, 'success');
  } catch (err) {
    if (list) list.innerHTML = `<p class="muted small">Couldn't detect moments: ${escapeHtml(err.message || String(err))}</p>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '✨ Detect moments'; }
  }
}

// Load a suggested moment into the trim selection so the user can preview and
// then "Add Trimmed Clip", or tweak the in/out handles first.
function applySuggestedMoment(start, end) {
  const dur = trimState.duration || 0;
  let s = Math.max(0, Number(start) || 0);
  let e = dur ? Math.min(dur, Number(end) || 0) : (Number(end) || 0);
  if (e <= s) e = s + 1;
  trimState.start = s;
  trimState.end = e;
  if (typeof window.updateTrimUI === 'function') window.updateTrimUI();
  const tv = document.getElementById('trim-video');
  if (tv) { try { tv.currentTime = s; } catch (_) { /* not seekable yet */ } }
  showToast(`Loaded moment ${fmtTrimTime(s)}–${fmtTrimTime(e)} into the trimmer.`, 'info');
}

// "None" camera position = no facecam: the gameplay renders full-frame with no
// PiP overlay, so the camera-clip picker and size are irrelevant. Hide them and
// swap the helper hint so the no-facecam gaming path is obvious.
function updateCamPositionUI() {
  const pos = document.getElementById('cam-position')?.value;
  const noCam = pos === 'none';
  document.getElementById('cam-clip-row')?.classList.toggle('hidden', noCam);
  document.getElementById('cam-scale-row')?.classList.toggle('hidden', noCam);
  document.getElementById('cam-facecam-hint')?.classList.toggle('hidden', noCam);
  document.getElementById('cam-none-hint')?.classList.toggle('hidden', !noCam);
}

async function pickCameraClip() {
  let camPath = null;
  if (window.clipperAPI && window.clipperAPI.selectCameraClip) {
    camPath = await window.clipperAPI.selectCameraClip();
  } else {
    // Browser fallback (no Electron).
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = 'video/*';
    fileInput.onchange = () => {
      if (fileInput.files[0] && fileInput.files[0].path) {
        camPath = fileInput.files[0].path;
      }
    };
    fileInput.click();
  }

  if (camPath) {
    trimState.camVideo = camPath;
    document.getElementById('cam-path').value = camPath;
  }
}

async function addTrimmedClip() {
  if (!selectedVideo) return;
  if (!trimState.duration) {
    showAlert('Please wait for the video to finish loading first.');
    return;
  }
  if (trimState.end - trimState.start < 1) {
    showAlert('Please select at least 1 second of footage.');
    return;
  }

  const layout = document.getElementById('trim-layout').value;
  const camPosition = document.getElementById('cam-position').value;
  // "None" facecam: don't send a camera clip, so the server renders full-frame
  // gameplay with no PiP overlay even in the game_reaction layout.
  const useCam = layout === 'game_reaction' && camPosition !== 'none';
  const payload = {
    video_path: selectedVideo,
    start_seconds: trimState.start,
    end_seconds: trimState.end,
    layout,
    aspect_ratio: layout === 'full' ? null : '9:16',
    cam_video: useCam ? trimState.camVideo : null,
    cam_scale: parseFloat(document.getElementById('cam-scale').value || '0.3'),
    cam_position: camPosition === 'none' ? 'bottom-right' : camPosition,
  };

  const btn = document.getElementById('add-trim-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Rendering...';

  try {
    const res = await fetch(`${serverUrl}/render/custom`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || data.message || 'Render failed');
    }

    // Append the trimmed clip to the results grid as a light clip.
    const clip = {
      clip_id: `trim-${Date.now()}`,
      title: data.title || `Manual Clip ${generatedClips.length + 1}`,
      score: 0,
      start_time: data.start_seconds,
      end_time: data.end_seconds,
      duration: data.duration,
      hook_text: `Manual trim ${fmtTrimTime(data.start_seconds)} - ${fmtTrimTime(data.end_seconds)}`,
      output_file: data.clip_path,
      virality: { hook_score: '-', flow_score: '-', engagement_score: '-', trend_potential: 'Manual' },
      words: data.words || [],
      layout: "full",
      cam_video: null,
      cam_scale: 0.3,
      cam_position: "bottom-right",
      crop_x_offset: null,
      wordsAreRelative: true,  // Server already rebased words to clip-local (0-based)
    };
    generatedClips.push(clip);
    appendClipCard(clip, generatedClips.length - 1);
    saveCurrentProjectSilently();
    document.getElementById('results').classList.remove('hidden');
    setWizardStep(4);
    playSuccessSound();
    showAlert('✅ Trimmed clip rendered!');
  } catch (err) {
    playErrorSound();
    showAlert(`Trim failed: ${err.message || err}`);
  } finally {
    btn.disabled = false;
    btn.textContent = '➕ Add Trimmed Clip';
  }
}

// ------------------------------------------------------------------
// Clipping
// ------------------------------------------------------------------
let lastLoggedStep = '';

async function startClipping() {
  if (!selectedVideo) return;

  const btn = document.getElementById('start-clipping');
  btn.disabled = true;
  btn.textContent = '⏳ Processing...';

  const fileName = document.getElementById('file-name')?.textContent || 'Selected Video';
  const procTitle = document.getElementById('processing-video-title');
  if (procTitle) procTitle.textContent = `Analyzing "${fileName}" — extracting audio, transcribing, and scoring viral clips...`;

  const stageBadge = document.getElementById('progress-stage-badge');
  if (stageBadge) stageBadge.textContent = 'Initializing';
  const percentEl = document.getElementById('progress-percent');
  if (percentEl) percentEl.textContent = '0%';
  const stepEl = document.getElementById('progress-step');
  if (stepEl) stepEl.textContent = 'Initializing AI models...';
  const fillEl = document.getElementById('progress-fill');
  if (fillEl) fillEl.style.width = '0%';

  const logEl = document.getElementById('transcription-log');
  if (logEl) {
    lastLoggedStep = '';
    logEl.innerHTML = '<p class="muted">Started AI video processing pipeline...</p>';
  }

  setWizardStep(3);
  setProcessingActive(true);  // show the spinner immediately; pollJob keeps it on

  const payload = buildProcessPayload();

  try {
    const res = await fetch(`${serverUrl}/process`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `Server returned ${res.status}`);
    }
    if (data.job_id) {
      pollJob(data.job_id);
    } else {
      throw new Error(data.detail || 'Failed to start job');
    }
  } catch (e) {
    showError(e.message);
    // The button was disabled when clipping started; re-enable so the user can retry.
    const btn = document.getElementById('start-clipping');
    if (btn) { btn.disabled = false; btn.textContent = '🚀 Start Clipping & Transcribing'; }
    setWizardStep(2);
  }
}

let currentActiveJobId = null;

function pollJob(jobId) {
  if (pollTimer) clearInterval(pollTimer);
  currentActiveJobId = jobId;
  setProcessingActive(true);
  logActivity('▶️ Processing started', 'start');

  const cancelBtn = document.getElementById('cancel-active-job-btn');
  if (cancelBtn) {
    cancelBtn.style.display = 'inline-block';
    cancelBtn.disabled = false;
    cancelBtn.textContent = '🛑 Cancel Processing Job';
  }

  let ticking = false;
  let consecutiveErrors = 0;
  const stopPolling = () => {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    currentActiveJobId = null;
    setProcessingActive(false);
    const cb = document.getElementById('cancel-active-job-btn');
    if (cb) cb.style.display = 'none';
  };

  pollTimer = setInterval(async () => {
    if (ticking) return;
    ticking = true;
    try {
      const res = await fetch(`${serverUrl}/job/${jobId}`);
      // Without this, a 404 (job gone) or 500 fell through to the code below
      // with an empty body, so status never matched a terminal state and the
      // timer polled forever. Give the server a few misses, then give up.
      if (!res.ok) {
        consecutiveErrors++;
        if (res.status === 404 || consecutiveErrors >= 5) {
          stopPolling();
          showError(res.status === 404
            ? 'The processing job is no longer available on the server.'
            : `Lost contact with the server (HTTP ${res.status}).`);
        }
        return;
      }
      consecutiveErrors = 0;
      const data = await res.json();

      const stepText = data.step || 'Processing...';
      const progressNum = Math.round(data.progress || 0);

      const stepEl = document.getElementById('progress-step');
      if (stepEl) stepEl.textContent = stepText;
      const fillEl = document.getElementById('progress-fill');
      if (fillEl) fillEl.style.width = `${progressNum}%`;
      const percentEl = document.getElementById('progress-percent');
      if (percentEl) percentEl.textContent = `${progressNum}%`;
      const barEl = document.getElementById('progress-bar');
      if (barEl) {
        barEl.setAttribute('aria-valuenow', String(progressNum));
        barEl.setAttribute('aria-valuetext', `${progressNum}% — ${stepText}`);
      }

      // Update stage badge
      let stage = 'Processing';
      const stepLower = stepText.toLowerCase();
      if (stepLower.includes('transcrib') || stepLower.includes('whisper')) stage = 'Transcribing';
      else if (stepLower.includes('audio') || stepLower.includes('energy')) stage = 'Audio Analysis';
      else if (stepLower.includes('highlight') || stepLower.includes('score') || stepLower.includes('llm')) stage = 'AI Scoring';
      else if (stepLower.includes('face') || stepLower.includes('crop') || stepLower.includes('track')) stage = 'Smart Cropping';
      else if (stepLower.includes('caption') || stepLower.includes('render') || stepLower.includes('burn')) stage = 'Rendering Subtitles';
      const badgeEl = document.getElementById('progress-stage-badge');
      if (badgeEl) badgeEl.textContent = stage;

      // Append log entry if changed
      if (stepText && stepText !== lastLoggedStep) {
        lastLoggedStep = stepText;
        const logEl = document.getElementById('transcription-log');
        if (logEl) {
          const p = document.createElement('p');
          const timeStr = new Date().toLocaleTimeString([], { hour12: false });
          p.className = 'log-entry';
          p.innerHTML = `<span class="log-time">[${timeStr}]</span> <span>${escapeHtml(stepText)} (${progressNum}%)</span>`;
          logEl.appendChild(p);
          logEl.scrollTop = logEl.scrollHeight;
        }
      }

      if (data.status === 'completed') {
        stopPolling();
        logActivity(`✅ Done — ${(data.clips || []).length} clip(s) generated`, 'ok');
        showResults(data.clips);
      } else if (data.status === 'cancelled') {
        stopPolling();
        logActivity('🛑 Job cancelled', 'err');
        showError('Job was cancelled by user.');
      } else if (data.status === 'failed') {
        stopPolling();
        logActivity('❌ ' + (data.error || 'Processing failed'), 'err');
        showError(data.error || 'Processing failed');
      }
    } catch (e) {
      // Network blip (server briefly busy). Tolerate a few, then stop so we
      // never poll a dead endpoint forever.
      consecutiveErrors++;
      if (consecutiveErrors >= 8) {
        stopPolling();
        showError('Lost contact with the server while processing.');
      }
    } finally {
      ticking = false;
    }
  }, 650);
}

async function cancelActiveJob() {
  if (!currentActiveJobId) {
    showToast('No active job running to cancel.', 'info');
    return;
  }
  const cancelBtn = document.getElementById('cancel-active-job-btn');
  if (cancelBtn) {
    cancelBtn.disabled = true;
    cancelBtn.textContent = '⏳ Cancelling...';
  }
  try {
    const res = await fetch(`${serverUrl}/job/${currentActiveJobId}/cancel`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Server returned ${res.status}`);
    showToast(data.message || 'Cancellation requested', 'info');
  } catch (err) {
    showToast(`Failed to cancel job: ${err.message}`, 'error');
    const cancelBtn = document.getElementById('cancel-active-job-btn');
    if (cancelBtn) { cancelBtn.disabled = false; cancelBtn.textContent = '🛑 Cancel Processing Job'; }
  }
}

// ------------------------------------------------------------------
// Results rendering & Viral Insights
// ------------------------------------------------------------------
function showResults(clips) {
  playSuccessSound();
  generatedClips = clips || [];
  saveCurrentProjectSilently();
  const btn = document.getElementById('start-clipping');
  if (btn) {
    btn.disabled = false;
    btn.textContent = '🚀 Start Clipping & Transcribing';
  }

  // Paints the cards + the ready-to-render summary (iterates the normalized
  // array, not the raw argument: a completed job with no `clips` field used to
  // throw here on clips.forEach).
  renderClipsGrid();

  // Un-hide the results wrapper (selectVideoFile/reset mark it hidden).
  const resultsEl = document.getElementById('results');
  if (resultsEl) resultsEl.classList.remove('hidden');

  setWizardStep(4);
}

// Rich empty-state for the generated-clips grid: gives the user a clear way out
// (back to options / new project) instead of a dead-end blank page.
function renderClipsEmptyState(grid) {
  grid.innerHTML = `
    <div class="empty-state muted">
      <div class="empty-state-icon" aria-hidden="true">🎬</div>
      <h3>No clips yet</h3>
      <p>No clips were generated. Try lowering the minimum duration or enabling more detectors — or start a fresh project.</p>
      <div class="empty-state-actions">
        <button class="btn btn-secondary" id="empty-back-to-options">⬅️ Back to Clipping Options</button>
        <button class="btn btn-primary" id="empty-new-project">➕ Start New Project</button>
      </div>
    </div>`;
  grid.querySelector('#empty-back-to-options')?.addEventListener('click', () => setWizardStep(2));
  grid.querySelector('#empty-new-project')?.addEventListener('click', resetWizardToStep1);
}

function buildClipCard(clip, idx) {
  const card = document.createElement('div');
  card.className = 'clip-card';
  card.dataset.clipIdx = String(idx);

  const v = clip.virality || { hook_score: 8.5, flow_score: 8.0, engagement_score: 9.0, trend_potential: 'High' };
  const title = clip.title || (clip.hook_text ? clip.hook_text.slice(0, 48) : 'Highlight');
  // Prefer the AI-written social description (populated when the Ollama toggle
  // is on); fall back to the hook line / reason for the heuristic path.
  const desc = clip.description || clip.hook_text || clip.reason || 'AI-selected moment with strong virality signals.';
  const score = clip.score != null ? Number(clip.score).toFixed(1) : '–';
  // escapeHtml covers quotes, so a path containing " cannot break out of the
  // attribute and inject markup (e.g. an onerror handler).
  const posterAttr = clip.thumbnail_path
    ? `poster="${escapeHtml(fileUrl(clip.thumbnail_path))}"`
    : '';

  card.innerHTML = `
    <div class="clip-video-wrap">
      <video preload="metadata" playsinline ${posterAttr}></video>
      <button class="clip-mute-btn" type="button" data-action="mute" aria-label="Unmute preview" title="Unmute preview">🔇</button>
      <span class="clip-hover-hint">Hover to preview</span>
      <div class="clip-seek-row">
        <input type="range" class="clip-seek" min="0" max="1000" value="0" step="1" aria-label="Seek clip preview" />
        <span class="clip-seek-time">0:00 / 0:00</span>
      </div>
    </div>
    <div class="clip-info">
      <div class="clip-reorder-bar">
        <span class="clip-order-badge" title="Position in the exported reel">#${idx + 1}</span>
        <span class="clip-drag-handle" title="Drag to reorder this clip in the reel" aria-hidden="true">⠿</span>
        <span class="reorder-spacer"></span>
        <button class="btn btn-small clip-move-btn" data-action="move-earlier" title="Move earlier in the reel" aria-label="Move clip ${idx + 1} earlier in the reel">◀</button>
        <button class="btn btn-small clip-move-btn" data-action="move-later" title="Move later in the reel" aria-label="Move clip ${idx + 1} later in the reel">▶</button>
      </div>
      <div class="clip-headline">
        <div class="clip-title">${escapeHtml(title)}</div>
        <span class="virality-badge">🔥 Virality: ${score}/10</span>
        <span class="ready-badge" title="Rendered and ready to export/share">✅ Ready</span>
      </div>
      <div class="virality-metrics">
        <span class="metric-pill">Hook: <strong>${escapeHtml(String(v.hook_score))}</strong></span>
        <span class="metric-pill">Flow: <strong>${escapeHtml(String(v.flow_score))}</strong></span>
        <span class="metric-pill">Trend: <strong>${escapeHtml(String(v.trend_potential))}</strong></span>
      </div>
      <div class="clip-meta">${escapeHtml(String(clip.duration))}s duration</div>
      <div class="clip-desc">${escapeHtml(desc)}</div>
      <div class="clip-platforms" role="group" aria-label="Export for platform">
        <span class="platforms-label">Export for:</span>
        <button class="platform-tile" data-action="platform" data-platform="TikTok" data-ratio="9:16" title="TikTok — 9:16">🎵 TikTok</button>
        <button class="platform-tile" data-action="platform" data-platform="Reels" data-ratio="9:16" title="Instagram Reels — 9:16">📸 Reels</button>
        <button class="platform-tile" data-action="platform" data-platform="Shorts" data-ratio="9:16" title="YouTube Shorts — 9:16">▶️ Shorts</button>
        <button class="platform-tile" data-action="platform" data-platform="Instagram" data-ratio="4:5" title="Instagram feed — 4:5">🟪 IG Feed</button>
        <button class="platform-tile" data-action="platform" data-platform="X" data-ratio="1:1" title="X / Twitter — 1:1">✖️ X</button>
      </div>
    </div>
    <div class="clip-actions">
      <button class="btn btn-small" data-action="copy-hook" title="Copy hook title / opening line to clipboard">📋 Copy Hook</button>
      <button class="btn btn-small" data-action="social-meta" title="Generate AI Social Title, Description, and Hashtags">📱 Social Post</button>
      <button class="btn btn-small" data-action="pick-thumb" title="Generate AI Thumbnail poster from current video frame">🖼️ Pick Frame</button>
      <button class="btn btn-small" data-action="edit-captions">✏️ Edit Captions</button>
      <button class="btn btn-small" data-action="reroll-hook" title="Swap in a fresh hook for this clip and re-render it">🎣 New Hook</button>
      <button class="btn btn-small" data-action="remove-hook" title="Remove the burned-in intro hook and re-render this clip without it">🚫 Remove Hook</button>
      <button class="btn btn-small" data-action="multi-aspect" title="Render 9:16 + 1:1 + 4:5 + 16:9 in one pass">📐 Multi-Aspect</button>
      <button class="btn btn-small" data-action="overlay" title="Add B-roll video cutaway or reaction image">🎭 B-Roll</button>
      <button class="btn btn-small" data-action="snip-silence" title="Auto-cut dead air pauses">✂️ Snip Silence</button>
      <button class="btn btn-small" data-action="remove-fillers" title="Cut filler words (um, uh…) + dead air using the transcript">🧹 Fillers</button>
      <button class="btn btn-small" data-action="translate" title="Translate this clip's captions to another language (local AI)">🌐 Translate</button>
      <button class="btn btn-small" data-action="speakers" title="Detect who spoke when (optional — needs pyannote)">🗣 Speakers</button>
      <button class="btn btn-small" data-action="bleep" title="Bleep or mute profanity">🔇 Bleep</button>
      <button class="btn btn-small" data-action="open-folder">📂 Open</button>
      <button class="btn btn-small btn-danger" data-action="delete" title="Remove this clip">🗑 Delete</button>
      <select class="export-format-select clip-export-fmt" data-clip-idx="${idx}" title="Clip output format">
        <option value="mp4">📦 MP4 (H.264)</option>
        <option value="webm">🌐 WebM (VP9)</option>
        <option value="av1">⚡ AV1 (Next-Gen)</option>
        <option value="mov">🍏 MOV</option>
        <option value="mkv">🎬 MKV</option>
        <option value="gif">🖼️ GIF</option>
      </select>
      <button class="btn btn-small btn-export" data-action="export" data-clip-idx="${idx}">🚀 Export</button>
    </div>
  `;

  const video = card.querySelector('video');
  video.src = fileUrl(clip.output_file);
  video.muted = true;
  // The mute button reflects the real state: preview starts muted, so show 🔇.
  const muteBtn = card.querySelector('.clip-mute-btn');
  const syncMuteBtn = () => {
    if (!muteBtn) return;
    muteBtn.textContent = video.muted ? '🔇' : '🔊';
    muteBtn.setAttribute('aria-label', video.muted ? 'Unmute preview' : 'Mute preview');
    muteBtn.setAttribute('title', video.muted ? 'Unmute preview' : 'Mute preview');
  };
  syncMuteBtn();

  // Scrub bar: mirror playback position and let the user seek/preview any point.
  const seek = card.querySelector('.clip-seek');
  const seekTime = card.querySelector('.clip-seek-time');
  const fmtClock = (s) => {
    if (!Number.isFinite(s) || s < 0) s = 0;
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${String(sec).padStart(2, '0')}`;
  };
  const updateSeekLabel = () => {
    if (seekTime) seekTime.textContent = `${fmtClock(video.currentTime)} / ${fmtClock(video.duration)}`;
  };
  video.addEventListener('loadedmetadata', updateSeekLabel);
  video.addEventListener('timeupdate', () => {
    if (seek && video.duration && !seek.dataset.scrubbing) {
      seek.value = String(Math.round((video.currentTime / video.duration) * 1000));
    }
    updateSeekLabel();
  });
  if (seek) {
    const scrub = () => {
      if (!video.duration) return;
      seek.dataset.scrubbing = '1';
      video.currentTime = (Number(seek.value) / 1000) * video.duration;
      updateSeekLabel();
    };
    // Don't let clicks on the scrubber bubble up to the card action handler.
    seek.addEventListener('click', (e) => e.stopPropagation());
    seek.addEventListener('input', scrub);
    seek.addEventListener('change', () => { delete seek.dataset.scrubbing; });
    seek.addEventListener('mouseup', () => { delete seek.dataset.scrubbing; });
  }

  card.addEventListener('mouseenter', () => video.play().catch(() => {}));
  card.addEventListener('mouseleave', () => { video.pause(); });

  card.addEventListener('click', (e) => {
    const actionBtn = e.target.closest('[data-action]');
    if (!actionBtn) return;
    const idx2 = parseInt(card.dataset.clipIdx, 10);
    switch (actionBtn.dataset.action) {
      case 'copy-hook': copyClipHook(idx2); break;
      case 'social-meta': openSocialMetaModal(idx2); break;
      case 'pick-thumb': pickClipThumbnail(idx2, video); break;
      case 'edit-captions': openCaptionEditor(idx2); break;
      case 'reroll-hook': quickRerollHook(idx2, actionBtn); break;
      case 'remove-hook': quickRemoveHook(idx2, actionBtn); break;
      case 'move-earlier': e.stopPropagation(); moveClip(idx2, idx2 - 1); break;
      case 'move-later': e.stopPropagation(); moveClip(idx2, idx2 + 1); break;
      case 'multi-aspect': exportMultiAspectPack(idx2); break;
      case 'overlay': openOverlayModal(idx2); break;
      case 'snip-silence': quickCutSilence(idx2); break;
      case 'remove-fillers': quickRemoveFillers(idx2, actionBtn); break;
      case 'translate': openTranslateModal(idx2); break;
      case 'speakers': detectSpeakers(idx2, actionBtn); break;
      case 'bleep': quickBleepClip(idx2); break;
      case 'open-folder': revealInFolder(clip.output_file); break;
      case 'mute':
        e.stopPropagation();
        video.muted = !video.muted;
        // If the user unmutes while hovering, make sure audio is actually playing.
        if (!video.muted && video.paused) video.play().catch(() => {});
        syncMuteBtn();
        break;
      case 'export': exportSingleClip(idx2); break;
      case 'platform':
        e.stopPropagation();
        exportForPlatform(idx2, actionBtn.dataset.platform, actionBtn.dataset.ratio, actionBtn);
        break;
    }
  });

  const deleteBtn = card.querySelector('[data-action="delete"]');
  deleteBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    const confirmed = await showConfirm(`Delete "${title || 'this clip'}"? This removes it from the project and deletes its rendered files.`);
    if (!confirmed) return;
    deleteClip(parseInt(card.dataset.clipIdx, 10));
  });

  wireClipDragReorder(card);

  return card;
}

// ------------------------------------------------------------------
// Clip reordering (drag & drop + keyboard/click) — the order here IS the
// order used by "Export All as Reel", which concatenates generatedClips.
// ------------------------------------------------------------------
let draggingClipIdx = null;

// Drag is armed only from the grip handle so the scrub bar, buttons and
// hover-preview keep working normally everywhere else on the card.
function wireClipDragReorder(card) {
  const handle = card.querySelector('.clip-drag-handle');
  if (handle) {
    handle.addEventListener('mousedown', () => { card.draggable = true; });
    handle.addEventListener('mouseup', () => { card.draggable = false; });
  }

  card.addEventListener('dragstart', (e) => {
    draggingClipIdx = parseInt(card.dataset.clipIdx, 10);
    card.classList.add('dragging');
    if (e.dataTransfer) {
      e.dataTransfer.effectAllowed = 'move';
      // Some platforms cancel the drag unless data is set.
      try { e.dataTransfer.setData('text/plain', String(draggingClipIdx)); } catch (_) { /* non-fatal */ }
    }
  });

  card.addEventListener('dragend', () => {
    card.classList.remove('dragging');
    card.draggable = false;
    draggingClipIdx = null;
    document.querySelectorAll('.clip-card.drop-target')
      .forEach((c) => c.classList.remove('drop-target'));
  });

  card.addEventListener('dragover', (e) => {
    if (draggingClipIdx === null) return;
    e.preventDefault();                     // required to allow a drop
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
    if (parseInt(card.dataset.clipIdx, 10) !== draggingClipIdx) {
      card.classList.add('drop-target');
    }
  });

  card.addEventListener('dragleave', () => card.classList.remove('drop-target'));

  card.addEventListener('drop', (e) => {
    e.preventDefault();
    card.classList.remove('drop-target');
    const to = parseInt(card.dataset.clipIdx, 10);
    let from = draggingClipIdx;
    if (from === null && e.dataTransfer) {
      const raw = parseInt(e.dataTransfer.getData('text/plain'), 10);
      if (Number.isInteger(raw)) from = raw;
    }
    if (Number.isInteger(from)) moveClip(from, to);
  });
}

// Move a clip to a new position and re-render. Clamps silently so the ◀/▶
// buttons on the first/last card are simply no-ops.
function moveClip(from, to) {
  if (!Number.isInteger(from) || !Number.isInteger(to)) return;
  if (from === to || from < 0 || from >= generatedClips.length) return;
  if (to < 0 || to >= generatedClips.length) return;

  const [moved] = generatedClips.splice(from, 1);
  generatedClips.splice(to, 0, moved);
  saveCurrentProjectSilently();
  renderClipsGrid();
  showToast(`↕️ Moved "${(moved.title || 'clip').slice(0, 30)}" to position ${to + 1}`, 'success');
}

// Single source of truth for painting the grid + summary, so reorder, delete
// and the initial render can't drift apart.
function renderClipsGrid() {
  const grid = document.getElementById('clips-grid');
  if (!grid) return;
  grid.innerHTML = '';
  if (!generatedClips.length) {
    renderClipsEmptyState(grid);
  } else {
    generatedClips.forEach((clip, i) => grid.appendChild(buildClipCard(clip, i)));
  }
  updateClipsSummary();
}

function updateClipsSummary() {
  const summary = document.getElementById('results-summary');
  if (!summary) return;
  const n = generatedClips.length;
  if (!n) { summary.textContent = 'No clips yet.'; return; }
  const totalSecs = generatedClips.reduce((a, c) => a + (Number(c.duration) || 0), 0);
  summary.textContent = `✅ ${n} clip${n === 1 ? '' : 's'} ready to export · ${Math.round(totalSecs)}s total · drag to reorder the reel`;
}

function copyClipHook(idx) {
  const clip = generatedClips[idx];
  if (!clip) return;
  const hook = (clip.hook_text || clip.title || '').trim();
  if (!hook) {
    showAlert('No hook text available for this clip.');
    return;
  }
  navigator.clipboard.writeText(hook).then(() => {
    showToast(`📋 Copied hook to clipboard: "${hook.slice(0, 40)}..."`, 'success');
  }).catch(() => {
    showAlert(`Hook: ${hook}`, 'Clip Hook');
  });
}

// Pick Frame: generate a few scored candidate cover frames and let the user
// choose one to set as the poster or download as an image (png/jpg/webp).
let thumbState = { clipIdx: null, video: null, selected: null };

async function pickClipThumbnail(idx, videoEl) {
  const clip = generatedClips[idx];
  if (!clip || !clip.output_file) return;
  thumbState = { clipIdx: idx, video: videoEl, selected: null };
  const wrap = document.getElementById('thumb-candidates');
  if (wrap) wrap.innerHTML = '<p class="muted small">⏳ Finding the best frames…</p>';
  document.getElementById('thumb-modal')?.classList.remove('hidden');
  try {
    const res = await fetch(`${serverUrl}/export/thumbnail-candidates`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_path: clip.output_file, count: 3 }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Failed to generate candidates');
    renderThumbCandidates(data.candidates || []);
  } catch (err) {
    if (wrap) wrap.innerHTML = `<p class="muted small">Couldn't generate candidates: ${escapeHtml(err.message)}</p>`;
  }
}

function renderThumbCandidates(cands) {
  const wrap = document.getElementById('thumb-candidates');
  if (!wrap) return;
  if (!cands.length) { wrap.innerHTML = '<p class="muted small">No candidate frames found.</p>'; return; }
  wrap.innerHTML = cands.map((c, i) => `
    <button type="button" class="thumb-candidate${i === 0 ? ' selected' : ''}" data-ts="${c.timestamp}" data-path="${escapeHtml(c.path)}">
      <img src="${escapeHtml(fileUrl(c.path, true))}" alt="Candidate frame at ${c.timestamp}s" />
      <span class="thumb-ts">${Number(c.timestamp).toFixed(1)}s</span>
    </button>`).join('');
  // Default-select the first (highest-scored) candidate.
  thumbState.selected = { path: cands[0].path, timestamp: cands[0].timestamp };
  wrap.querySelectorAll('.thumb-candidate').forEach((btn) => {
    btn.addEventListener('click', () => {
      wrap.querySelectorAll('.thumb-candidate').forEach((b) => b.classList.remove('selected'));
      btn.classList.add('selected');
      thumbState.selected = { path: btn.dataset.path, timestamp: parseFloat(btn.dataset.ts) };
    });
  });
}

document.getElementById('thumb-close')?.addEventListener('click', () => {
  document.getElementById('thumb-modal')?.classList.add('hidden');
});

document.getElementById('thumb-set-poster')?.addEventListener('click', () => {
  const clip = generatedClips[thumbState.clipIdx];
  if (!clip || !thumbState.selected) { showToast('Pick a frame first', 'info'); return; }
  clip.thumbnail_path = thumbState.selected.path;
  if (thumbState.video) thumbState.video.setAttribute('poster', fileUrl(thumbState.selected.path, true));
  saveCurrentProjectSilently();
  playSuccessSound();
  showToast('🖼️ Cover frame set', 'success');
  document.getElementById('thumb-modal')?.classList.add('hidden');
});

document.getElementById('thumb-download')?.addEventListener('click', async () => {
  const clip = generatedClips[thumbState.clipIdx];
  if (!clip || !thumbState.selected) { showToast('Pick a frame first', 'info'); return; }
  const fmt = document.getElementById('thumb-format')?.value || 'png';
  const folder = await chooseExportFolder();
  if (!folder) return;
  const btn = document.getElementById('thumb-download');
  const orig = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Saving…'; }
  try {
    const res = await fetch(`${serverUrl}/export/thumbnail-save`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_path: clip.output_file,
        timestamp: thumbState.selected.timestamp,
        output_dir: folder,
        image_format: fmt,
        title: clip.title || clip.hook_text || 'thumbnail',
      }),
    });
    const data = await res.json();
    if (res.ok) {
      playSuccessSound();
      revealInFolder(data.path);
      showToast('⬇️ Thumbnail saved', 'success');
    } else {
      showAlert(`Thumbnail save failed: ${data.detail || 'error'}`);
    }
  } catch (e) {
    showAlert(`Error: ${e.message}`);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = orig; }
  }
});

async function openSocialMetaModal(idx) {
  const clip = generatedClips[idx];
  if (!clip) return;
  
  try {
    const res = await fetch(`${serverUrl}/social/metadata`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: clip.title || '',
        hook_text: clip.hook_text || '',
        full_text: clip.full_text || clip.reason || '',
        duration: clip.duration || 0,
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Failed to generate metadata');
    
    // Copy directly to clipboard and show full prompt in modal/toast
    navigator.clipboard.writeText(data.formatted_post).then(() => {
      showToast('📱 Generated & Copied ready-to-publish social post + hashtags to clipboard!', 'success');
    }).catch(() => {});
    
    showAlert(
      `📝 TITLE:\n${data.title}\n\n` +
      `📋 HASHTAGS:\n${data.hashtags.join(' ')}\n\n` +
      `📦 COMPLETE POST (Copied to Clipboard):\n\n${data.formatted_post}\n\n` +
      `⚠️ Disclaimer: ${data.disclaimer}`,
      '📱 AI Social Media Post Bundle'
    );
  } catch (err) {
    showAlert(`Error generating social metadata: ${err.message}`);
  }
}

// Collect the rendered artifacts (video + sidecar subs) for a single clip so a
// grid deletion can remove the files too, not just the card + manifest entry.
function clipArtifactPaths(clip) {
  const paths = [];
  if (!clip) return paths;
  if (clip.output_file) paths.push(clip.output_file);
  if (clip.srt_path) paths.push(clip.srt_path);
  if (clip.vtt_path) paths.push(clip.vtt_path);
  if (clip.ass_path) paths.push(clip.ass_path);
  if (clip.thumbnail_path) paths.push(clip.thumbnail_path);
  return paths;
}

async function deleteClip(idx) {
  if (!generatedClips.length) return;
  const [removed] = generatedClips.splice(idx, 1);
  // Remove the clip from the saved project AND delete its rendered footage.
  // The server only deletes inside the output folder, never the source video.
  saveCurrentProjectSilently();
  const paths = clipArtifactPaths(removed);
  if (paths.length) {
    try {
      await fetch(`${serverUrl}/project/delete`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths }),
      });
    } catch (_) { /* card + manifest removal still proceed */ }
  }
  // Re-index the remaining cards (order badges + move buttons depend on it).
  renderClipsGrid();
}

function appendClipCard(clip, idx) {
  const grid = document.getElementById('clips-grid');
  grid.appendChild(buildClipCard(clip, idx));
}

// ------------------------------------------------------------------
// NLE Project Export (Premiere Pro, DaVinci Resolve, CapCut)
// ------------------------------------------------------------------
async function exportProject(format) {
  if (!selectedVideo || !generatedClips.length) {
    playErrorSound();
    showAlert("Please generate clips first before exporting a project timeline.");
    return;
  }

  const exportFolder = await chooseExportFolder();
  if (!exportFolder) return;
  try {
    const res = await fetch(`${serverUrl}/export/project`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_path: selectedVideo,
        clips: generatedClips,
        format: format,
        fps: 30.0,
        output_dir: exportFolder,
      })
    });
    const data = await res.json();
    if (res.ok) {
      playSuccessSound();
      showAlert(`✅ ${data.message}\nSaved at: ${data.export_path}`, 'Export Complete', data.export_path || null);
    } else {
      playErrorSound();
      showAlert(`Export failed: ${data.detail}`);
    }
  } catch (err) {
    playErrorSound();
    showAlert(`Export error: ${err.message}`);
  }
}

document.getElementById('export-premiere')?.addEventListener('click', () => exportProject('fcpxml'));
document.getElementById('export-davinci')?.addEventListener('click', () => exportProject('edl'));
document.getElementById('export-capcut')?.addEventListener('click', () => exportProject('capcut'));

// ------------------------------------------------------------------
// Export Standalone Assets (Audio Only MP3/WAV/FLAC/AAC/M4A, Subtitles SRT/VTT)
// ------------------------------------------------------------------
async function exportStandaloneAsset(ev) {
  if (!selectedVideo) {
    playErrorSound();
    showAlert("Please select and load a video file first.");
    return;
  }
  // The clicked button points at its own <select> via data-select (audio vs
  // captions), so one handler drives both grouped export boxes.
  const btn = ev?.currentTarget || document.getElementById('export-audio-btn');
  const selectId = btn?.dataset?.select || 'standalone-audio';
  const sel = document.getElementById(selectId);
  const assetType = sel ? sel.value : 'audio_mp3';
  // Let the user choose the destination folder, matching the per-clip Export.
  const exportFolder = await chooseExportFolder();
  if (!exportFolder) return;
  const originalLabel = btn ? btn.textContent : '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = '⏳ Exporting…';
  }

  try {
    const res = await fetch(`${serverUrl}/export/standalone`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_path: selectedVideo,
        asset_type: assetType,
        output_dir: exportFolder,
      })
    });
    const data = await res.json();
    if (res.ok) {
      playSuccessSound();
      showAlert(`✅ ${data.message}\nSaved at: ${data.export_path}`, 'Export Complete', data.export_path || null);
    } else {
      playErrorSound();
      showAlert(`Standalone export failed: ${data.detail}`);
    }
  } catch (err) {
    playErrorSound();
    showAlert(`Export error: ${err.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = originalLabel || '💾 Export';
    }
  }
}

document.getElementById('export-audio-btn')?.addEventListener('click', exportStandaloneAsset);
document.getElementById('export-subs-btn')?.addEventListener('click', exportStandaloneAsset);

// ------------------------------------------------------------------
// Export-As Media (single clip -> mp4/mov/mkv/webm/gif)
// ------------------------------------------------------------------
function safeFileName(value) {
  return String(value || 'clip').replace(/[^a-z0-9 _-]/gi, '').trim().replace(/\s+/g, '_').slice(0, 70) || 'clip';
}

async function chooseExportFolder(clip) {
  const configured = document.getElementById('output-folder-input')?.value?.trim();
  // Always let the user choose where THIS export lands, starting from the
  // configured output folder (if any). Previously a configured folder was used
  // silently and the picker never opened.
  if (window.clipperAPI?.selectOutputFolder) {
    const picked = await window.clipperAPI.selectOutputFolder(configured || undefined);
    return picked || null;   // null → user cancelled, so the export is aborted
  }
  // Non-Electron fallback (window.prompt is unavailable in Electron).
  const fallback = window.prompt?.('Choose a folder for this exported clip bundle:', configured || '');
  return fallback?.trim() || configured || null;
}

window.exportSingleClip = async function (clipIndex) {
  const clip = generatedClips[clipIndex];
  if (!clip || !clip.output_file) {
    playErrorSound();
    showAlert('No rendered clip to export yet.');
    return;
  }
  const sel = document.querySelector(`.clip-export-fmt[data-clip-idx="${clipIndex}"]`);
  const fmt = sel ? sel.value : 'mp4';
  const exportFolder = await chooseExportFolder(clip);
  if (!exportFolder) return;
  const btn = document.querySelector(`.btn-export[data-clip-idx="${clipIndex}"]`);
  if (btn) {
    btn.disabled = true;
    btn.textContent = '⏳ Exporting…';
  }
  try {
    const res = await fetch(`${serverUrl}/export/clip-bundle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_path: clip.output_file,
        output_dir: exportFolder,
        title: clip.title || clip.hook_text || 'clip',
        format: fmt,
        srt_path: clip.srt_path,
        ass_path: clip.ass_path,
      })
    });
    const data = await res.json();
    if (res.ok) {
      playSuccessSound();
      // /export/clip-bundle returns export_dir + video_path (not export_path):
      // reading export_path here is what produced the "Saved at: undefined" bug.
      const savedPath = data.export_dir || data.video_path || '';
      // Auto-open the destination the user picked, then show the confirmation
      // (which also keeps an "Open folder" button for reopening later).
      if (savedPath) revealInFolder(savedPath);
      showAlert(`✅ ${data.message}\nSaved at: ${savedPath}`, 'Export Complete', savedPath || null);
    } else {
      playErrorSound();
      showAlert(`Export failed: ${data.detail}`);
    }
  } catch (err) {
    playErrorSound();
    showAlert(`Export error: ${err.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = '🚀 Export';
    }
  }
};

// ------------------------------------------------------------------
// Export-All-as-Reel (compile all clips into one media file)
// ------------------------------------------------------------------
async function exportCompileReel() {
  if (!generatedClips.length) {
    playErrorSound();
    showAlert('Please generate clips first before compiling a reel.');
    return;
  }
  const fmt = document.getElementById('compile-format') ? document.getElementById('compile-format').value : 'mp4';
  // Let the user choose where the reel lands, just like the per-clip Export.
  const exportFolder = await chooseExportFolder();
  if (!exportFolder) return;
  const btn = document.getElementById('export-compile');
  if (btn) {
    btn.disabled = true;
    btn.textContent = '⏳ Compiling reel…';
  }
  try {
    const clipPaths = generatedClips.map((c) => c.output_file).filter(Boolean);
    const res = await fetch(`${serverUrl}/export/compile`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        clip_paths: clipPaths,
        format: fmt,
        title: 'highlights_reel',
        output_dir: exportFolder,
      })
    });
    const data = await res.json();
    if (res.ok) {
      playSuccessSound();
      showAlert(`✅ ${data.message}\nSaved at: ${data.export_path}`, 'Export Complete', data.export_path || null);
    } else {
      playErrorSound();
      showAlert(`Compile failed: ${data.detail}`);
    }
  } catch (err) {
    playErrorSound();
    showAlert(`Compile error: ${err.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = '🎬 Export All as Reel';
    }
  }
}

// ------------------------------------------------------------------
// Caption preset preview
// Converts ASS colors (&HB BG R R) to CSS and mirrors the preset's
// primary + highlight so the style bar shows a live sample.
// ------------------------------------------------------------------
const CAPTION_PREVIEW = {
  viral_yellow:  { text: '#ffffff', accent: '#ffd21e', back: '#000000', font: 'Arial Black' },
  neon_green:    { text: '#ffffff', accent: '#1bff3a', back: '#111111', font: 'Impact' },
  bold_white:    { text: '#e0e0e0', accent: '#ffffff', back: '#000000', font: 'Montserrat, Arial' },
  cyberpunk_cyan:{ text: '#64e6ff', accent: '#ff40d0', back: '#050515', font: 'Arial Black' },
  tiktok_pop:    { text: '#ffd21e', accent: '#ff2a3a', back: '#000000', font: 'Arial Black' },
  fire_red:      { text: '#ffffff', accent: '#ff4530', back: '#000000', font: 'Impact' },
  retro_vaporwave:{ text: '#e0b0ff', accent: '#ffff59', back: '#330033', font: 'Trebuchet MS' },
  mrbeast_impact:{ text: '#ffffff', accent: '#ffd21e', back: '#000000', font: 'Impact' },
  pastel_pink:   { text: '#ffffff', accent: '#ffa4d8', back: '#2e1b33', font: 'Arial' },
  minimalist_dark:{ text: '#f0f0f0', accent: '#ff8a4d', back: '#000000', font: 'Helvetica' },
  comic_punch:   { text: '#ffd21e', accent: '#ffffff', back: '#000000', font: 'Impact' },
  golden_hour:   { text: '#fff0e6', accent: '#ffa510', back: '#1a1005', font: 'Arial Black' },
  electric_purple:{ text: '#ffffff', accent: '#b833ff', back: '#1b0324', font: 'Arial Black' },
  sunset_orange: { text: '#ffffff', accent: '#ff7b14', back: '#050905', font: 'Impact' },
  matrix_green:  { text: '#00cc33', accent: '#80ff80', back: '#001500', font: 'Courier New' },
  deep_blue:     { text: '#ffffff', accent: '#33aaff', back: '#051024', font: 'Arial Black' },
  boxed_karaoke: { text: '#dddddd', accent: '#ffe500', back: '#000000', font: 'Arial' },
  glitch_shadow: { text: '#ffffff', accent: '#ff30e0', back: '#000000', font: 'Arial Black' },
  elegant_serif: { text: '#f5f5f5', accent: '#ff6bd3', back: '#1c1c1c', font: 'Georgia' },
  high_contrast: { text: '#000000', accent: '#ffcc00', back: '#000000', font: 'Arial Black' },
  monochrome_chic:{ text: '#888888', accent: '#ffffff', back: '#111111', font: 'Helvetica' },
  gaming_rgb:    { text: '#80ffaa', accent: '#ff20b0', back: '#000000', font: 'Impact' },
};

function captionFontSize() {
  // The generated-clips toolbar is the single source of truth for both the
  // initial render and the caption editor preview.
  const slider = document.getElementById('generated-caption-font-size');
  const n = parseFloat(slider ? slider.value : '40');
  return Number.isFinite(n) ? n : 40;
}

function globalCaptionFontSize() {
  const slider = document.getElementById('generated-caption-font-size');
  const n = parseFloat(slider ? slider.value : '40');
  return Number.isFinite(n) ? n : 40;
}

function collectCaptionOptions() {
  const preset = document.getElementById('generated-caption-preset')?.value || 'viral_yellow';
  const fontSize = globalCaptionFontSize();
  const fontName = document.getElementById('caption-font-name')?.value || null;
  const primaryColor = document.getElementById('caption-primary-color')?.value || null;
  const highlightColor = document.getElementById('caption-highlight-color')?.value || null;
  const outlineColor = document.getElementById('caption-outline-color')?.value || null;
  const outlineWidthVal = document.getElementById('caption-outline-width')?.value;
  const outlineWidth = outlineWidthVal !== undefined && outlineWidthVal !== '' ? parseInt(outlineWidthVal, 10) : null;
  const positionVal = document.getElementById('caption-position')?.value;
  const position = positionVal ? parseInt(positionVal, 10) : null;
  const chunkSizeVal = document.getElementById('caption-chunk-size')?.value;
  const chunkSize = chunkSizeVal && chunkSizeVal !== '0' ? parseInt(chunkSizeVal, 10) : null;
  const uppercase = document.getElementById('caption-uppercase')?.checked || false;
  const bold = document.getElementById('caption-bold')?.checked || false;
  const italic = document.getElementById('caption-italic')?.checked || false;
  const introEnabled = document.getElementById('caption-intro-enabled')?.checked || false;
  const introCaption = introEnabled ? (document.getElementById('caption-intro-text')?.value || '').trim() : null;
  const introCaptionDuration = parseFloat(document.getElementById('caption-intro-duration')?.value || '3');

  return {
    caption_style: preset,
    style_preset: preset,
    font_size: fontSize,
    font_name: fontName || undefined,
    primary_color: primaryColor || undefined,
    highlight_color: highlightColor || undefined,
    outline_color: outlineColor || undefined,
    outline_width: Number.isFinite(outlineWidth) ? outlineWidth : undefined,
    position: Number.isFinite(position) ? position : undefined,
    chunk_size: Number.isFinite(chunkSize) ? chunkSize : undefined,
    uppercase,
    bold,
    italic,
    intro_caption: introCaption || undefined,
    intro_caption_duration: Number.isFinite(introCaptionDuration) ? introCaptionDuration : 3,
    // Flag so the backend auto-generates a hook per clip when the toggle is on
    // but the optional custom text is left blank.
    intro_enabled: introEnabled,
    // Optional bigger font for the intro hook (from the caption editor's slider).
    intro_font_size: parseInt(document.getElementById('generated-intro-font-size')?.value || '', 10) || undefined,
  };
}

// Live preview of the intro hook at the top of the caption stage, styled with
// the current preset/colors and sized by the hook font-size slider.
function updateHookPreview() {
  const el = document.getElementById('hook-preview');
  if (!el) return;
  const raw = (document.getElementById('edit-intro-hook')?.value || '').trim();
  if (!raw) { el.classList.add('hidden'); el.textContent = ''; return; }
  el.classList.remove('hidden');
  const isUpper = document.getElementById('caption-uppercase')?.checked;
  el.textContent = isUpper ? raw.toUpperCase() : raw;

  const presetId = document.getElementById('generated-caption-preset')?.value || 'viral_yellow';
  const base = CAPTION_PREVIEW[presetId] || CAPTION_PREVIEW.viral_yellow;
  const primary = document.getElementById('caption-primary-color')?.value || base.text;
  const accent = document.getElementById('caption-highlight-color')?.value || base.accent;
  const stroke = document.getElementById('caption-outline-color')?.value || base.back;
  const font = document.getElementById('caption-font-name')?.value || base.font;
  const hookSize = parseInt(document.getElementById('intro-hook-font-size')?.value || '64', 10);

  const stage = el.parentElement;
  const ratio = document.getElementById('clip-aspect-ratio')?.value || '9:16';
  const canvasWidth = ratio === '16:9' ? 1920 : 1080;
  const stageStyle = stage ? getComputedStyle(stage) : null;
  const padX = stageStyle
    ? (parseFloat(stageStyle.paddingLeft) || 0) + (parseFloat(stageStyle.paddingRight) || 0)
    : 28;
  const previewWidth = (stage && stage.clientWidth > 0 ? stage.clientWidth : 540) - padX;
  const scale = previewWidth / canvasWidth;
  // Keep the hook overlay bounded so a long title used as a hook can't flood
  // the top of the stage and reach the caption.
  const stageMinHeight = stageStyle ? (parseFloat(stageStyle.minHeight) || 132) : 132;
  const maxHookFont = Math.max(12, stageMinHeight / 4);

  el.style.color = primary;
  el.style.fontFamily = font;
  el.style.fontSize = `${Math.max(10, Math.round(Math.min(hookSize * scale, maxHookFont)))}px`;
  el.style.fontWeight = document.getElementById('caption-bold')?.checked ? '900' : '800';
  const w = parseInt(document.getElementById('caption-outline-width')?.value || '3', 10);
  const o = stroke || '#000000';
  el.style.textShadow = w > 0
    ? `${w}px 0 0 ${o}, -${w}px 0 0 ${o}, 0 ${w}px 0 ${o}, 0 -${w}px 0 ${o}, 0 0 14px ${accent}55`
    : `0 0 14px ${accent}55`;
}

function applyCaptionPreviewStyle() {
  const preview = document.getElementById('caption-preview');
  if (!preview) return;
  const presetId = document.getElementById('generated-caption-preset')?.value || 'viral_yellow';
  const baseStyle = CAPTION_PREVIEW[presetId] || CAPTION_PREVIEW.viral_yellow;

  const fontName = document.getElementById('caption-font-name')?.value;
  const primaryColor = document.getElementById('caption-primary-color')?.value;
  const highlightColor = document.getElementById('caption-highlight-color')?.value;
  const outlineColor = document.getElementById('caption-outline-color')?.value;
  const outlineWidth = parseInt(document.getElementById('caption-outline-width')?.value || '3', 10);
  const isUppercase = document.getElementById('caption-uppercase')?.checked;
  const isBold = document.getElementById('caption-bold')?.checked;
  const isItalic = document.getElementById('caption-italic')?.checked;

  const font = fontName || baseStyle.font;
  const textColor = primaryColor || baseStyle.text;
  const accentColor = highlightColor || baseStyle.accent;
  const strokeColor = outlineColor || baseStyle.back;

  preview.style.color = textColor;
  preview.style.fontFamily = font;
  const previewStage = preview.parentElement;
  const ratio = document.getElementById('clip-aspect-ratio')?.value || '9:16';
  const canvasWidth = ratio === '16:9' ? 1920 : 1080;
  // Map the caption to the stage's *usable* width (minus horizontal padding) so
  // it lands in the same 1080/1920-px coordinate space the exporter uses.
  const stageStyle = previewStage ? getComputedStyle(previewStage) : null;
  const padX = stageStyle
    ? (parseFloat(stageStyle.paddingLeft) || 0) + (parseFloat(stageStyle.paddingRight) || 0)
    : 28;
  const previewWidth = (previewStage?.clientWidth > 0 ? previewStage.clientWidth : 540) - padX;
  const previewScale = previewWidth / canvasWidth;
  // The stage is a short landscape box, so a purely width-based scale can look
  // oversized. Clamp to a fraction of the stage's *fixed* min-height (not its
  // live height, which would grow as text wraps and defeat the clamp) so the
  // preview reads like one or two caption lines and never balloons.
  const stageMinHeight = stageStyle ? (parseFloat(stageStyle.minHeight) || 90) : 90;
  const padY = stageStyle
    ? (parseFloat(stageStyle.paddingTop) || 0) + (parseFloat(stageStyle.paddingBottom) || 0)
    : 28;
  const maxPreviewFont = Math.max(14, (stageMinHeight - padY) / 1.5);
  const scaledFont = Math.min(captionFontSize() * previewScale, maxPreviewFont);
  preview.style.fontSize = `${Math.max(1, Math.round(scaledFont))}px`;
  preview.style.fontWeight = isBold ? '900' : 'normal';
  preview.style.fontStyle = isItalic ? 'italic' : 'normal';
  preview.style.textTransform = isUppercase ? 'uppercase' : 'none';

  if (outlineWidth > 0) {
    const o = strokeColor || '#000000';
    const w = outlineWidth;
    preview.style.textShadow = `${w}px 0 0 ${o}, -${w}px 0 0 ${o}, 0 ${w}px 0 ${o}, 0 -${w}px 0 ${o}, ${w}px ${w}px 0 ${o}, -${w}px -${w}px 0 ${o}, ${w}px -${w}px 0 ${o}, -${w}px ${w}px 0 ${o}, 0 0 16px ${accentColor}55`;
  } else {
    preview.style.textShadow = `0 0 16px ${accentColor}55`;
  }

  const highlights = preview.querySelectorAll('mark');
  highlights.forEach((m) => {
    m.style.color = accentColor;
    m.style.background = 'transparent';
  });
  applyPortraitCaptionPreviewStyle();
  updateHookPreview();  // keep the hook preview in sync with style changes
}

function refreshCaptionPreview() {
  const preview = document.getElementById('caption-preview');
  if (!preview) return;
  const rawWords = preview.dataset.words || 'Your caption appears here';
  const isUppercase = document.getElementById('caption-uppercase')?.checked;
  const words = isUppercase ? rawWords.toUpperCase() : rawWords;


  // Render words, highlighting every few so the accent shows like a karaoke lead.

  const list = words.split(' ').map((w, i) => (i % 3 === 0 ? `<mark>${escapeHtml(w)}</mark>` : escapeHtml(w)));
  preview.innerHTML = list.join(' ');
  preview.dataset.words = rawWords;
  refreshPortraitCaptionPreview();
  applyCaptionPreviewStyle();
}

function applyPortraitCaptionPreviewStyle() {
  const preview = document.getElementById('portrait-caption-preview');
  if (!preview) return;
  const presetId = document.getElementById('generated-caption-preset')?.value || 'viral_yellow';
  const baseStyle = CAPTION_PREVIEW[presetId] || CAPTION_PREVIEW.viral_yellow;

  const fontName = document.getElementById('caption-font-name')?.value;
  const primaryColor = document.getElementById('caption-primary-color')?.value;
  const highlightColor = document.getElementById('caption-highlight-color')?.value;
  const outlineColor = document.getElementById('caption-outline-color')?.value;
  const outlineWidth = parseInt(document.getElementById('caption-outline-width')?.value || '3', 10);
  const isBold = document.getElementById('caption-bold')?.checked;
  const isItalic = document.getElementById('caption-italic')?.checked;
  const isUppercase = document.getElementById('caption-uppercase')?.checked;

  const font = fontName || baseStyle.font;
  const textColor = primaryColor || baseStyle.text;
  const accentColor = highlightColor || baseStyle.accent;
  const strokeColor = outlineColor || baseStyle.back;

  const positionVal = document.getElementById('caption-position')?.value || '2';
  const alignClass = ['1'].includes(positionVal) ? 'caption-align-left' : (['3'].includes(positionVal) ? 'caption-align-right' : 'caption-align-center');
  const verticalClass = ['8'].includes(positionVal) ? 'caption-pos-top' : (['2', '1', '3'].includes(positionVal) ? 'caption-pos-bottom' : 'caption-pos-middle');
  const screenEl = preview.parentElement;
  if (screenEl) {
    screenEl.classList.remove('caption-align-left', 'caption-align-right', 'caption-align-center');
    screenEl.classList.remove('caption-pos-top', 'caption-pos-bottom', 'caption-pos-middle');
    screenEl.classList.add(alignClass, verticalClass);
  }

  // Calculate proportional font size matching 1080x1920 video canvas
  const containerWidth = (screenEl && screenEl.clientWidth > 0) ? screenEl.clientWidth : 200;
  const rawSize = captionFontSize();
  const ratio = document.getElementById('clip-aspect-ratio')?.value || '9:16';
  const canvasWidth = ratio === '16:9' ? 1920 : (ratio === '1:1' ? 1080 : (ratio === '4:5' ? 1080 : 1080));
  const scaledFontSize = Math.max(1, Math.round(rawSize * (containerWidth / canvasWidth)));
  const scaledOutline = Math.max(0, Math.round(outlineWidth * (containerWidth / canvasWidth)));

  preview.style.color = textColor;
  preview.style.fontFamily = font;
  preview.style.fontSize = `${scaledFontSize}px`;
  preview.style.fontWeight = isBold ? '900' : 'normal';
  preview.style.fontStyle = isItalic ? 'italic' : 'normal';
  preview.style.textTransform = isUppercase ? 'uppercase' : 'none';

  if (outlineWidth > 0) {
    const o = strokeColor || '#000000';
    const w = scaledOutline;
    preview.style.textShadow = `${w}px 0 0 ${o}, -${w}px 0 0 ${o}, 0 ${w}px 0 ${o}, 0 -${w}px 0 ${o}, ${w}px ${w}px 0 ${o}, -${w}px -${w}px 0 ${o}, ${w}px -${w}px 0 ${o}, -${w}px ${w}px 0 ${o}, 0 0 14px ${accentColor}55`;
  } else {
    preview.style.textShadow = `0 0 14px ${accentColor}55`;
  }

  const highlights = preview.querySelectorAll('mark');
  highlights.forEach((m) => {
    m.style.color = accentColor;
    m.style.background = 'transparent';
  });
}


function refreshPortraitCaptionPreview() {
  const preview = document.getElementById('portrait-caption-preview');
  if (!preview) return;
  const rawWords = preview.dataset.words || 'Your caption appears here';
  const isUppercase = document.getElementById('caption-uppercase')?.checked;
  const words = isUppercase ? rawWords.toUpperCase() : rawWords;


  // Mirror the modal preview: highlight every 3rd word with the accent color.
  const list = words.split(' ').map((w, i) => (i % 3 === 0 ? `<mark>${escapeHtml(w)}</mark>` : escapeHtml(w)));
  preview.innerHTML = list.join(' ');
  preview.dataset.words = rawWords;
  applyPortraitCaptionPreviewStyle();
  refreshIntroPreview();
}

// Separate live preview for the INTRO HOOK (top-center, bigger font) shown in
// the same phone frame. Reuses the caption preset + color controls, but with
// its own font size so the user can size the hook independently of the captions.
function refreshIntroPreview() {
  const el = document.getElementById('portrait-intro-preview');
  if (!el) return;
  const enabled = document.getElementById('caption-intro-enabled')?.checked;
  if (!enabled) { el.classList.add('hidden'); return; }
  el.classList.remove('hidden');

  const raw = (document.getElementById('caption-intro-text')?.value || '').trim() || 'Your hook here';
  const isUppercase = document.getElementById('caption-uppercase')?.checked;
  el.textContent = isUppercase ? raw.toUpperCase() : raw;

  const presetId = document.getElementById('generated-caption-preset')?.value || 'viral_yellow';
  const baseStyle = CAPTION_PREVIEW[presetId] || CAPTION_PREVIEW.viral_yellow;
  const fontName = document.getElementById('caption-font-name')?.value || baseStyle.font;
  const accentColor = document.getElementById('caption-highlight-color')?.value || baseStyle.accent;
  const strokeColor = document.getElementById('caption-outline-color')?.value || baseStyle.back;
  const outlineWidth = parseInt(document.getElementById('caption-outline-width')?.value || '3', 10);

  const screenEl = el.parentElement;
  const containerWidth = (screenEl && screenEl.clientWidth > 0) ? screenEl.clientWidth : 200;
  const ratio = document.getElementById('clip-aspect-ratio')?.value || '9:16';
  const canvasWidth = ratio === '16:9' ? 1920 : 1080;
  const rawSize = parseInt(document.getElementById('generated-intro-font-size')?.value || '64', 10);
  const scaled = Math.max(1, Math.round(rawSize * (containerWidth / canvasWidth)));
  const w = Math.max(0, Math.round(outlineWidth * (containerWidth / canvasWidth)));

  el.style.color = accentColor;        // hooks pop in the highlight color
  el.style.fontFamily = fontName;
  el.style.fontSize = `${scaled}px`;
  el.style.fontWeight = '900';
  el.style.textShadow = w > 0
    ? `${w}px 0 0 ${strokeColor}, -${w}px 0 0 ${strokeColor}, 0 ${w}px 0 ${strokeColor}, 0 -${w}px 0 ${strokeColor}, 0 0 14px ${accentColor}55`
    : `0 0 14px ${accentColor}55`;
}

// Wire the intro-hook controls to the live preview (runs once at load).
(function wireIntroHookPreview() {
  const introSize = document.getElementById('generated-intro-font-size');
  const introSizeLabel = document.getElementById('generated-intro-font-size-label');
  document.getElementById('caption-intro-text')?.addEventListener('input', refreshIntroPreview);
  document.getElementById('caption-intro-enabled')?.addEventListener('change', refreshIntroPreview);
  introSize?.addEventListener('input', () => {
    if (introSizeLabel) introSizeLabel.textContent = introSize.value;
    refreshIntroPreview();
  });
})();

// ------------------------------------------------------------------
// Interactive Caption Editor
// ------------------------------------------------------------------
window.openCaptionEditor = function(clipIndex) {
  const clip = generatedClips[clipIndex];
  if (!clip) return;
  currentEditingClip = clip;

  // Seed the intro-hook editor with this clip's current hook and reset the
  // rotation cache so "Suggest another" pulls fresh candidates for this clip.
  const titleInput = document.getElementById('edit-clip-title');
  if (titleInput) titleInput.value = clip.title || clip.hook_text || '';
  const hookInput = document.getElementById('edit-intro-hook');
  if (hookInput) hookInput.value = clip.intro_caption || clip.hook_text || '';
  const hookSizeInput = document.getElementById('intro-hook-font-size');
  if (hookSizeInput) {
    hookSizeInput.value = clip.intro_font_size || 64;
    const lbl = document.getElementById('intro-hook-font-size-label');
    if (lbl) lbl.textContent = hookSizeInput.value;
  }
  hookCandidates = [];
  hookCandidateIdx = -1;
  const vbox = document.getElementById('hook-variants');
  if (vbox) { vbox.classList.add('hidden'); vbox.innerHTML = ''; }
  updateHookPreview();

  const modal = document.getElementById('caption-modal');
  const chipsContainer = document.getElementById('word-chips');
  chipsContainer.innerHTML = '';

  const words = clip.words && clip.words.length ? clip.words : (clip.hook_text || '').split(' ').map((w, i) => ({ word: w, start: i * 0.4, end: (i + 1) * 0.4 }));

  words.forEach((w) => {
    // Coerce timings defensively: backend word entries occasionally omit
    // start/end, which used to throw on .toFixed and leave the editor half-built.
    const start = Number.isFinite(w.start) ? w.start : 0;
    const end = Number.isFinite(w.end) ? w.end : start;
    const chip = document.createElement('div');
    chip.className = 'word-chip';
    chip.innerHTML =
      `<span class="word-text" contenteditable="true">${escapeHtml(w.word || String(w))}</span> ` +
      `<small class="word-time" data-start="${escapeHtml(start)}" data-end="${escapeHtml(end)}" style="color:var(--text-muted);">[${start.toFixed(1)}s]</small>`;
    chipsContainer.appendChild(chip);
  });

  // Seed the preview with a SHORT snippet that mimics one on-screen caption
  // line. Using the whole transcript (or the full hook_text title) floods the
  // preview box and collides with the hook overlay, so cap it to a few words
  // drawn from the actual spoken words.
  const preview = document.getElementById('caption-preview');
  const sampleWords = (clip.words || [])
    .map((x) => (x && x.word ? String(x.word) : ''))
    .filter(Boolean);
  let sample = sampleWords.slice(0, 6).join(' ').trim();
  if (!sample) {
    // No word-level data: fall back to the first few words of any available text.
    sample = (clip.hook_text || '').trim().split(/\s+/).slice(0, 6).join(' ');
  }
  if (preview) {
    preview.dataset.words = sample.length ? sample : 'Your caption appears here';
    refreshCaptionPreview();
  }
  const previewFontSize = document.getElementById('caption-preview-font-size');
  const generatedFontSize = document.getElementById('generated-caption-font-size');
  const previewFontLabel = document.getElementById('caption-preview-font-size-label');
  if (previewFontSize && generatedFontSize) {
    previewFontSize.value = generatedFontSize.value;
    if (previewFontLabel) previewFontLabel.textContent = generatedFontSize.value;
  }

  modal.classList.remove('hidden');
};

document.getElementById('close-caption-modal')?.addEventListener('click', () => {
  document.getElementById('caption-modal').classList.add('hidden');
});

// Rotate through hook suggestions drawn from THIS clip's transcript. Fetches
// once, then cycles on each click (wrapping around).
document.getElementById('suggest-hook-btn')?.addEventListener('click', async () => {
  if (!currentEditingClip) return;
  const input = document.getElementById('edit-intro-hook');
  const btn = document.getElementById('suggest-hook-btn');
  if (!hookCandidates.length) {
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Thinking…'; }
    try {
      const res = await fetch(`${serverUrl}/tools/suggest-hooks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: currentEditingClip.full_text || currentEditingClip.reason || currentEditingClip.hook_text || '',
          words: currentEditingClip.words || [],
          count: 8,
        }),
      });
      const data = await res.json();
      hookCandidates = (data.hooks || []).filter(Boolean);
    } catch (_) {
      hookCandidates = [];
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '🔄 Suggest another'; }
    }
  }
  if (!hookCandidates.length) {
    showToast('No alternative hooks found for this clip', 'info');
    return;
  }
  hookCandidateIdx = (hookCandidateIdx + 1) % hookCandidates.length;
  if (input) input.value = hookCandidates[hookCandidateIdx];
  updateHookPreview();
});

// A/B hook variants: fetch several options at once (AI if Ollama is running,
// else transcript heuristics) and show them as clickable chips to compare/pick.
async function showHookVariants() {
  if (!currentEditingClip) return;
  const box = document.getElementById('hook-variants');
  const btn = document.getElementById('hook-variants-btn');
  if (!box) return;
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Generating…'; }
  let hooks = [];
  let usedAi = false;
  try {
    const res = await fetch(`${serverUrl}/tools/rewrite-hook`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: currentEditingClip.full_text || currentEditingClip.reason || currentEditingClip.hook_text || '',
        words: currentEditingClip.words || [],
        current_hook: document.getElementById('edit-intro-hook')?.value || '',
        count: 6,
        preset: document.getElementById('generated-caption-preset')?.value || '',
      }),
    });
    const data = await res.json();
    hooks = (data.hooks || []).filter(Boolean);
    usedAi = !!data.used_ai;
  } catch (_) { hooks = []; }
  if (btn) { btn.disabled = false; btn.textContent = '⚖️ Variants'; }
  if (!hooks.length) { showToast('No hook variants found for this clip', 'info'); return; }
  box.classList.remove('hidden');
  box.innerHTML = `<div class="hook-variants-head muted small">${usedAi ? '✨ AI' : 'Suggested'} variants — click one to use it:</div>` +
    hooks.map((h) => `<button type="button" class="hook-variant-chip">${escapeHtml(h)}</button>`).join('');
  box.querySelectorAll('.hook-variant-chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      const input = document.getElementById('edit-intro-hook');
      if (input) input.value = chip.textContent;
      box.querySelectorAll('.hook-variant-chip').forEach((c) => c.classList.remove('chosen'));
      chip.classList.add('chosen');
      updateHookPreview();
    });
  });
}

// Optional AI rewrite: punch up the hook with the user's local Ollama model.
// Falls back to the offline suggestions when Ollama isn't running.
document.getElementById('ai-rewrite-hook-btn')?.addEventListener('click', async () => {
  if (!currentEditingClip) return;
  const input = document.getElementById('edit-intro-hook');
  const titleInput = document.getElementById('edit-clip-title');
  const btn = document.getElementById('ai-rewrite-hook-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Rewriting…'; }
  try {
    // Full copywriter pass: regenerate the hook AND the title (and a description)
    // together, so clicking "AI rewrite" visibly refreshes both fields.
    const res = await fetch(`${serverUrl}/tools/rewrite-copy`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: currentEditingClip.full_text || currentEditingClip.reason || currentEditingClip.hook_text || '',
        words: currentEditingClip.words || [],
        current_hook: input?.value || '',
        preset: document.getElementById('generated-caption-preset')?.value || '',
      }),
    });
    const data = await res.json();
    const newHook = (data.hook || '').trim();
    const newTitle = (data.title || '').trim();
    if (!newHook && !newTitle) { showToast('No copy generated for this clip', 'info'); return; }
    if (input && newHook) { input.value = newHook; }
    if (titleInput && newTitle) { titleInput.value = newTitle; }
    if (data.description) currentEditingClip.description = data.description;
    updateHookPreview();
    if (data.used_ai) showToast(`✨ AI hook + title from ${data.model} — edit or Save & Apply to keep`, 'success');
    else showToast('Ollama not running — used offline suggestions. Start Ollama in Setup for AI rewrites.', 'info');
  } catch (e) {
    showToast('AI rewrite failed — try again', 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '✨ AI rewrite'; }
  }
});

document.getElementById('save-captions-btn')?.addEventListener('click', async () => {
  if (!currentEditingClip) {
    document.getElementById('caption-modal').classList.add('hidden');
    return;
  }

  const saveBtn = document.getElementById('save-captions-btn');
  const originalText = saveBtn ? saveBtn.textContent : '💾 Save & Apply Subtitles';
  if (saveBtn) {
    saveBtn.disabled = true;
    saveBtn.textContent = '⏳ Saving & Burning Captions…';
  }

  // Read edited word chips: [word] [start] now editable
  const chips = document.querySelectorAll('#word-chips .word-chip');
  const editedWords = [];
  chips.forEach((chip) => {
    const textEl = chip.querySelector('.word-text');
    const timeEl = chip.querySelector('.word-time');
    const word = (textEl ? textEl.textContent : '').trim();
    if (!word) return;
    let start = parseFloat((timeEl?.dataset.start || '0').replace(/[^0-9.]/g, ''));
    let end = parseFloat((timeEl?.dataset.end || '0').replace(/[^0-9.]/g, ''));
    if (isNaN(start)) start = 0;
    if (isNaN(end) || end <= start) end = start + 1;
    editedWords.push({ word, start, end });
  });

  if (!editedWords.length) {
    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.textContent = originalText;
    }
    showAlert('No editable words found.');
    return;
  }

  const captionOpts = collectCaptionOptions();
  const outputPath = currentEditingClip.ass_path
    ? currentEditingClip.ass_path.replace(/\.ass$/i, '.srt')
    : `${currentEditingClip.output_file}.srt`;

  try {
    const res = await fetch(`${serverUrl}/export/subtitles`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        output_path: outputPath,
        words: editedWords,
        style_preset: captionOpts.style_preset,
        font_size: captionOpts.font_size,
        font_name: captionOpts.font_name,
        primary_color: captionOpts.primary_color,
        highlight_color: captionOpts.highlight_color,
        outline_color: captionOpts.outline_color,
        outline_width: captionOpts.outline_width,
        position: captionOpts.position,
        chunk_size: captionOpts.chunk_size,
        uppercase: captionOpts.uppercase,
        bold: captionOpts.bold,
        italic: captionOpts.italic,
        source_video: selectedVideo,
        clip_output_file: currentEditingClip.output_file,
        start_seconds: currentEditingClip.start_time,
        end_seconds: currentEditingClip.end_time,
        aspect_ratio: document.getElementById('clip-aspect-ratio')?.value || '9:16',
        // Burn the (possibly edited/rotated) intro hook for this clip.
        intro_caption: (document.getElementById('edit-intro-hook')?.value || '').trim() || undefined,
        intro_enabled: !!(document.getElementById('edit-intro-hook')?.value || '').trim(),
        intro_caption_duration: parseFloat(document.getElementById('caption-intro-duration')?.value || '3') || 3,
        intro_font_size: parseInt(document.getElementById('intro-hook-font-size')?.value || '', 10) || undefined,
        re_render: true,
      })
    });
    const data = await res.json();
    if (res.ok) {
      // Update in-memory clip state
      currentEditingClip.words = editedWords;
      if (data.export_path) currentEditingClip.srt_path = data.export_path;
      // Persist the chosen intro hook so it's reflected on the card and re-used.
      const newHook = (document.getElementById('edit-intro-hook')?.value || '').trim();
      currentEditingClip.intro_caption = newHook;
      if (newHook) currentEditingClip.hook_text = newHook;
      currentEditingClip.intro_font_size = parseInt(document.getElementById('intro-hook-font-size')?.value || '', 10) || undefined;
      // Editable clip title (used for the export file name + the card label).
      const newTitle = (document.getElementById('edit-clip-title')?.value || '').trim();
      if (newTitle) currentEditingClip.title = newTitle;

      // Reload the matching clip card so it plays the freshly burned captions.
      // Match on the card's own index rather than fuzzy src string-matching,
      // which was both fragile and mis-grouped (&& binds tighter than ||, so
      // the old condition reloaded the wrong card or none at all).
      const targetIdx = generatedClips.indexOf(currentEditingClip);
      if (targetIdx !== -1) {
        const card = document.querySelector(`.clip-card[data-clip-idx="${targetIdx}"]`);
        const vid = card && card.querySelector('video');
        if (vid) {
          vid.src = fileUrl(currentEditingClip.output_file, true);
          vid.load();
        }
        // Reflect the edited hook + title on the card immediately. Prefer the
        // AI-written description for the card blurb (matches buildClipCard),
        // falling back to the hook line when there's no description.
        const descEl = card && card.querySelector('.clip-desc');
        const cardBlurb = (currentEditingClip.description || newHook || '').trim();
        if (descEl && cardBlurb) descEl.textContent = cardBlurb;
        const titleEl = card && card.querySelector('.clip-title');
        if (titleEl && newTitle) titleEl.textContent = newTitle;
      }
      saveCurrentProjectSilently();
      playSuccessSound();
      showAlert(`✅ Captions updated and applied to clip!`);
    } else {
      showAlert(`Save failed: ${data.detail || 'Unknown error'}`);
    }
  } catch (err) {
    showAlert(`Save error: ${err.message}`);
  } finally {
    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.textContent = originalText;
    }
    document.getElementById('caption-modal').classList.add('hidden');
  }
});

function revealInFolder(filePath) {
  // Reveal in OS file manager via Electron IPC (fallback: copy path).
  if (window.clipperAPI && window.clipperAPI.revealInFolder) {
    window.clipperAPI.revealInFolder(filePath);
  } else {
    navigator.clipboard.writeText(filePath).catch(() => {});
    showAlert(`Path copied to clipboard:\n${filePath}`);
  }
}

window.quickCutSilence = async function(clipIndex) {
  const clip = generatedClips[clipIndex];
  if (!clip) return;
  try {
    const res = await fetch(`${serverUrl}/tools/remove-silence`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_path: clip.output_file }),
    });
    const data = await res.json();
    if (res.ok) {
      playSuccessSound();
      showAlert(`✂️ Dead air removed! Saved ${data.time_saved}s (${data.original_duration}s -> ${data.cut_duration}s)`);
      clip.output_file = data.output_path;
      clip.duration = data.cut_duration;
      showResults(generatedClips);
    } else {
      throw new Error(data.detail || 'Silence removal failed');
    }
  } catch (e) {
    showError(e.message);
  }
};

window.quickBleepClip = async function(clipIndex) {
  const clip = generatedClips[clipIndex];
  if (!clip) return;
  try {
    const res = await fetch(`${serverUrl}/tools/bleep-mute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_path: clip.output_file,
        mode: 'bleep',
        // Rebase to the clip's 0-based timeline (words carry absolute source
        // times) and let the server keep only profanity, so the bleep lands on
        // the right moments instead of the whole clip.
        timestamps: wordsClipRelative(clip.words || [], clip),
        profanity_only: true,
      }),
    });
    const data = await res.json();
    if (res.ok) {
      playSuccessSound();
      showAlert(`🔇 Bleep filter applied! (${data.message})`);
      clip.output_file = data.output_path;
      showResults(generatedClips);
    } else {
      throw new Error(data.detail || 'Bleeping failed');
    }
  } catch (e) {
    showError(e.message);
  }
};


function showError(message) {
  playErrorSound();
  const btn = document.getElementById('start-clipping');
  if (btn) {
    btn.disabled = false;
    btn.textContent = '🚀 Start Clipping & Transcribing';
  }
  setWizardStep(2);
  showAlert(`Error: ${message}`);
}

// ------------------------------------------------------------------
// Chat
// ------------------------------------------------------------------
async function sendChat() {
  const input = document.getElementById('chat-input');
  const message = input.value.trim();
  if (!message) return;

  const sendBtn = document.getElementById('chat-send');
  appendMessage('user', message);
  input.value = '';
  if (sendBtn) sendBtn.disabled = true;

  // Show an animated "typing" bubble while the local model composes a reply,
  // so the chat isn't silent between send and response.
  const typingEl = showTypingIndicator();
  try {
    const res = await fetch(`${serverUrl}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    });
    const data = await res.json();
    typingEl?.remove();
    appendMessage('bot', data.reply);
  } catch (e) {
    typingEl?.remove();
    appendMessage('bot', '⚠️ Could not reach the AI server. Is it running?');
  } finally {
    if (sendBtn) sendBtn.disabled = false;
    input.focus();
  }
}

// Appends a bot "typing…" bubble with three animated dots and returns the
// element so the caller can remove it when the real reply arrives.
function showTypingIndicator() {
  const messagesEl = document.getElementById('chat-messages');
  if (!messagesEl) return null;
  const div = document.createElement('div');
  div.className = 'chat-message bot chat-typing';
  div.setAttribute('aria-label', 'Assistant is typing');
  div.innerHTML = '<div class="bubble typing-bubble"><span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span></div>';
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

function appendMessage(role, text) {
  const messagesEl = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = `chat-message ${role}`;
  div.innerHTML = `<div class="bubble">${escapeHtml(text)}</div>`;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

// Escapes for BOTH text content and quoted attribute values. The textContent
// round-trip alone leaves " and ' intact, which made interpolating a value into
// an attribute (e.g. poster="...") an injection point.
function escapeHtml(text) {
  return String(text == null ? '' : text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Builds a usable file:// URL from a Windows or POSIX path.
//
// Two traps this avoids:
//  - Backslashes and spaces/# in paths need normalising and encoding.
//  - file: URLs have no query component, so the old `?t=<timestamp>` trick for
//    cache-busting became part of the *path* and the file silently 404'd. A
//    fragment is ignored by the filesystem layer but still changes the URL
//    string, so the browser treats it as a new resource.
function fileUrl(filePath, cacheBust) {
  if (!filePath) return '';
  const normalized = String(filePath).replace(/\\/g, '/');
  const encoded = normalized
    .split('/')
    .map((segment) => encodeURIComponent(segment).replace(/%3A/gi, ':'))
    .join('/');
  const prefix = encoded.startsWith('/') ? 'file://' : 'file:///';
  return `${prefix}${encoded}${cacheBust ? `#t=${Date.now()}` : ''}`;
}

// ------------------------------------------------------------------
// Setup / System panel
// ------------------------------------------------------------------
async function loadSetupPanel() {
  loadSupportLinks();
  try {
    const res = await fetch(`${serverUrl}/api/setup/status`);
    const data = await res.json();
    renderHardware(data);
    loadGpuAcceleration();
    renderRecommendations(data.recommendations);
    renderDeps(data);
    bindInstallAll();
    bindClearCache();
    loadAiModels(data);
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
        const r = await fetch(`${serverUrl}/api/setup/install-optional?component=diarization`, { method: 'POST' });
        const d = await r.json();
        if (d.ok) { showToast('✅ pyannote installed — restart the app to load it', 'success'); }
        else throw new Error(d.error || d.stderr || 'install failed');
      } catch (e) {
        showToast(`Install failed: ${e.message || e}`, 'error');
      } finally {
        installBtn.disabled = false;
        installBtn.textContent = orig;
        loadOptionalAddons();
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
    if (isActive) badges.push('<span class="model-badge active">● In use</span>');
    else if (m.installed) badges.push('<span class="model-badge dl">✓ Downloaded</span>');
    if (m.tested) badges.push('<span class="model-badge tested" title="Benchmarked for clip selection on real footage">🧪 Tested</span>');
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
      `<div class="model-inuse-banner" style="grid-column:1/-1;">🟢 <strong>In use:</strong> ${escapeHtml(am ? am.label : active)} <span class="muted small">— powering hooks, titles &amp; chat right now.</span></div>`);
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
      const model = btn.dataset.use;
      try {
        const r = await fetch(`${serverUrl}/api/setup/ai-model`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ kind: 'ollama', model }),
        });
        if (!r.ok) throw new Error(`Server returned ${r.status}`);
        showToast(`AI model set to ${model}`, 'success');
        renderModelCatalog();
        loadAiModels();
      } catch (e) {
        showToast(`Could not set model: ${e.message}`, 'error');
      }
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
          const res = await fetch(`${serverUrl}/api/setup/install`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ component: key }),
          });
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

// Populate + wire the "Change AI models" selectors.
async function loadAiModels(statusData) {
  // Whisper size: keep in sync with the clip-time #whisper-model select + persist.
  const whisperSel = document.getElementById('ai-whisper-model');
  if (whisperSel && !whisperSel.dataset.bound) {
    whisperSel.dataset.bound = '1';
    const saved = localStorage.getItem('klipzy.whisperModel');
    const clipSel = document.getElementById('whisper-model');
    if (saved) whisperSel.value = saved;
    else if (clipSel && clipSel.value) whisperSel.value = clipSel.value;
    whisperSel.addEventListener('change', () => {
      localStorage.setItem('klipzy.whisperModel', whisperSel.value);
      const cs = document.getElementById('whisper-model');
      if (cs) cs.value = whisperSel.value;  // the clipping run reads this select
      showToast(`Transcription model set to ${whisperSel.value} for the next run`, 'success');
    });
  }

  // Ollama model: list what's installed locally, let the user pick the active one.
  const ollamaSel = document.getElementById('ai-ollama-model');
  if (!ollamaSel) return;
  try {
    const res = await fetch(`${serverUrl}/api/setup/ai-models`);
    if (!res.ok) throw new Error();
    const data = await res.json();
    const models = data.installed_ollama_models || [];
    const active = data.ollama;
    if (!models.length) {
      const installed = statusData && statusData.ollama && statusData.ollama.installed;
      ollamaSel.innerHTML = `<option value="">${installed ? 'No models pulled yet — use “Pull model”' : 'Ollama not installed'}</option>`;
      ollamaSel.disabled = true;
    } else {
      ollamaSel.disabled = false;
      ollamaSel.innerHTML = models.map((m) =>
        `<option value="${escapeHtml(m)}"${m === active ? ' selected' : ''}>${escapeHtml(m)}</option>`
      ).join('');
      if (active && !models.includes(active)) {
        ollamaSel.insertAdjacentHTML('afterbegin', `<option value="${escapeHtml(active)}" selected>${escapeHtml(active)} (active)</option>`);
      }
    }
    if (!ollamaSel.dataset.bound) {
      ollamaSel.dataset.bound = '1';
      ollamaSel.addEventListener('change', async () => {
        if (!ollamaSel.value) return;
        try {
          const r = await fetch(`${serverUrl}/api/setup/ai-model`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ kind: 'ollama', model: ollamaSel.value }),
          });
          if (!r.ok) throw new Error(`Server returned ${r.status}`);
          showToast(`AI model set to ${ollamaSel.value}`, 'success');
        } catch (e) {
          showToast(`Could not set model: ${e.message}`, 'error');
        }
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
  loadSetupPanel();                 // refresh diagnostics on the way in
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
  const accel = torch.cuda ? 'CUDA ✓' : (torch.mps ? 'Apple Silicon (MPS) ✓' : 'CPU only — GPU build of PyTorch not installed');
  const accelHint = torch.cuda ? '(RTX-class GPU — full GPU speed)'
    : (torch.mps ? '(Apple Silicon Metal)'
    : (gpu.name ? `Detected ${escapeHtml(gpu.name)} — install CUDA PyTorch to unlock` : 'Install PyTorch to enable GPU'));
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

  const dotClass = g.state === 'active' ? 'ok' : (g.state === 'cpu_only' ? '' : 'warn');
  const cmdBlock = (label, cmd) => (cmd ? `
    <div class="gpu-cmd">
      <span class="gpu-cmd-label">${escapeHtml(label)}</span>
      <code class="gpu-cmd-text">${escapeHtml(cmd)}</code>
      <button class="btn btn-small btn-ghost gpu-copy" data-cmd="${escapeHtml(cmd)}" title="Copy command">📋</button>
    </div>` : '');

  let actions = '';
  let guide = '';
  if (g.state === 'active') {
    actions = `<button class="btn btn-small" disabled>✓ Acceleration active (${escapeHtml(g.engine || 'GPU')})</button>
               <button class="btn btn-small btn-ghost" id="gpu-revert-btn" title="Remove the GPU build (revert to CPU)">Revert to CPU…</button>`;
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
    <p class="muted small">These commands run inside the app\u2019s own Python environment. Restart the app after changing PyTorch.</p>`;

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

  const enableBtn = document.getElementById('gpu-enable-btn');
  if (enableBtn) enableBtn.addEventListener('click', async () => {
    const ok = await showConfirm(
      'Install the GPU (CUDA / Apple-Metal) build of PyTorch? This downloads ~2.5GB and replaces the current CPU build. A restart is needed afterward to load it.',
      'Enable GPU acceleration');
    if (!ok) return;
    const orig = enableBtn.textContent;
    enableBtn.disabled = true;
    enableBtn.textContent = '⏳ Installing… (~2.5GB, several min)';
    showToast('Installing the GPU build of PyTorch — large download, please wait', 'info');
    try {
      const r = await fetch(`${serverUrl}/api/setup/gpu/install`, { method: 'POST' });
      const d = await r.json();
      if (d.ok) showToast('✅ GPU build installed — restart the app to activate it', 'success');
      else throw new Error(d.error || d.stderr || 'install failed');
    } catch (e) {
      showToast(`Install failed: ${e.message || e}. You can copy the command and run it manually.`, 'error');
    } finally {
      enableBtn.disabled = false;
      enableBtn.textContent = orig;
      loadGpuAcceleration();
    }
  });

  const revertBtn = document.getElementById('gpu-revert-btn');
  if (revertBtn) revertBtn.addEventListener('click', async () => {
    const ok = await showConfirm(
      'Remove the current PyTorch build? Face-tracking will be unavailable until you reinstall PyTorch (the CPU command is shown for that).',
      'Revert to CPU');
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
      loadGpuAcceleration();
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
      ${it.choices ? `<div class="rec-choices">${it.choices.map((choice, i) => {
        const selected = it.key === 'whisper' ? (choice.model === whisperSel) : (i === 0);
        return `<button class="btn btn-small rec-choice${selected ? ' is-selected' : ''}" data-model-kind="${it.key}" data-model="${escapeHtml(choice.model)}">
          ${escapeHtml(choice.tier)}: ${escapeHtml(choice.model)}
        </button>`;
      }).join('')}</div>` : ''}
    </div>`).join('');
  document.querySelectorAll('.rec-choice').forEach((button) => {
    button.addEventListener('click', () => {
      if (button.dataset.modelKind === 'whisper') {
        const select = document.getElementById('whisper-model');
        if (select) select.value = button.dataset.model;
        // Move the highlight to the clicked whisper choice.
        button.parentElement.querySelectorAll('.rec-choice').forEach((b) => b.classList.remove('is-selected'));
        button.classList.add('is-selected');
        showToast(`Whisper model set to ${button.dataset.model}`, 'success');
      } else {
        showToast(`${button.dataset.model} is the recommended Ollama choice. Ollama will use it locally when AI is enabled.`, 'info');
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
    const ok = def.check(data);
    const cmdAvailable = !!cmds[key];
    const canUninstall = ok && !!uninstallCmds[key];
    const statusMark = ok ? '✅' : '❌';
    const tag = def.statusText ? def.statusText(data) : (ok ? 'Installed' : 'Missing');
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
        const res = await fetch(`${serverUrl}/api/setup/install`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ component: key }),
        });
        const data = await res.json();
        if (data.error) {
          showAlert(`Install failed: ${data.error}`);
          btn.disabled = false;
          btn.textContent = '⬇️ Install';
        } else if (data.returncode === 0) {
          showAlert(`✅ ${key} installed successfully!\n\nCommand: ${data.command}`);
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

// Start
// ------------------------------------------------------------------
// Audio Feedback (Clicks, Success Chimes, Error Alerts)
// ------------------------------------------------------------------
let _audioCtx = null;
function getAudioContext() {
  if (!_audioCtx) {
    _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (_audioCtx.state === 'suspended') {
    _audioCtx.resume();
  }
  return _audioCtx;
}

function playClick() {
  try {
    const ctx = getAudioContext();
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = 'sine';
    o.frequency.setValueAtTime(660, ctx.currentTime);
    g.gain.setValueAtTime(0.04, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.04);
    o.connect(g);
    g.connect(ctx.destination);
    o.start();
    o.stop(ctx.currentTime + 0.05);
  } catch (e) { /* audio unavailable */ }
}

function playSuccessSound() {
  try {
    const ctx = getAudioContext();
    const now = ctx.currentTime;
    // Pleasant ascending major triad chord / chime (C5 -> E5 -> G5 -> C6)
    const notes = [523.25, 659.25, 783.99, 1046.50];
    notes.forEach((freq, i) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = 'triangle';
      o.frequency.setValueAtTime(freq, now + i * 0.08);
      g.gain.setValueAtTime(0.0, now + i * 0.08);
      g.gain.linearRampToValueAtTime(0.12, now + i * 0.08 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.001, now + i * 0.08 + 0.35);
      o.connect(g);
      g.connect(ctx.destination);
      o.start(now + i * 0.08);
      o.stop(now + i * 0.08 + 0.4);
    });
  } catch (e) { /* audio unavailable */ }
}

function playErrorSound() {
  try {
    const ctx = getAudioContext();
    const now = ctx.currentTime;
    // Low double buzz / error tone
    [0, 0.12].forEach((offset) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = 'sawtooth';
      o.frequency.setValueAtTime(160, now + offset);
      o.frequency.linearRampToValueAtTime(110, now + offset + 0.1);
      g.gain.setValueAtTime(0.1, now + offset);
      g.gain.exponentialRampToValueAtTime(0.001, now + offset + 0.11);
      o.connect(g);
      g.connect(ctx.destination);
      o.start(now + offset);
      o.stop(now + offset + 0.12);
    });
  } catch (e) { /* audio unavailable */ }
}

// ------------------------------------------------------------------
// Multi-Aspect Export Pack (9:16, 1:1, 4:5, 16:9)
// ------------------------------------------------------------------
// Export one clip in a specific platform's preferred aspect ratio. Reuses the
// proven multi-aspect endpoint with a single ratio so we don't duplicate render
// logic. 9:16 platforms reuse the clip as-is; feed/landscape get a re-render.
async function exportForPlatform(idx, platform, ratio, btnEl) {
  const clip = generatedClips[idx];
  if (!clip) return;
  // Let the user choose where this platform export lands (matches the per-clip
  // Export and the other export buttons).
  const exportFolder = await chooseExportFolder();
  if (!exportFolder) return;
  const original = btnEl ? btnEl.textContent : '';
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = '⏳'; }
  showToast(`⏳ Exporting for ${platform} (${ratio})…`, 'info');
  try {
    const res = await fetch(`${serverUrl}/export/multi-aspect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        clip_path: clip.output_file,
        source_video: selectedVideo,
        start_seconds: clip.start_time,
        end_seconds: clip.end_time,
        title: `${clip.title || 'clip'} [${platform}]`,
        burn_captions: true,
        subtitle_path: clip.ass_path || clip.srt_path,
        aspect_ratios: [ratio],
        output_dir: exportFolder,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Server returned ${res.status}`);
    playSuccessSound();
    const out = data.exports ? Object.values(data.exports)[0] : null;
    showToast(`✅ ${platform} export ready`, 'success');
    if (out) revealInFolder(out);
  } catch (err) {
    playErrorSound();
    showAlert(`${platform} export failed: ${err.message}`);
  } finally {
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = original; }
  }
}

// Quick per-clip hook swap from the card: rotate to a fresh suggested hook and
// re-render just this clip (burns the new top hook in place).
async function quickRerollHook(idx, btn) {
  const clip = generatedClips[idx];
  if (!clip || !clip.output_file) { showAlert('No rendered clip to update yet.'); return; }
  // Build the richest transcript context we have for this clip: prefer the full
  // clip transcript, then the clip's own word list (present even on older clips),
  // falling back to the hook line. Using ONLY hook_text returned a single
  // candidate, so "New Hook" kept re-picking the exact same line — which is why
  // the text never appeared to change.
  const clipText = clip.full_text || clip.reason
    || (Array.isArray(clip.words) && clip.words.length ? clip.words.map((w) => w.word).join(' ') : '')
    || clip.hook_text || '';
  // Fetch + cache AI hook options per clip (grounded in that transcript), then
  // rotate on each click. rewrite-hook returns several DISTINCT viral hooks and
  // falls back to offline suggestions server-side when Ollama isn't running.
  if (!Array.isArray(clip._hookCandidates) || !clip._hookCandidates.length) {
    try {
      const res = await fetch(`${serverUrl}/tools/rewrite-hook`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: clipText,
          words: clip.words || [],
          current_hook: clip.hook_text || '',
          count: 6,
          preset: document.getElementById('generated-caption-preset')?.value || '',
        }),
      });
      const data = await res.json();
      clip._hookCandidates = (data.hooks || []).filter(Boolean);
      clip._hookIdx = -1;
    } catch (_) { clip._hookCandidates = []; }
  }
  if (!clip._hookCandidates.length) { showToast('No alternative hooks found for this clip', 'info'); return; }
  // Rotate to the next candidate; if it matches the current hook, skip once so
  // the burned text visibly changes.
  clip._hookIdx = ((clip._hookIdx == null ? -1 : clip._hookIdx) + 1) % clip._hookCandidates.length;
  let newHook = clip._hookCandidates[clip._hookIdx];
  if (clip._hookCandidates.length > 1 && newHook.trim() === (clip.hook_text || '').trim()) {
    clip._hookIdx = (clip._hookIdx + 1) % clip._hookCandidates.length;
    newHook = clip._hookCandidates[clip._hookIdx];
  }

  const orig = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Re-rendering…'; }

  const words = (clip.words && clip.words.length)
    ? clip.words
    : (clip.hook_text || '').split(' ').map((w, i) => ({ word: w, start: i * 0.4, end: (i + 1) * 0.4 }));
  const captionOpts = collectCaptionOptions();
  const outputPath = clip.ass_path ? clip.ass_path.replace(/\.ass$/i, '.srt') : `${clip.output_file}.srt`;
  try {
    const res = await fetch(`${serverUrl}/export/subtitles`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        output_path: outputPath,
        words,
        style_preset: captionOpts.style_preset,
        font_size: captionOpts.font_size,
        font_name: captionOpts.font_name,
        primary_color: captionOpts.primary_color,
        highlight_color: captionOpts.highlight_color,
        outline_color: captionOpts.outline_color,
        outline_width: captionOpts.outline_width,
        position: captionOpts.position,
        chunk_size: captionOpts.chunk_size,
        uppercase: captionOpts.uppercase,
        bold: captionOpts.bold,
        italic: captionOpts.italic,
        source_video: selectedVideo,
        clip_output_file: clip.output_file,
        start_seconds: clip.start_time,
        end_seconds: clip.end_time,
        aspect_ratio: document.getElementById('clip-aspect-ratio')?.value || '9:16',
        intro_caption: newHook,
        intro_enabled: true,
        intro_caption_duration: parseFloat(document.getElementById('caption-intro-duration')?.value || '3') || 3,
        intro_font_size: clip.intro_font_size || captionOpts.intro_font_size,
        re_render: true,
      }),
    });
    const data = await res.json();
    if (res.ok) {
      clip.intro_caption = newHook;
      clip.hook_text = newHook;
      if (data.export_path) clip.srt_path = data.export_path;
      const card = document.querySelector(`.clip-card[data-clip-idx="${idx}"]`);
      const vid = card && card.querySelector('video');
      if (vid) { vid.src = fileUrl(clip.output_file, true); vid.load(); }
      const descEl = card && card.querySelector('.clip-desc');
      if (descEl) descEl.textContent = newHook;
      saveCurrentProjectSilently();
      playSuccessSound();
      showToast(`🎣 New hook: "${newHook}"`, 'success');
    } else {
      showAlert(`Could not update hook: ${data.detail || 'error'}`);
    }
  } catch (e) {
    showAlert(`Error: ${e.message}`);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = orig; }
  }
}

// Remove the burned-in intro hook from a clip and re-render it. Mirrors
// quickRerollHook but disables the intro so no hook is drawn on the video.
async function quickRemoveHook(idx, btn) {
  const clip = generatedClips[idx];
  if (!clip || !clip.output_file) { showAlert('No rendered clip to update yet.'); return; }
  if (!clip.intro_caption && !clip.hook_text) {
    showToast('This clip has no intro hook to remove', 'info');
    return;
  }
  const orig = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Re-rendering…'; }

  const words = (clip.words && clip.words.length)
    ? clip.words
    : (clip.hook_text || '').split(' ').map((w, i) => ({ word: w, start: i * 0.4, end: (i + 1) * 0.4 }));
  const captionOpts = collectCaptionOptions();
  const outputPath = clip.ass_path ? clip.ass_path.replace(/\.ass$/i, '.srt') : `${clip.output_file}.srt`;
  try {
    const res = await fetch(`${serverUrl}/export/subtitles`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        output_path: outputPath,
        words,
        style_preset: captionOpts.style_preset,
        font_size: captionOpts.font_size,
        font_name: captionOpts.font_name,
        primary_color: captionOpts.primary_color,
        highlight_color: captionOpts.highlight_color,
        outline_color: captionOpts.outline_color,
        outline_width: captionOpts.outline_width,
        position: captionOpts.position,
        chunk_size: captionOpts.chunk_size,
        uppercase: captionOpts.uppercase,
        bold: captionOpts.bold,
        italic: captionOpts.italic,
        source_video: selectedVideo,
        clip_output_file: clip.output_file,
        start_seconds: clip.start_time,
        end_seconds: clip.end_time,
        aspect_ratio: document.getElementById('clip-aspect-ratio')?.value || '9:16',
        intro_caption: '',
        intro_enabled: false,
        re_render: true,
      }),
    });
    const data = await res.json();
    if (res.ok) {
      clip.intro_caption = '';
      if (data.export_path) clip.srt_path = data.export_path;
      const card = document.querySelector(`.clip-card[data-clip-idx="${idx}"]`);
      const vid = card && card.querySelector('video');
      if (vid) { vid.src = fileUrl(clip.output_file, true); vid.load(); }
      saveCurrentProjectSilently();
      playSuccessSound();
      showToast('🚫 Intro hook removed', 'success');
    } else {
      showAlert(`Could not remove hook: ${data.detail || 'error'}`);
    }
  } catch (e) {
    showAlert(`Error: ${e.message}`);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = orig; }
  }
}

// #13 Filler-word + dead-air removal for a clip (uses its word timestamps).
async function quickRemoveFillers(idx, btn) {
  const clip = generatedClips[idx];
  if (!clip || !clip.output_file) { showAlert('No rendered clip yet.'); return; }
  const orig = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Cutting…'; }
  try {
    const aggressive = !!document.getElementById('filler-aggressive')?.checked;
    const res = await fetch(`${serverUrl}/tools/remove-fillers`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_path: clip.output_file,
        // Rebase to the clip's 0-based timeline so filler cuts line up with the
        // rendered clip (clip.words carry absolute source-video times).
        words: wordsClipRelative(clip.words || [], clip),
        also_remove_silence: true,
        remove_phrases: aggressive,  // conservative (disfluencies only) unless the toggle is on
      }),
    });
    const data = await res.json();
    if (res.ok) {
      clip.output_file = data.output_path;
      if (typeof data.cut_duration === 'number') clip.duration = data.cut_duration;
      const card = document.querySelector(`.clip-card[data-clip-idx="${idx}"]`);
      const vid = card && card.querySelector('video');
      if (vid) { vid.src = fileUrl(clip.output_file, true); vid.load(); }
      saveCurrentProjectSilently();
      playSuccessSound();
      showToast(`🧹 ${data.message || 'Fillers removed'}`, 'success');
    } else {
      showAlert(`Filler removal failed: ${data.detail || 'error'}`);
    }
  } catch (e) {
    showAlert(`Error: ${e.message}`);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = orig; }
  }
}

// #14 Translate a clip's captions into another language (local Ollama).
let translateClip = null;
async function openTranslateModal(idx) {
  const clip = generatedClips[idx];
  if (!clip) return;
  if (!clip.srt_path) { showAlert('No subtitle file for this clip yet — generate captions first.'); return; }
  translateClip = clip;
  const sel = document.getElementById('translate-lang');
  if (sel && sel.dataset.loaded !== '1') {
    try {
      const r = await fetch(`${serverUrl}/tools/languages`);
      const d = await r.json();
      sel.innerHTML = (d.languages || []).map((l) => `<option value="${escapeHtml(l)}">${escapeHtml(l)}</option>`).join('');
      sel.dataset.loaded = '1';
    } catch (_) { sel.innerHTML = '<option value="Spanish">Spanish</option>'; }
  }
  document.getElementById('translate-modal')?.classList.remove('hidden');
}
document.getElementById('translate-close')?.addEventListener('click', () => document.getElementById('translate-modal')?.classList.add('hidden'));
document.getElementById('translate-cancel')?.addEventListener('click', () => document.getElementById('translate-modal')?.classList.add('hidden'));
document.getElementById('translate-go')?.addEventListener('click', async () => {
  if (!translateClip) return;
  const custom = (document.getElementById('translate-lang-custom')?.value || '').trim();
  const lang = custom || document.getElementById('translate-lang')?.value || '';
  if (!lang) { showToast('Pick or type a language', 'info'); return; }
  const burn = !!document.getElementById('translate-burn')?.checked;
  const btn = document.getElementById('translate-go');
  const orig = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = burn ? '⏳ Translating + rendering…' : '⏳ Translating…'; }
  try {
    const body = { srt_path: translateClip.srt_path, target_lang: lang };
    if (burn) {
      body.burn = true;
      body.source_video = selectedVideo;
      body.start_seconds = translateClip.start_time;
      body.end_seconds = translateClip.end_time;
      body.aspect_ratio = document.getElementById('clip-aspect-ratio')?.value || '9:16';
      body.style_preset = document.getElementById('generated-caption-preset')?.value || 'viral_yellow';
      body.font_size = parseInt(document.getElementById('generated-caption-font-size')?.value || '', 10) || undefined;
    }
    const res = await fetch(`${serverUrl}/tools/translate-captions`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (res.ok) {
      document.getElementById('translate-modal')?.classList.add('hidden');
      playSuccessSound();
      let msg = `✅ Translated ${data.translated} caption lines to ${lang}.\nSaved:\n• ${data.srt}\n• ${data.vtt}`;
      const reveal = data.burned_video || data.srt || null;
      if (data.burned_video) msg += `\n\n🎬 Burned video:\n• ${data.burned_video}`;
      else if (data.burn_error) msg += `\n\n⚠️ Couldn't burn the video: ${data.burn_error}`;
      if (reveal) revealInFolder(reveal);
      showAlert(msg, 'Translation Complete', reveal);
    } else {
      showAlert(`Translation failed: ${data.detail || 'error'}`);
    }
  } catch (e) {
    showAlert(`Error: ${e.message}`);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = orig; }
  }
});

// #16 Speaker diarization (optional — needs pyannote + HF token).
// Detects who spoke when, then rewrites the clip's captions with friendly
// "Speaker 1:" / "Speaker 2:" labels (whoever talks first = Speaker 1) and can
// re-render the clip to burn the labels in. Degrades gracefully when pyannote
// isn't installed.
async function detectSpeakers(idx, btn) {
  const clip = generatedClips[idx];
  if (!clip || !clip.output_file) return;

  const words = (clip.words && clip.words.length) ? clip.words : null;
  if (!words) {
    showAlert('This clip has no word timestamps to label. Re-transcribe it first.', 'Speakers');
    return;
  }

  const burn = await showConfirm(
    'Detect speakers and add "Speaker 1:" / "Speaker 2:" labels to this clip\'s captions?\n\n' +
    'Choose OK to also re-render the clip and burn the labels in, or Cancel to just write the caption files (.srt/.ass).',
    'Speaker-labeled captions',
  );

  const orig = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Analyzing…'; }
  try {
    const captionOpts = collectCaptionOptions();
    const outputPath = clip.ass_path ? clip.ass_path.replace(/\.ass$/i, '.srt') : `${clip.output_file}.srt`;
    const res = await fetch(`${serverUrl}/tools/speaker-captions`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        output_path: outputPath,
        words,
        style_preset: captionOpts.style_preset,
        font_size: captionOpts.font_size,
        font_name: captionOpts.font_name,
        primary_color: captionOpts.primary_color,
        highlight_color: captionOpts.highlight_color,
        outline_color: captionOpts.outline_color,
        outline_width: captionOpts.outline_width,
        position: captionOpts.position,
        chunk_size: captionOpts.chunk_size,
        uppercase: captionOpts.uppercase,
        bold: captionOpts.bold,
        italic: captionOpts.italic,
        source_video: selectedVideo,
        clip_output_file: clip.output_file,
        start_seconds: clip.start_time,
        end_seconds: clip.end_time,
        aspect_ratio: document.getElementById('clip-aspect-ratio')?.value || clip.aspect_ratio || '9:16',
        layout: clip.layout || '',
        cam_video: clip.cam_video,
        cam_scale: clip.cam_scale,
        cam_position: clip.cam_position,
        crop_x_offset: clip.crop_x_offset,
        re_render: !!burn,
      }),
    });
    const d = await res.json();
    if (d.available) {
      if (d.srt_path) clip.srt_path = d.srt_path;
      if (d.ass_path) clip.ass_path = d.ass_path;
      if (d.re_rendered) {
        const card = document.querySelector(`.clip-card[data-clip-idx="${idx}"]`);
        const vid = card && card.querySelector('video');
        if (vid) { vid.src = fileUrl(clip.output_file, true); vid.load(); }
      }
      const who = (d.speakers || []).join(', ');
      showToast(`🗣 ${d.message}`, 'success');
      showAlert(`🗣 ${d.message}${who ? `\n\nSpeakers: ${who}` : ''}`, 'Speaker-labeled captions');
    } else {
      showAlert(`Speaker detection is optional and not enabled yet.\n\n${d.message}\n\nTo enable, open Setup → Optional AI add-ons, install pyannote.audio, and set a Hugging Face token.`, 'Speaker Diarization (optional)');
    }
  } catch (e) {
    showAlert(`Error: ${e.message}`);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = orig; }
  }
}

const MULTI_ASPECT_RATIOS = [
  { ratio: '9:16', label: 'Vertical 9:16', tag: 'TikTok / Reels / Shorts' },
  { ratio: '1:1', label: 'Square 1:1', tag: 'Feed' },
  { ratio: '4:5', label: 'Portrait 4:5', tag: 'IG feed' },
  { ratio: '16:9', label: 'Landscape 16:9', tag: 'YouTube / X' },
];
let multiAspectClip = null;

// Opens a preview-before-export modal (OpenClipper-style): shows the clip framed
// in each aspect ratio, lets the user pick which to render, then choose a folder.
// Active-speaker crop offsets computed by the preview, reused on export so the
// rendered file matches exactly what the preview showed.
let maCropOffsets = {};

function exportMultiAspectPack(idx) {
  const clip = generatedClips[idx];
  if (!clip || !clip.output_file) {
    showAlert('No rendered clip to export yet.');
    return;
  }
  multiAspectClip = clip;
  maCropOffsets = {};
  const wrap = document.getElementById('multi-aspect-previews');
  if (wrap) {
    const fallbackSrc = fileUrl(clip.output_file);
    wrap.innerHTML = MULTI_ASPECT_RATIOS.map(({ ratio, label, tag }) => `
      <div class="ma-card">
        <label class="ma-card-head">
          <input type="checkbox" class="ma-check" value="${ratio}" checked />
          <span class="ma-label">${label}</span>
        </label>
        <div class="ma-frame" style="aspect-ratio:${ratio.replace(':', ' / ')}">
          <div class="ma-loading" data-ratio="${ratio}">⏳</div>
          <img class="ma-img" data-ratio="${ratio}" alt="${label} preview" hidden />
          <video class="ma-fallback" data-ratio="${ratio}" src="${escapeHtml(fallbackSrc)}" muted playsinline preload="metadata" hidden></video>
        </div>
        <span class="muted small">${tag}</span>
        <button class="btn btn-small btn-secondary ma-export-one" data-ratio="${ratio}">⬇️ Export ${ratio}</button>
      </div>`).join('');
    // Individual per-aspect export (OpenClipper-style): export just this ratio.
    wrap.querySelectorAll('.ma-export-one').forEach((b) => {
      b.addEventListener('click', () => runMultiAspectExport([b.dataset.ratio], b));
    });
    // Lazily render a REAL cropped still per ratio (active-speaker framing).
    MULTI_ASPECT_RATIOS.forEach(({ ratio }) => loadAspectPreview(clip, ratio));
  }
  document.getElementById('multi-aspect-modal')?.classList.remove('hidden');
}

// Fetch a real cropped preview frame for one ratio and swap it in. Stores the
// computed crop offset so the export reuses the exact same framing. Falls back
// to the plain CSS-cover video if the still can't be rendered.
async function loadAspectPreview(clip, ratio) {
  const wrap = document.getElementById('multi-aspect-previews');
  if (!wrap) return;
  const loadingEl = wrap.querySelector(`.ma-loading[data-ratio="${ratio}"]`);
  const imgEl = wrap.querySelector(`.ma-img[data-ratio="${ratio}"]`);
  const fbEl = wrap.querySelector(`.ma-fallback[data-ratio="${ratio}"]`);
  try {
    const res = await fetch(`${serverUrl}/export/aspect-preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        clip_path: clip.output_file,
        source_video: selectedVideo,
        start_seconds: clip.start_time,
        end_seconds: clip.end_time,
        aspect_ratio: ratio,
      }),
    });
    const data = await res.json();
    if (!res.ok || !data.image_path) throw new Error(data.detail || 'preview failed');
    maCropOffsets[ratio] = (data.crop_x_offset ?? null);
    if (imgEl) {
      // The still can come back as a valid path that the renderer still can't
      // load (write race / file access), which showed as a broken-image icon.
      // Swap to the video fallback on a load error instead of leaving it broken.
      imgEl.addEventListener('error', () => {
        imgEl.hidden = true;
        if (fbEl) fbEl.hidden = false;
      }, { once: true });
      imgEl.addEventListener('load', () => { imgEl.hidden = false; }, { once: true });
      imgEl.src = fileUrl(data.image_path, true);
    }
  } catch (_) {
    if (fbEl) fbEl.hidden = false;  // graceful fallback to the CSS-cover video
  } finally {
    if (loadingEl) loadingEl.remove();
  }
}

document.getElementById('multi-aspect-close')?.addEventListener('click', () => {
  document.getElementById('multi-aspect-modal')?.classList.add('hidden');
});
document.getElementById('multi-aspect-cancel')?.addEventListener('click', () => {
  document.getElementById('multi-aspect-modal')?.classList.add('hidden');
});

document.getElementById('multi-aspect-export')?.addEventListener('click', () => {
  const ratios = Array.from(document.querySelectorAll('#multi-aspect-previews .ma-check:checked')).map((c) => c.value);
  if (!ratios.length) {
    showToast('Pick at least one aspect ratio', 'info');
    return;
  }
  runMultiAspectExport(ratios, document.getElementById('multi-aspect-export'));
});

// Render the given aspect ratios for the current clip into a chosen folder,
// then reveal it. Shared by "Export selected" and the per-aspect buttons.
async function runMultiAspectExport(ratios, btn) {
  if (!multiAspectClip || !ratios || !ratios.length) return;
  const exportFolder = await chooseExportFolder(multiAspectClip);
  if (!exportFolder) return;   // cancelled the folder picker
  const orig = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Rendering…'; }
  try {
    const res = await fetch(`${serverUrl}/export/multi-aspect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        clip_path: multiAspectClip.output_file,
        source_video: selectedVideo,
        start_seconds: multiAspectClip.start_time,
        end_seconds: multiAspectClip.end_time,
        title: multiAspectClip.title,
        burn_captions: true,
        subtitle_path: multiAspectClip.ass_path || multiAspectClip.srt_path,
        aspect_ratios: ratios,
        output_dir: exportFolder,
        crop_offsets: maCropOffsets,
      })
    });
    const data = await res.json();
    if (res.ok) {
      playSuccessSound();
      const firstOut = data.exports ? Object.values(data.exports)[0] : null;
      if (firstOut) revealInFolder(firstOut);
      showAlert(`✅ Exported:\n${Object.entries(data.exports).map(([k, v]) => `• ${k}: ${v}`).join('\n')}`, 'Export Complete', firstOut || null);
    } else {
      playErrorSound();
      showAlert(`Multi-aspect export failed: ${data.detail}`);
    }
  } catch (err) {
    playErrorSound();
    showAlert(`Error: ${err.message}`);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = orig; }
  }
}

// -----------------------------------------------------------------------
// Local KEYWORD_EMOJIS map (mirrors server/overlay_manager.py) for
// client-side emoji enrichment without network round-trips.
// -----------------------------------------------------------------------
const KEYWORD_EMOJIS = {
  money: "💸", cash: "💰", dollar: "💵", rich: "🤑", wealth: "📈",
  crazy: "🤯", insane: "😱", shocking: "⚡", wild: "🔥",
  love: "❤️", hate: "💔", heart: "💖", best: "⭐",
  win: "🏆", winner: "🥇", success: "🚀", goal: "🎯",
  stop: "🛑", warning: "⚠️", danger: "🚨", secret: "🤫",
  idea: "💡", think: "🧠", smart: "🧐", truth: "🔍",
  fire: "🔥", hot: "🌶️", power: "⚡", strong: "💪",
  food: "🍔", coffee: "☕", drink: "🥤", music: "🎵",
  laugh: "😂", funny: "🤣", joke: "🎭", cry: "😭",
  game: "🎮", gaming: "👾", code: "💻", tech: "🤖",
  ai: "🤖", future: "🔮", magic: "✨", time: "⏳",
  fast: "⚡", slow: "🐢", car: "🏎️", travel: "✈️",
};

// Server-side emoji suggestion results cache: keyed by lower-cased word so
// repeated overlay attempts re-use previous answers instead of re-hitting
// /tools/suggest-emojis on every burn.
const emojiSuggestionCache = new Map();

function enrichWordsWithEmojisLocal(words) {
  if (!words || !words.length) return words;
  return words.map(w => {
    const clean = String(w.word).replace(/[^A-Za-z]+/g, '').toLowerCase();
    const emoji = KEYWORD_EMOJIS[clean];
    return emoji ? { word: `${w.word} ${emoji}`, start: w.start, end: w.end } : { word: w.word, start: w.start, end: w.end };
  });
}

// clip.words carry ABSOLUTE source-video timestamps (Whisper table), but the
// clip file itself is cut with input-seek (-ss) so its timeline starts at 0.
// Shift word events to clip-local time so caption burning via /tools/overlay
// (which applies a 0.0 offset to the 0-based clip) lands captions on-frame.
// Exception: manual trim clips have wordsAreRelative=true (server already rebased).
function wordsClipRelative(words, clip) {
  if (!words || !words.length) return words;
  if (clip?.wordsAreRelative) return words;  // Already clip-local
  const offset = (clip && typeof clip.start_time === 'number') ? clip.start_time : 0;
  if (!offset) return words;
  return words.map(w => {
    const start = Math.max(0, (w.start ?? 0) - offset);
    const end = Math.max(start, (w.end ?? start) - offset);
    return { word: w.word, start, end };
  });
}

// ------------------------------------------------------------------
// B-Roll / Visual Overlay Modal & Emojis
// ------------------------------------------------------------------
let activeOverlayClipIdx = null;

function openOverlayModal(idx) {
  activeOverlayClipIdx = idx;
  const clip = generatedClips[idx];
  if (!clip) return;

  const modal = document.getElementById('overlay-modal');
  if (modal) modal.classList.remove('hidden');
}

function closeOverlayModal() {
  activeOverlayClipIdx = null;
  const modal = document.getElementById('overlay-modal');
  if (modal) modal.classList.add('hidden');
}

document.getElementById('close-overlay-modal')?.addEventListener('click', closeOverlayModal);

document.getElementById('pick-broll-btn')?.addEventListener('click', async () => {
  if (window.clipperAPI && window.clipperAPI.selectCameraClip) {
    const file = await window.clipperAPI.selectCameraClip();
    if (file) {
      const input = document.getElementById('overlay-broll-path');
      if (input) input.value = file;
    }
  }
});

// Background music picker: choose a track, mixed under the speech at render time.
document.getElementById('pick-music-btn')?.addEventListener('click', async () => {
  let file = null;
  if (window.clipperAPI && window.clipperAPI.selectMusic) {
    file = await window.clipperAPI.selectMusic();
  } else {
    file = window.prompt('Paste the full path to a music file:');
  }
  if (file) {
    const input = document.getElementById('music-path');
    if (input) input.value = file;
    const clearBtn = document.getElementById('clear-music-btn');
    if (clearBtn) clearBtn.style.display = '';
    showToast('🎵 Background music added — it will duck under the speech.', 'success');
  }
});

document.getElementById('clear-music-btn')?.addEventListener('click', () => {
  const input = document.getElementById('music-path');
  if (input) input.value = '';
  const clearBtn = document.getElementById('clear-music-btn');
  if (clearBtn) clearBtn.style.display = 'none';
});

// Builds the /process request body from the current UI settings. Shared by the
// single-video "Start Clipping" flow and the batch flow (which swaps in a list
// of video_paths).
function buildProcessPayload() {
  const captionOpts = collectCaptionOptions();
  return {
    video_path: selectedVideo,
    vertical_crop: document.getElementById('vertical-crop').checked,
    aspect_ratio: document.getElementById('clip-aspect-ratio') ? document.getElementById('clip-aspect-ratio').value : '9:16',
    caption_style: captionOpts.caption_style,
    font_size: captionOpts.font_size,
    font_name: captionOpts.font_name,
    primary_color: captionOpts.primary_color,
    highlight_color: captionOpts.highlight_color,
    outline_color: captionOpts.outline_color,
    outline_width: captionOpts.outline_width,
    position: captionOpts.position,
    chunk_size: captionOpts.chunk_size,
    uppercase: captionOpts.uppercase,
    bold: captionOpts.bold,
    italic: captionOpts.italic,
    intro_caption: captionOpts.intro_caption,
    intro_caption_duration: captionOpts.intro_caption_duration,
    intro_enabled: captionOpts.intro_enabled,
    intro_font_size: captionOpts.intro_font_size,
    max_clips: parseInt(document.getElementById('max-clips').value) || 5,
    min_duration: parseFloat(document.getElementById('min-duration').value) || 20,
    max_duration: parseFloat(document.getElementById('max-duration').value) || 60,
    whisper_model: document.getElementById('whisper-model').value,
    use_audio_energy: document.getElementById('audio-energy').checked,
    use_llm: document.getElementById('use-llm').checked,
    speaker_aware_selection: document.getElementById('speaker-aware-selection') ? document.getElementById('speaker-aware-selection').checked : false,
    speaker_aware_crop: document.getElementById('speaker-aware-crop') ? document.getElementById('speaker-aware-crop').checked : false,
    burn_captions: document.getElementById('burn-captions').checked,
    remove_silence: document.getElementById('remove-silence') ? document.getElementById('remove-silence').checked : false,
    bleep_profanity: document.getElementById('censor-profanity') ? document.getElementById('censor-profanity').checked : false,
    mute_profanity: false,
    normalize_audio: document.getElementById('normalize-audio') ? document.getElementById('normalize-audio').checked : false,
    auto_zoom: document.getElementById('auto-zoom') ? document.getElementById('auto-zoom').checked : false,
    music_path: (document.getElementById('music-path') && document.getElementById('music-path').value) || null,
    music_volume: parseFloat(document.getElementById('music-volume') ? document.getElementById('music-volume').value : '0.12') || 0.12,
    duck_music: document.getElementById('duck-music') ? document.getElementById('duck-music').checked : true,
  };
}

// Batch processing: pick several videos and queue them with the current settings.
document.getElementById('batch-process-btn')?.addEventListener('click', batchProcessVideos);

async function batchProcessVideos() {
  let files = [];
  if (window.clipperAPI && window.clipperAPI.selectVideosMulti) {
    files = await window.clipperAPI.selectVideosMulti();
  } else {
    const paths = window.prompt('Paste video paths separated by a newline or comma:');
    if (paths) files = paths.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);
  }
  if (!files || !files.length) return;

  const ok = await showConfirm(
    `Queue ${files.length} video${files.length > 1 ? 's' : ''} for processing with the current settings? ` +
    'They run one after another so your machine isn\'t overloaded.',
    'Batch process',
  );
  if (!ok) return;

  // Reuse the single-video payload builder's settings, but send the file list.
  const payload = buildProcessPayload();
  if (!payload) return;
  payload.video_paths = files;
  delete payload.video_path;

  try {
    const res = await fetch(`${serverUrl}/process/batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...payload, video_path: files[0] }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Server returned ${res.status}`);
    showToast(`✅ Queued ${data.count} videos. Watch progress in the jobs panel.`, 'success');
    if (Array.isArray(data.job_ids) && data.job_ids.length) {
      // Follow the first job so the UI shows live progress; the rest drain after.
      pollJob(data.job_ids[0]);
    }
  } catch (e) {
    showError(e.message);
  }
}

// Helper: prepare emoji-injected ASS path for overlay (returns path or null)
async function prepareEmojiAssPath(clip, injectEmojis) {
  if (!injectEmojis) return null;
  if (!clip.words || !clip.words.length) {
    showToast('⚠️ No transcript word timing available — skipping emoji injection.', 'info');
    return null;
  }

  // clip.words carry ABSOLUTE source-video timestamps, but the clip file
  // is cut with input-seek so its timeline starts at 0. Rebase to
  // clip-local time before generating the caption file /tools/overlay
  // burns (that path applies a 0.0 offset to the 0-based clip).
  const clipWords = wordsClipRelative(clip.words, clip);
  const localEnriched = enrichWordsWithEmojisLocal(clipWords);

  // Count real emoji matches explicitly (a .map() always returns a new
  // array, so any `!==` comparison against the source array is always true
  // and would make the "no matches" branch dead code).
  let matchCount = 0;
  for (let i = 0; i < localEnriched.length; i++) {
    if (localEnriched[i].word !== (clipWords[i] && clipWords[i].word)) matchCount += 1;
  }
  let suggestions = [];
  let wordsWithEmoji = localEnriched;

  if (matchCount === 0) {
    // No local matches: consult the server, but cache per-word suggestions
    // (keyed by lower-cased word) so repeated burns don't re-hit the API.
    const missingKeys = clipWords
      .map(w => String(w.word).replace(/[^A-Za-z]+/g, '').toLowerCase())
      .filter(clean => clean && !emojiSuggestionCache.has(clean));
    if (missingKeys.length) {
      const suggestRes = await fetch(`${serverUrl}/tools/suggest-emojis`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          segments: [{ id: 1, start: clip.start_time, end: clip.end_time, text: clip.hook_text || '', words: clipWords }]
        })
      });
      if (suggestRes.ok) {
        const suggestData = await suggestRes.json();
        const fetched = (suggestData.suggestions || []).filter(s => s && s.word);
        for (const s of fetched) {
          const key = String(s.word).replace(/[^A-Za-z]+/g, '').toLowerCase();
          if (s.emoji && key) emojiSuggestionCache.set(key, s.emoji);
        }
      }
    }

    // Merge cached suggestions
    let serverMatchCount = 0;
    wordsWithEmoji = clipWords.map(w => {
      const key = String(w.word).replace(/[^A-Za-z]+/g, '').toLowerCase();
      const emoji = emojiSuggestionCache.get(key);
      if (emoji) {
        serverMatchCount += 1;
        return { word: `${w.word} ${emoji}`, start: w.start, end: w.end };
      }
      return { word: w.word, start: w.start, end: w.end };
    });
    matchCount = serverMatchCount;
  }

  if (matchCount > 0) {
    const outBase = clip.ass_path || clip.srt_path;
    const outputPath = outBase
      ? outBase.replace(/\.(ass|srt)$/i, '.srt')
      : `${clip.output_file}.srt`;

    const captionOpts = collectCaptionOptions();
    const subRes = await fetch(`${serverUrl}/export/subtitles`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        output_path: outputPath,
        words: wordsWithEmoji,
        style_preset: captionOpts.style_preset,
        font_size: captionOpts.font_size,
        font_name: captionOpts.font_name,
        primary_color: captionOpts.primary_color,
        highlight_color: captionOpts.highlight_color,
        outline_color: captionOpts.outline_color,
        outline_width: captionOpts.outline_width,
        position: captionOpts.position,
        chunk_size: captionOpts.chunk_size,
        uppercase: captionOpts.uppercase,
        bold: captionOpts.bold,
        italic: captionOpts.italic,
        intro_caption: captionOpts.intro_caption,
        intro_caption_duration: captionOpts.intro_caption_duration,
        source_video: selectedVideo,
        clip_output_file: clip.output_file,
        start_seconds: clip.start_time,
        end_seconds: clip.end_time,
        aspect_ratio: document.getElementById('clip-aspect-ratio')?.value || '9:16',
        re_render: false,
        layout: clip.layout || '',
        cam_video: clip.cam_video,
        cam_scale: clip.cam_scale,
        cam_position: clip.cam_position,
        crop_x_offset: clip.crop_x_offset,
      })
    });
    const subData = await subRes.json();
    if (subRes.ok) {
      // Prefer the server-returned export path (source of truth for where
      // the regenerated SRT/ASS actually landed) and derive the ASS from it.
      const srvSrt = subData.export_path || outputPath;
      clip.srt_path = srvSrt;
      clip.ass_path = srvSrt.replace(/\.srt$/i, '.ass');
      let emojiAssPath = clip.ass_path;
      if (subData.re_rendered) {
        // Server actually re-burned the captions into the clip file, so the
        // overlay will run on the already-branded video (no ASS needed).
        emojiAssPath = null;
      }
      showToast(`✨ Emojis injected into captions (${matchCount} keywords)${subData.re_rendered ? ' — burned into clip ✓' : ' — will burn with B-roll!'}`, 'success');
      return emojiAssPath;
    } else {
      showToast(`⚠️ Emoji injection failed: ${subData.detail}`, 'error');
      return null;
    }
  } else {
    showToast('No high-energy hook keywords detected — no emojis to inject.', 'info');
    return null;
  }
}

document.getElementById('apply-overlay-btn')?.addEventListener('click', async () => {
  if (activeOverlayClipIdx === null) return;
  const clip = generatedClips[activeOverlayClipIdx];
  if (!clip) return;

  const brollPath = document.getElementById('overlay-broll-path')?.value;
  if (!brollPath) {
    showAlert('Please choose a B-roll image or video asset first.');
    return;
  }

  const startTime = parseFloat(document.getElementById('overlay-start-time')?.value || '0');
  const duration = parseFloat(document.getElementById('overlay-duration')?.value || '3');
  const position = document.getElementById('overlay-position')?.value || 'center';
  const scale = parseFloat(document.getElementById('overlay-scale')?.value || '0.9');

  const btn = document.getElementById('apply-overlay-btn');
  if (btn) {
    btn.disabled = true;
    btn.textContent = '⏳ Burning Overlay...';
  }

  try {
    // Prepare emoji-injected ASS path (if enabled) for single-pass B-roll + captions burn
    const injectEmojis = document.getElementById('inject-emojis-toggle')?.checked || false;
    const emojiAssPath = await prepareEmojiAssPath(clip, injectEmojis);

    const res = await fetch(`${serverUrl}/tools/overlay`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_path: clip.output_file,
        broll_path: brollPath,
        start_time: startTime,
        duration: duration,
        scale: scale,
        position: position,
        // Pass the ASS path so captions + B-roll are burned in ONE encode
        subtitle_path: emojiAssPath,
      })
    });
    const data = await res.json();
    if (res.ok) {
      playSuccessSound();
      closeOverlayModal();
      if (data.output_file) clip.output_file = data.output_file;
      else if (data.output_path) clip.output_file = data.output_path;
      showResults(generatedClips);
      showToast('✨ B-Roll Overlay burned into clip!', 'success');
    } else {
      playErrorSound();
      showAlert(`Overlay failed: ${data.detail}`);
    }
  } catch (err) {
    playErrorSound();
    showAlert(`Overlay error: ${err.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = '✨ Burn B-Roll Overlay';
    }
  }
});

document.getElementById('suggest-emojis-btn')?.addEventListener('click', async () => {
  if (activeOverlayClipIdx === null) return;
  const clip = generatedClips[activeOverlayClipIdx];
  if (!clip || !clip.words || !clip.words.length) {
    showAlert('No transcript word timing available for this clip.');
    return;
  }

  try {
    const res = await fetch(`${serverUrl}/tools/suggest-emojis`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        segments: [{ id: 1, start: clip.start_time, end: clip.end_time, text: clip.hook_text || '', words: clip.words }]
      })
    });
    const data = await res.json();
    if (res.ok && data.suggestions && data.suggestions.length) {
      playSuccessSound();
      showAlert(`💡 Suggested Emojis based on transcript:\n${data.suggestions.map(s => `• "${s.word}" (${s.timestamp}s) -> ${s.emoji}`).join('\n')}`);
    } else {
      showAlert('No high-energy hook keywords detected in this clip segment.');
    }
  } catch (err) {
    showAlert(`Error: ${err.message}`);
  }
});

document.addEventListener('click', (e) => {
  if (e.target.closest('button, .btn, .dep-row')) playClick();
});

// ------------------------------------------------------------------
// Job Queue Manager Modal & Background Poller
// ------------------------------------------------------------------
let queuePollerInterval = null;
let queueBadgeInterval = null;

function openQueueModal() {
  const modal = document.getElementById('queue-modal');
  if (modal) modal.classList.remove('hidden');
  refreshQueueList();
  if (!queuePollerInterval) {
    queuePollerInterval = setInterval(refreshQueueList, 2000);
  }
}

function closeQueueModal() {
  const modal = document.getElementById('queue-modal');
  if (modal) modal.classList.add('hidden');
  if (queuePollerInterval) {
    clearInterval(queuePollerInterval);
    queuePollerInterval = null;
  }
}

document.getElementById('open-queue-btn')?.addEventListener('click', openQueueModal);
document.getElementById('close-queue-modal')?.addEventListener('click', closeQueueModal);
document.getElementById('refresh-queue-btn')?.addEventListener('click', refreshQueueList);

async function refreshQueueList() {
  try {
    const res = await fetch(`${serverUrl}/jobs`);
    if (!res.ok) return;
    const data = await res.json();
    const jobs = data.jobs || [];

    // Update queue badge count (active or queued jobs)
    const activeCount = jobs.filter(j => j.status === 'queued' || j.status === 'processing').length;
    updateQueueBadges(activeCount);

    const summary = document.getElementById('queue-summary-text');
    if (summary) {
      const done = jobs.filter(j => j.status === 'completed').length;
      const failed = jobs.filter(j => j.status === 'failed' || j.status === 'cancelled').length;
      const parts = [`${activeCount} active`];
      if (done) parts.push(`${done} done`);
      if (failed) parts.push(`${failed} stopped`);
      summary.textContent = `${parts.join(' · ')} — ${jobs.length} total this session`;
    }

    const list = document.getElementById('queue-list');
    if (!list) return;

    if (!jobs.length) {
      list.innerHTML = `
        <div class="empty-state muted">
          <div class="empty-state-icon" aria-hidden="true">📭</div>
          <h3>Queue is empty</h3>
          <p>Background rendering jobs appear here. Drop multiple videos at once, or start a clip while another is running, and they'll line up.</p>
        </div>`;
      return;
    }

    const STATUS_META = {
      queued: { icon: '⏳', label: 'Queued' },
      processing: { icon: '⚙️', label: 'Processing' },
      completed: { icon: '✅', label: 'Completed' },
      failed: { icon: '❌', label: 'Failed' },
      cancelled: { icon: '🚫', label: 'Cancelled' },
    };

    list.innerHTML = '';
    jobs.slice().reverse().forEach(job => {
      const item = document.createElement('div');
      const status = String(job.status || '').toLowerCase();
      const statusClass = `status-${status.replace(/[^a-z0-9_-]/gi, '')}`;
      item.className = `queue-item ${statusClass}`;

      const isRunning = status === 'processing' || status === 'queued';
      // Clamp progress to a real 0-100 number before it lands in a style attr.
      const pct = Math.max(0, Math.min(100, Number(job.progress) || 0));
      const jobId = String(job.job_id || '');
      const meta = STATUS_META[status] || { icon: '•', label: job.status || 'Unknown' };
      const clipCount = Array.isArray(job.clips) ? job.clips.length : (job.clip_count || null);
      const srcName = job.source_name || job.video_name || (job.video_path
        ? String(job.video_path).replace(/\\/g, '/').split('/').pop()
        : `Job #${jobId.slice(0, 8)}`);

      item.innerHTML = `
        <div class="queue-item-header">
          <span class="queue-item-title" title="${escapeHtml(srcName)}">${escapeHtml(srcName)}</span>
          <span class="queue-item-badge ${statusClass}">${meta.icon} ${escapeHtml(meta.label)}</span>
        </div>
        <div class="queue-item-step">${escapeHtml(job.step || (isRunning ? 'Waiting in line…' : ''))}${isRunning ? ` · ${pct}%` : (clipCount != null ? ` · ${clipCount} clip${clipCount === 1 ? '' : 's'}` : '')}</div>
        <div class="queue-progress-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}">
          <div class="queue-progress-fill" style="width: ${pct}%;"></div>
        </div>
        <div class="queue-item-actions">
          ${isRunning ? `<button class="btn btn-small btn-danger" data-cancel-job="${escapeHtml(jobId)}">🛑 Cancel</button>` : ''}
          ${status === 'completed' ? `<button class="btn btn-small btn-primary" data-view-job="${escapeHtml(jobId)}">👁️ View Clips</button>` : ''}
        </div>
      `;

      item.querySelector('[data-cancel-job]')?.addEventListener('click', async () => {
        try {
          await fetch(`${serverUrl}/job/${job.job_id}/cancel`, { method: 'POST' });
          showToast('Cancellation requested', 'info');
        } catch (_) {
          showToast('Could not reach the server to cancel.', 'error');
        }
        refreshQueueList();
      });

      item.querySelector('[data-view-job]')?.addEventListener('click', async () => {
        try {
          const r = await fetch(`${serverUrl}/job/${job.job_id}`);
          if (!r.ok) return;
          const jobData = await r.json();
          if (jobData.clips) {
            closeQueueModal();
            showResults(jobData.clips);
          }
        } catch (_) { /* server offline */ }
      });

      list.appendChild(item);
    });
  } catch (e) { /* server offline or unreachable */ }
}

// Background badge poller every 5 seconds
queueBadgeInterval = setInterval(async () => {
  try {
    const res = await fetch(`${serverUrl}/jobs`);
    if (!res.ok) return;
    const data = await res.json();
    const jobs = data.jobs || [];
    const activeCount = jobs.filter(j => j.status === 'queued' || j.status === 'processing').length;
    updateQueueBadges(activeCount);
  } catch (e) {}
}, 5000);

// Keep both the header and sidebar queue badges in sync.
function updateQueueBadges(activeCount) {
  ['queue-badge', 'sidebar-queue-badge'].forEach((id) => {
    const badge = document.getElementById(id);
    if (badge) {
      badge.textContent = String(activeCount);
      badge.classList.toggle('hidden', activeCount === 0);
    }
  });
}

// Initialize on DOM ready. The <script> tag sits at the end of <body>, so the
// DOM is already parsed; DOMContentLoaded is still the single entry point so
// initialization can never run twice.
document.addEventListener('DOMContentLoaded', () => {
  bindEstimator();
  init();
});

// Stop every timer and release the audio context when the window goes away,
// so a reload or quit does not leave orphaned pollers behind.
window.addEventListener('beforeunload', () => {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  if (queuePollerInterval) { clearInterval(queuePollerInterval); queuePollerInterval = null; }
  if (queueBadgeInterval) { clearInterval(queueBadgeInterval); queueBadgeInterval = null; }
});
