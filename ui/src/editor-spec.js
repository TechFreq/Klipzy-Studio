/*
 * Editor Spec builder — turns the editor UI's plain state object into the Edit
 * Spec the backend compositor consumes (server/core/edit_spec.py /
 * compositor.py). Kept PURE and dependency-injected so it unit-tests without a
 * browser, same dual-mode contract as the other extracted modules.
 *
 * Phase 1 scope mirrors EXACTLY what the compositor compiles today, so preview
 * and export stay WYSIWYG: reframe (canvas ratio), trim (clip in/out), a gentle
 * zoom, and one background music track (gain + duck). Overlays/text/filters are
 * later phases and are deliberately NOT offered yet.
 */
(function (root) {
  'use strict';

  var RATIOS = ['full', '9:16', '4:5', '1:1', '16:9'];

  function _num(v, d) {
    var n = parseFloat(v);
    return isFinite(n) ? n : d;
  }

  // Build the Edit Spec from editor UI state:
  //   { source, duration, ratio, trimIn, trimOut, zoom, fps,
  //     music: { enabled, path, gain, duck } }
  // Returns null when there's no source (nothing to render).
  function buildEditSpec(state) {
    state = state || {};
    var src = state.source;
    if (!src) return null;

    var ratio = RATIOS.indexOf(state.ratio) !== -1 ? state.ratio : 'full';
    var duration = _num(state.duration, 0);

    var trimIn = Math.max(0, _num(state.trimIn, 0));
    var trimOut = _num(state.trimOut, 0);
    // Fall back to the full duration (or a 1s floor) when the out-point is unset
    // or not after the in-point.
    if (!(trimOut > trimIn)) trimOut = duration > trimIn ? duration : trimIn + 1;

    var zoom = _num(state.zoom, 1.0);
    if (zoom < 1) zoom = 1.0;

    var tracks = [{
      kind: 'video',
      clips: [{
        src: src,
        in: trimIn,
        out: trimOut,
        start: 0,
        transform: {
          // 'full' means no re-crop; a concrete ratio re-frames the clip.
          cropRatio: ratio === 'full' ? null : ratio,
          zoom: zoom,
        },
      }],
    }];

    var music = state.music;
    if (music && music.enabled && music.path) {
      tracks.push({
        kind: 'audio',
        items: [{
          src: music.path,
          gain: Math.max(0, Math.min(4, _num(music.gain, 0.12))),
          duck: music.duck !== false,
        }],
      });
    }

    return {
      version: 1,
      source: src,
      canvas: { ratio: ratio, fps: _num(state.fps, 30) || 30 },
      duration: trimOut - trimIn,
      tracks: tracks,
    };
  }

  // The preview crop-overlay aspect (width/height) for a ratio, or null for
  // 'full' (no overlay — the clip is shown untouched).
  function overlayAspect(ratio) {
    switch (ratio) {
      case '9:16': return 9 / 16;
      case '4:5': return 4 / 5;
      case '1:1': return 1;
      case '16:9': return 16 / 9;
      default: return null; // 'full'
    }
  }

  var api = {
    RATIOS: RATIOS,
    buildEditSpec: buildEditSpec,
    overlayAspect: overlayAspect,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.KlipzyEditorSpec = api;
})(typeof window !== 'undefined' ? window : this);
