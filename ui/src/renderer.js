// Renderer - UI logic for Klipzy Studio (client)
// Talks to the local Python FastAPI server

let serverUrl = 'http://127.0.0.1:8765';
let selectedVideo = null;
let pollTimer = null;
let generatedClips = [];
let currentEditingClip = null;
let currentProjectId = null;
const PROJECTS_KEY = 'klipzy.projects.v1';

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

function showCustomDialog({ title = 'Notification', message = '', isConfirm = false }) {
  return new Promise((resolve) => {
    const modal = document.getElementById('dialog-modal');
    const titleEl = document.getElementById('dialog-title');
    const msgEl = document.getElementById('dialog-message');
    const cancelBtn = document.getElementById('dialog-cancel-btn');
    const confirmBtn = document.getElementById('dialog-confirm-btn');
    const closeBtn = document.getElementById('dialog-close');

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

    modal.classList.remove('hidden');

    function cleanup(val) {
      modal.classList.add('hidden');
      confirmBtn.removeEventListener('click', onConfirm);
      cancelBtn.removeEventListener('click', onCancel);
      closeBtn.removeEventListener('click', onCancel);
      resolve(val);
    }

    function onConfirm() { cleanup(true); }
    function onCancel() { cleanup(false); }

    confirmBtn.addEventListener('click', onConfirm);
    cancelBtn.addEventListener('click', onCancel);
    closeBtn.addEventListener('click', onCancel);
  });
}

function showAlert(message, title = 'Notification') {
  return showCustomDialog({ title, message, isConfirm: false });
}

function showConfirm(message, title = 'Please Confirm') {
  return showCustomDialog({ title, message, isConfirm: true });
}

// ------------------------------------------------------------------
// Init
// ------------------------------------------------------------------
async function init() {
  if (window.clipperAPI) {
    serverUrl = await window.clipperAPI.getServerUrl();
  }
  checkHealth();
  bindEvents();
  loadSetupPanel();
  loadProjectList();
  populateCaptionPresets();
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
  let presets = null;
  try {
    const res = await fetch(`${serverUrl}/caption-presets`);
    if (res.ok) presets = await res.json();
  } catch (_) { /* offline -> fall back to static list */ }

  const ids = presets && presets.length
    ? presets.map((p) => p.id)
    : CAPTION_PRESET_IDS;

  const opts = ids
    .map((id) => `<option value="${escapeHtml(id)}">${escapeHtml(CAPTION_PRESET_LABELS[id] || id)}</option>`)
    .join('');

  ['global-caption-preset', 'trim-caption-preset', 'caption-preset'].forEach((selId) => {
    const sel = document.getElementById(selId);
    if (sel && !sel.dataset.populated) {
      const current = sel.value;
      sel.innerHTML = opts;
      if (current && ids.includes(current)) sel.value = current;
      sel.dataset.populated = '1';
    }
  });
}

function bindEvents() {
  // Navigation
  document.querySelectorAll('.nav-item').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.nav-item').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
      document.getElementById(`view-${btn.dataset.view}`).classList.add('active');
    });
  });

  // File selection
  const dropZone = document.getElementById('drop-zone');
  const fileInput = document.getElementById('file-input');

  dropZone.addEventListener('click', () => fileInput.click());
  dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
  dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    const file = e.dataTransfer.files[0];
    if (file) selectVideoFile(file);
  });
  fileInput.addEventListener('change', (e) => {
    if (e.target.files[0]) selectVideoFile(e.target.files[0]);
  });

  document.getElementById('change-file').addEventListener('click', () => fileInput.click());
  document.getElementById('save-project').addEventListener('click', saveCurrentProject);
  document.getElementById('project-list').addEventListener('change', (e) => {
    if (e.target.value) openProject(e.target.value);
  });
  document.getElementById('delete-project').addEventListener('click', deleteCurrentProject);

  // Start clipping
  document.getElementById('start-clipping').addEventListener('click', startClipping);

  // Manual trim
  document.getElementById('add-trim-btn').addEventListener('click', addTrimmedClip);
  document.getElementById('trim-preview-btn').addEventListener('click', previewTrimSelection);
  document.getElementById('trim-layout').addEventListener('change', (e) => {
    const isReaction = e.target.value === 'game_reaction';
    document.getElementById('cam-options').classList.toggle('hidden', !isReaction);
    // Reaction PiP is full-frame, so no smart-crop offset needed.
    applyTrimPreviewRatio(e.target.value);
    document.getElementById('trim-video').style.display = '';
  });
  document.getElementById('pick-cam-btn').addEventListener('click', pickCameraClip);
  document.getElementById('trim-video').addEventListener('loadedmetadata', () => {
    trimState.duration = document.getElementById('trim-video').duration || 0;
    if (trimState.duration) {
      const third = trimState.duration / 3;
      trimState.start = 0;
      trimState.end = trimState.duration;
      applyTrimPreviewRatio();
      updateTrimUI();
    }
  });
  setupTrimTimeline();

  // Chat
  document.getElementById('chat-send').addEventListener('click', sendChat);
  document.getElementById('chat-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') sendChat();
  });

  // Export-as format actions
  document.getElementById('export-compile')?.addEventListener('click', exportCompileReel);

  // Caption preset select inside the caption editor modal refreshes the preview.
  const captionPresetSelect = document.getElementById('caption-preset');
  if (captionPresetSelect) {
    captionPresetSelect.addEventListener('change', refreshCaptionPreview);
  }
}

// ------------------------------------------------------------------
// Server health
// ------------------------------------------------------------------
async function checkHealth() {
  const statusEl = document.getElementById('health-status');
  try {
    const res = await fetch(`${serverUrl}/health`);
    const data = await res.json();
    statusEl.innerHTML = `<span class="dot ok"></span> Server ready`;
    if (!data.ffmpeg_available) {
      statusEl.innerHTML = `<span class="dot error"></span> FFmpeg missing`;
  // Caption preset select inside manual trim refreshes preview
  const trimCaptionPresetSelect = document.getElementById('trim-caption-preset');
  if (trimCaptionPresetSelect) {
    trimCaptionPresetSelect.addEventListener('change', applyTrimCaptionStyle);
    applyTrimCaptionStyle();
  }

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
  currentProjectId = null;
  document.getElementById('file-name').textContent = file.name;
  document.getElementById('file-info').classList.remove('hidden');
  document.getElementById('project-bar').classList.remove('hidden');
  document.getElementById('project-name').value = file.name.replace(/\.[^.]+$/, '');
  document.getElementById('start-clipping').disabled = false;
  document.getElementById('clips-grid').innerHTML = '';
  document.getElementById('results').classList.add('hidden');

  // Initialize the manual trimmer with this source.
  const video = document.getElementById('trim-video');
  video.src = `file://${selectedVideo}`;
  trimState.camVideo = null;
  document.getElementById('trim-panel').classList.remove('hidden');
  document.getElementById('cam-path').value = '';
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
  select.innerHTML = '<option value="">Open saved project…</option>';
  readProjects().sort((a, b) => (b.updatedAt || '').localeCompare(a.updatedAt || '')).forEach((project) => {
    const option = document.createElement('option');
    option.value = project.id;
    option.textContent = project.name || 'Untitled project';
    select.appendChild(option);
  });
}

function saveProjectManifest(showMessage = false) {
  if (!selectedVideo) return;
  const name = document.getElementById('project-name').value.trim() || 'Untitled project';
  const projects = readProjects();
  const project = {
    id: currentProjectId || `project-${Date.now()}`,
    name,
    source: selectedVideo,
    sourceName: document.getElementById('file-name').textContent,
    clips: generatedClips,
    updatedAt: new Date().toISOString(),
  };
  currentProjectId = project.id;
  const index = projects.findIndex((item) => item.id === project.id);
  if (index >= 0) projects[index] = project; else projects.push(project);
  writeProjects(projects);
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
  currentProjectId = project.id;
  selectedVideo = project.source;
  generatedClips = Array.isArray(project.clips) ? project.clips : [];
  document.getElementById('file-name').textContent = project.sourceName || project.source;
  document.getElementById('file-info').classList.remove('hidden');
  document.getElementById('project-bar').classList.remove('hidden');
  document.getElementById('project-name').value = project.name || '';
  document.getElementById('start-clipping').disabled = !selectedVideo;
  const video = document.getElementById('trim-video');
  video.src = `file://${selectedVideo}`;
  document.getElementById('trim-panel').classList.remove('hidden');
  showResults(generatedClips);
  document.getElementById('project-list').value = id;
}

async function deleteCurrentProject() {
  if (!currentProjectId) return showAlert('Save this project first, then it can be deleted.');
  const projects = readProjects();
  const project = projects.find((item) => item.id === currentProjectId);
  const confirmed = await showConfirm(`Delete project “${project.name}” and its generated files?`);
  if (!project || !confirmed) return;
  const paths = (project.clips || []).map((clip) => clip.output_file).filter(Boolean);
  try {
    await fetch(`${serverUrl}/project/delete`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths }),
    });
  } catch (_) { /* local project deletion still proceeds */ }
  writeProjects(projects.filter((item) => item.id !== currentProjectId));
  currentProjectId = null;
  generatedClips = [];
  selectedVideo = null;
  document.getElementById('project-name').value = '';
  document.getElementById('project-bar').classList.add('hidden');
  document.getElementById('file-info').classList.add('hidden');
  document.getElementById('trim-panel').classList.add('hidden');
  document.getElementById('results').classList.add('hidden');
  document.getElementById('clips-grid').innerHTML = '';
  loadProjectList();
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
  const payload = {
    video_path: selectedVideo,
    start_seconds: trimState.start,
    end_seconds: trimState.end,
    layout,
    aspect_ratio: layout === 'full' ? null : '9:16',
    cam_video: layout === 'game_reaction' ? trimState.camVideo : null,
    cam_scale: parseFloat(document.getElementById('cam-scale').value || '0.3'),
    cam_position: document.getElementById('cam-position').value,
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
    };
    generatedClips.push(clip);
    appendClipCard(clip, generatedClips.length - 1);
    saveCurrentProjectSilently();
    document.getElementById('results').classList.remove('hidden');
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
async function startClipping() {
  if (!selectedVideo) return;

  const btn = document.getElementById('start-clipping');
  btn.disabled = true;
  btn.textContent = '⏳ Processing...';

  document.getElementById('progress-container').classList.remove('hidden');
  document.getElementById('results').classList.add('hidden');

  const payload = {
    video_path: selectedVideo,
    vertical_crop: document.getElementById('vertical-crop').checked,
    aspect_ratio: document.getElementById('clip-aspect-ratio') ? document.getElementById('clip-aspect-ratio').value : '9:16',
    caption_style: document.getElementById('global-caption-preset') ? document.getElementById('global-caption-preset').value : 'viral_yellow',
    max_clips: parseInt(document.getElementById('max-clips').value) || 5,
    min_duration: parseFloat(document.getElementById('min-duration').value) || 20,
    max_duration: parseFloat(document.getElementById('max-duration').value) || 60,
    whisper_model: document.getElementById('whisper-model').value,
    use_audio_energy: document.getElementById('audio-energy').checked,
    use_llm: document.getElementById('use-llm').checked,
    burn_captions: document.getElementById('burn-captions').checked,
    remove_silence: document.getElementById('remove-silence') ? document.getElementById('remove-silence').checked : false,
    bleep_profanity: document.getElementById('censor-profanity') ? document.getElementById('censor-profanity').checked : false,
    mute_profanity: false,
  };

  try {
    const res = await fetch(`${serverUrl}/process`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.job_id) {
      pollJob(data.job_id);
    } else {
      throw new Error(data.detail || 'Failed to start job');
    }
  } catch (e) {
    showError(e.message);
  }
}

function pollJob(jobId) {
  if (pollTimer) clearInterval(pollTimer);

  let ticking = false;
  pollTimer = setInterval(async () => {
    if (ticking) return; // don't stack requests if a poll takes longer than the interval
    ticking = true;
    try {
      const res = await fetch(`${serverUrl}/job/${jobId}`);
      const data = await res.json();

      document.getElementById('progress-step').textContent = data.step || 'Processing...';
      document.getElementById('progress-fill').style.width = `${data.progress || 0}%`;

      if (data.status === 'completed') {
        clearInterval(pollTimer);
        showResults(data.clips);
      } else if (data.status === 'failed') {
        clearInterval(pollTimer);
        showError(data.error || 'Processing failed');
      }
    } catch (e) {
      // transient error, keep polling
    } finally {
      ticking = false;
    }
  }, 650); // 650ms is smooth enough for the progress bar without hammering localhost
}

// ------------------------------------------------------------------
// Results rendering & Viral Insights
// ------------------------------------------------------------------
function showResults(clips) {
  playSuccessSound();
  generatedClips = clips || [];
  saveCurrentProjectSilently();
  const btn = document.getElementById('start-clipping');
  btn.disabled = false;
  btn.textContent = '🚀 Start Clipping';

  document.getElementById('progress-container').classList.add('hidden');

  const grid = document.getElementById('clips-grid');
  grid.innerHTML = '';

  clips.forEach((clip, idx) => {
    grid.appendChild(buildClipCard(clip, idx));
  });

  document.getElementById('results').classList.remove('hidden');
}

function buildClipCard(clip, idx) {
  const card = document.createElement('div');
  card.className = 'clip-card';
  card.dataset.clipIdx = String(idx);

  const v = clip.virality || { hook_score: 8.5, flow_score: 8.0, engagement_score: 9.0, trend_potential: 'High' };
  const title = clip.title || (clip.hook_text ? clip.hook_text.slice(0, 48) : 'Highlight');
  const desc = clip.hook_text || clip.reason || 'AI-selected moment with strong virality signals.';
  const score = clip.score != null ? Number(clip.score).toFixed(1) : '–';

  card.innerHTML = `
    <video controls preload="metadata"></video>
    <div class="clip-info">
      <div class="clip-headline">
        <div class="clip-title">${escapeHtml(title)}</div>
        <span class="virality-badge">🔥 Virality: ${score}/10</span>
      </div>
      <div class="virality-metrics">
        <span class="metric-pill">Hook: <strong>${escapeHtml(String(v.hook_score))}</strong></span>
        <span class="metric-pill">Flow: <strong>${escapeHtml(String(v.flow_score))}</strong></span>
        <span class="metric-pill">Trend: <strong>${escapeHtml(String(v.trend_potential))}</strong></span>
      </div>
      <div class="clip-meta">${escapeHtml(String(clip.duration))}s duration</div>
      <div class="clip-desc">${escapeHtml(desc)}</div>
    </div>
    <div class="clip-actions">
      <button class="btn btn-small" data-action="edit-captions">✏️ Edit Captions</button>
      <button class="btn btn-small" data-action="snip-silence" title="Auto-cut dead air pauses">✂️ Snip Silence</button>
      <button class="btn btn-small" data-action="bleep" title="Bleep or mute profanity">🔇 Bleep</button>
      <button class="btn btn-small" data-action="open-folder">📂 Open</button>
      <button class="btn btn-small btn-danger" data-action="delete" title="Remove this clip">🗑 Delete</button>
      <select class="export-format-select clip-export-fmt" data-clip-idx="${idx}" title="Clip output format">
        <option value="mp4">📦 MP4</option>
        <option value="mov">📦 MOV</option>
        <option value="mkv">📦 MKV</option>
        <option value="webm">📦 WebM</option>
        <option value="gif">📦 GIF</option>
      </select>
      <button class="btn btn-small btn-export" data-action="export" data-clip-idx="${idx}">🚀 Export</button>
    </div>
  `;

  const video = card.querySelector('video');
  video.src = `file://${clip.output_file}`;

  card.addEventListener('click', (e) => {
    const actionBtn = e.target.closest('[data-action]');
    if (!actionBtn) return;
    const idx2 = parseInt(card.dataset.clipIdx, 10);
    switch (actionBtn.dataset.action) {
      case 'edit-captions': openCaptionEditor(idx2); break;
      case 'snip-silence': quickCutSilence(idx2); break;
      case 'bleep': quickBleepClip(idx2); break;
      case 'open-folder': revealInFolder(clip.output_file); break;
      case 'export': exportSingleClip(idx2); break;
    }
  });

  const deleteBtn = card.querySelector('[data-action="delete"]');
  deleteBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    const confirmed = await showConfirm(`Delete "${title || 'this clip'}" from the grid?`);
    if (!confirmed) return;
    deleteClip(idx);
  });

  return card;
}

function deleteClip(idx) {
  if (!generatedClips.length) return;
  generatedClips.splice(idx, 1);
  saveCurrentProjectSilently();
  const grid = document.getElementById('clips-grid');
  grid.innerHTML = '';
  generatedClips.forEach((clip, i) => grid.appendChild(buildClipCard(clip, i)));
  if (!generatedClips.length) {
    document.getElementById('results').classList.add('hidden');
  }
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

  try {
    const res = await fetch(`${serverUrl}/export/project`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_path: selectedVideo,
        clips: generatedClips,
        format: format,
        fps: 30.0
      })
    });
    const data = await res.json();
    if (res.ok) {
      playSuccessSound();
      showAlert(`✅ ${data.message}\nSaved at: ${data.export_path}`);
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
async function exportStandaloneAsset() {
  if (!selectedVideo) {
    playErrorSound();
    showAlert("Please select and load a video file first.");
    return;
  }
  const sel = document.getElementById('standalone-asset');
  const assetType = sel ? sel.value : 'audio_mp3';
  const btn = document.getElementById('export-standalone-btn');
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
      })
    });
    const data = await res.json();
    if (res.ok) {
      playSuccessSound();
      showAlert(`✅ ${data.message}\nSaved at: ${data.export_path}`);
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
      btn.textContent = '💾 Export Asset';
    }
  }
}

document.getElementById('export-standalone-btn')?.addEventListener('click', exportStandaloneAsset);

// ------------------------------------------------------------------
// Export-As Media (single clip -> mp4/mov/mkv/webm/gif)
// ------------------------------------------------------------------
window.exportSingleClip = async function (clipIndex) {
  const clip = generatedClips[clipIndex];
  if (!clip || !clip.output_file) {
    playErrorSound();
    showAlert('No rendered clip to export yet.');
    return;
  }
  const sel = document.querySelector(`.clip-export-fmt[data-clip-idx="${clipIndex}"]`);
  const fmt = sel ? sel.value : 'mp4';
  const btn = document.querySelector(`.btn-export[data-clip-idx="${clipIndex}"]`);
  if (btn) {
    btn.disabled = true;
    btn.textContent = '⏳ Exporting…';
  }
  try {
    const res = await fetch(`${serverUrl}/export/media`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_path: clip.output_file,
        format: fmt,
      })
    });
    const data = await res.json();
    if (res.ok) {
      playSuccessSound();
      showAlert(`✅ ${data.message}\nSaved at: ${data.export_path}`);
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
      })
    });
    const data = await res.json();
    if (res.ok) {
      playSuccessSound();
      showAlert(`✅ ${data.message}\nSaved at: ${data.export_path}`);
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

function applyCaptionPreviewStyle() {
  const preview = document.getElementById('caption-preview');
  if (!preview) return;
  const presetId = document.getElementById('caption-preset')?.value || 'viral_yellow';
  const style = CAPTION_PREVIEW[presetId] || CAPTION_PREVIEW.viral_yellow;

  preview.style.color = style.text;
  preview.style.fontFamily = style.font;
  preview.style.textShadow = style.back !== '#000000'
    ? `3px 3px 0 ${style.back}, 0 0 18px ${style.accent}55`
    : `0 0 18px ${style.accent}55, 0 2px 12px rgba(0,0,0,0.9)`;

  const highlights = preview.querySelectorAll('mark');
  highlights.forEach((m) => { m.style.color = style.accent; });
}

function refreshCaptionPreview() {
  const preview = document.getElementById('caption-preview');
  if (!preview) return;
  const words = preview.dataset.words || 'Your caption appears here';
  // Render words, highlighting every few so the accent shows like a karaoke lead.
  const list = words.split(' ').map((w, i) => (i % 3 === 0 ? `<mark>${escapeHtml(w)}</mark>` : escapeHtml(w)));
  preview.innerHTML = list.join(' ');
  preview.dataset.words = words;
  applyCaptionPreviewStyle();
}
function applyTrimCaptionStyle() {
  const sampleEl = document.getElementById('trim-caption-sample');
  if (!sampleEl) return;
  const presetId = document.getElementById('trim-caption-preset')?.value || 'viral_yellow';
  const style = CAPTION_PREVIEW[presetId] || CAPTION_PREVIEW.viral_yellow;

  sampleEl.style.color = style.text;
  sampleEl.style.fontFamily = style.font;
  sampleEl.style.textShadow = style.back !== '#000000'
    ? `2px 2px 0 ${style.back}, 0 0 14px ${style.accent}66`
    : `0 0 14px ${style.accent}66, 0 2px 10px rgba(0,0,0,0.9)`;

  const highlights = sampleEl.querySelectorAll('mark');
  highlights.forEach((m) => { m.style.color = style.accent; });
}


// ------------------------------------------------------------------
// Interactive Caption Editor
// ------------------------------------------------------------------
window.openCaptionEditor = function(clipIndex) {
  const clip = generatedClips[clipIndex];
  if (!clip) return;
  currentEditingClip = clip;

  const modal = document.getElementById('caption-modal');
  const chipsContainer = document.getElementById('word-chips');
  chipsContainer.innerHTML = '';

  const words = clip.words && clip.words.length ? clip.words : (clip.hook_text || '').split(' ').map((w, i) => ({ word: w, start: i * 0.4, end: (i + 1) * 0.4 }));

  words.forEach((w) => {
    const chip = document.createElement('div');
    chip.className = 'word-chip';
    chip.innerHTML =
      `<span class="word-text" contenteditable="true">${escapeHtml(w.word || String(w))}</span> ` +
      `<small class="word-time" data-start="${w.start}" data-end="${w.end}" style="color:var(--text-muted);">[${w.start.toFixed(1)}s]</small>`;
    chipsContainer.appendChild(chip);
  });

  // Seed the preview with a real snippet from this clip's words.
  const preview = document.getElementById('caption-preview');
  const sample = (clip.hook_text || (clip.words || []).map((x) => x.word).join(' ') || 'Your caption appears here').trim();
  if (preview) {
    preview.dataset.words = sample.length ? sample : 'Your caption appears here';
    refreshCaptionPreview();
  }

  modal.classList.remove('hidden');
};

document.getElementById('close-caption-modal')?.addEventListener('click', () => {
  document.getElementById('caption-modal').classList.add('hidden');
});

document.getElementById('save-captions-btn')?.addEventListener('click', async () => {
  if (!currentEditingClip) {
    document.getElementById('caption-modal').classList.add('hidden');
    return;
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
    showAlert('No editable words found.');
    return;
  }

  const preset = document.getElementById('caption-preset')?.value || 'viral_yellow';
  const outputPath = currentEditingClip.ass_path ? currentEditingClip.ass_path.replace(/\.ass$/i, '.srt') : '';

  try {
    const res = await fetch(`${serverUrl}/export/subtitles`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        output_path: outputPath || `${currentEditingClip.output_file}.srt`,
        words: editedWords,
        style_preset: preset,
      })
    });
    const data = await res.json();
    if (res.ok) {
      // Update in-memory clip state
      currentEditingClip.words = editedWords;
      if (data.export_path) currentEditingClip.srt_path = data.export_path;
      showAlert(`✅ ${data.message}\n\nNote: burned-in captions require re-rendering the clip to take effect.`);
    } else {
      showAlert(`Save failed: ${data.detail || 'Unknown error'}`);
    }
  } catch (err) {
    showAlert(`Save error: ${err.message}`);
  }
  document.getElementById('caption-modal').classList.add('hidden');
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
        timestamps: clip.words ? clip.words.map(w => ({ word: w.word, start: w.start, end: w.end })) : [],
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
  btn.disabled = false;
  btn.textContent = '🚀 Start Clipping';
  document.getElementById('progress-container').classList.add('hidden');
  showAlert(`Error: ${message}`);
}

// ------------------------------------------------------------------
// Chat
// ------------------------------------------------------------------
async function sendChat() {
  const input = document.getElementById('chat-input');
  const message = input.value.trim();
  if (!message) return;

  const messagesEl = document.getElementById('chat-messages');
  appendMessage('user', message);
  input.value = '';

  try {
    const res = await fetch(`${serverUrl}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    });
    const data = await res.json();
    appendMessage('bot', data.reply);
  } catch (e) {
    appendMessage('bot', '⚠️ Could not reach the AI server. Is it running?');
  }
}

function appendMessage(role, text) {
  const messagesEl = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = `chat-message ${role}`;
  div.innerHTML = `<div class="bubble">${escapeHtml(text)}</div>`;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
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
    renderRecommendations(data.recommendations);
    renderDeps(data);
  } catch (e) {
    document.getElementById('setup-hardware').innerHTML = '<span class="muted">⚠️ Could not reach the server.</span>';
  }
}

function loadSupportLinks() {
  fetch(`${serverUrl}/api/setup/support`)
    .then((r) => r.json())
    .then((s) => {
      const map = { 'support-paypal': s.paypal, 'support-beacons': s.beacons, 'support-star': s.star, 'support-issues': s.issues };
      Object.entries(map).forEach(([id, url]) => {
        const el = document.getElementById(id);
        if (el && url) el.setAttribute('href', url);
      });
    })
    .catch(() => {});
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

function renderRecommendations(recs) {
  if (!recs) return;
  const items = [
    { name: '🎤 Whisper (transcription)', ...recs.whisper },
    { name: '👁️ YOLO (face tracking)', ...recs.yolo },
    { name: '🤖 Ollama (AI edit chat)', ...recs.ollama },
  ];
  document.getElementById('setup-recommendations').innerHTML = items.map((it) => `
    <div class="rec-card">
      <div class="rec-name">${escapeHtml(it.name)}</div>
      <div class="rec-model">${escapeHtml(it.model)}</div>
      <div class="rec-meta">
        ${it.engine ? `<span class="rec-chip">⚡ ${escapeHtml(it.engine)}</span>` : ''}
        ${it.realtime_factor ? `<span class="rec-chip">${escapeHtml(it.realtime_factor)}</span>` : ''}
      </div>
      <div class="rec-note muted">${escapeHtml(it.note || '')}</div>
    </div>`).join('');
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
    desc: 'Speech-to-text engine for captions and transcript search',
    check: (d) => d.whisper && d.whisper.installed,
    statusText: (d) => (d.whisper && d.whisper.installed ? 'Installed' : 'Missing'),
  },
  ollama: {
    label: 'Ollama (Required)',
    desc: 'Local LLM engine — auto-pulls the model on first use (or via Pull button below)',
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
  const renderable = ['ollama', 'whisper', 'pytorch', 'ultralytics', 'lmstudio', 'ffmpeg'];
  const rows = renderable.map((key) => {
    const def = DEP_DEF[key];
    const ok = def.check(data);
    const cmdAvailable = !!cmds[key];
    const statusMark = ok ? '✅' : '❌';
    const tag = def.statusText ? def.statusText(data) : (ok ? 'Installed' : 'Missing');
    return `
      <div class="dep-row ${ok ? 'ok' : 'missing'}" data-key="${key}">
        <div class="dep-info">
          <span class="dep-status">${statusMark}</span>
          <div>
            <strong>${def.label}</strong>
            <span class="muted small">${def.desc}</span>
          </div>
        </div>
        <div class="dep-actions">
          ${ok
            ? `<span class="dep-installed">${escapeHtml(tag)}</span>`
            : (cmdAvailable
              ? `<button class="btn btn-small btn-primary" data-install="${key}">⬇️ Install</button>`
              : '<span class="muted small">Manual install needed</span>')}
        </div>
      </div>`;
  }).join('');
  document.getElementById('setup-deps').innerHTML = rows;

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

document.addEventListener('click', (e) => {
  if (e.target.closest('button, .btn, .dep-row')) playClick();
});

bindEstimator();
init();