/*
 * Whisper model pre-flight orchestration (bug fix §5.1), extracted from
 * renderer.js so the whole async flow can be unit-tested — the part that was
 * previously "assumed". Same dual-mode pattern as whisper-sync.js / ui-helpers.js.
 *
 * runModelPreflight() decides whether a download is needed, starts it, polls
 * progress to a terminal state, and reports readiness. All I/O and side effects
 * are injected (fetchFn, sleep, onProgress, onLog, onError, onDownloadStart/End)
 * so a test can drive it with a fake server and a fake clock — no network, no
 * DOM, no timers.
 *
 * Returns a Promise<boolean>: true when the model is ready to transcribe with,
 * false when the user cancelled or the download failed.
 */
(function (root) {
  'use strict';

  function needsDownload(status) {
    return !!(status && status.present === false && status.downloadable === true);
  }

  async function runModelPreflight(deps) {
    var model = (deps.model || 'base').trim();
    var serverUrl = deps.serverUrl || '';
    var fetchFn = deps.fetchFn;
    var sleep = deps.sleep || function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
    var onProgress = deps.onProgress || function () {};
    var onLog = deps.onLog || function () {};
    var onError = deps.onError || function () {};
    var formatMB = deps.formatMB || function (b) { return String(b); };
    var onDownloadStart = deps.onDownloadStart || function () {};
    var onDownloadEnd = deps.onDownloadEnd || function () {};
    var pollIntervalMs = deps.pollIntervalMs != null ? deps.pollIntervalMs : 650;
    var q = encodeURIComponent(model);

    // 1. Is the model already present? A status fetch that fails must NOT block
    //    the run — let the job proceed and surface any real error itself.
    var st;
    try {
      var sres = await fetchFn(serverUrl + '/api/setup/whisper/model-status?model=' + q);
      st = await sres.json();
    } catch (_e) {
      return true;
    }
    if (!needsDownload(st)) return true;

    // 2. Start the download.
    onProgress('Downloading model', 'Preparing to download the Whisper "' + model + '" model...', 0);
    onLog('⬇️ Downloading Whisper "' + model + '" model (first use)', 'start');

    var start;
    try {
      var pres = await fetchFn(serverUrl + '/api/setup/whisper/pull-start?model=' + q, { method: 'POST' });
      start = await pres.json();
    } catch (e) {
      onError('Couldn\'t start the model download: ' + (e && e.message ? e.message : e));
      return false;
    }
    if (start && (start.already_present || start.not_needed)) return true;
    if (start && start.started === false && !start.already_running) {
      // Nothing started and not already running — treat as ready rather than hang.
      return true;
    }

    // 3. Poll progress until a terminal state.
    onDownloadStart(model);
    try {
      while (true) {
        await sleep(pollIntervalMs);
        var p;
        try {
          var gres = await fetchFn(serverUrl + '/api/setup/whisper/pull-progress?model=' + q);
          p = await gres.json();
        } catch (_e2) {
          continue;  // transient; keep polling
        }
        var pctText = p.total ? (formatMB(p.completed) + ' of ' + formatMB(p.total)) : 'starting...';
        onProgress('Downloading model', 'Downloading Whisper "' + model + '" model — ' + pctText, p.percent || 0);

        if (p.state === 'success' || (p.done && !p.error && p.state !== 'cancelled')) {
          onLog('✅ Whisper "' + model + '" model ready', 'ok');
          return true;
        }
        if (p.state === 'cancelled') {
          onLog('🛑 Model download cancelled', 'err');
          return false;
        }
        if (p.state === 'error') {
          onError('Model download failed: ' + (p.error || 'unknown error'));
          return false;
        }
      }
    } finally {
      onDownloadEnd(model);
    }
  }

  var api = { runModelPreflight: runModelPreflight, needsDownload: needsDownload };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.KlipzyModelPreflight = api;
})(typeof window !== 'undefined' ? window : this);
