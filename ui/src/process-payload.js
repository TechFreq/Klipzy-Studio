/*
 * /process request body builder — extracted from renderer.js so it can be unit
 * tested without a browser (Step 3 of the test-hardening pass).
 *
 * Same dual-mode contract as whisper-sync.js:
 *   - In the browser it loads as a CLASSIC script and attaches
 *     KlipzyProcessPayload to window; it must load BEFORE renderer.js, whose
 *     buildProcessPayload() is now a thin wrapper over build() here.
 *   - Under Node it is require()-able (module.exports) for ui/test.
 *
 * It is DEPENDENCY-INJECTED: the caller passes the `document` to read, the
 * resolved `selectedVideo` path, and the already-collected `captionOptions`
 * (collectCaptionOptions lives in caption-editor.js). Everything else is read
 * off `doc`, so a fake DOM drives it in tests.
 *
 * IMPORTANT: this preserves renderer.js's EXACT access pattern, including the
 * UNGUARDED reads (getElementById('max-clips').value with no `?.`). Those
 * elements are guaranteed by index.html and asserted by the page-wiring test;
 * if one goes missing, "Start Clipping" should still fail loudly rather than
 * silently send a half-built payload. Optional controls keep their `?.`/ternary
 * guards and defaults exactly as before.
 */
(function (root) {
  'use strict';

  // Whether the profanity censor is armed, and which mode (bleep vs mute). The
  // pipeline implements both; a single checkbox arms it and the mode select
  // chooses. Read off `doc` so the module stays self-contained.
  function censorProfanity(doc) {
    var el = doc.getElementById('censor-profanity');
    return !!(el && el.checked);
  }

  function censorMode(doc) {
    var el = doc.getElementById('censor-mode');
    return (el && el.value) || 'bleep';
  }

  // Build the /process body. opts: { doc, selectedVideo, captionOptions }.
  function build(opts) {
    opts = opts || {};
    var doc = opts.doc;
    var selectedVideo = opts.selectedVideo;
    var captionOpts = opts.captionOptions || {};
    if (!doc) return null;

    var aspectEl = doc.getElementById('clip-aspect-ratio');
    var armed = censorProfanity(doc);
    var mode = censorMode(doc);

    var musicPathEl = doc.getElementById('music-path');
    var musicVolEl = doc.getElementById('music-volume');

    return {
      video_path: selectedVideo,
      audio_track_gains: (doc.getElementById("export-audio-gains")?.value || "").trim() ? doc.getElementById("export-audio-gains").value.split(",").map(Number) : [],
      visual_review: !!doc.getElementById("visual-review")?.checked,
      analysis_audio_tracks: (doc.getElementById("analysis-audio-tracks")?.value || "same").replace(/\s/g, "").toLowerCase(),
      audio_tracks: (doc.getElementById("source-audio-tracks")?.value || "default").replace(/\s/g, "").toLowerCase(),
      vertical_crop: doc.getElementById('vertical-crop').checked,
      aspect_ratio: aspectEl ? aspectEl.value : '9:16',
      caption_style: captionOpts.caption_style,
      font_size: captionOpts.font_size,
      font_name: captionOpts.font_name,
      primary_color: captionOpts.primary_color,
      highlight_color: captionOpts.highlight_color,
      outline_color: captionOpts.outline_color,
      outline_width: captionOpts.outline_width,
      position: captionOpts.position,
      chunk_size: captionOpts.chunk_size,
      uppercase: captionOpts.uppercase,
      bold: captionOpts.bold,
      italic: captionOpts.italic,
      intro_caption: captionOpts.intro_caption,
      intro_caption_duration: captionOpts.intro_caption_duration,
      intro_enabled: captionOpts.intro_enabled,
      intro_font_size: captionOpts.intro_font_size,
      intro_style: captionOpts.intro_style,
      auto_clip_count: !!doc.getElementById('auto-clip-count')?.checked,
      include_unreviewed_action: !!doc.getElementById('include-unreviewed-action')?.checked,
      max_clips: parseInt(doc.getElementById('max-clips').value, 10) || 5,
      min_duration: parseFloat(doc.getElementById('min-duration').value) || 20,
      max_duration: parseFloat(doc.getElementById('max-duration').value) || 60,
      whisper_model: doc.getElementById('whisper-model').value,
      use_audio_energy: doc.getElementById('audio-energy').checked,
      use_llm: doc.getElementById('use-llm').checked,
      speaker_aware_selection: doc.getElementById('speaker-aware-selection')
        ? doc.getElementById('speaker-aware-selection').checked : false,
      speaker_aware_crop: doc.getElementById('speaker-aware-crop')
        ? doc.getElementById('speaker-aware-crop').checked : false,
      burn_captions: doc.getElementById('burn-captions').checked,
      remove_silence: doc.getElementById('remove-silence')
        ? doc.getElementById('remove-silence').checked : false,
      // One checkbox arms censoring; the mode select picks bleep vs mute.
      bleep_profanity: armed && mode === 'bleep',
      mute_profanity: armed && mode === 'mute',
      normalize_audio: doc.getElementById('normalize-audio')
        ? doc.getElementById('normalize-audio').checked : false,
      auto_zoom: doc.getElementById('auto-zoom')
        ? doc.getElementById('auto-zoom').checked : false,
      music_path: (musicPathEl && musicPathEl.value) || null,
      music_volume: parseFloat(musicVolEl ? musicVolEl.value : '0.12') || 0.12,
      duck_music: doc.getElementById('duck-music')
        ? doc.getElementById('duck-music').checked : true,
    };
  }

  var api = {
    build: build,
    censorProfanity: censorProfanity,
    censorMode: censorMode,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.KlipzyProcessPayload = api;
})(typeof window !== 'undefined' ? window : this);
