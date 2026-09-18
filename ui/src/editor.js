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
  var sourceOffset = 0, sourceSpan = 0, editorSource = null;
  var cropPosition = {x:0.5,y:0.5}, facecamCrop = {x:0.7,y:0.05,w:0.25,h:0.25};
  var activeCrop = null;
  var previewFrame = null;
  var trimIn = 0;
  var trimOut = 0;
  var selected = null;       // { kind, index } | { kind:'video'|'music' }

  // Live ASR captions shown on the preview (decoupled from burn-in). `chunks`
  // are built from editorClip.words, rebased to CLIP-LOCAL time so they line up
  // with the playhead in both "rendered" and "original" source modes. They are
  // only baked into the export when `burn` is on.
  var editorCaptions = {
    show: false, burn: false, position: 'bottom', chunk: 4, size: 72,
    color: '#ffffff', highlight: '#ffe000', chunks: [],
  };

  // Draft identity is captured at open time, never taken from a later project switch.
  var draftKey = null, draftReady = false, restoringDraft = false, pendingDraft = null;
  var history = [], historyIndex = -1, draftTimer = null, exportStarting = false;
  var editorClipIndex = -1, editorOriginalSource = null;
  var timelineMedia = null, timelineRequest = 0, mediaAbort = null, sourceFps = 30;
  function paintTimelineMedia() {
    var strip=$('editor-thumbnail-strip'), waveform=$('editor-waveform');
    if(!strip || !waveform) return;
    strip.replaceChildren();waveform.replaceChildren();
    if(!timelineMedia) return;
    (timelineMedia.thumbnails || []).forEach(function(url){if(!url.startsWith('data:image/jpeg;base64,'))return;var img=document.createElement('img');img.src=url;img.alt='';strip.append(img);});
    (timelineMedia.peaks || []).forEach(function(peak){var bar=document.createElement('span');bar.style.height=Math.max(2,Math.min(100,Number(peak)*100))+'%';waveform.append(bar);});
  }
  async function loadTimelineMedia() {
    var request=++timelineRequest;
    if(mediaAbort) mediaAbort.abort();mediaAbort=new AbortController();
    timelineMedia=null;paintTimelineMedia();sourceFps=30;
    var duration=clipDuration(), status=$('editor-media-status');
    if(duration>600) {status.textContent='Thumbnail and waveform previews support clips up to 10 minutes.';return;}
    if(!(duration>0)) return;
    status.textContent='Loading thumbnails and source audio waveform…';
    try {
      var response=await fetch(serverUrl+'/editor/timeline-media',{method:'POST',headers:{'Content-Type':'application/json'},signal:mediaAbort.signal,body:JSON.stringify({path:editorSource,start:sourceOffset,duration:duration})});
      var data=await response.json();
      if(request!==timelineRequest) return;
      if(!response.ok) throw new Error(data.detail || 'Preview unavailable');
      if(!Array.isArray(data.thumbnails)) return;
      timelineMedia=data;sourceFps=Number(data.fps)>0?Number(data.fps):30;paintTimelineMedia();
      $('editor-frame-prev').title='Step back 1 frame at '+sourceFps.toFixed(2)+' fps';$('editor-frame-next').title='Step forward 1 frame at '+sourceFps.toFixed(2)+' fps';
      status.textContent=data.peaks.length?'Source audio waveform · hold Alt while dragging to bypass snapping':'This source has no audio waveform.';
    } catch(error) {if(request===timelineRequest && error.name!=='AbortError') status.textContent='Timeline previews unavailable; editing and export still work.';}
  }
  function draftStatus(message) { if ($('editor-draft-status')) $('editor-draft-status').textContent = message; }
  function snapshotEdit() {
    var controls = {};
    document.querySelectorAll('#editor-modal input[id], #editor-modal select[id]').forEach(function(el) {
      if (['editor-clip-picker','editor-transcript-search','editor-text-input'].includes(el.id)) return;
      controls[el.id] = el.type === 'checkbox' ? el.checked : el.value;
    });
    return JSON.parse(JSON.stringify({version:1, controls:controls, ratio:selectedRatio(), filter:selectedFilter(),
      texts:editorTexts, stickers:editorStickers, sfx:editorSfx, crop:cropPosition, facecam:facecamCrop,
      trimIn:trimIn, trimOut:trimOut, captions:editorCaptions, playhead:Math.max(0,$('editor-video').currentTime-sourceOffset)}));
  }
  function snapshotContent(value) { var copy = Object.assign({},value); delete copy.playhead; return JSON.stringify(copy); }
  function updateHistoryButtons() {
    if ($('editor-undo')) $('editor-undo').disabled = historyIndex <= 0;
    if ($('editor-redo')) $('editor-redo').disabled = historyIndex >= history.length - 1;
  }
  function saveDraft(checkpoint) {
    if (!draftKey || !draftReady || restoringDraft) return;
    clearTimeout(draftTimer);
    var state = snapshotEdit();
    if (checkpoint !== false && (historyIndex < 0 || snapshotContent(history[historyIndex]) !== snapshotContent(state))) {
      history = history.slice(0, historyIndex + 1); history.push(state);
      if (history.length > 80) history.shift();
      historyIndex = history.length - 1;
    }
    try { localStorage.setItem(draftKey, JSON.stringify(state)); draftStatus('Draft saved on this device'); }
    catch (_) { draftStatus('Draft not saved — local storage is full or unavailable'); }
    updateHistoryButtons();
  }
  function restoreEdit(state, loadSource) {
    restoringDraft = true;
    Object.entries(state.controls || {}).forEach(function(entry) {
      var el = $(entry[0]); if (!el || !el.closest('#editor-modal')) return;
      if (el.type === 'checkbox') el.checked = !!entry[1]; else el.value = entry[1];
    });
    document.querySelectorAll('#editor-ratio-chips .ratio-chip').forEach(function(el){el.classList.toggle('is-selected',el.dataset.ratio===state.ratio);});
    document.querySelectorAll('#editor-filter-chips .filter-chip').forEach(function(el){el.classList.toggle('is-selected',el.dataset.filter===state.filter);});
    if (loadSource) loadEditorSource();
    editorTexts = JSON.parse(JSON.stringify(state.texts || [])); editorStickers = JSON.parse(JSON.stringify(state.stickers || [])); editorSfx = JSON.parse(JSON.stringify(state.sfx || []));
    cropPosition = Object.assign({x:.5,y:.5},state.crop); facecamCrop = Object.assign({x:.7,y:.05,w:.25,h:.25},state.facecam);
    trimIn = Number(state.trimIn) || 0; trimOut = Number(state.trimOut) || clipDuration();
    if (state.captions) editorCaptions = JSON.parse(JSON.stringify(state.captions));
    selected = null;
    $('editor-video').playbackRate = Number($('editor-speed').value) || 1;
    $('editor-video').volume = Math.min(1,Number($('editor-clip-volume').value));
    $('editor-tl-tracks').style.width = (100*Number($('editor-timeline-zoom').value || 1))+'%';
    ['speed','clip-volume','music-volume','trans-dur','captions-size','captions-chunk'].forEach(function(name) {
      var input=$('editor-'+name), output=$('editor-'+name+'-label');
      if(output) output.textContent = name.includes('volume') ? Math.round(Number(input.value)*100)+'%' : name==='speed' ? Number(input.value).toFixed(2)+'×' : name==='trans-dur' ? input.value+'s' : input.value;
    });
    applyZoomPreview(); applyFilterPreview(); updateCropOverlay(); renderPreviewOverlays(); renderTimeline(); renderInspector(); renderTranscript();
    seekTo(Number(state.playhead)||trimIn);
    restoringDraft = false;
  }
  function travelHistory(delta) {
    saveDraft();
    var target = historyIndex + delta;
    if (target < 0 || target >= history.length) return;
    historyIndex = target;
    var state=history[target], sourceChanged=state.controls['editor-source-mode']!==$('editor-source-mode').value;
    if(sourceChanged) { draftReady=false; pendingDraft=state; }
    restoreEdit(state,sourceChanged); saveDraft(false); updateHistoryButtons();
  }
  function renderTranscript() {
    var list=$('editor-transcript-list'); if(!list) return;
    list.replaceChildren();
    var query=($('editor-transcript-search').value || '').toLowerCase();
    editorCaptions.chunks.forEach(function(chunk) {
      if (!chunk.words.some(function(word){return word.word.toLowerCase().includes(query);})) return;
      var row=document.createElement('div'); row.className='editor-transcript-line';
      var stamp=document.createElement('span'); stamp.className='muted small'; stamp.textContent=fmtClock(chunk.start); row.append(stamp);
      chunk.words.forEach(function(word) {
        var button=document.createElement('button'); button.type='button';button.textContent=word.word;button.title='Seek to '+fmtClock(word.start);
        button.addEventListener('click',function(){seekTo(word.start);syncCaptionOverlay();updatePlayhead();});row.append(button);
      });list.append(row);
    });
    if(!list.children.length) list.textContent=editorCaptions.chunks.length?'No matching words.':'No timed transcript is available for this clip.';
  }

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
    if (sourceSpan > 0) return sourceSpan;
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
      source: editorSource,
      sourceOffset: sourceOffset,
      crop: activeCrop,
      facecam: facecamState(),
      duration: duration,
      ratio: selectedRatio(),
      trimIn: trimIn,
      trimOut: trimOut || duration,
      zoom: parseFloat($('editor-zoom').value) || 1,
      speed: parseFloat($('editor-speed').value) || 1,
      volume: parseFloat($('editor-clip-volume').value),
      // Transitions map to fade duration + colour (Flash = white).
      fadeIn: ($('editor-trans-in').value !== 'none') ? (parseFloat($('editor-trans-dur').value) || 0.5) : 0,
      fadeOut: ($('editor-trans-out').value !== 'none') ? (parseFloat($('editor-trans-dur').value) || 0.5) : 0,
      fadeInColor: ($('editor-trans-in').value === 'flash') ? 'white' : 'black',
      fadeOutColor: ($('editor-trans-out').value === 'flash') ? 'white' : 'black',
      filter: selectedFilter(),
      music: {
        enabled: $('editor-music-enabled').checked,
        path: $('editor-music-path').value || '',
        gain: Number($('editor-music-volume').value),
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
      el.style.fontFamily = t.font || 'Arial'; el.style.fontWeight = t.bold === false ? '400' : '700'; el.style.fontStyle = t.italic ? 'italic' : 'normal';
      var outline=(t.outline === undefined ? 4 : t.outline)*scale;
      el.style.webkitTextStroke=outline+'px '+(t.outlineColor || '#000000');el.style.paintOrder='stroke fill';
      var shadow=(t.shadow || 0)*scale;el.style.textShadow=shadow+'px '+shadow+'px 0 #000';
      el.style.backgroundColor=t.box ? (t.boxColor || '#111111') : 'transparent';
      el.style.padding=t.box ? ((t.padding === undefined ? 12 : t.padding)*scale)+'px' : '0';
      el.style.borderRadius='0';
      el.style.fontSize = Math.max(10, (t.size || 96) * scale) + 'px';
      el.dataset.start = String(t.start);
      el.dataset.end = String(t.end);
      makeDraggable(el, 'text', i);
      layer.appendChild(el);
    });

    // Live caption line (single element, content swapped on each timeupdate so
    // the active word can highlight). Positioned over the shown video rect.
    if (editorCaptions.show && editorCaptions.chunks.length) {
      var cap = document.createElement('div');
      cap.id = 'editor-caption-live';
      cap.className = 'editor-ov-caption';
      var fs = Math.max(10, (editorCaptions.size || 72) * scale);
      var ow = Math.max(1, Math.round(fs * 0.06));
      var o = '#000';
      cap.style.position = 'absolute';
      cap.style.textAlign = 'center';
      cap.style.left = rect.left + 'px';
      cap.style.width = rect.w + 'px';
      cap.style.padding = '0 ' + (rect.w * 0.06) + 'px';
      cap.style.boxSizing = 'border-box';
      cap.style.pointerEvents = 'none';
      cap.style.fontFamily = '"Arial Black", Arial, sans-serif';
      cap.style.fontWeight = '800';
      cap.style.lineHeight = '1.15';
      cap.style.fontSize = fs + 'px';
      cap.style.color = editorCaptions.color || '#fff';
      cap.style.textShadow = [
        ow + 'px 0 0 ' + o, '-' + ow + 'px 0 0 ' + o, '0 ' + ow + 'px 0 ' + o, '0 -' + ow + 'px 0 ' + o,
        ow + 'px ' + ow + 'px 0 ' + o, '-' + ow + 'px -' + ow + 'px 0 ' + o,
        ow + 'px -' + ow + 'px 0 ' + o, '-' + ow + 'px ' + ow + 'px 0 ' + o,
      ].join(', ');
      var pos = editorCaptions.position || 'bottom';
      var topFrac = pos === 'top' ? 0.08 : (pos === 'middle' ? 0.45 : 0.72);
      cap.style.top = (rect.top + rect.h * topFrac) + 'px';
      layer.appendChild(cap);
      syncCaptionOverlay();
    }

    syncOverlayVisibility();
  }

  function escapeCap(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  // Rebuild caption chunks from the clip's word timings, grouped `chunk` words
  // at a time and rebased to clip-local seconds (word times are absolute source
  // times; the clip starts at editorClip.start_time).
  function buildCaptionChunks() {
    editorCaptions.chunks = [];
    var words = (editorClip && editorClip.words) || [];
    if (!words.length) return;
    var base = Number(editorClip.start_time) || 0;
    var n = Math.max(1, Math.min(6, parseInt(editorCaptions.chunk, 10) || 4));
    for (var i = 0; i < words.length; i += n) {
      var group = words.slice(i, i + n).map(function (w) {
        var s = Number(w.start); var e = Number(w.end);
        if (!isFinite(s)) s = 0;
        if (!isFinite(e)) e = s;
        return { word: String(w.word || '').trim(), start: Math.max(0, s - base), end: Math.max(0, e - base) };
      }).filter(function (w) { return w.word; });
      if (!group.length) continue;
      editorCaptions.chunks.push({ start: group[0].start, end: group[group.length - 1].end, words: group });
    }
  }

  // Swap the live caption text for the current playhead time, highlighting the
  // word being spoken (matches the exporter's active-word highlight).
  function syncCaptionOverlay() {
    var el = document.getElementById('editor-caption-live');
    if (!el) return;
    var vid = $('editor-video');
    var t = vid ? vid.currentTime - sourceOffset : 0;
    var chunk = null;
    for (var i = 0; i < editorCaptions.chunks.length; i++) {
      var c = editorCaptions.chunks[i];
      if (t >= c.start && t <= c.end + 0.05) { chunk = c; break; }
    }
    if (!chunk) { el.style.visibility = 'hidden'; el.innerHTML = ''; return; }
    el.style.visibility = 'visible';
    var active = 0;
    for (var j = 0; j < chunk.words.length; j++) { if (chunk.words[j].start <= t) active = j; }
    el.innerHTML = chunk.words.map(function (w, k) {
      var safe = escapeCap(w.word);
      return k === active
        ? '<span style="color:' + (editorCaptions.highlight || '#ffe000') + '">' + safe + '</span>'
        : '<span>' + safe + '</span>';
    }).join(' ');
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
    var t = vid ? vid.currentTime - sourceOffset : 0;
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
    return m + ':' + String(sec).padStart(2, '0') + '.' + String(Math.floor((s % 1) * 100)).padStart(2, '0');
  }

  // ---- TIMELINE ----------------------------------------------------------
  function seekTo(t) {
    var vid = $('editor-video');
    if (vid && isFinite(t)) vid.currentTime = sourceOffset + Math.max(0, Math.min(clipDuration(), t));
  }

  function updatePlayhead() {
    var ph = $('editor-tl-playhead');
    var vid = $('editor-video');
    var dur = clipDuration();
    if (!ph || !vid || !dur) return;
    ph.style.left = (Math.min(vid.currentTime - sourceOffset, dur) / dur * 100) + '%';
  }

  // Current [start,end] for a timeline item (video/text/sticker/sfx/music).
  function getSE(kind, index) {
    var dur = clipDuration() || 1;
    if (kind === 'video') return { start: trimIn, end: trimOut || dur };
    if (kind === 'music') return { start: 0, end: dur };
    if (kind === 'text') { var t = editorTexts[index]; return { start: t.start, end: t.end }; }
    if (kind === 'sticker') { var s = editorStickers[index]; return { start: s.start, end: s.end }; }
    var f = editorSfx[index]; return { start: f.start, end: Math.min(dur, f.start + SFX_DISPLAY_DUR) };
  }

  function writeSE(kind, index, s, e) {
    if (kind === 'video') { trimIn = s; trimOut = e; }
    else if (kind === 'text') { editorTexts[index].start = s; editorTexts[index].end = e; }
    else if (kind === 'sticker') { editorStickers[index].start = s; editorStickers[index].end = e; }
    else if (kind === 'sfx') { editorSfx[index].start = s; }   // one-shot: move only
  }

  function blockGeom(startT, endT) {
    var dur = clipDuration() || 1;
    return { left: (startT / dur * 100) + '%', width: (Math.max(0.001, endT - startT) / dur * 100) + '%' };
  }

  function makeBlock(kind, index, label, extraClass, withHandles) {
    var se = getSE(kind, index);
    var g = blockGeom(se.start, se.end);
    var block = document.createElement('div');
    block.className = 'tl-block ' + (extraClass || '');
    block.style.left = g.left; block.style.width = g.width;
    if (selected && selected.kind === kind && (selected.index === index || (index === -1 && selected.index === undefined))) {
      block.classList.add('is-selected');
    }
    var lbl = document.createElement('span'); lbl.className = 'tl-block-label'; lbl.textContent = label;
    block.appendChild(lbl);
    if (withHandles) {
      var l = document.createElement('span'); l.className = 'tl-handle tl-handle-l';
      var r = document.createElement('span'); r.className = 'tl-handle tl-handle-r';
      block.appendChild(l); block.appendChild(r);
    }
    block.addEventListener('pointerdown', function (e) { beginDrag(e, block, kind, index); });
    return block;
  }

  // ONE drag path for every block. Updates geometry + state LIVE (no full
  // re-render, which would destroy the block mid-drag — the bug this fixes),
  // then does a clean renderTimeline() only on release.
  function beginDrag(e, block, kind, index) {
    e.preventDefault();
    e.stopPropagation();
    // Select immediately (highlight without rebuilding, so the drag survives).
    selected = (index === -1) ? { kind: kind } : { kind: kind, index: index };
    document.querySelectorAll('#editor-timeline .tl-block.is-selected').forEach(function (b) { b.classList.remove('is-selected'); });
    block.classList.add('is-selected');
    renderInspector();

    if (kind === 'music') return;   // bed spans the whole clip; not draggable

    var lane = block.parentElement;
    var dur = clipDuration() || 1;
    var wpx = lane.getBoundingClientRect().width || 1;
    var mode = e.target.classList.contains('tl-handle-l') ? 'start'
      : e.target.classList.contains('tl-handle-r') ? 'end' : 'move';
    var orig = getSE(kind, index);
    var guide = $('editor-trim-guide');
    if (mode !== 'move') {
      guide.classList.remove('hidden');
      guide.style.left = ((mode === 'start' ? orig.start : orig.end) / dur * 100) + '%';
      $('editor-trim-feedback').textContent = 'Dragging ' + (mode === 'start' ? 'in' : 'out') + ' edge';
    }
    var startX = e.clientX;
    try { block.setPointerCapture(e.pointerId); } catch (_) { /* ignore */ }

    function onMove(ev) {
      var dt = (ev.clientX - startX) / wpx * dur;
      var s = orig.start, en = orig.end, len = orig.end - orig.start;
      if (mode === 'move') { s = Math.max(0, Math.min(orig.start + dt, dur - len)); en = s + len; }
      else if (mode === 'start') { s = Math.max(0, Math.min(orig.start + dt, orig.end - 0.2)); }
      else { en = Math.max(orig.start + 0.2, Math.min(orig.end + dt, dur)); }
      if($('editor-snap').checked && !ev.altKey) {
        var points=[0,dur,trimIn,trimOut,$('editor-video').currentTime-sourceOffset];
        editorTexts.concat(editorStickers).forEach(function(item){if((kind==='text' && item===editorTexts[index])||(kind==='sticker' && item===editorStickers[index]))return;points.push(item.start,item.end);});
        function snap(value){var closest=value, distance=8/wpx*dur;points.forEach(function(point){if(Math.abs(point-value)<distance){closest=point;distance=Math.abs(point-value);}});return closest;}
        if(mode==='start') s=Math.max(0,Math.min(snap(s),en-.2));
        else if(mode==='end') en=Math.min(dur,Math.max(s+.2,snap(en)));
        else {var snapped=snap(s);if(snapped===s) snapped=snap(en)-len;s=Math.max(0,Math.min(dur-len,snapped));en=s+len;}
      }
      writeSE(kind, index, s, en);
      if (mode !== 'move') guide.style.left = ((mode === 'start' ? s : en) / dur * 100) + '%';
      $('editor-trim-feedback').textContent = fmtClock(s) + ' → ' + fmtClock(en);
      $('editor-trim-in').value = trimIn.toFixed(2); $('editor-trim-out').value = trimOut.toFixed(2);
      var se = getSE(kind, index);
      var g = blockGeom(se.start, se.end);
      block.style.left = g.left; block.style.width = g.width;
      if (kind === 'video') seekTo(mode === 'start' ? s : en);
      if (kind === 'text' || kind === 'sticker') renderPreviewOverlays();
      else syncOverlayVisibility();
    }
    function onUp() {
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
      document.removeEventListener('pointercancel', onUp);
      guide.classList.add('hidden');
      renderTimeline();
      renderInspector();saveDraft();
    }
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
    document.addEventListener('pointercancel', onUp);
  }

  function renderTimeline() {
    var dur = clipDuration();
    $('editor-trim-in').value = trimIn.toFixed(2); $('editor-trim-out').value = trimOut.toFixed(2);
    var ruler = $('editor-tl-ruler');
    if (ruler) {
      ruler.innerHTML = '';
      var ticks = 4 * Number($('editor-timeline-zoom').value || 1);
      for (var k = 0; k <= ticks; k++) {
        var tick = document.createElement('span');
        tick.className = 'tl-tick';
        tick.style.left = (k / ticks * 100) + '%';
        tick.textContent = fmtClock(dur * k / ticks);
        ruler.appendChild(tick);
      }
    }

    var lv = $('lane-video');
    if (lv) {
      lv.innerHTML = '';
      lv.appendChild(makeBlock('video', -1, '🎬 ' + fmtClock(Math.max(0, (trimOut || dur) - trimIn)), 'tl-video', true));
    }
    var lt = $('lane-text');
    if (lt) {
      lt.innerHTML = '';
      editorTexts.forEach(function (t, i) { lt.appendChild(makeBlock('text', i, '🅣 ' + t.text.slice(0, 14), 'tl-text', true)); });
    }
    var lg = $('lane-graphics');
    if (lg) {
      lg.innerHTML = '';
      editorStickers.forEach(function (s, i) { lg.appendChild(makeBlock('sticker', i, '🖼 ' + s.name, 'tl-graphics', true)); });
    }
    var la = $('lane-audio');
    if (la) {
      la.innerHTML = '';
      if ($('editor-music-enabled').checked && $('editor-music-path').value) {
        la.appendChild(makeBlock('music', -1, '🎵 music', 'tl-music', false));
      }
      editorSfx.forEach(function (_s, i) { la.appendChild(makeBlock('sfx', i, '🔊', 'tl-sfx', false)); });
    }
    updatePlayhead();
  }

  // ---- selection inspector ----------------------------------------------
  function selectItem(kind, index) {
    selected = (index === undefined) ? { kind: kind } : { kind: kind, index: index };
    renderTimeline();
    renderInspector();
    var rail = document.querySelector('#editor-modal .editor-rail');
    if (rail) rail.scrollTop = 0;
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
    var dupBtn = function (fn) {
      var b = document.createElement('button');
      b.className = 'btn btn-small btn-secondary'; b.textContent = '⧉ Duplicate';
      b.addEventListener('click', fn);
      return b;
    };
    var actionRow = function () { var d = document.createElement('div'); d.className = 'editor-inspector-actions'; return d; };

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
      var font=document.createElement('select');
      ['Arial','Arial Black','Impact','Georgia','Courier New','Verdana'].forEach(function(name){var option=document.createElement('option');option.value=name;option.textContent=name;option.style.fontFamily=name;font.append(option);});font.value=t.font||'Arial';
      font.addEventListener('change',function(){t.font=font.value;renderPreviewOverlays();});box.append(inspectorRow('Font',font));
      function toggleStyle(label,key,fallback) {var input=document.createElement('input');input.type='checkbox';input.checked=t[key]===undefined?fallback:!!t[key];input.addEventListener('change',function(){t[key]=input.checked;renderPreviewOverlays();});box.append(inspectorRow(label,input));}
      function colorStyle(label,key,fallback) {var input=document.createElement('input');input.type='color';input.value=t[key]||fallback;input.addEventListener('input',function(){t[key]=input.value;renderPreviewOverlays();});box.append(inspectorRow(label,input));}
      toggleStyle('Bold','bold',true);toggleStyle('Italic','italic',false);
      colorStyle('Outline color','outlineColor','#000000');
      box.append(inspectorRow('Outline width',mkRange(0,12,1,t.outline===undefined?4:t.outline,function(v){t.outline=v;renderPreviewOverlays();})));
      box.append(inspectorRow('Shadow distance',mkRange(0,20,1,t.shadow||0,function(v){t.shadow=v;renderPreviewOverlays();})));
      toggleStyle('Square background','box',false);colorStyle('Background color','boxColor','#111111');
      box.append(inspectorRow('Background padding',mkRange(0,40,1,t.padding===undefined?12:t.padding,function(v){t.padding=v;renderPreviewOverlays();})));
      var timing=document.createElement('p');timing.className='muted small';timing.textContent='Applies to this text item only. Drag its timeline edges to set when it appears.';box.append(timing);
      var tRow = actionRow();
      tRow.appendChild(dupBtn(function () {
        var c = Object.assign({}, t); c.start = Math.min(clipDuration(), t.start + 0.3); c.y = Math.min(1, t.y + 0.06);
        editorTexts.push(c); selectItem('text', editorTexts.length - 1); renderPreviewOverlays();
      }));
      tRow.appendChild(removeBtn(function () { editorTexts.splice(selected.index, 1); selected = null; renderTimeline(); renderPreviewOverlays(); renderInspector(); }));
      box.appendChild(tRow);
    } else if (selected.kind === 'sticker') {
      var s = editorStickers[selected.index]; if (!s) { selected = null; return renderInspector(); }
      title.textContent = '🖼 ' + s.name;
      box.appendChild(inspectorRow('Size', mkRange(0.05, 1.5, 0.01, s.scale || 0.25, function (v) { s.scale = v; renderPreviewOverlays(); })));
      var sRow = actionRow();
      sRow.appendChild(dupBtn(function () {
        var c = Object.assign({}, s); c.start = Math.min(clipDuration(), s.start + 0.3); c.x = Math.min(1, s.x + 0.05);
        editorStickers.push(c); selectItem('sticker', editorStickers.length - 1); renderPreviewOverlays();
      }));
      sRow.appendChild(removeBtn(function () { editorStickers.splice(selected.index, 1); selected = null; renderTimeline(); renderPreviewOverlays(); renderInspector(); }));
      box.appendChild(sRow);
    } else if (selected.kind === 'sfx') {
      var fx = editorSfx[selected.index]; if (!fx) { selected = null; return renderInspector(); }
      title.textContent = '🔊 ' + fx.name;
      box.appendChild(inspectorRow('Volume', mkRange(0, 2, 0.05, fx.gain === undefined ? 0.8 : fx.gain, function (v) { fx.gain = v; })));
      var fRow = actionRow();
      fRow.appendChild(dupBtn(function () {
        var c = Object.assign({}, fx); c.start = Math.min(clipDuration(), fx.start + 0.3);
        editorSfx.push(c); selectItem('sfx', editorSfx.length - 1);
      }));
      fRow.appendChild(removeBtn(function () { editorSfx.splice(selected.index, 1); selected = null; renderTimeline(); renderInspector(); }));
      box.appendChild(fRow);
    } else if (selected.kind === 'music') {
      title.textContent = '🎵 Music bed';
      var vol = mkRange(0, 1, 0.01, Number($('editor-music-volume').value), function (v) {
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

  function facecamState() {
    if (!$('editor-facecam-enabled').checked) return null;
    return {crop: {...facecamCrop}, layout:$('editor-facecam-layout').value, size:Number($('editor-facecam-size').value)/100, corner:$('editor-facecam-corner').value};
  }
  function updateCropOverlay() {
    var overlay = $('editor-crop-overlay'), vid = $('editor-video'), rect = displayedVideoRect();
    if (!rect) return;
    var aspect = KlipzyEditorSpec.overlayAspect(selectedRatio()) || vid.videoWidth / vid.videoHeight;
    var face = facecamState();
    if (face && face.layout !== 'pip') aspect /= (1 - face.size);
    var cropW = Math.min(rect.w, rect.h * aspect), cropH = cropW / aspect;
    activeCrop = {x: (rect.w - cropW) * cropPosition.x / rect.w, y:(rect.h - cropH) * cropPosition.y / rect.h, w:cropW/rect.w, h:cropH/rect.h};
    overlay.classList.toggle('hidden', selectedRatio() === 'full' && !face);
    paintCrop(overlay, activeCrop, rect);
    var cam = $('editor-facecam-overlay'); cam.classList.toggle('hidden', !face);
    if (face) paintCrop(cam, facecamCrop, rect);
    drawOutputPreview();
  }
  function paintCrop(el, crop, rect) {
    el.style.left = (rect.left + rect.w * crop.x) + 'px'; el.style.top = (rect.top + rect.h * crop.y) + 'px';
    el.style.width = rect.w * crop.w + 'px'; el.style.height = rect.h * crop.h + 'px';
  }
  function bindCropDrag(id, camera) {
    var el = $(id);
    el.addEventListener('pointerdown', function (event) {
      event.preventDefault(); event.stopPropagation(); el.setPointerCapture(event.pointerId);
      var rect = displayedVideoRect(); if (!rect) return;
      var start = {x:event.clientX,y:event.clientY}, initial = {...(camera ? facecamCrop : activeCrop)};
      function move(e) {
        var x = Math.max(0, Math.min(1-initial.w, initial.x + (e.clientX-start.x)/rect.w));
        var y = Math.max(0, Math.min(1-initial.h, initial.y + (e.clientY-start.y)/rect.h));
        if (camera) { facecamCrop.x=x; facecamCrop.y=y; }
        else { cropPosition.x = x / (1-initial.w || 1); cropPosition.y = y / (1-initial.h || 1); }
        updateCropOverlay();
      }
      function up() { el.removeEventListener('pointermove',move); el.removeEventListener('pointerup',up); el.removeEventListener('pointercancel',up);saveDraft(); }
      el.addEventListener('pointermove',move); el.addEventListener('pointerup',up); el.addEventListener('pointercancel',up);
    });
    el.addEventListener('keydown', function(e) {
      if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)) return;
      e.preventDefault(); var dx = e.key === 'ArrowLeft' ? -.01 : e.key === 'ArrowRight' ? .01 : 0;
      var dy = e.key === 'ArrowUp' ? -.01 : e.key === 'ArrowDown' ? .01 : 0;
      if (camera) {facecamCrop.x=Math.max(0,Math.min(1-facecamCrop.w,facecamCrop.x+dx));facecamCrop.y=Math.max(0,Math.min(1-facecamCrop.h,facecamCrop.y+dy));}
      else {cropPosition.x=Math.max(0,Math.min(1,cropPosition.x+dx));cropPosition.y=Math.max(0,Math.min(1,cropPosition.y+dy));}
      updateCropOverlay();
    });
  }
  function drawOutputPreview() {
    var vid=$('editor-video'), canvas=$('editor-output-canvas');
    if (!vid.videoWidth || vid.readyState < 2 || !activeCrop) return;
    var ratio=KlipzyEditorSpec.overlayAspect(selectedRatio()) || vid.videoWidth/vid.videoHeight;
    canvas.width=320; canvas.height=Math.round(320/ratio);
    var ctx=canvas.getContext('2d'), face=facecamState(), w=canvas.width,h=canvas.height;
    function draw(crop,x,y,dw,dh) {
      var sx=crop.x*vid.videoWidth,sy=crop.y*vid.videoHeight,sw=crop.w*vid.videoWidth,sh=crop.h*vid.videoHeight;
      var target=dw/dh;
      if(sw/sh>target) {sx+=(sw-sh*target)/2;sw=sh*target;} else {sy+=(sh-sw/target)/2;sh=sw/target;}
      ctx.drawImage(vid,sx,sy,sw,sh,x,y,dw,dh);
    }
    var ch=face && face.layout!=='pip' ? h*face.size : 0;
    draw(activeCrop,0,face?.layout==='top'?ch:0,w,h-ch);
    if(face) {
      if(face.layout==='pip') {
        var cw=w*face.size; ch=Math.min(h*.45,cw*face.crop.h*vid.videoHeight/(face.crop.w*vid.videoWidth));
        draw(face.crop,face.corner.includes('right')?w-cw:0,face.corner.includes('bottom')?h-ch:0,cw,ch);
      } else draw(face.crop,0,face.layout==='top'?0:h-ch,w,ch);
    }
  }
  function loadEditorSource() {
    var original=$('editor-source-mode').value==='original';
    editorSource=original ? (editorOriginalSource) : editorClip.output_file;
    sourceOffset=original ? Number(editorClip.start_time)||0 : 0;
    sourceSpan=original ? Math.max(.1,Number(editorClip.end_time)-sourceOffset) : 0;
    timelineRequest++;if(mediaAbort)mediaAbort.abort();timelineMedia=null;paintTimelineMedia();
    cropPosition={x:.5,y:.5}; activeCrop=null;
    $('editor-source-note').textContent=original ? 'Original source: rebuild framing from the full image. Baked captions, silence cuts and effects from the rendered clip are not included.' : 'Rendered clip: existing captions and effects are preserved. Choose Original video to recover areas outside this crop.';
    var vid=$('editor-video');vid.pause();vid.src=fileUrl(editorSource);vid.load();
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
    if(exportStarting || editorJobId) return;
    saveDraft();
    var spec = KlipzyEditorSpec.buildEditSpec(editorState());
    if (!spec) { showToast('Nothing to export.', 'info'); return; }
    exportStarting=true;
    var btn = $('editor-export-btn');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Rendering…'; }
    $('editor-cancel-btn').classList.remove('hidden');
    setEditorProgress(0, 'Starting…', 'Rendering');
    var body = { spec: spec, output_dir:$('editor-export-folder').value.trim() || undefined, filename:$('editor-export-filename').value.trim() || undefined };
    var capPayload = captionExportPayload();
    if (capPayload) {
      body.burn_captions = true;
      body.caption_words = capPayload.words;
      body.caption_style = capPayload.style;
    }
    try {
      var res = await fetch(serverUrl + '/editor/export', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      var data = await res.json().catch(function () { return {}; });
      if (!res.ok) throw new Error(data.detail || ('Server returned ' + res.status));
      pollEditorExport(data.job_id);
    } catch (e) { stopEditorPolling(); showError(e.message); }
    finally { exportStarting=false; }
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
      '5. Zoom / speed / clip volume / transitions — in the controls rail.\n' +
      '6. Export — renders locally using your available hardware.',
      '✂️ Editor — quick tour'
    );
  }

  // ---- wiring ------------------------------------------------------------
  function setupEditorWorkspace() {
    var top=document.querySelector('#editor-modal .editor-top');
    if(top.querySelector('.editor-media-bin')) return;
    var media=document.createElement('aside');media.className='editor-media-bin';media.setAttribute('aria-label','Project clips');
    var heading=document.createElement('h4');heading.textContent='Project clips';media.append(heading);
    media.append(document.querySelector('.editor-clip-navigation'));
    var list=document.createElement('div');list.id='editor-media-clips';media.append(list);top.prepend(media);
    var preview=document.querySelector('#editor-modal .editor-preview');
    var viewers=document.createElement('div');viewers.className='editor-viewers';
    preview.prepend(viewers);viewers.append($('editor-stage'));
    var output=document.querySelector('#editor-modal .editor-output-preview');
    var toggle=document.createElement('details');toggle.className='editor-framing-preview';
    var summary=document.createElement('summary');summary.textContent='Output framing';toggle.append(summary,output);viewers.append(toggle);
    toggle.addEventListener('toggle',function(){requestAnimationFrame(function(){updateCropOverlay();renderPreviewOverlays();drawOutputPreview();});});
    output.querySelector('span').remove();
    var header=document.querySelector('#editor-modal .editor-header-actions');
    header.insertBefore($('editor-export-btn'),$('close-editor-modal'));header.insertBefore($('editor-cancel-btn'),$('close-editor-modal'));
    var timelineTools=document.querySelector('#editor-modal .editor-timeline-tools');timelineTools.prepend($('editor-undo'),$('editor-redo'));
    media.append(document.querySelector('#editor-modal .editor-export-settings'));
    $('editor-export-btn').textContent='Export';
  }
  function renderEditorMediaBin() {
    var list=$('editor-media-clips');if(!list)return;list.replaceChildren();
    generatedClips.forEach(function(clip,index){
      var button=document.createElement('button');button.type='button';button.className='editor-media-item';button.setAttribute('aria-pressed',String(index===editorClipIndex));
      if(clip.thumbnail_path){var image=document.createElement('img');image.src=fileUrl(clip.thumbnail_path);image.alt='';button.append(image);}
      var title=document.createElement('span');title.textContent=clip.title||clip.hook_text||('Clip '+(index+1));button.append(title);
      button.addEventListener('click',function(){window.openEditor(index);});list.append(button);
    });
  }

  function setupEditorTools() {
    var rail = document.querySelector('#editor-modal .editor-rail');
    if (!rail || rail.querySelector('.editor-tool-tabs')) return;
    var groups = Array.from(rail.children);
    var tabs = document.createElement('div'); tabs.className = 'editor-tool-tabs'; tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', 'Editing tools');
    var definitions = [ ['layout', 'Layout'], ['text', 'Text'], ['audio', 'Audio'], ['captions', 'Captions'], ['effects', 'Effects'] ];
    var panels = {};
    definitions.forEach(function (entry) {
      var key = entry[0], button = document.createElement('button'), panel = document.createElement('div');
      button.type = 'button'; button.id = 'editor-tool-' + key; button.textContent = entry[1]; button.setAttribute('role', 'tab'); button.setAttribute('aria-controls', 'editor-panel-' + key);
      panel.id = 'editor-panel-' + key; panel.className = 'editor-tool-panel'; panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', button.id);
      panels[key] = panel;
      button.addEventListener('click', function () {
        Array.from(tabs.children).forEach(function (tab) { var active = tab === button; tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1; });
        Object.keys(panels).forEach(function (name) { panels[name].hidden = name !== key; });
      });
      button.addEventListener('keydown', function (event) {
        var buttons = Array.from(tabs.children), index = buttons.indexOf(button), next;
        if (event.key === 'ArrowRight') next = (index + 1) % buttons.length;
        if (event.key === 'ArrowLeft') next = (index + buttons.length - 1) % buttons.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = buttons.length - 1;
        if (next !== undefined) { event.preventDefault(); buttons[next].click(); buttons[next].focus(); }
      });
      tabs.append(button); rail.append(panel);
    });
    // Move the existing inputs, retaining their values and event handlers.
    groups.forEach(function (group) {
      if (group.id === 'editor-inspector') return;
      var key = group.querySelector('#editor-captions-show, #editor-transcript-search') ? 'captions' :
        group.querySelector('#editor-add-text') ? 'text' :
        group.querySelector('#editor-filter-chips, #editor-zoom, #editor-trans-in') ? 'effects' : 'layout';
      panels[key].append(group);
    });
    var audio = document.createElement('div'); audio.className = 'editor-group';
    var label = document.createElement('span'); label.className = 'editor-group-label'; label.textContent = 'Audio & music'; audio.append(label);
    audio.append($('editor-clip-volume').closest('label'));
    var music = $('editor-music-enabled').closest('label');
    while (music) { var next = music.nextElementSibling; audio.append(music); music = next; }
    audio.prepend($('editor-add-sfx'));
    panels.audio.append(audio);
    rail.prepend(tabs);
    var inspector = $('editor-inspector'); rail.insertBefore(inspector, tabs.nextSibling);
    tabs.firstElementChild.click();
  }

  function bindEditorOnce() {
    var modal = $('editor-modal');
    if (!modal || modal.dataset.editorBound) return;
    modal.dataset.editorBound = '1';
    setupEditorTools();
    setupEditorWorkspace();
    $('editor-save-draft').addEventListener('click',function(){saveDraft();});
    $('editor-undo').addEventListener('click',function(){travelHistory(-1);});
    $('editor-redo').addEventListener('click',function(){travelHistory(1);});
    $('editor-clip-picker').addEventListener('change',function(){window.openEditor(Number(this.value));this.value=editorClipIndex;});
    $('editor-transcript-search').addEventListener('input',renderTranscript);
    ['prev','next'].forEach(function(direction){$('editor-frame-'+direction).addEventListener('click',function(){var vid=$('editor-video');vid.pause();seekTo(vid.currentTime-sourceOffset+(direction==='prev'?-1:1)/sourceFps);updatePlayhead();syncCaptionOverlay();});});
    function checkpoint(event) {
      if(event.target.closest('#editor-undo, #editor-redo, #editor-clip-picker, .editor-tool-tabs')) return;
      queueMicrotask(function(){saveDraft();});
    }
    modal.addEventListener('change',checkpoint);modal.addEventListener('click',checkpoint);modal.addEventListener('pointerup',checkpoint);modal.addEventListener('keyup',checkpoint);
    modal.addEventListener('input',function(event){
      if(event.target.id==='editor-transcript-search') return;
      if(!draftReady || restoringDraft) return;
      draftStatus('Saving draft…');clearTimeout(draftTimer);draftTimer=setTimeout(function(){saveDraft();},450);
    });
    window.addEventListener('beforeunload',function(){saveDraft();});
    modal.addEventListener('keydown',function(event){
      var typing=event.target.matches('input, textarea, select, [contenteditable]');
      if(!typing && (event.ctrlKey||event.metaKey) && ['z','y'].includes(event.key.toLowerCase())) {event.preventDefault();travelHistory(event.key.toLowerCase()==='y'||event.shiftKey?1:-1);}
      if((event.ctrlKey||event.metaKey) && event.key.toLowerCase()==='s') {event.preventDefault();saveDraft();}
    });

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
      if (sourceSpan) sourceSpan = Math.max(.1, Math.min(sourceSpan, vid.duration - sourceOffset));
      dur = clipDuration(); trimIn = 0; trimOut = dur;
      seekTo(0);
      updateCropOverlay();
      renderPreviewOverlays();
      renderTimeline();
      $('editor-time').textContent = fmtClock(0) + ' / ' + fmtClock(dur);
      if (pendingDraft) { var restored=pendingDraft; pendingDraft=null; restoreEdit(restored,false); trimOut=Math.min(trimOut,dur);trimIn=Math.min(trimIn,Math.max(0,trimOut-.04));renderTimeline(); }
      draftReady=true;saveDraft();renderTranscript();loadTimelineMedia();
    });
    vid.addEventListener('timeupdate', function () {
      var o = trimOut || vid.duration;
      if (!vid.paused && vid.currentTime - sourceOffset > o) seekTo(trimIn);   // loop within trim
      $('editor-time').textContent = fmtClock(vid.currentTime - sourceOffset) + ' / ' + fmtClock(clipDuration());
      syncOverlayVisibility();
      syncCaptionOverlay();
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

    bindCropDrag('editor-crop-overlay',false); bindCropDrag('editor-facecam-overlay',true);
    $('editor-source-mode').addEventListener('change',loadEditorSource);
    ['editor-facecam-enabled','editor-facecam-layout','editor-facecam-size','editor-facecam-corner','editor-facecam-width','editor-facecam-height'].forEach(function(id) {
      $(id).addEventListener('input',function() {
        facecamCrop.w=Number($('editor-facecam-width').value)/100; facecamCrop.h=Number($('editor-facecam-height').value)/100;
        facecamCrop.x=Math.min(facecamCrop.x,1-facecamCrop.w);facecamCrop.y=Math.min(facecamCrop.y,1-facecamCrop.h);updateCropOverlay();
      });
    });
    $('editor-timeline-zoom').addEventListener('input',function() { $('editor-tl-tracks').style.width=(Number(this.value)*100)+'%';renderTimeline(); });
    ['editor-trim-in','editor-trim-out'].forEach(function(id) {$(id).addEventListener('change',function() {
      trimIn=Math.max(0,Math.min(Number($('editor-trim-in').value)||0,clipDuration()-.01));
      trimOut=Math.max(trimIn+.01,Math.min(Number($('editor-trim-out').value)||clipDuration(),clipDuration()));seekTo(trimIn);renderTimeline();
    });});
    modal.addEventListener('dragover', function(e) { e.preventDefault(); });
    modal.addEventListener('drop', function(e) {
      e.preventDefault();
      Array.from(e.dataTransfer.files).forEach(function(file) {
        var path=file.path || window.clipperAPI?.getPathForFile?.(file); if(!path) return;
        var start=Math.max(0,vid.currentTime-sourceOffset);
        if (/\.(png|jpe?g|webp)$/i.test(file.name)) editorStickers.push({path:path,name:file.name,x:.5,y:.5,scale:.25,start:start,end:clipDuration()});
        else if (/\.(wav|mp3|m4a|ogg|flac)$/i.test(file.name)) editorSfx.push({path:path,name:file.name,start:start,gain:.8});
        else showToast('Drop an image or audio file. Use the source selector for video framing.', 'info');
      });renderTimeline();renderPreviewOverlays();
    });
    vid.addEventListener('seeked',drawOutputPreview);
    vid.addEventListener('play',function tick() {drawOutputPreview();if(!vid.paused) previewFrame=requestAnimationFrame(tick);});
    $('editor-zoom').addEventListener('input', applyZoomPreview);
    $('editor-speed').addEventListener('input', function () {
      var sp = parseFloat(this.value) || 1;
      $('editor-speed-label').textContent = sp.toFixed(2) + '×';
      if (vid) { try { vid.playbackRate = sp; } catch (_) { /* clamp */ } }
    });
    $('editor-trans-dur').addEventListener('input', function () { $('editor-trans-dur-label').textContent = (parseFloat(this.value) || 0).toFixed(1) + 's'; });
    $('editor-clip-volume').addEventListener('input', function () { $('editor-clip-volume-label').textContent = Math.round((parseFloat(this.value) || 0) * 100) + '%'; });

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
    $('editor-add-hook').addEventListener('click',function(){
      var text=($('editor-text-input').value || editorClip.intro_caption || editorClip.hook_text || '').trim();
      if(!text) {showToast('Enter your opening headline first.','info');return;}
      editorTexts.push({text:text,x:.5,y:.12,color:'#ffffff',font:'Arial Black',size:80,start:trimIn,end:Math.min(trimOut||clipDuration(),trimIn+3),box:true,boxColor:'#111111',padding:12,outline:0});
      $('editor-text-input').value='';renderPreviewOverlays();renderTimeline();selectItem('text',editorTexts.length-1);
    });
    $('editor-add-sticker').addEventListener('click', addEditorSticker);
    $('editor-add-sfx').addEventListener('click', addEditorSfx);

    // ---- captions -------------------------------------------------------
    var capShow = $('editor-captions-show');
    if (capShow) capShow.addEventListener('change', function () { editorCaptions.show = this.checked; renderPreviewOverlays(); });
    var capBurn = $('editor-captions-burn');
    if (capBurn) capBurn.addEventListener('change', function () { editorCaptions.burn = this.checked; });
    var capPos = $('editor-captions-position');
    if (capPos) capPos.addEventListener('change', function () { editorCaptions.position = this.value; renderPreviewOverlays(); });
    var capChunk = $('editor-captions-chunk');
    if (capChunk) capChunk.addEventListener('input', function () {
      editorCaptions.chunk = parseInt(this.value, 10) || 4;
      var lbl = $('editor-captions-chunk-label'); if (lbl) lbl.textContent = String(editorCaptions.chunk);
      buildCaptionChunks(); renderPreviewOverlays();
    });
    var capSize = $('editor-captions-size');
    if (capSize) capSize.addEventListener('input', function () {
      editorCaptions.size = parseInt(this.value, 10) || 72;
      var lbl = $('editor-captions-size-label'); if (lbl) lbl.textContent = String(editorCaptions.size);
      renderPreviewOverlays();
    });
    var capColor = $('editor-captions-color');
    if (capColor) capColor.addEventListener('input', function () { editorCaptions.color = this.value; renderPreviewOverlays(); });
    var capHi = $('editor-captions-highlight');
    if (capHi) capHi.addEventListener('input', function () { editorCaptions.highlight = this.value; renderPreviewOverlays(); });

    window.addEventListener('resize', function () { updateCropOverlay(); renderPreviewOverlays(); });
  }

  async function addEditorSticker() {
    var file = null;
    if (window.clipperAPI && window.clipperAPI.selectCameraClip) file = await window.clipperAPI.selectCameraClip();
    else file = window.prompt('Paste the full path to an image/sticker (png/jpg):');
    if (!file) return;
    editorStickers.push({ path: file, name: baseName(file), x: 0.5, y: 0.5, scale: 0.25, start: 0, end: clipDuration() });
    renderPreviewOverlays(); renderTimeline();
    selectItem('sticker', editorStickers.length - 1);saveDraft();
  }

  async function addEditorSfx() {
    var file = null;
    if (window.clipperAPI && window.clipperAPI.selectMusic) file = await window.clipperAPI.selectMusic();
    else file = window.prompt('Paste the full path to a sound effect (mp3/wav):');
    if (!file) return;
    editorSfx.push({ path: file, name: baseName(file), start: 0, gain: 0.8 });
    renderTimeline();
    selectItem('sfx', editorSfx.length - 1);saveDraft();
  }

  async function pickEditorMusic() {
    var file = null;
    if (window.clipperAPI && window.clipperAPI.selectMusic) file = await window.clipperAPI.selectMusic();
    else file = window.prompt('Paste the full path to a music file:');
    if (file) {
      $('editor-music-path').value = file;
      $('editor-music-enabled').checked = true;
      renderTimeline();
      saveDraft();
      showToast('🎵 Music added — it ducks under speech on export.', 'success');
    }
  }

  function baseName(p) { return String(p || '').split(/[\\/]/).pop() || p; }

  // Reset + populate the caption panel for a freshly opened clip. Captions are
  // shown by default when the clip has word timings, but never burned unless
  // the user opts in.
  function initEditorCaptions(clip) {
    var hasWords = !!(clip && clip.words && clip.words.length);
    editorCaptions.show = hasWords;
    editorCaptions.burn = false;
    editorCaptions.position = 'bottom';
    editorCaptions.chunk = 4;
    editorCaptions.size = 72;
    editorCaptions.color = '#ffffff';
    editorCaptions.highlight = '#ffe000';
    editorCaptions.chunks = [];

    var group = $('editor-captions-controls');
    var none = $('editor-captions-none');
    if (group) group.classList.toggle('hidden', !hasWords);
    if (none) none.classList.toggle('hidden', hasWords);

    var setVal = function (id, v) { var el = $(id); if (el) { if (el.type === 'checkbox') el.checked = !!v; else el.value = v; } };
    setVal('editor-captions-show', editorCaptions.show);
    setVal('editor-captions-burn', editorCaptions.burn);
    setVal('editor-captions-position', editorCaptions.position);
    setVal('editor-captions-chunk', editorCaptions.chunk);
    setVal('editor-captions-size', editorCaptions.size);
    setVal('editor-captions-color', editorCaptions.color);
    setVal('editor-captions-highlight', editorCaptions.highlight);
    var cl = $('editor-captions-chunk-label'); if (cl) cl.textContent = String(editorCaptions.chunk);
    var sl = $('editor-captions-size-label'); if (sl) sl.textContent = String(editorCaptions.size);

    if (hasWords) buildCaptionChunks();
  }

  // Caption payload for the export request — only when the user opts to burn.
  // Word times are rebased to the OUTPUT timeline: clip-local minus the trim
  // in-point, divided by playback speed, and clamped to the visible range.
  function captionExportPayload() {
    if (!editorCaptions.burn || !editorCaptions.chunks.length) return null;
    var speed = parseFloat($('editor-speed').value) || 1;
    if (!(speed > 0)) speed = 1;
    var tIn = trimIn || 0;
    var tOut = trimOut || clipDuration();
    var words = [];
    editorCaptions.chunks.forEach(function (c) {
      c.words.forEach(function (w) {
        if (w.end <= tIn || w.start >= tOut) return;   // outside the kept range
        var s = (w.start - tIn) / speed;
        var e = (Math.min(w.end,tOut) - tIn) / speed;
        words.push({ word: w.word, start: Math.max(0, s), end: Math.max(0.04, e) });
      });
    });
    if (!words.length) return null;
    return {
      words: words,
      style: {
        primary_color: editorCaptions.color,
        highlight_color: editorCaptions.highlight,
        font_size: Math.round(editorCaptions.size),
        chunk_size: parseInt(editorCaptions.chunk, 10) || 4,
        position: editorCaptions.position === 'top' ? 8 : (editorCaptions.position === 'middle' ? 5 : 2),
      },
    };
  }

  function closeEditor() {
    saveDraft();
    timelineRequest++;if(mediaAbort)mediaAbort.abort();
    draftReady = false;
    var vid = $('editor-video');
    if (vid) { vid.pause(); if(!editorJobId && !exportStarting) {vid.removeAttribute('src'); vid.load();} }
    if(previewFrame) cancelAnimationFrame(previewFrame);
    $('editor-progress').classList.add('hidden');
    $('editor-modal').classList.add('hidden');
  }

  window.openEditor = function (clipIndex) {
    if (editorJobId || exportStarting) { $('editor-modal').classList.remove('hidden'); showToast('The current edit is still exporting. You can close this panel and keep working.', 'info'); return; }
    var clip = generatedClips[clipIndex];
    if (!clip) return;
    saveDraft();
    draftReady=false; clearTimeout(draftTimer); pendingDraft=null;
    editorClipIndex=clipIndex;
    draftKey='klipzy.editor.draft.v1.'+JSON.stringify([typeof currentProjectId!=='undefined'?currentProjectId:null,clip.source_file||'',clip.id||clip.output_file,clip.start_time,clip.end_time]);
    try { var saved=JSON.parse(localStorage.getItem(draftKey)); if(saved && saved.version===1) pendingDraft=saved; } catch (_) { draftStatus('Could not read the saved draft'); }
    history=[]; historyIndex=-1;
    editorClip = clip;
    editorOriginalSource=clip.source_file || (typeof selectedVideo!=='undefined'?selectedVideo:null);
    $('editor-export-folder').value=$('output-folder-input')?.value || '';
    $('editor-export-filename').value='';
    sourceSpan=0;sourceOffset=0;
    bindEditorOnce();
    $('editor-tool-layout')?.click();
    $('editor-modal-title').textContent = 'Edit Clip · ' + (clip.title || clip.hook_text || 'Untitled');

    document.querySelectorAll('#editor-ratio-chips .ratio-chip').forEach(function (c) { c.classList.toggle('is-selected', c.dataset.ratio === 'full'); });
    document.querySelectorAll('#editor-filter-chips .filter-chip').forEach(function (c) { c.classList.toggle('is-selected', c.dataset.filter === 'none'); });
    $('editor-zoom').value = '1'; applyZoomPreview(); applyFilterPreview();
    $('editor-speed').value = '1'; $('editor-speed-label').textContent = '1.00×';
    try { $('editor-video').playbackRate = 1; } catch (_) { /* ignore */ }
    $('editor-clip-volume').value = '1'; $('editor-clip-volume-label').textContent = '100%';
    $('editor-trans-in').value = 'none'; $('editor-trans-out').value = 'none';
    $('editor-trans-dur').value = '0.5'; $('editor-trans-dur-label').textContent = '0.5s';
    $('editor-music-enabled').checked = false; $('editor-music-path').value = '';
    $('editor-music-volume').value = '0.12'; $('editor-music-volume-label').textContent = '12%';
    $('editor-music-duck').checked = true;
    editorTexts = []; editorStickers = []; editorSfx = [];
    initEditorCaptions(clip);
    trimIn = 0; trimOut = Number(clip.duration) || 0;
    selected = null;
    renderInspector();
    var layer = $('editor-overlay-layer'); if (layer) layer.innerHTML = '';
    renderTimeline();
    $('editor-progress').classList.add('hidden');
    $('editor-crop-overlay').classList.add('hidden');

    var vid = $('editor-video');
    $('editor-facecam-enabled').checked=false; facecamCrop={x:.7,y:.05,w:.25,h:.25};
    $('editor-facecam-width').value='25';$('editor-facecam-height').value='25';
    $('editor-timeline-zoom').value='1';$('editor-tl-tracks').style.width='100%';
    $('editor-source-mode').value='rendered';
    $('editor-source-mode').options[1].disabled=!editorOriginalSource;
    vid.muted = false; loadEditorSource();

    var picker=$('editor-clip-picker');picker.replaceChildren();
    generatedClips.forEach(function(item,index){var option=document.createElement('option');option.value=index;option.textContent=item.title||item.hook_text||('Clip '+(index+1));picker.append(option);});picker.value=clipIndex;renderEditorMediaBin();
    if(pendingDraft) restoreEdit(pendingDraft,true);
    draftStatus(pendingDraft?'Restoring saved draft…':'Loading clip…'); updateHistoryButtons(); renderTranscript();
    $('editor-modal').classList.remove('hidden');

    try {
      if (!localStorage.getItem(TIP_SEEN_KEY)) {
        localStorage.setItem(TIP_SEEN_KEY, '1');
        setTimeout(function () { showToast('Tip: drag the 🎬 block ends to trim, add text/graphics, then Export. Tap ❓ Tutorial any time.', 'info'); }, 400);
      }
    } catch (_) { /* localStorage unavailable */ }
  };
})();
