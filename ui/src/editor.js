/*
 * Built-in clip editor (Phase 1) — the UI behind the "✂️ Edit" button on each
 * generated clip card. Opens a modal with a live <video> preview, reframe chips,
 * a trim timeline, a zoom slider and a music bed, then compiles an Edit Spec
 * (KlipzyEditorSpec) and renders it via the backend /editor/export endpoint.
 *
 * Classic script sharing renderer.js's globals (loaded AFTER it): reuses
 * serverUrl, fileUrl, showToast/showError/showAlert, revealInFolder,
 * generatedClips and window.clipperAPI.* — no duplication of that plumbing.
 *
 * Preview is fully browser-side (HTML5 video + a CSS crop-guide overlay + a CSS
 * zoom transform), so scrubbing/looking stays fluid; ffmpeg only runs on Export.
 */
(function () {
  'use strict';

  var editorClip = null;
  var editorPollTimer = null;
  var editorJobId = null;
  var TIP_SEEN_KEY = 'klipzy.editor.tipSeen';

  function $(id) { return document.getElementById(id); }

  function editorState() {
    var vid = $('editor-video');
    var duration = vid && isFinite(vid.duration) ? vid.duration : (editorClip ? Number(editorClip.duration) || 0 : 0);
    var selChip = document.querySelector('#editor-ratio-chips .ratio-chip.is-selected');
    return {
      source: editorClip ? editorClip.output_file : null,
      duration: duration,
      ratio: selChip ? selChip.dataset.ratio : 'full',
      trimIn: parseFloat($('editor-trim-in').value) || 0,
      trimOut: parseFloat($('editor-trim-out').value) || duration,
      zoom: parseFloat($('editor-zoom').value) || 1,
      music: {
        enabled: $('editor-music-enabled').checked,
        path: $('editor-music-path').value || '',
        gain: parseFloat($('editor-music-volume').value) || 0.12,
        duck: $('editor-music-duck').checked,
      },
    };
  }

  function fmtClock(s) {
    if (!isFinite(s) || s < 0) s = 0;
    var m = Math.floor(s / 60);
    var sec = Math.floor(s % 60);
    return m + ':' + String(sec).padStart(2, '0');
  }

  function updateTrimLabel() {
    var i = parseFloat($('editor-trim-in').value) || 0;
    var o = parseFloat($('editor-trim-out').value) || 0;
    $('editor-trim-label').textContent = 'Clip: ' + fmtClock(i) + ' → ' + fmtClock(o) + '  (' + (Math.max(0, o - i)).toFixed(1) + 's)';
  }

  // Draw a centered crop-guide box over the video matching the target aspect.
  // Purely a visual guide; the real crop happens on export.
  function updateCropOverlay() {
    var overlay = $('editor-crop-overlay');
    var vid = $('editor-video');
    if (!overlay || !vid) return;
    var state = editorState();
    var aspect = KlipzyEditorSpec.overlayAspect(state.ratio);
    if (!aspect || !vid.videoWidth || !vid.videoHeight) {
      overlay.classList.add('hidden');
      return;
    }
    // The video is letterboxed inside its box (object-fit: contain); compute the
    // displayed video rect, then a centered target-aspect box inside it.
    var boxW = vid.clientWidth, boxH = vid.clientHeight;
    var vidAspect = vid.videoWidth / vid.videoHeight;
    var dispW, dispH;
    if (vidAspect > boxW / boxH) { dispW = boxW; dispH = boxW / vidAspect; }
    else { dispH = boxH; dispW = boxH * vidAspect; }

    var cropW, cropH;
    if (aspect > dispW / dispH) { cropW = dispW; cropH = dispW / aspect; }
    else { cropH = dispH; cropW = dispH * aspect; }

    overlay.classList.remove('hidden');
    overlay.style.width = Math.round(cropW) + 'px';
    overlay.style.height = Math.round(cropH) + 'px';
  }

  function applyZoomPreview() {
    var vid = $('editor-video');
    var z = parseFloat($('editor-zoom').value) || 1;
    $('editor-zoom-label').textContent = z.toFixed(2) + '×';
    if (vid) vid.style.transform = z > 1 ? 'scale(' + z + ')' : '';
  }

  function setEditorProgress(pct, step, badge) {
    var p = $('editor-progress');
    if (p) p.classList.remove('hidden');
    var fill = $('editor-progress-fill');
    var percent = Math.max(0, Math.min(100, Math.round(pct || 0)));
    if (fill) fill.style.width = percent + '%';
    var pctEl = $('editor-progress-percent');
    if (pctEl) pctEl.textContent = percent + '%';
    var stepEl = $('editor-progress-step');
    if (stepEl && step) stepEl.textContent = step;
    var badgeEl = $('editor-progress-badge');
    if (badgeEl && badge) badgeEl.textContent = badge;
    var bar = $('editor-progress-bar');
    if (bar) bar.setAttribute('aria-valuenow', String(percent));
  }

  function stopEditorPolling() {
    if (editorPollTimer) { clearInterval(editorPollTimer); editorPollTimer = null; }
    editorJobId = null;
    $('editor-cancel-btn').classList.add('hidden');
    var btn = $('editor-export-btn');
    if (btn) { btn.disabled = false; btn.textContent = '🚀 Export edit'; }
  }

  function pollEditorExport(jobId) {
    editorJobId = jobId;
    var misses = 0;
    editorPollTimer = setInterval(async function () {
      try {
        var res = await fetch(serverUrl + '/editor/export/' + jobId);
        if (!res.ok) {
          misses++;
          if (res.status === 404 || misses >= 5) {
            stopEditorPolling();
            showError('Lost track of the export job.');
          }
          return;
        }
        misses = 0;
        var data = await res.json();
        setEditorProgress(data.percent, data.step, data.state === 'rendering' ? 'Rendering' : data.state);
        if (data.done) {
          stopEditorPolling();
          if (data.state === 'completed') {
            playSuccessSound();
            showAlert('✅ Edited clip exported!', 'Export complete', data.output || null);
          } else if (data.state === 'cancelled') {
            showToast('Export cancelled.', 'info');
          } else {
            showError(data.error || 'Export failed.');
          }
        }
      } catch (e) {
        misses++;
        if (misses >= 8) { stopEditorPolling(); showError('Lost contact with the server during export.'); }
      }
    }, 600);
  }

  async function startExport() {
    var spec = KlipzyEditorSpec.buildEditSpec(editorState());
    if (!spec) { showToast('Nothing to export.', 'info'); return; }
    var btn = $('editor-export-btn');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Rendering…'; }
    $('editor-cancel-btn').classList.remove('hidden');
    setEditorProgress(0, 'Starting…', 'Rendering');
    try {
      var res = await fetch(serverUrl + '/editor/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ spec: spec }),
      });
      var data = await res.json().catch(function () { return {}; });
      if (!res.ok) throw new Error(data.detail || ('Server returned ' + res.status));
      pollEditorExport(data.job_id);
    } catch (e) {
      stopEditorPolling();
      showError(e.message);
    }
  }

  async function cancelExport() {
    if (!editorJobId) return;
    var btn = $('editor-cancel-btn');
    if (btn) { btn.disabled = true; btn.textContent = '⏳…'; }
    try {
      await fetch(serverUrl + '/editor/export/' + editorJobId + '/cancel', { method: 'POST' });
      showToast('Cancellation requested.', 'info');
    } catch (e) {
      showToast('Failed to cancel: ' + e.message, 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '🛑 Cancel'; }
    }
  }

  function showTutorial() {
    showAlert(
      '1. Reframe — pick a ratio (or keep Original).\n' +
      '2. Trim — drag the start/end handles.\n' +
      '3. Zoom — add a gentle push-in if you like.\n' +
      '4. Music — add a track; it ducks under speech.\n' +
      '5. Export — renders on your GPU; nothing is uploaded.',
      '✂️ Editor — quick tour'
    );
  }

  // Bind all controls once (idempotent via a dataset guard on the modal).
  function bindEditorOnce() {
    var modal = $('editor-modal');
    if (!modal || modal.dataset.editorBound) return;
    modal.dataset.editorBound = '1';

    $('close-editor-modal').addEventListener('click', closeEditor);
    $('editor-tutorial-btn').addEventListener('click', showTutorial);
    $('editor-export-btn').addEventListener('click', startExport);
    $('editor-cancel-btn').addEventListener('click', cancelExport);

    var vid = $('editor-video');
    $('editor-playpause').addEventListener('click', function () {
      if (vid.paused) vid.play().catch(function () {}); else vid.pause();
    });
    vid.addEventListener('play', function () { $('editor-playpause').textContent = '⏸️'; });
    vid.addEventListener('pause', function () { $('editor-playpause').textContent = '▶️'; });
    vid.addEventListener('loadedmetadata', function () {
      var dur = isFinite(vid.duration) ? vid.duration : (Number(editorClip && editorClip.duration) || 0);
      ['editor-trim-in', 'editor-trim-out'].forEach(function (id) { $(id).max = String(dur.toFixed(1)); });
      $('editor-trim-in').value = '0';
      $('editor-trim-out').value = String(dur.toFixed(1));
      updateTrimLabel();
      updateCropOverlay();
      $('editor-time').textContent = fmtClock(0) + ' / ' + fmtClock(dur);
    });
    vid.addEventListener('timeupdate', function () {
      var i = parseFloat($('editor-trim-in').value) || 0;
      var o = parseFloat($('editor-trim-out').value) || vid.duration;
      // Loop playback within the trimmed window so the preview reflects the cut.
      if (vid.currentTime > o) vid.currentTime = i;
      $('editor-time').textContent = fmtClock(vid.currentTime) + ' / ' + fmtClock(vid.duration);
    });

    $('editor-ratio-chips').addEventListener('click', function (e) {
      var chip = e.target.closest('.ratio-chip');
      if (!chip) return;
      document.querySelectorAll('#editor-ratio-chips .ratio-chip').forEach(function (c) { c.classList.remove('is-selected'); });
      chip.classList.add('is-selected');
      updateCropOverlay();
    });

    $('editor-zoom').addEventListener('input', applyZoomPreview);

    var trimIn = $('editor-trim-in'), trimOut = $('editor-trim-out');
    trimIn.addEventListener('input', function () {
      if (parseFloat(trimIn.value) >= parseFloat(trimOut.value)) trimIn.value = String(Math.max(0, parseFloat(trimOut.value) - 0.1));
      if (vid) vid.currentTime = parseFloat(trimIn.value) || 0;
      updateTrimLabel();
    });
    trimOut.addEventListener('input', function () {
      if (parseFloat(trimOut.value) <= parseFloat(trimIn.value)) trimOut.value = String(parseFloat(trimIn.value) + 0.1);
      updateTrimLabel();
    });

    $('editor-music-enabled').addEventListener('change', function () {
      if (this.checked && !$('editor-music-path').value) pickEditorMusic();
    });
    $('editor-pick-music').addEventListener('click', pickEditorMusic);
    $('editor-music-volume').addEventListener('input', function () {
      $('editor-music-volume-label').textContent = Math.round((parseFloat(this.value) || 0) * 100) + '%';
    });

    window.addEventListener('resize', updateCropOverlay);
  }

  async function pickEditorMusic() {
    var file = null;
    if (window.clipperAPI && window.clipperAPI.selectMusic) file = await window.clipperAPI.selectMusic();
    else file = window.prompt('Paste the full path to a music file:');
    if (file) {
      $('editor-music-path').value = file;
      $('editor-music-enabled').checked = true;
      showToast('🎵 Music added — it ducks under speech on export.', 'success');
    }
  }

  function closeEditor() {
    var vid = $('editor-video');
    if (vid) { vid.pause(); vid.removeAttribute('src'); vid.load(); }
    stopEditorPolling();
    $('editor-progress').classList.add('hidden');
    $('editor-modal').classList.add('hidden');
  }

  // Public entry point, called from the clip card's ✂️ Edit action.
  window.openEditor = function (clipIndex) {
    var clip = generatedClips[clipIndex];
    if (!clip) return;
    editorClip = clip;
    bindEditorOnce();

    // Reset controls to defaults for this clip.
    document.querySelectorAll('#editor-ratio-chips .ratio-chip').forEach(function (c) {
      c.classList.toggle('is-selected', c.dataset.ratio === 'full');
    });
    $('editor-zoom').value = '1';
    applyZoomPreview();
    $('editor-music-enabled').checked = false;
    $('editor-music-path').value = '';
    $('editor-music-volume').value = '0.12';
    $('editor-music-volume-label').textContent = '12%';
    $('editor-music-duck').checked = true;
    $('editor-progress').classList.add('hidden');
    $('editor-crop-overlay').classList.add('hidden');

    var vid = $('editor-video');
    vid.src = fileUrl(clip.output_file);
    vid.muted = false;
    vid.load();

    $('editor-modal').classList.remove('hidden');

    // First-run coach tip (once).
    try {
      if (!localStorage.getItem(TIP_SEEN_KEY)) {
        localStorage.setItem(TIP_SEEN_KEY, '1');
        setTimeout(function () {
          showToast('Tip: pick a ratio, trim the ends, add music — then Export. Tap ❓ Tutorial any time.', 'info');
        }, 400);
      }
    } catch (_) { /* localStorage unavailable — non-fatal */ }
  };
})();
