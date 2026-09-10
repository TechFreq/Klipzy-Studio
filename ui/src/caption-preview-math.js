/*
 * Caption-preview scaling math — the pure arithmetic behind the live caption /
 * hook previews in caption-editor.js.
 *
 * Why this exists: the same "map a 1080/1920-px caption size down to the on-
 * screen preview" math was copy-pasted across updateHookPreview,
 * applyCaptionPreviewStyle and applyPortraitCaptionPreviewStyle — the exact
 * "duplicated logic drifting apart" bug class the handoff calls out (one place
 * handled 16:9 differently than another). Centralising the canvas-width and
 * scale rules here means a change lands in ALL previews at once, and it's unit-
 * testable with no DOM.
 *
 * Same dual-mode contract as the other extracted modules: classic script on
 * window (load before caption-editor.js) + require()-able under Node.
 * Dependency-free.
 */
(function (root) {
  'use strict';

  // The export canvas width for a delivery ratio. Vertical/square deliveries
  // (9:16, 4:5, 1:1) all render on a 1080-px-wide canvas; only 16:9 is 1920.
  // (One place used to spell this out ratio-by-ratio and another as a one-liner;
  // now there is a single source of truth.)
  function canvasWidthForRatio(ratio) {
    return ratio === '16:9' ? 1920 : 1080;
  }

  // Preview-to-canvas scale factor. Guards a zero/absent canvas width so callers
  // never divide by zero.
  function scaleFor(previewWidth, canvasWidth) {
    return canvasWidth ? previewWidth / canvasWidth : 0;
  }

  // Round a canvas-space size into preview pixels, clamped to a floor. Used for
  // the portrait caption font (floor 1) and its outline width (floor 0).
  function scaledPx(rawSize, scale, floorPx) {
    return Math.max(floorPx, Math.round(rawSize * scale));
  }

  // Like scaledPx, but also caps the result at maxPx BEFORE the floor — the
  // short landscape preview stage would otherwise show an oversized font. Used
  // for the modal caption preview and the intro-hook preview.
  function boundedPx(rawSize, scale, maxPx, floorPx) {
    return Math.max(floorPx, Math.round(Math.min(rawSize * scale, maxPx)));
  }

  // Resolve the usable preview width: the element's live width when it has one,
  // otherwise a sensible fallback, minus the horizontal padding so the caption
  // lands in the same coordinate space the exporter uses.
  function usableWidth(clientWidth, padX, fallback) {
    var base = clientWidth > 0 ? clientWidth : (fallback || 0);
    return base - (padX || 0);
  }

  var api = {
    canvasWidthForRatio: canvasWidthForRatio,
    scaleFor: scaleFor,
    scaledPx: scaledPx,
    boundedPx: boundedPx,
    usableWidth: usableWidth,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.KlipzyCaptionPreviewMath = api;
})(typeof window !== 'undefined' ? window : this);
