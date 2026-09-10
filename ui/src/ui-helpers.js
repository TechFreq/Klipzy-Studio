/*
 * Small, pure-ish UI helpers extracted from renderer.js so they can be unit
 * tested (with node --test, and against the real page via jsdom). Same dual-mode
 * pattern as whisper-sync.js: attaches KlipzyUI to window in the browser, and is
 * require()-able under Node. No app globals — safe to load first.
 *
 * These were inline in renderer.js's progress/polling code; centralizing them
 * both removes duplication and gives us something testable to point at.
 */
(function (root) {
  'use strict';

  // Map a backend progress step string to a short stage label for the badge.
  // Mirrors the logic that was inline in pollJob().
  function stageFromStep(stepText) {
    var s = String(stepText || '').toLowerCase();
    if (s.indexOf('download') !== -1) return 'Downloading model';
    if (s.indexOf('transcrib') !== -1 || s.indexOf('whisper') !== -1) return 'Transcribing';
    if (s.indexOf('audio') !== -1 || s.indexOf('energy') !== -1) return 'Audio Analysis';
    if (s.indexOf('highlight') !== -1 || s.indexOf('score') !== -1 || s.indexOf('llm') !== -1) return 'AI Scoring';
    if (s.indexOf('face') !== -1 || s.indexOf('crop') !== -1 || s.indexOf('track') !== -1) return 'Smart Cropping';
    if (s.indexOf('caption') !== -1 || s.indexOf('render') !== -1 || s.indexOf('burn') !== -1) return 'Rendering Subtitles';
    return 'Processing';
  }

  function formatMB(bytes) {
    return (Number(bytes || 0) / (1024 * 1024)).toFixed(0) + ' MB';
  }

  function clampPercent(percent) {
    var p = Math.round(Number(percent) || 0);
    return Math.max(0, Math.min(100, p));
  }

  // Fill the shared progress UI (badge / step / percent / bar). Takes the
  // document so it's testable against a real DOM without global state.
  function setProgressUI(doc, badge, stepText, percent) {
    if (!doc) return;
    var badgeEl = doc.getElementById('progress-stage-badge');
    if (badgeEl) badgeEl.textContent = badge;
    var stepEl = doc.getElementById('progress-step');
    if (stepEl) stepEl.textContent = stepText;
    var pct = clampPercent(percent);
    var fillEl = doc.getElementById('progress-fill');
    if (fillEl) fillEl.style.width = pct + '%';
    var percentEl = doc.getElementById('progress-percent');
    if (percentEl) percentEl.textContent = pct + '%';
  }

  var api = {
    stageFromStep: stageFromStep,
    formatMB: formatMB,
    clampPercent: clampPercent,
    setProgressUI: setProgressUI,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.KlipzyUI = api;
})(typeof window !== 'undefined' ? window : this);
