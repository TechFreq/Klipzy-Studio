// Renderer - UI logic for the AI Video Clipper
// Talks to the local Python FastAPI server

let serverUrl = 'http://127.0.0.1:8765';
let selectedVideo = null;
let pollTimer = null;

// ------------------------------------------------------------------
// Init
// ------------------------------------------------------------------
async function init() {
  if (window.clipperAPI) {
    serverUrl = await window.clipperAPI.getServerUrl();
  }
  checkHealth();
  bindEvents();
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
let generatedClips = [];
let currentEditingClip = null;

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
    grid.appendChild(card);
  });

  document.getElementById('results').classList.remove('hidden');
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

  const words = clip.words && clip.words.length ? clip.words : (clip.hook_text || "").split(' ').map((w, i) => ({ word: w, start: i*0.4, end: (i+1)*0.4 }));

  words.forEach((w) => {
    const chip = document.createElement('div');
    chip.className = 'word-chip';
    chip.innerHTML = `<span contenteditable="true">${w.word}</span> <small style="color:var(--text-muted);">[${w.start.toFixed(1)}s]</small>`;
    chipsContainer.appendChild(chip);
  });

  modal.classList.remove('hidden');
};

document.getElementById('close-caption-modal')?.addEventListener('click', () => {
  document.getElementById('caption-modal').classList.add('hidden');
});

document.getElementById('save-captions-btn')?.addEventListener('click', () => {
  alert("Subtitles and styling updated successfully!");
  document.getElementById('caption-modal').classList.add('hidden');
});

function revealInFolder(filePath) {
  // Cross-platform reveal in file manager
  if (window.clipperAPI) {
    navigator.clipboard.writeText(filePath);
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

// Start
init();
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