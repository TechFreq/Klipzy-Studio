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

  // Phase 2 in-memory item lists for the open clip (reset on openEditor).
  var editorTexts = [];      // { text, x, y, color, size, start, end }
  var editorStickers = [];   // { path, name, x, y, scale, start, end }
  var editorSfx = [];        // { path, name, start, gain }

  // Approximate CSS equivalents of the ffmpeg colour presets, for live preview.
  var FILTER_CSS = {
    none: '',
    warm: 'saturate(1.15) sepia(0.15)',
    cool: 'saturate(1.05) hue-rotate(-8deg) brightness(1.02)',
    vivid: 'saturate(1.5) contrast(1.1)',
    bw: 'grayscale(1)',
    film: 'sepia(0.35) contrast(1.05) saturate(0.9)',
  };

  function $(id) { return document.getElementById(id); }

  function selectedFilter() {
    var chip = document.querySelector('#editor-filter-chips .filter-chip.is-selected');
    return chip ? chip.dataset.filter : 'none';
  }

  function clipDuration() {
    var vid = $('editor-video');
    if (vid && isFinite(vid.duration)) return vid.duration;
    return editorClip ? Number(editorClip.duration) || 0 : 0;
  }

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
      filter: selectedFilter(),
      music: {
        enabled: $('editor-music-enabled').checked,
        path: $('editor-music-path').value || '',
        gain: parseFloat($('editor-music-volume').value) || 0.12,
        duck: $('editor-music-duck').checked,
      },
      texts: editorTexts.slice(),
      stickers: editorStickers.slice(),
      sfx: editorSfx.slice(),
    };
  }

  function applyFilterPreview() {
    var vid = $('editor-video');
    if (vid) vid.style.filter = FILTER_CSS[selectedFilter()] || '';
  }

  // Displayed video width inside the letterboxed stage — shared by the crop
  // overlay and the text/sticker preview so everything lines up.
  function displayedVideoRect() {
    var vid = $('editor-video');
    if (!vid || !vid.videoWidth) return null;
    var boxW = vid.clientWidth, boxH = vid.clientHeight;
    var vidAspect = vid.videoWidth / vid.videoHeight;
    var w, h;
    if (vidAspect > boxW / boxH) { w = boxW; h = boxW / vidAspect; }
    else { h = boxH; w = boxH * vidAspect; }
    return { w: w, h: h, left: (boxW - w) / 2, top: (boxH - h) / 2 };
  }

  var _CANVAS_W = { '9:16': 1080, '4:5': 1080, '1:1': 1080, '16:9': 1920, full: 1080 };

  // Rebuild the live text/sticker preview layer from the item arrays.
  function renderPreviewOverlays() {
    var layer = $('editor-overlay-layer');
    if (!layer) return;
    layer.innerHTML = '';
    var rect = displayedVideoRect();
    if (!rect) return;
    var canvasW = _CANVAS_W[editorState().ratio] || 1080;
    var scale = rect.w / canvasW;

    editorStickers.forEach(function (s, i) {
      var img = document.createElement('img');
      img.src = fileUrl(s.path);
      img.className = 'editor-ov-item';
      img.style.left = (rect.left + s.x * rect.w) + 'px';
      img.style.top = (rect.top + s.y * rect.h) + 'px';
      img.style.width = Math.max(12, s.scale * rect.w) + 'px';
      img.dataset.start = String(s.start);
      img.dataset.end = String(s.end);
      makeDraggable(img, 'sticker', i);
      layer.appendChild(img);
    });

    editorTexts.forEach(function (t, i) {
      var el = document.createElement('div');
      el.className = 'editor-ov-item editor-ov-text';
      el.textContent = t.text;
      el.style.left = (rect.left + t.x * rect.w) + 'px';
      el.style.top = (rect.top + t.y * rect.h) + 'px';
      el.style.color = t.color || '#fff';
      el.style.fontSize = Math.max(10, (t.size || 96) * scale) + 'px';
      el.dataset.start = String(t.start);
      el.dataset.end = String(t.end);
      makeDraggable(el, 'text', i);
      layer.appendChild(el);
    });
    syncOverlayVisibility();
  }

  // Drag an overlay item to reposition it; writes back normalized x/y (0..1)
  // to the underlying item so the export lands where the preview shows it.
  function makeDraggable(el, kind, index) {
    el.style.pointerEvents = 'auto';
    el.style.cursor = 'move';
    el.title = 'Drag to reposition';
    el.addEventListener('pointerdown', function (e) {
      e.preventDefault();
      var rect = displayedVideoRect();
      var layer = $('editor-overlay-layer');
      if (!rect || !layer) return;
      var arr = kind === 'text' ? editorTexts : editorStickers;
      try { el.setPointerCapture(e.pointerId); } catch (_) { /* older engines */ }
      function onMove(ev) {
        var lr = layer.getBoundingClientRect();
        var x = ((ev.clientX - lr.left) - rect.left) / rect.w;
        var y = ((ev.clientY - lr.top) - rect.top) / rect.h;
        x = Math.max(0, Math.min(1, x));
        y = Math.max(0, Math.min(1, y));
        if (arr[index]) { arr[index].x = x; arr[index].y = y; }
        el.style.left = (rect.left + x * rect.w) + 'px';
        el.style.top = (rect.top + y * rect.h) + 'px';
      }
      function onUp() {
        el.removeEventListener('pointermove', onMove);
        el.removeEventListener('pointerup', onUp);
      }
      el.addEventListener('pointermove', onMove);
      el.addEventListener('pointerup', onUp);
    });
  }

  // Show/hide preview overlays based on the playhead so timing reads true.
  function syncOverlayVisibility() {
    var vid = $('editor-video');
    var t = vid ? vid.currentTime : 0;
    document.querySelectorAll('#editor-overlay-layer .editor-ov-item').forEach(function (el) {
      var s = parseFloat(el.dataset.start) || 0;
      var e = parseFloat(el.dataset.end);
      if (!isFinite(e)) e = Infinity;
      el.style.visibility = (t >= s && t <= e) ? 'visible' : 'hidden';
    });
  }

  // A compact labelled number input that writes back to item[key] on change.
  function numField(label, item, key, opts) {
    opts = opts || {};
    var wrap = document.createElement('label');
    wrap.className = 'editor-item-num';
    wrap.textContent = label;
    var inp = document.createElement('input');
    inp.type = 'number';
    inp.step = opts.step || '0.1';
    if (opts.min !== undefined) inp.min = String(opts.min);
    if (opts.max !== undefined) inp.max = String(opts.max);
    inp.value = String(item[key]);
    inp.addEventListener('change', function () {
      var v = parseFloat(inp.value);
      if (isFinite(v)) { item[key] = v; if (opts.onChange) opts.onChange(); }
    });
    // Don't let clicks/drover bubble to the video card handlers.
    inp.addEventListener('click', function (e) { e.stopPropagation(); });
    wrap.appendChild(inp);
    return wrap;
  }

  function removeBtn(arr, i) {
    var rm = document.createElement('button');
    rm.className = 'btn btn-small btn-danger';
    rm.textContent = '✕';
    rm.title = 'Remove';
    rm.addEventListener('click', function () {
      arr.splice(i, 1);
      renderItemLists();
      renderPreviewOverlays();
    });
    return rm;
  }

  function renderItemLists() {
    var dur = clipDuration();
    var reflow = function () { renderPreviewOverlays(); };

    // Text: label + start/end + size.
    var tl = $('editor-text-list');
    if (tl) {
      tl.innerHTML = '';
      editorTexts.forEach(function (t, i) {
        var li = document.createElement('li');
        li.className = 'editor-item';
        var name = document.createElement('span');
        name.textContent = '🅣 ' + t.text.slice(0, 18);
        li.appendChild(name);
        li.appendChild(numField('start', t, 'start', { min: 0, max: dur, onChange: reflow }));
        li.appendChild(numField('end', t, 'end', { min: 0, max: dur, onChange: reflow }));
        li.appendChild(numField('size', t, 'size', { step: '4', min: 8, onChange: reflow }));
        li.appendChild(removeBtn(editorTexts, i));
        tl.appendChild(li);
      });
    }

    // Stickers: name + start/end + scale.
    var sl = $('editor-sticker-list');
    if (sl) {
      sl.innerHTML = '';
      editorStickers.forEach(function (s, i) {
        var li = document.createElement('li');
        li.className = 'editor-item';
        var name = document.createElement('span');
        name.textContent = '🖼️ ' + s.name;
        li.appendChild(name);
        li.appendChild(numField('start', s, 'start', { min: 0, max: dur, onChange: reflow }));
        li.appendChild(numField('end', s, 'end', { min: 0, max: dur, onChange: reflow }));
        li.appendChild(numField('size', s, 'scale', { step: '0.05', min: 0.02, max: 4, onChange: reflow }));
        li.appendChild(removeBtn(editorStickers, i));
        sl.appendChild(li);
      });
    }

    // SFX: name + start + volume.
    var fl = $('editor-sfx-list');
    if (fl) {
      fl.innerHTML = '';
      editorSfx.forEach(function (s, i) {
        var li = document.createElement('li');
        li.className = 'editor-item';
        var name = document.createElement('span');
        name.textContent = '🔊 ' + s.name;
        li.appendChild(name);
        li.appendChild(numField('at', s, 'start', { min: 0, max: dur }));
        li.appendChild(numField('vol', s, 'gain', { step: '0.05', min: 0, max: 4 }));
        li.appendChild(removeBtn(editorSfx, i));
        fl.appendChild(li);
      });
    }
  }

  function baseName(p) {
    return String(p || '').split(/[\\/]/).pop() || p;
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
      '4. Text / graphics — add them, then DRAG them on the preview to position; set start/end in the list.\n' +
      '5. Music / SFX — add a track; music ducks under speech.\n' +
      '6. Export — renders on your GPU; nothing is uploaded.',
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
      renderPreviewOverlays();
      $('editor-time').textContent = fmtClock(0) + ' / ' + fmtClock(dur);
    });
    vid.addEventListener('timeupdate', function () {
      var i = parseFloat($('editor-trim-in').value) || 0;
      var o = parseFloat($('editor-trim-out').value) || vid.duration;
      // Loop playback within the trimmed window so the preview reflects the cut.
      if (vid.currentTime > o) vid.currentTime = i;
      $('editor-time').textContent = fmtClock(vid.currentTime) + ' / ' + fmtClock(vid.duration);
      syncOverlayVisibility();
    });

    $('editor-ratio-chips').addEventListener('click', function (e) {
      var chip = e.target.closest('.ratio-chip');
      if (!chip) return;
      document.querySelectorAll('#editor-ratio-chips .ratio-chip').forEach(function (c) { c.classList.remove('is-selected'); });
      chip.classList.add('is-selected');
      updateCropOverlay();
      renderPreviewOverlays();
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

    // Filters — live CSS preview + re-scale text overlays.
    $('editor-filter-chips').addEventListener('click', function (e) {
      var chip = e.target.closest('.filter-chip');
      if (!chip) return;
      document.querySelectorAll('#editor-filter-chips .filter-chip').forEach(function (c) { c.classList.remove('is-selected'); });
      chip.classList.add('is-selected');
      applyFilterPreview();
    });

    // Text — add a title from the inline form.
    $('editor-add-text').addEventListener('click', function () {
      var input = $('editor-text-input');
      var txt = (input.value || '').trim();
      if (!txt) { showToast('Type some text first.', 'info'); return; }
      editorTexts.push({
        text: txt,
        x: 0.5,
        y: parseFloat($('editor-text-pos').value) || 0.85,
        color: $('editor-text-color').value || '#ffffff',
        size: 96,
        start: 0,
        end: clipDuration(),
      });
      input.value = '';
      renderItemLists();
      renderPreviewOverlays();
    });

    $('editor-add-sticker').addEventListener('click', addEditorSticker);
    $('editor-add-sfx').addEventListener('click', addEditorSfx);

    var reflow = function () { updateCropOverlay(); renderPreviewOverlays(); };
    window.addEventListener('resize', reflow);
  }

  async function addEditorSticker() {
    var file = null;
    // Reuse the camera-clip picker (images/video) if present, else prompt.
    if (window.clipperAPI && window.clipperAPI.selectCameraClip) file = await window.clipperAPI.selectCameraClip();
    else file = window.prompt('Paste the full path to an image/sticker (png/jpg):');
    if (!file) return;
    editorStickers.push({ path: file, name: baseName(file), x: 0.5, y: 0.5, scale: 0.25, start: 0, end: clipDuration() });
    renderItemLists();
    renderPreviewOverlays();
  }

  async function addEditorSfx() {
    var file = null;
    if (window.clipperAPI && window.clipperAPI.selectMusic) file = await window.clipperAPI.selectMusic();
    else file = window.prompt('Paste the full path to a sound effect (mp3/wav):');
    if (!file) return;
    editorSfx.push({ path: file, name: baseName(file), start: 0, gain: 0.8 });
    renderItemLists();
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

    // Reset controls + item lists to defaults for this clip.
    document.querySelectorAll('#editor-ratio-chips .ratio-chip').forEach(function (c) {
      c.classList.toggle('is-selected', c.dataset.ratio === 'full');
    });
    document.querySelectorAll('#editor-filter-chips .filter-chip').forEach(function (c) {
      c.classList.toggle('is-selected', c.dataset.filter === 'none');
    });
    $('editor-zoom').value = '1';
    applyZoomPreview();
    applyFilterPreview();
    $('editor-music-enabled').checked = false;
    $('editor-music-path').value = '';
    $('editor-music-volume').value = '0.12';
    $('editor-music-volume-label').textContent = '12%';
    $('editor-music-duck').checked = true;
    editorTexts = [];
    editorStickers = [];
    editorSfx = [];
    renderItemLists();
    var layer = $('editor-overlay-layer');
    if (layer) layer.innerHTML = '';
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
