/*
 * Whisper model selection: single source of truth + two-way sync.
 *
 * This is the logic behind bug fix §5.3 (the model setting that appeared to be
 * ignored). It lives in its own file for one reason: it is DEPENDENCY-INJECTED
 * (document / storage / announce are passed in), so it can be unit-tested with a
 * fake DOM and fake localStorage — no browser, no jsdom, no build step.
 *
 * Dual-mode by design:
 *   - In the browser it loads as a CLASSIC script and attaches KlipzyWhisperSync
 *     to window, so the other classic scripts can call it (they share global
 *     scope). It must be listed BEFORE renderer.js in index.html.
 *   - Under Node it is require()-able (module.exports), which is how
 *     ui/test/whisper-sync.test.js exercises it via the built-in `node --test`.
 *
 * It intentionally has NO dependencies on any app global, so loading it first is
 * safe and testing it needs nothing else.
 */
(function (root) {
  'use strict';

  var STORAGE_KEY = 'klipzy.whisperModel';

  // Restore both whisper selects from the saved preference and wire two-way
  // change listeners, so the clip-time select (#whisper-model, the one the
  // /process payload reads), the Setup select (#ai-whisper-model), and
  // localStorage can never drift apart. Idempotent: a dataset guard means it is
  // safe to call on startup AND every time the Setup panel loads.
  //
  // opts: { doc, storage, announce? }  -> returns the resolved initial value.
  function syncWhisperModelSelects(opts) {
    opts = opts || {};
    var doc = opts.doc;
    var storage = opts.storage;
    var announce = opts.announce; // optional (message) => void, e.g. a toast

    if (!doc) return null;

    var clipSel = doc.getElementById('whisper-model');
    var setupSel = doc.getElementById('ai-whisper-model');
    var saved = storage ? storage.getItem(STORAGE_KEY) : null;

    // Saved preference wins; otherwise fall back to whatever a select already
    // shows (its HTML default) so an explicit default still beats "nothing".
    var initial = saved
      || (clipSel && clipSel.value)
      || (setupSel && setupSel.value)
      || 'base';
    if (clipSel) clipSel.value = initial;
    if (setupSel) setupSel.value = initial;

    function wire(source, mirror, doAnnounce) {
      if (!source) return;
      if (source.dataset && source.dataset.whisperSyncBound) return;
      if (source.dataset) source.dataset.whisperSyncBound = '1';
      source.addEventListener('change', function () {
        if (storage) storage.setItem(STORAGE_KEY, source.value);
        if (mirror && mirror.value !== source.value) mirror.value = source.value;
        // Keep the Setup recommendation highlight on the active choice, if shown.
        if (doc.querySelectorAll) {
          var btns = doc.querySelectorAll('.rec-choice[data-model-kind="whisper"]');
          for (var i = 0; i < btns.length; i++) {
            var b = btns[i];
            if (b.classList && b.dataset) {
              b.classList.toggle('is-selected', b.dataset.model === source.value);
            }
          }
        }
        if (doAnnounce && typeof announce === 'function') {
          announce('Transcription model set to ' + source.value + ' for the next run');
        }
      });
    }
    wire(clipSel, setupSel, false);
    wire(setupSel, clipSel, true);
    return initial;
  }

  // Pure decision (bug fix §5.1): given a /whisper/model-status response, does the
  // selected model need a visible download step before the job starts? True only
  // when the backend has something to fetch and it isn't already present.
  function shouldDownloadModel(status) {
    return !!(status && status.present === false && status.downloadable === true);
  }

  var api = {
    STORAGE_KEY: STORAGE_KEY,
    syncWhisperModelSelects: syncWhisperModelSelects,
    shouldDownloadModel: shouldDownloadModel,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.KlipzyWhisperSync = api;
})(typeof window !== 'undefined' ? window : this);
