/*
 * Built-in clip editor — timeline UI behind the "✂️ Edit" button on each clip.
 *
 * Layout: a live <video> preview on top, a slim controls rail beside it, and a
 * horizontal TIMELINE at the bottom (ruler + playhead + Video / Text / Media /
 * Audio lanes). Trim = dragging the video block's edges; item timing = dragging
 * blocks along their lane; selecting a block opens a small inspector for its
 * non-timing props. Everything compiles to the same Edit Spec (KlipzyEditorSpec)
 * and renders via /editor/export, so preview stays WYSIWYG.
 *
 * Classic script sharing renderer.js's globals (loaded AFTER it): reuses
 * serverUrl, fileUrl, showToast/showError/showAlert, generatedClips and
 * window.clipperAPI.* — no plumbing duplicated. Preview is browser-side so
 * scrubbing stays fluid; ffmpeg only runs on Export.
 */
(function () {
  'use strict';

  var editorClip = null;
  var editorPollTimer = null;
  var editorJobId = null;
  var TIP_SEEN_KEY = 'klipzy.editor.tipSeen';

  // Edit state for the open clip (reset on openEditor).
  var editorTexts = [];      // { text, x, y, color, size, start, end }
  var editorStickers = [];   // { path, name, x, y, scale, start, end }
  var editorSfx = [];        // { path, name, start, gain }
  var trimIn = 0;
  var trimOut = 0;
  var selected = null;       // { kind, index } | { kind:'video'|'music' }

  var FILTER_CSS = {
    none: '',
    warm: 'saturate(1.15) sepia(0.15)',
    cool: 'saturate(1.05) hue-rotate(-8deg) brightness(1.02)',
    vivid: 'saturate(1.5) contrast(1.1)',
    bw: 'grayscale(1)',
    film: 'sepia(0.35) contrast(1.05) saturate(0.9)',
  };
  var SFX_DISPLAY_DUR = 0.8;   // one-shot SFX have no length; show a short block

  function $(id) { return document.getElementById(id); }

  function selectedFilter() {
    var chip = document.querySelector('#editor-filter-chips .filter-chip.is-selected');
    return chip ? chip.dataset.filter : 'none';
  }

  function clipDuration() {
    var vid = $('editor-video');
    if (vid && isFinite(vid.duration) && vid.duration > 0) return vid.duration;
    return editorClip ? Number(editorClip.duration) || 0 : 0;
  }

  function selectedRatio() {
    var chip = document.querySelector('#editor-ratio-chips .ratio-chip.is-selected');
    return chip ? chip.dataset.ratio : 'full';
  }

  function editorState() {
    var duration = clipDuration();
    return {
      source: editorClip ? editorClip.output_file : null,
      duration: duration,
      ratio: selectedRatio(),
      trimIn: trimIn,
      trimOut: trimOut || duration,
      zoom: parseFloat($('editor-zoom').value) || 1,
      speed: parseFloat($('editor-speed').value) || 1,
      fadeIn: parseFloat($('editor-fade-in').value) || 0,
      fadeOut: parseFloat($('editor-fade-out').value) || 0,
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

  // ---- preview overlays (unchanged behaviour) ----------------------------
  function applyFilterPreview() {
    var vid = $('editor-video');
    if (vid) vid.style.filter = FILTER_CSS[selectedFilter()] || '';
  }

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

  function renderPreviewOverlays() {
    var layer = $('editor-overlay-layer');
    if (!layer) return;
    layer.innerHTML = '';
    var rect = displayedVideoRect();
    if (!rect) return;
    var canvasW = _CANVAS_W[selectedRatio()] || 1080;
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

  function makeDraggable(el, kind, index) {
    el.style.pointerEvents = 'auto';
    el.style.cursor = 'move';
    el.title = 'Drag to reposition';
    el.addEventListener('pointerdown', function (e) {
      e.preventDefault();
      selectItem(kind, index);
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

  function fmtClock(s) {
    if (!isFinite(s) || s < 0) s = 0;
    var m = Math.floor(s / 60);
    var sec = Math.floor(s % 60);
    return m + ':' + String(sec).padStart(2, '0');
  }

  // ---- TIMELINE ----------------------------------------------------------
  function makeBlock(kind, index, startT, endT, label, extraClass) {
    var dur = clipDuration() || 1;
    var block = document.createElement('div');
    block.className = 'tl-block ' + (extraClass || '');
    block.style.left = (startT / dur * 100) + '%';
    block.style.width = (Math.max(0.001, endT - startT) / dur * 100) + '%';
    if (selected && selected.kind === kind && selected.index === index) block.classList.add('is-selected');
    var lbl = document.createElement('span');
    lbl.className = 'tl-block-label';
    lbl.textContent = label;
    block.appendChild(lbl);
    return block;
  }

  function addHandles(block) {
    var l = document.createElement('span'); l.className = 'tl-handle tl-handle-l';
    var r = document.createElement('span'); r.className = 'tl-handle tl-handle-r';
    block.appendChild(l); block.appendChild(r);
    return { l: l, r: r };
  }

  function laneWidthPx(lane) {
    return lane.getBoundingClientRect().width || 1;
  }

  // Generic drag: mode 'move' | 'start' | 'end'. get/set operate on the item.
  function wireDrag(handleEl, lane, mode, getSE, setSE, onChange) {
    handleEl.addEventListener('pointerdown', function (e) {
      e.preventDefault();
      e.stopPropagation();
      var dur = clipDuration() || 1;
      var wpx = laneWidthPx(lane);
      var startX = e.clientX;
      var orig = getSE();
      try { handleEl.setPointerCapture(e.pointerId); } catch (_) { /* ignore */ }
      function onMove(ev) {
        var dt = (ev.clientX - startX) / wpx * dur;
        var s = orig.start, en = orig.end;
        if (mode === 'move') { s = orig.start + dt; en = orig.end + dt; var len = orig.end - orig.start;
          s = Math.max(0, Math.min(s, dur - len)); en = s + len; }
        else if (mode === 'start') { s = Math.max(0, Math.min(orig.start + dt, orig.end - 0.2)); }
        else { en = Math.min(dur, Math.max(orig.end + dt, orig.start + 0.2)); }
        setSE(s, en);
        onChange();
      }
      function onUp() {
        document.removeEventListener('pointermove', onMove);
        document.removeEventListener('pointerup', onUp);
      }
      document.addEventListener('pointermove', onMove);
      document.addEventListener('pointerup', onUp);
    });
  }

  function renderTimeline() {
    var dur = clipDuration();
    var ruler = $('editor-tl-ruler');
    if (ruler) {
      ruler.innerHTML = '';
      var ticks = 4;
      for (var k = 0; k <= ticks; k++) {
        var tick = document.createElement('span');
        tick.className = 'tl-tick';
        tick.style.left = (k / ticks * 100) + '%';
        tick.textContent = fmtClock(dur * k / ticks);
        ruler.appendChild(tick);
      }
    }

    // Video lane: one block for the kept (trimmed) region, with edge handles.
    var lv = $('lane-video');
    if (lv) {
      lv.innerHTML = '';
      var vb = makeBlock('video', -1, trimIn, trimOut || dur, '🎬 ' + fmtClock(Math.max(0, (trimOut || dur) - trimIn)), 'tl-video');
      var vh = addHandles(vb);
      vb.addEventListener('pointerdown', function () { selectItem('video'); });
      wireDrag(vh.l, lv, 'start',
        function () { return { start: trimIn, end: trimOut || dur }; },
        function (s) { trimIn = s; seekTo(trimIn); }, function () { renderTimeline(); });
      wireDrag(vh.r, lv, 'end',
        function () { return { start: trimIn, end: trimOut || dur }; },
        function (_s, e) { trimOut = e; seekTo(trimOut); }, function () { renderTimeline(); });
      lv.appendChild(vb);
    }

    // Text lane.
    var lt = $('lane-text');
    if (lt) {
      lt.innerHTML = '';
      editorTexts.forEach(function (t, i) {
        var b = makeBlock('text', i, t.start, t.end, '🅣 ' + t.text.slice(0, 14), 'tl-text');
        var h = addHandles(b);
        b.addEventListener('pointerdown', function () { selectItem('text', i); });
        wireDrag(b, lt, 'move', function () { return { start: t.start, end: t.end }; },
          function (s, e) { t.start = s; t.end = e; }, refreshTimingLive);
        wireDrag(h.l, lt, 'start', function () { return { start: t.start, end: t.end }; },
          function (s) { t.start = s; }, refreshTimingLive);
        wireDrag(h.r, lt, 'end', function () { return { start: t.start, end: t.end }; },
          function (_s, e) { t.end = e; }, refreshTimingLive);
        lt.appendChild(b);
      });
    }

    // Graphics (sticker) lane.
    var lg = $('lane-graphics');
    if (lg) {
      lg.innerHTML = '';
      editorStickers.forEach(function (s, i) {
        var b = makeBlock('sticker', i, s.start, s.end, '🖼 ' + s.name, 'tl-graphics');
        var h = addHandles(b);
        b.addEventListener('pointerdown', function () { selectItem('sticker', i); });
        wireDrag(b, lg, 'move', function () { return { start: s.start, end: s.end }; },
          function (ns, ne) { s.start = ns; s.end = ne; }, refreshTimingLive);
        wireDrag(h.l, lg, 'start', function () { return { start: s.start, end: s.end }; },
          function (ns) { s.start = ns; }, refreshTimingLive);
        wireDrag(h.r, lg, 'end', function () { return { start: s.start, end: s.end }; },
          function (_ns, ne) { s.end = ne; }, refreshTimingLive);
        lg.appendChild(b);
      });
    }

    // Audio lane: full-width music bed (if enabled) + one-shot SFX markers.
    var la = $('lane-audio');
    if (la) {
      la.innerHTML = '';
      if ($('editor-music-enabled').checked && $('editor-music-path').value) {
        var mb = makeBlock('music', -1, 0, dur, '🎵 music', 'tl-music');
        mb.addEventListener('pointerdown', function () { selectItem('music'); });
        la.appendChild(mb);
      }
      editorSfx.forEach(function (sfx, i) {
        var end = Math.min(dur, sfx.start + SFX_DISPLAY_DUR);
        var b = makeBlock('sfx', i, sfx.start, end, '🔊', 'tl-sfx');
        b.addEventListener('pointerdown', function () { selectItem('sfx', i); });
        wireDrag(b, la, 'move', function () { return { start: sfx.start, end: sfx.start + SFX_DISPLAY_DUR }; },
          function (ns) { sfx.start = ns; }, refreshTimingLive);
        la.appendChild(b);
      });
    }
    updatePlayhead();
  }

  // During a block drag, keep the timeline + preview in sync without a full
  // re-render (which would drop the pointer capture on the dragged element).
  function refreshTimingLive() {
    document.querySelectorAll('#editor-overlay-layer .editor-ov-item').forEach(function () {});
    // Update block geometry live.
    var dur = clipDuration() || 1;
    var apply = function (laneId, arr) {
      var lane = $(laneId); if (!lane) return;
      var blocks = lane.querySelectorAll('.tl-block');
      arr.forEach(function (it, i) {
        var b = blocks[i]; if (!b) return;
        var en = (it.end != null) ? it.end : (it.start + SFX_DISPLAY_DUR);
        b.style.left = (it.start / dur * 100) + '%';
        b.style.width = (Math.max(0.001, en - it.start) / dur * 100) + '%';
      });
    };
    apply('lane-text', editorTexts);
    apply('lane-graphics', editorStickers);
    apply('lane-audio', editorSfx.length ? editorSfx : []);
    syncOverlayVisibility();
    // reflect timing edits in the preview overlays' dataset for show/hide
    var layer = $('editor-overlay-layer');
    if (layer) {
      var texts = layer.querySelectorAll('.editor-ov-text');
      // simplest: rebuild overlays so dataset start/end are fresh
    }
    renderPreviewOverlays();
  }

  function updatePlayhead() {
    var ph = $('editor-tl-playhead');
    var vid = $('editor-video');
    var dur = clipDuration();
    if (!ph || !vid || !dur) return;
    ph.style.left = (Math.min(vid.currentTime, dur) / dur * 100) + '%';
  }

  function seekTo(t) {
    var vid = $('editor-video');
    if (vid && isFinite(t)) vid.currentTime = Math.max(0, t);
  }

  // ---- selection inspector ----------------------------------------------
  function selectItem(kind, index) {
    selected = (index === undefined) ? { kind: kind } : { kind: kind, index: index };
    renderTimeline();
    renderInspector();
  }

  function inspectorRow(labelText, inputEl) {
    var row = document.createElement('label');
    row.className = 'editor-field';
    var sp = document.createElement('span'); sp.textContent = labelText;
    row.appendChild(sp); row.appendChild(inputEl);
    return row;
  }

  function renderInspector() {
    var box = $('editor-inspector');
    if (!box) return;
    box.innerHTML = '';
    if (!selected) { box.classList.add('hidden'); return; }
    box.classList.remove('hidden');

    var title = document.createElement('div');
    title.className = 'editor-group-label';
    box.appendChild(title);

    var mkRange = function (min, max, step, val, oninput) {
      var r = document.createElement('input'); r.type = 'range';
      r.min = min; r.max = max; r.step = step; r.value = val;
      r.addEventListener('input', function () { oninput(parseFloat(r.value)); });
      return r;
    };
    var removeBtn = function (fn) {
      var b = document.createElement('button');
      b.className = 'btn btn-small btn-danger'; b.textContent = '🗑 Remove';
      b.addEventListener('click', fn);
      return b;
    };

    if (selected.kind === 'text') {
      var t = editorTexts[selected.index]; if (!t) { selected = null; return renderInspector(); }
      title.textContent = '🅣 Text';
      var txt = document.createElement('input'); txt.type = 'text'; txt.value = t.text; txt.maxLength = 120;
      txt.addEventListener('input', function () { t.text = txt.value; renderPreviewOverlays(); renderTimeline(); });
      box.appendChild(inspectorRow('Text', txt));
      var col = document.createElement('input'); col.type = 'color'; col.value = t.color || '#ffffff';
      col.addEventListener('input', function () { t.color = col.value; renderPreviewOverlays(); });
      box.appendChild(inspectorRow('Colour', col));
      box.appendChild(inspectorRow('Size', mkRange(24, 200, 2, t.size || 96, function (v) { t.size = v; renderPreviewOverlays(); })));
      box.appendChild(removeBtn(function () { editorTexts.splice(selected.index, 1); selected = null; renderTimeline(); renderPreviewOverlays(); renderInspector(); }));
    } else if (selected.kind === 'sticker') {
      var s = editorStickers[selected.index]; if (!s) { selected = null; return renderInspector(); }
      title.textContent = '🖼 ' + s.name;
      box.appendChild(inspectorRow('Size', mkRange(0.05, 1.5, 0.01, s.scale || 0.25, function (v) { s.scale = v; renderPreviewOverlays(); })));
      box.appendChild(removeBtn(function () { editorStickers.splice(selected.index, 1); selected = null; renderTimeline(); renderPreviewOverlays(); renderInspector(); }));
    } else if (selected.kind === 'sfx') {
      var fx = editorSfx[selected.index]; if (!fx) { selected = null; return renderInspector(); }
      title.textContent = '🔊 ' + fx.name;
      box.appendChild(inspectorRow('Volume', mkRange(0, 2, 0.05, fx.gain || 0.8, function (v) { fx.gain = v; })));
      box.appendChild(removeBtn(function () { editorSfx.splice(selected.index, 1); selected = null; renderTimeline(); renderInspector(); }));
    } else if (selected.kind === 'music') {
      title.textContent = '🎵 Music bed';
      var vol = mkRange(0, 1, 0.01, parseFloat($('editor-music-volume').value) || 0.12, function (v) {
        $('editor-music-volume').value = v; $('editor-music-volume-label').textContent = Math.round(v * 100) + '%';
      });
      box.appendChild(inspectorRow('Volume', vol));
      box.appendChild(removeBtn(function () {
        $('editor-music-enabled').checked = false; $('editor-music-path').value = '';
        selected = null; renderTimeline(); renderInspector();
      }));
    } else { // video
      title.textContent = '🎬 Clip — drag the ends on the timeline to trim';
    }
  }

  function applyZoomPreview() {
    var vid = $('editor-video');
    var z = parseFloat($('editor-zoom').value) || 1;
    $('editor-zoom-label').textContent = z.toFixed(2) + '×';
    if (vid) vid.style.transform = z > 1 ? 'scale(' + z + ')' : '';
  }

  function updateCropOverlay() {
    var overlay = $('editor-crop-overlay');
    var vid = $('editor-video');
    if (!overlay || !vid) return;
    var aspect = KlipzyEditorSpec.overlayAspect(selectedRatio());
    if (!aspect || !vid.videoWidth || !vid.videoHeight) { overlay.classList.add('hidden'); return; }
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

  // ---- export ------------------------------------------------------------
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
          if (res.status === 404 || misses >= 5) { stopEditorPolling(); showError('Lost track of the export job.'); }
          return;
        }
        misses = 0;
        var data = await res.json();
        setEditorProgress(data.percent, data.step, data.state === 'rendering' ? 'Rendering' : data.state);
        if (data.done) {
          stopEditorPolling();
          if (data.state === 'completed') { playSuccessSound(); showAlert('✅ Edited clip exported!', 'Export complete', data.output || null); }
          else if (data.state === 'cancelled') { showToast('Export cancelled.', 'info'); }
          else { showError(data.error || 'Export failed.'); }
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
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ spec: spec }),
      });
      var data = await res.json().catch(function () { return {}; });
      if (!res.ok) throw new Error(data.detail || ('Server returned ' + res.status));
      pollEditorExport(data.job_id);
    } catch (e) { stopEditorPolling(); showError(e.message); }
  }

  async function cancelExport() {
    if (!editorJobId) return;
    var btn = $('editor-cancel-btn');
    if (btn) { btn.disabled = true; btn.textContent = '⏳…'; }
    try {
      await fetch(serverUrl + '/editor/export/' + editorJobId + '/cancel', { method: 'POST' });
      showToast('Cancellation requested.', 'info');
    } catch (e) { showToast('Failed to cancel: ' + e.message, 'error'); }
    finally { if (btn) { btn.disabled = false; btn.textContent = '🛑 Cancel'; } }
  }

  function showTutorial() {
    showAlert(
      '1. Reframe — pick a ratio (or keep Original).\n' +
      '2. Trim — drag the ends of the 🎬 Video block on the timeline.\n' +
      '3. Add text / graphics / SFX — they appear as blocks; drag to move, drag edges to time them, drag on the preview to position.\n' +
      '4. Click a block to tweak it (text, colour, size, volume) in the panel.\n' +
      '5. Zoom / speed / fades — in the controls rail.\n' +
      '6. Export — renders on your GPU; nothing is uploaded.',
      '✂️ Editor — quick tour'
    );
  }

  // ---- wiring ------------------------------------------------------------
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
      var dur = clipDuration();
      trimIn = 0; trimOut = dur;
      updateCropOverlay();
      renderPreviewOverlays();
      renderTimeline();
      $('editor-time').textContent = fmtClock(0) + ' / ' + fmtClock(dur);
    });
    vid.addEventListener('timeupdate', function () {
      var o = trimOut || vid.duration;
      if (vid.currentTime > o) vid.currentTime = trimIn;   // loop within trim
      $('editor-time').textContent = fmtClock(vid.currentTime) + ' / ' + fmtClock(vid.duration);
      syncOverlayVisibility();
      updatePlayhead();
    });

    // Seek by clicking/dragging the timeline tracks (but not while dragging a block).
    var tracks = $('editor-tl-tracks');
    if (tracks) {
      var seekFromEvent = function (ev) {
        if (ev.target.closest('.tl-block')) return;   // let block drags win
        var dur = clipDuration(); if (!dur) return;
        var r = tracks.getBoundingClientRect();
        var pct = Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width));
        seekTo(pct * dur); updatePlayhead();
      };
      tracks.addEventListener('pointerdown', function (ev) {
        seekFromEvent(ev);
        var move = function (e2) { if (e2.buttons) seekFromEvent(e2); };
        var up = function () { document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up); };
        document.addEventListener('pointermove', move);
        document.addEventListener('pointerup', up);
      });
    }

    $('editor-ratio-chips').addEventListener('click', function (e) {
      var chip = e.target.closest('.ratio-chip'); if (!chip) return;
      document.querySelectorAll('#editor-ratio-chips .ratio-chip').forEach(function (c) { c.classList.remove('is-selected'); });
      chip.classList.add('is-selected');
      updateCropOverlay(); renderPreviewOverlays();
    });

    $('editor-zoom').addEventListener('input', applyZoomPreview);
    $('editor-speed').addEventListener('input', function () {
      var sp = parseFloat(this.value) || 1;
      $('editor-speed-label').textContent = sp.toFixed(2) + '×';
      if (vid) { try { vid.playbackRate = sp; } catch (_) { /* clamp */ } }
    });
    $('editor-fade-in').addEventListener('input', function () { $('editor-fade-in-label').textContent = (parseFloat(this.value) || 0).toFixed(1) + 's'; });
    $('editor-fade-out').addEventListener('input', function () { $('editor-fade-out-label').textContent = (parseFloat(this.value) || 0).toFixed(1) + 's'; });

    $('editor-music-enabled').addEventListener('change', function () {
      if (this.checked && !$('editor-music-path').value) pickEditorMusic(); else renderTimeline();
    });
    $('editor-pick-music').addEventListener('click', pickEditorMusic);
    $('editor-music-volume').addEventListener('input', function () {
      $('editor-music-volume-label').textContent = Math.round((parseFloat(this.value) || 0) * 100) + '%';
    });

    $('editor-filter-chips').addEventListener('click', function (e) {
      var chip = e.target.closest('.filter-chip'); if (!chip) return;
      document.querySelectorAll('#editor-filter-chips .filter-chip').forEach(function (c) { c.classList.remove('is-selected'); });
      chip.classList.add('is-selected');
      applyFilterPreview();
    });

    $('editor-add-text').addEventListener('click', function () {
      var input = $('editor-text-input');
      var txt = (input.value || '').trim();
      if (!txt) { showToast('Type some text first.', 'info'); return; }
      var dur = clipDuration();
      editorTexts.push({ text: txt, x: 0.5, y: 0.85, color: $('editor-text-color').value || '#ffffff', size: 96, start: 0, end: dur });
      input.value = '';
      renderPreviewOverlays(); renderTimeline();
      selectItem('text', editorTexts.length - 1);
    });
    $('editor-add-sticker').addEventListener('click', addEditorSticker);
    $('editor-add-sfx').addEventListener('click', addEditorSfx);

    window.addEventListener('resize', function () { updateCropOverlay(); renderPreviewOverlays(); });
  }

  async function addEditorSticker() {
    var file = null;
    if (window.clipperAPI && window.clipperAPI.selectCameraClip) file = await window.clipperAPI.selectCameraClip();
    else file = window.prompt('Paste the full path to an image/sticker (png/jpg):');
    if (!file) return;
    editorStickers.push({ path: file, name: baseName(file), x: 0.5, y: 0.5, scale: 0.25, start: 0, end: clipDuration() });
    renderPreviewOverlays(); renderTimeline();
    selectItem('sticker', editorStickers.length - 1);
  }

  async function addEditorSfx() {
    var file = null;
    if (window.clipperAPI && window.clipperAPI.selectMusic) file = await window.clipperAPI.selectMusic();
    else file = window.prompt('Paste the full path to a sound effect (mp3/wav):');
    if (!file) return;
    editorSfx.push({ path: file, name: baseName(file), start: 0, gain: 0.8 });
    renderTimeline();
    selectItem('sfx', editorSfx.length - 1);
  }

  async function pickEditorMusic() {
    var file = null;
    if (window.clipperAPI && window.clipperAPI.selectMusic) file = await window.clipperAPI.selectMusic();
    else file = window.prompt('Paste the full path to a music file:');
    if (file) {
      $('editor-music-path').value = file;
      $('editor-music-enabled').checked = true;
      renderTimeline();
      showToast('🎵 Music added — it ducks under speech on export.', 'success');
    }
  }

  function baseName(p) { return String(p || '').split(/[\\/]/).pop() || p; }

  function closeEditor() {
    var vid = $('editor-video');
    if (vid) { vid.pause(); vid.removeAttribute('src'); vid.load(); }
    stopEditorPolling();
    $('editor-progress').classList.add('hidden');
    $('editor-modal').classList.add('hidden');
  }

  window.openEditor = function (clipIndex) {
    var clip = generatedClips[clipIndex];
    if (!clip) return;
    editorClip = clip;
    bindEditorOnce();

    document.querySelectorAll('#editor-ratio-chips .ratio-chip').forEach(function (c) { c.classList.toggle('is-selected', c.dataset.ratio === 'full'); });
    document.querySelectorAll('#editor-filter-chips .filter-chip').forEach(function (c) { c.classList.toggle('is-selected', c.dataset.filter === 'none'); });
    $('editor-zoom').value = '1'; applyZoomPreview(); applyFilterPreview();
    $('editor-speed').value = '1'; $('editor-speed-label').textContent = '1.00×';
    try { $('editor-video').playbackRate = 1; } catch (_) { /* ignore */ }
    $('editor-fade-in').value = '0'; $('editor-fade-in-label').textContent = '0.0s';
    $('editor-fade-out').value = '0'; $('editor-fade-out-label').textContent = '0.0s';
    $('editor-music-enabled').checked = false; $('editor-music-path').value = '';
    $('editor-music-volume').value = '0.12'; $('editor-music-volume-label').textContent = '12%';
    $('editor-music-duck').checked = true;
    editorTexts = []; editorStickers = []; editorSfx = [];
    trimIn = 0; trimOut = Number(clip.duration) || 0;
    selected = null;
    renderInspector();
    var layer = $('editor-overlay-layer'); if (layer) layer.innerHTML = '';
    renderTimeline();
    $('editor-progress').classList.add('hidden');
    $('editor-crop-overlay').classList.add('hidden');

    var vid = $('editor-video');
    vid.src = fileUrl(clip.output_file); vid.muted = false; vid.load();

    $('editor-modal').classList.remove('hidden');

    try {
      if (!localStorage.getItem(TIP_SEEN_KEY)) {
        localStorage.setItem(TIP_SEEN_KEY, '1');
        setTimeout(function () { showToast('Tip: drag the 🎬 block ends to trim, add text/graphics, then Export. Tap ❓ Tutorial any time.', 'info'); }, 400);
      }
    } catch (_) { /* localStorage unavailable */ }
  };
})();
