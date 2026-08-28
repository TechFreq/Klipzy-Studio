// Renderer - UI logic for the AI Video Clipper
// Talks to the local Python FastAPI server

let serverUrl = 'http://127.0.0.1:8765';
let selectedVideo = null;
let pollTimer = null;
let generatedClips = [];
let currentEditingClip = null;

// Manual trim state
let trimState = {
  duration: 0,
  start: 0,
  end: 0,
  camVideo: null,
};

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

  // Start clipping
  document.getElementById('start-clipping').addEventListener('click', startClipping);

  // Manual trim
  document.getElementById('add-trim-btn').addEventListener('click', addTrimmedClip);
  document.getElementById('trim-preview-btn').addEventListener('click', previewTrimSelection);
  document.getElementById('trim-layout').addEventListener('change', (e) => {
    const isReaction = e.target.value === 'game_reaction';
    document.getElementById('cam-options').classList.toggle('hidden', !isReaction);
    // Reaction PiP is full-frame, so no smart-crop offset needed.
    document.getElementById('trim-video').style.display = '';
  });
  document.getElementById('pick-cam-btn').addEventListener('click', pickCameraClip);
  document.getElementById('trim-video').addEventListener('loadedmetadata', () => {
    trimState.duration = document.getElementById('trim-video').duration || 0;
    if (trimState.duration) {
      const third = trimState.duration / 3;
      trimState.start = 0;
      trimState.end = trimState.duration;
      updateTrimUI();
    }
  });
  setupTrimTimeline();

  // Chat
  document.getElementById('chat-send').addEventListener('click', sendChat);
  document.getElementById('chat-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') sendChat();
  });
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
  document.getElementById('file-name').textContent = file.name;
  document.getElementById('file-info').classList.remove('hidden');
  document.getElementById('start-clipping').disabled = false;

  // Initialize the manual trimmer with this source.
  const video = document.getElementById('trim-video');
  video.src = `file://${selectedVideo}`;
  trimState.camVideo = null;
  document.getElementById('trim-panel').classList.remove('hidden');
  document.getElementById('cam-path').value = '';
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

  // Click on empty timeline seeks the video.
  timeline.addEventListener('click', (e) => {
    if (e.target === timeline) {
      video.currentTime = xToTime(e.clientX);
    }
  });

  // Drag anywhere non-handle seeks playhead.
  timeline.addEventListener('mousedown', (e) => {
    if (e.target !== handleIn && e.target !== handleOut) {
      const seek = () => { video.currentTime = xToTime(e.clientX); };
      seek();
      const move = (ev) => { ev.preventDefault(); seek(); };
      const up = () => {
        window.removeEventListener('mousemove', move);
        window.removeEventListener('mouseup', up);
      };
      window.addEventListener('mousemove', move);
      window.addEventListener('mouseup', up);
    }
  });

  // Drag in/out handles.
  const handleDrag = (which) => (e) => {
    e.stopPropagation();
    const move = (ev) => {
      ev.preventDefault();
      let t = xToTime(ev.clientX);
      if (which === 'in') trimState.start = Math.min(t, trimState.end - 0.1);
      else trimState.end = Math.max(t, trimState.start + 0.1);
      paint(document.getElementById('trim-selection'), timeToPct(trimState.start), timeToPct(trimState.end));
    };
    const up = () => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  };

  handleIn.addEventListener('mousedown', handleDrag('in'));
  handleOut.addEventListener('mousedown', handleDrag('out'));

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
    alert('Please wait for the video to finish loading first.');
    return;
  }
  if (trimState.end - trimState.start < 1) {
    alert('Please select at least 1 second of footage.');
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
    document.getElementById('results').classList.remove('hidden');
    alert('✅ Trimmed clip rendered!');
  } catch (err) {
    alert(`Trim failed: ${err.message || err}`);
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
    max_clips: parseInt(document.getElementById('max-clips').value) || 5,
    min_duration: parseFloat(document.getElementById('min-duration').value) || 20,
    max_duration: parseFloat(document.getElementById('max-duration').value) || 60,
    whisper_model: document.getElementById('whisper-model').value,
    use_audio_energy: document.getElementById('audio-energy').checked,
    use_llm: document.getElementById('use-llm').checked,
    burn_captions: document.getElementById('burn-captions').checked,
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

  pollTimer = setInterval(async () => {
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
    }
  }, 1000);
}

// ------------------------------------------------------------------
// Results rendering & Viral Insights
// ------------------------------------------------------------------
function showResults(clips) {
  generatedClips = clips;
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

  const v = clip.virality || { hook_score: 8.5, flow_score: 8.0, engagement_score: 9.0, trend_potential: 'High' };

  card.innerHTML = `
    <video controls src="file://${clip.output_file}"></video>
    <div class="clip-info">
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <div class="clip-title">${clip.title}</div>
        <span class="virality-badge">🔥 Virality: ${clip.score}/10</span>
      </div>
      <div class="virality-metrics">
        <span class="metric-pill">Hook: <strong>${v.hook_score}/10</strong></span>
        <span class="metric-pill">Flow: <strong>${v.flow_score}/10</strong></span>
        <span class="metric-pill">Trend: <strong>${v.trend_potential}</strong></span>
      </div>
      <div class="clip-meta">${clip.duration}s duration</div>
      <div class="clip-hook">"${clip.hook_text}"</div>
      <div class="clip-actions" style="margin-top: 10px; display: flex; gap: 8px;">
        <button class="btn btn-small" onclick="openCaptionEditor(${idx})">✏️ Edit Captions</button>
        <button class="btn btn-small" onclick="revealInFolder('${clip.output_file}')">📂 Open</button>
      </div>
    </div>
  `;
  return card;
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
    alert("Please generate clips first before exporting a project timeline.");
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
      alert(`✅ ${data.message}\nSaved at: ${data.export_path}`);
    } else {
      alert(`Export failed: ${data.detail}`);
    }
  } catch (err) {
    alert(`Export error: ${err.message}`);
  }
}

document.getElementById('export-premiere')?.addEventListener('click', () => exportProject('fcpxml'));
document.getElementById('export-davinci')?.addEventListener('click', () => exportProject('edl'));
document.getElementById('export-capcut')?.addEventListener('click', () => exportProject('capcut'));

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
      `<span class="word-text" contenteditable="true">${w.word}</span> ` +
      `<small class="word-time" data-start="${w.start}" data-end="${w.end}" style="color:var(--text-muted);">[${w.start.toFixed(1)}s]</small>`;
    chipsContainer.appendChild(chip);
  });

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
    alert('No editable words found.');
    return;
  }

  const preset = document.getElementById('caption-preset')?.value || 'opus_yellow';
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
      alert(`✅ ${data.message}\n\nNote: burned-in captions require re-rendering the clip to take effect.`);
    } else {
      alert(`Save failed: ${data.detail || 'Unknown error'}`);
    }
  } catch (err) {
    alert(`Save error: ${err.message}`);
  }
  document.getElementById('caption-modal').classList.add('hidden');
});

function revealInFolder(filePath) {
  // Reveal in OS file manager via Electron IPC (fallback: copy path).
  if (window.clipperAPI && window.clipperAPI.revealInFolder) {
    window.clipperAPI.revealInFolder(filePath);
  } else {
    navigator.clipboard.writeText(filePath).catch(() => {});
    alert(`Path copied to clipboard:\n${filePath}`);
  }
}

function showError(message) {
  const btn = document.getElementById('start-clipping');
  btn.disabled = false;
  btn.textContent = '🚀 Start Clipping';
  document.getElementById('progress-container').classList.add('hidden');
  alert(`Error: ${message}`);
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
  const accel = torch.cuda ? 'CUDA ✓' : (torch.mps ? 'Apple Silicon (MPS) ✓' : 'CPU only');
  document.getElementById('setup-hardware').innerHTML = `
    <div class="hw-grid">
      <div class="hw-item"><span class="hw-label">OS</span><strong>${escapeHtml(data.os || 'unknown')}</strong></div>
      <div class="hw-item"><span class="hw-label">Python</span><strong>${escapeHtml(py.version || '?')}</strong></div>
      <div class="hw-item"><span class="hw-label">CPU</span><strong>${escapeHtml(String(cpu.cores || '?'))} cores</strong></div>
      <div class="hw-item"><span class="hw-label">RAM</span><strong>${escapeHtml(String(cpu.ram_gb || '?'))} GB</strong></div>
      <div class="hw-item"><span class="hw-label">GPU</span><strong>${escapeHtml(gpuName)}</strong></div>
      <div class="hw-item"><span class="hw-label">Acceleration</span><strong>${escapeHtml(accel)}</strong></div>
    </div>`;
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
      <div class="rec-meta">${escapeHtml(it.realtime_factor || '')}</div>
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
    label: 'Ollama',
    desc: 'Local AI engine for clip suggestions (port 11434)',
    check: (d) => d.ollama && d.ollama.installed,
    statusText: (d) => {
      if (!d.ollama || !d.ollama.installed) return 'Missing';
      return d.ollama.running ? '✅ Running' : 'Installed (offline · launch app)';
    },
  },
  lmstudio: {
    label: 'LM Studio',
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
  const renderable = ['ffmpeg', 'whisper', 'ollama', 'lmstudio', 'pytorch', 'ultralytics'];
  const rows = renderable.map((key) => {
    const def = DEP_DEF[key];
    const ok = def.check(data);
    const cmdAvailable = !!cmds[key];
    const statusMark = ok ? '✅' : '❌';
    const tag = def.statusText ? def.statusText(data) : (ok ? 'Installed' : 'Missing');
    return `
      <div class="dep-row ${ok ? 'ok' : 'missing'}">
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
          alert(`Install failed: ${data.error}`);
          btn.disabled = false;
          btn.textContent = '⬇️ Install';
        } else if (data.returncode === 0) {
          alert(`✅ ${key} installed successfully!\n\nCommand: ${data.command}`);
          loadSetupPanel();
        } else {
          alert(`Install may have failed (code ${data.returncode}).\n\nCommand: ${data.command}\n\n${data.stderr || data.stdout || ''}`);
          btn.disabled = false;
          btn.textContent = '⬇️ Install';
        }
      } catch (e) {
        alert(`Install error: ${e.message}`);
        btn.disabled = false;
        btn.textContent = '⬇️ Install';
      }
    });
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
bindEstimator();
init();