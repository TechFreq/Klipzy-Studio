/*
 * Every export path: NLE timelines, standalone audio/subtitles, single-clip + reel media, and the multi-aspect pack.
 *
 * Split out of renderer.js, which had grown past 5,800 lines and made bugs easy
 * to hide. This is a CLASSIC script (not an ES module), loaded after
 * renderer.js in index.html, so it shares one global scope with it: top-level
 * functions and state declared there are available here and vice versa. Nothing
 * here runs work at load time beyond registering listeners, so load order only
 * needs renderer.js to come first.
 */

// ------------------------------------------------------------------
// NLE Project Export (Premiere Pro, DaVinci Resolve, CapCut)
// ------------------------------------------------------------------
async function exportProject(format) {
  if (!selectedVideo || !generatedClips.length) {
    playErrorSound();
    showAlert("Please generate clips first before exporting a project timeline.");
    return;
  }

  const exportFolder = await chooseExportFolder();
  if (!exportFolder) return;
  try {
    const res = await fetch(`${serverUrl}/export/project`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_path: selectedVideo,
        clips: generatedClips,
        format: format,
        fps: 30.0,
        output_dir: exportFolder,
      })
    });
    const data = await res.json();
    if (res.ok) {
      playSuccessSound();
      showAlert(`✅ ${data.message}\nSaved at: ${data.export_path}`, 'Export Complete', data.export_path || null);
    } else {
      playErrorSound();
      showAlert(`Export failed: ${data.detail}`);
    }
  } catch (err) {
    playErrorSound();
    showAlert(`Export error: ${err.message}`);
  }
}

document.getElementById('export-premiere')?.addEventListener('click', () => exportProject('fcpxml'));
document.getElementById('export-davinci')?.addEventListener('click', () => exportProject('edl'));
document.getElementById('export-capcut')?.addEventListener('click', () => exportProject('capcut'));

// ------------------------------------------------------------------
// Export Standalone Assets (Audio Only MP3/WAV/FLAC/AAC/M4A, Subtitles SRT/VTT)
// ------------------------------------------------------------------
async function exportStandaloneAsset(ev) {
  if (!selectedVideo) {
    playErrorSound();
    showAlert("Please select and load a video file first.");
    return;
  }
  // The clicked button points at its own <select> via data-select (audio vs
  // captions), so one handler drives both grouped export boxes.
  const btn = ev?.currentTarget || document.getElementById('export-audio-btn');
  const selectId = btn?.dataset?.select || 'standalone-audio';
  const sel = document.getElementById(selectId);
  const assetType = sel ? sel.value : 'audio_mp3';
  // Let the user choose the destination folder, matching the per-clip Export.
  const exportFolder = await chooseExportFolder();
  if (!exportFolder) return;
  const originalLabel = btn ? btn.textContent : '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = '⏳ Exporting…';
  }

  try {
    const res = await fetch(`${serverUrl}/export/standalone`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_path: selectedVideo,
        asset_type: assetType,
        output_dir: exportFolder,
      })
    });
    const data = await res.json();
    if (res.ok) {
      playSuccessSound();
      showAlert(`✅ ${data.message}\nSaved at: ${data.export_path}`, 'Export Complete', data.export_path || null);
    } else {
      playErrorSound();
      showAlert(`Standalone export failed: ${data.detail}`);
    }
  } catch (err) {
    playErrorSound();
    showAlert(`Export error: ${err.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = originalLabel || '💾 Export';
    }
  }
}

document.getElementById('export-audio-btn')?.addEventListener('click', exportStandaloneAsset);
document.getElementById('export-subs-btn')?.addEventListener('click', exportStandaloneAsset);

// ------------------------------------------------------------------
// Export-As Media (single clip -> mp4/mov/mkv/webm/gif)
// ------------------------------------------------------------------
function safeFileName(value) {
  return String(value || 'clip').replace(/[^a-z0-9 _-]/gi, '').trim().replace(/\s+/g, '_').slice(0, 70) || 'clip';
}

async function chooseExportFolder(clip) {
  const configured = document.getElementById('output-folder-input')?.value?.trim();
  // Always let the user choose where THIS export lands, starting from the
  // configured output folder (if any). Previously a configured folder was used
  // silently and the picker never opened.
  if (window.clipperAPI?.selectOutputFolder) {
    const picked = await window.clipperAPI.selectOutputFolder(configured || undefined);
    return picked || null;   // null → user cancelled, so the export is aborted
  }
  // Non-Electron fallback (window.prompt is unavailable in Electron).
  const fallback = window.prompt?.('Choose a folder for this exported clip bundle:', configured || '');
  return fallback?.trim() || configured || null;
}

window.exportSingleClip = async function (clipIndex) {
  const clip = generatedClips[clipIndex];
  if (!clip || !clip.output_file) {
    playErrorSound();
    showAlert('No rendered clip to export yet.');
    return;
  }
  const sel = document.querySelector(`.clip-export-fmt[data-clip-idx="${clipIndex}"]`);
  const fmt = sel ? sel.value : 'mp4';
  const exportFolder = await chooseExportFolder(clip);
  if (!exportFolder) return;
  const btn = document.querySelector(`.btn-export[data-clip-idx="${clipIndex}"]`);
  if (btn) {
    btn.disabled = true;
    btn.textContent = '⏳ Exporting…';
  }
  try {
    const res = await fetch(`${serverUrl}/export/clip-bundle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_path: clip.output_file,
        output_dir: exportFolder,
        title: clip.title || clip.hook_text || 'clip',
        format: fmt,
        srt_path: clip.srt_path,
        ass_path: clip.ass_path,
      })
    });
    const data = await res.json();
    if (res.ok) {
      playSuccessSound();
      // /export/clip-bundle returns export_dir + video_path (not export_path):
      // reading export_path here is what produced the "Saved at: undefined" bug.
      const savedPath = data.export_dir || data.video_path || '';
      // Auto-open the destination the user picked, then show the confirmation
      // (which also keeps an "Open folder" button for reopening later).
      if (savedPath) revealInFolder(savedPath);
      showAlert(`✅ ${data.message}\nSaved at: ${savedPath}`, 'Export Complete', savedPath || null);
    } else {
      playErrorSound();
      showAlert(`Export failed: ${data.detail}`);
    }
  } catch (err) {
    playErrorSound();
    showAlert(`Export error: ${err.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = '🚀 Export';
    }
  }
};

// ------------------------------------------------------------------
// Export-All-as-Reel (compile all clips into one media file)
// ------------------------------------------------------------------
async function exportCompileReel() {
  if (!generatedClips.length) {
    playErrorSound();
    showAlert('Please generate clips first before compiling a reel.');
    return;
  }
  const fmt = document.getElementById('compile-format') ? document.getElementById('compile-format').value : 'mp4';
  // Let the user choose where the reel lands, just like the per-clip Export.
  const exportFolder = await chooseExportFolder();
  if (!exportFolder) return;
  const btn = document.getElementById('export-compile');
  if (btn) {
    btn.disabled = true;
    btn.textContent = '⏳ Compiling reel…';
  }
  try {
    const clipPaths = generatedClips.map((c) => c.output_file).filter(Boolean);
    const res = await fetch(`${serverUrl}/export/compile`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        clip_paths: clipPaths,
        format: fmt,
        title: 'highlights_reel',
        output_dir: exportFolder,
      })
    });
    const data = await res.json();
    if (res.ok) {
      playSuccessSound();
      showAlert(`✅ ${data.message}\nSaved at: ${data.export_path}`, 'Export Complete', data.export_path || null);
    } else {
      playErrorSound();
      showAlert(`Compile failed: ${data.detail}`);
    }
  } catch (err) {
    playErrorSound();
    showAlert(`Compile error: ${err.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = '🎬 Export All as Reel';
    }
  }
}
// ------------------------------------------------------------------
// Multi-Aspect Export Pack (9:16, 1:1, 4:5, 16:9)
// ------------------------------------------------------------------
// Export one clip in a specific platform's preferred aspect ratio. Reuses the
// proven multi-aspect endpoint with a single ratio so we don't duplicate render
// logic. 9:16 platforms reuse the clip as-is; feed/landscape get a re-render.
async function exportForPlatform(idx, platform, ratio, btnEl) {
  const clip = generatedClips[idx];
  if (!clip) return;
  // Let the user choose where this platform export lands (matches the per-clip
  // Export and the other export buttons).
  const exportFolder = await chooseExportFolder();
  if (!exportFolder) return;
  const original = btnEl ? btnEl.textContent : '';
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = '⏳'; }
  showToast(`⏳ Exporting for ${platform} (${ratio})…`, 'info');
  try {
    const res = await fetch(`${serverUrl}/export/multi-aspect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        clip_path: clip.output_file,
        source_video: selectedVideo,
        start_seconds: clip.start_time,
        end_seconds: clip.end_time,
        title: `${clip.title || 'clip'} [${platform}]`,
        burn_captions: true,
        subtitle_path: clip.ass_path || clip.srt_path,
        aspect_ratios: [ratio],
        output_dir: exportFolder,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Server returned ${res.status}`);
    playSuccessSound();
    const out = data.exports ? Object.values(data.exports)[0] : null;
    showToast(`✅ ${platform} export ready`, 'success');
    if (out) revealInFolder(out);
  } catch (err) {
    playErrorSound();
    showAlert(`${platform} export failed: ${err.message}`);
  } finally {
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = original; }
  }
}

// Quick per-clip hook swap from the card: rotate to a fresh suggested hook and
// re-render just this clip (burns the new top hook in place).
async function quickRerollHook(idx, btn) {
  const clip = generatedClips[idx];
  if (!clip || !clip.output_file) { showAlert('No rendered clip to update yet.'); return; }
  // Build the richest transcript context we have for this clip: prefer the full
  // clip transcript, then the clip's own word list (present even on older clips),
  // falling back to the hook line. Using ONLY hook_text returned a single
  // candidate, so "New Hook" kept re-picking the exact same line — which is why
  // the text never appeared to change.
  const clipText = clip.full_text || clip.reason
    || (Array.isArray(clip.words) && clip.words.length ? clip.words.map((w) => w.word).join(' ') : '')
    || clip.hook_text || '';
  // Fetch + cache AI hook options per clip (grounded in that transcript), then
  // rotate on each click. rewrite-hook returns several DISTINCT viral hooks and
  // falls back to offline suggestions server-side when Ollama isn't running.
  if (!Array.isArray(clip._hookCandidates) || !clip._hookCandidates.length) {
    try {
      const res = await fetch(`${serverUrl}/tools/rewrite-hook`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: clipText,
          words: clip.words || [],
          current_hook: clip.hook_text || '',
          count: 6,
          preset: document.getElementById('generated-caption-preset')?.value || '',
        }),
      });
      const data = await res.json();
      clip._hookCandidates = (data.hooks || []).filter(Boolean);
      clip._hookIdx = -1;
    } catch (_) { clip._hookCandidates = []; }
  }
  if (!clip._hookCandidates.length) { showToast('No alternative hooks found for this clip', 'info'); return; }
  // Rotate to the next candidate; if it matches the current hook, skip once so
  // the burned text visibly changes.
  clip._hookIdx = ((clip._hookIdx == null ? -1 : clip._hookIdx) + 1) % clip._hookCandidates.length;
  let newHook = clip._hookCandidates[clip._hookIdx];
  if (clip._hookCandidates.length > 1 && newHook.trim() === (clip.hook_text || '').trim()) {
    clip._hookIdx = (clip._hookIdx + 1) % clip._hookCandidates.length;
    newHook = clip._hookCandidates[clip._hookIdx];
  }

  setBtnBusy(btn, '⏳ Re-rendering…');
  // Re-rendering burns captions with ffmpeg, so it can run a while on long
  // clips — say so, otherwise the button just looks hung.
  showToast('⏳ Re-rendering this clip with the new hook…', 'info');

  const words = (clip.words && clip.words.length)
    ? clip.words
    : (clip.hook_text || '').split(' ').map((w, i) => ({ word: w, start: i * 0.4, end: (i + 1) * 0.4 }));
  const captionOpts = collectCaptionOptions();
  const outputPath = clip.ass_path ? clip.ass_path.replace(/\.ass$/i, '.srt') : `${clip.output_file}.srt`;
  try {
    const res = await fetch(`${serverUrl}/export/subtitles`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        output_path: outputPath,
        words,
        style_preset: captionOpts.style_preset,
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
        source_video: selectedVideo,
        clip_output_file: clip.output_file,
        start_seconds: clip.start_time,
        end_seconds: clip.end_time,
        aspect_ratio: document.getElementById('clip-aspect-ratio')?.value || '9:16',
        intro_caption: newHook,
        intro_enabled: true,
        intro_caption_duration: parseFloat(document.getElementById('caption-intro-duration')?.value || '3') || 3,
        intro_font_size: clip.intro_font_size || captionOpts.intro_font_size,
        re_render: true,
      }),
    });
    const data = await res.json();
    if (res.ok) {
      clip.intro_caption = newHook;
      clip.hook_text = newHook;
      if (data.export_path) clip.srt_path = data.export_path;
      const card = document.querySelector(`.clip-card[data-clip-idx="${idx}"]`);
      const vid = card && card.querySelector('video');
      if (vid) { vid.src = fileUrl(clip.output_file, true); vid.load(); }
      const descEl = card && card.querySelector('.clip-desc');
      if (descEl) descEl.textContent = newHook;
      saveCurrentProjectSilently();
      playSuccessSound();
      showToast(`🎣 New hook: "${newHook}"`, 'success');
    } else {
      showAlert(`Could not update hook: ${data.detail || 'error'}`);
    }
  } catch (e) {
    showAlert(`Error: ${e.message}`);
  } finally {
    setBtnIdle(btn);
  }
}

// Remove the burned-in intro hook from a clip and re-render it. Mirrors
// quickRerollHook but disables the intro so no hook is drawn on the video.
async function quickRemoveHook(idx, btn) {
  const clip = generatedClips[idx];
  if (!clip || !clip.output_file) { showAlert('No rendered clip to update yet.'); return; }
  if (!clip.intro_caption && !clip.hook_text) {
    showToast('This clip has no intro hook to remove', 'info');
    return;
  }
  setBtnBusy(btn, '⏳ Re-rendering…');
  showToast('⏳ Re-rendering this clip without the hook…', 'info');

  const words = (clip.words && clip.words.length)
    ? clip.words
    : (clip.hook_text || '').split(' ').map((w, i) => ({ word: w, start: i * 0.4, end: (i + 1) * 0.4 }));
  const captionOpts = collectCaptionOptions();
  const outputPath = clip.ass_path ? clip.ass_path.replace(/\.ass$/i, '.srt') : `${clip.output_file}.srt`;
  try {
    const res = await fetch(`${serverUrl}/export/subtitles`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        output_path: outputPath,
        words,
        style_preset: captionOpts.style_preset,
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
        source_video: selectedVideo,
        clip_output_file: clip.output_file,
        start_seconds: clip.start_time,
        end_seconds: clip.end_time,
        aspect_ratio: document.getElementById('clip-aspect-ratio')?.value || '9:16',
        intro_caption: '',
        intro_enabled: false,
        re_render: true,
      }),
    });
    const data = await res.json();
    if (res.ok) {
      clip.intro_caption = '';
      if (data.export_path) clip.srt_path = data.export_path;
      const card = document.querySelector(`.clip-card[data-clip-idx="${idx}"]`);
      const vid = card && card.querySelector('video');
      if (vid) { vid.src = fileUrl(clip.output_file, true); vid.load(); }
      saveCurrentProjectSilently();
      playSuccessSound();
      showToast('🚫 Intro hook removed', 'success');
    } else {
      showAlert(`Could not remove hook: ${data.detail || 'error'}`);
    }
  } catch (e) {
    showAlert(`Error: ${e.message}`);
  } finally {
    setBtnIdle(btn);
  }
}

// #13 Filler-word + dead-air removal for a clip (uses its word timestamps).
async function quickRemoveFillers(idx, btn) {
  const clip = generatedClips[idx];
  if (!clip || !clip.output_file) { showAlert('No rendered clip yet.'); return; }
  setBtnBusy(btn, '⏳ Cutting…');
  try {
    const aggressive = !!document.getElementById('filler-aggressive')?.checked;
    const res = await fetch(`${serverUrl}/tools/remove-fillers`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_path: clip.output_file,
        // Rebase to the clip's 0-based timeline so filler cuts line up with the
        // rendered clip (clip.words carry absolute source-video times).
        words: wordsClipRelative(clip.words || [], clip),
        also_remove_silence: true,
        remove_phrases: aggressive,  // conservative (disfluencies only) unless the toggle is on
      }),
    });
    const data = await res.json();
    if (res.ok) {
      clip.output_file = data.output_path;
      if (typeof data.cut_duration === 'number') clip.duration = data.cut_duration;
      const card = document.querySelector(`.clip-card[data-clip-idx="${idx}"]`);
      const vid = card && card.querySelector('video');
      if (vid) { vid.src = fileUrl(clip.output_file, true); vid.load(); }
      saveCurrentProjectSilently();
      playSuccessSound();
      showToast(`🧹 ${data.message || 'Fillers removed'}`, 'success');
    } else {
      showAlert(`Filler removal failed: ${data.detail || 'error'}`);
    }
  } catch (e) {
    showAlert(`Error: ${e.message}`);
  } finally {
    setBtnIdle(btn);
  }
}

// #14 Translate a clip's captions into another language (local Ollama).
let translateClip = null;
async function openTranslateModal(idx) {
  const clip = generatedClips[idx];
  if (!clip) return;
  if (!clip.srt_path) { showAlert('No subtitle file for this clip yet — generate captions first.'); return; }
  translateClip = clip;
  const sel = document.getElementById('translate-lang');
  if (sel && sel.dataset.loaded !== '1') {
    try {
      const r = await fetch(`${serverUrl}/tools/languages`);
      const d = await r.json();
      sel.innerHTML = (d.languages || []).map((l) => `<option value="${escapeHtml(l)}">${escapeHtml(l)}</option>`).join('');
      sel.dataset.loaded = '1';
    } catch (_) { sel.innerHTML = '<option value="Spanish">Spanish</option>'; }
  }
  document.getElementById('translate-modal')?.classList.remove('hidden');
}
document.getElementById('translate-close')?.addEventListener('click', () => document.getElementById('translate-modal')?.classList.add('hidden'));
document.getElementById('translate-cancel')?.addEventListener('click', () => document.getElementById('translate-modal')?.classList.add('hidden'));
document.getElementById('translate-go')?.addEventListener('click', async () => {
  if (!translateClip) return;
  const custom = (document.getElementById('translate-lang-custom')?.value || '').trim();
  const lang = custom || document.getElementById('translate-lang')?.value || '';
  if (!lang) { showToast('Pick or type a language', 'info'); return; }
  const burn = !!document.getElementById('translate-burn')?.checked;
  const btn = document.getElementById('translate-go');
  setBtnBusy(btn, burn ? '⏳ Translating + rendering…' : '⏳ Translating…');
  try {
    const body = { srt_path: translateClip.srt_path, target_lang: lang };
    if (burn) {
      body.burn = true;
      body.source_video = selectedVideo;
      body.start_seconds = translateClip.start_time;
      body.end_seconds = translateClip.end_time;
      body.aspect_ratio = document.getElementById('clip-aspect-ratio')?.value || '9:16';
      body.style_preset = document.getElementById('generated-caption-preset')?.value || 'viral_yellow';
      body.font_size = parseInt(document.getElementById('generated-caption-font-size')?.value || '', 10) || undefined;
    }
    const res = await fetch(`${serverUrl}/tools/translate-captions`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (res.ok) {
      document.getElementById('translate-modal')?.classList.add('hidden');
      playSuccessSound();
      let msg = `✅ Translated ${data.translated} caption lines to ${lang}.\nSaved:\n• ${data.srt}\n• ${data.vtt}`;
      const reveal = data.burned_video || data.srt || null;
      if (data.burned_video) msg += `\n\n🎬 Burned video:\n• ${data.burned_video}`;
      else if (data.burn_error) msg += `\n\n⚠️ Couldn't burn the video: ${data.burn_error}`;
      if (reveal) revealInFolder(reveal);
      showAlert(msg, 'Translation Complete', reveal);
    } else {
      showAlert(`Translation failed: ${data.detail || 'error'}`);
    }
  } catch (e) {
    showAlert(`Error: ${e.message}`);
  } finally {
    setBtnIdle(btn);
  }
});

// #16 Speaker diarization (optional — needs pyannote + HF token).
// Detects who spoke when, then rewrites the clip's captions with friendly
// "Speaker 1:" / "Speaker 2:" labels (whoever talks first = Speaker 1) and can
// re-render the clip to burn the labels in. Degrades gracefully when pyannote
// isn't installed.
async function detectSpeakers(idx, btn) {
  const clip = generatedClips[idx];
  if (!clip || !clip.output_file) return;

  const words = (clip.words && clip.words.length) ? clip.words : null;
  if (!words) {
    showAlert('This clip has no word timestamps to label. Re-transcribe it first.', 'Speakers');
    return;
  }

  const burn = await showConfirm(
    'Detect speakers and add "Speaker 1:" / "Speaker 2:" labels to this clip\'s captions?\n\n' +
    'Choose OK to also re-render the clip and burn the labels in, or Cancel to just write the caption files (.srt/.ass).',
    'Speaker-labeled captions',
  );

  setBtnBusy(btn, '⏳ Analyzing…');
  try {
    const captionOpts = collectCaptionOptions();
    const outputPath = clip.ass_path ? clip.ass_path.replace(/\.ass$/i, '.srt') : `${clip.output_file}.srt`;
    const res = await fetch(`${serverUrl}/tools/speaker-captions`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        output_path: outputPath,
        words,
        style_preset: captionOpts.style_preset,
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
        source_video: selectedVideo,
        clip_output_file: clip.output_file,
        start_seconds: clip.start_time,
        end_seconds: clip.end_time,
        aspect_ratio: document.getElementById('clip-aspect-ratio')?.value || clip.aspect_ratio || '9:16',
        layout: clip.layout || '',
        cam_video: clip.cam_video,
        cam_scale: clip.cam_scale,
        cam_position: clip.cam_position,
        crop_x_offset: clip.crop_x_offset,
        re_render: !!burn,
      }),
    });
    const d = await res.json();
    if (d.available) {
      if (d.srt_path) clip.srt_path = d.srt_path;
      if (d.ass_path) clip.ass_path = d.ass_path;
      if (d.re_rendered) {
        const card = document.querySelector(`.clip-card[data-clip-idx="${idx}"]`);
        const vid = card && card.querySelector('video');
        if (vid) { vid.src = fileUrl(clip.output_file, true); vid.load(); }
      }
      const who = (d.speakers || []).join(', ');
      showToast(`🗣 ${d.message}`, 'success');
      showAlert(`🗣 ${d.message}${who ? `\n\nSpeakers: ${who}` : ''}`, 'Speaker-labeled captions');
    } else {
      showAlert(`Speaker detection is optional and not enabled yet.\n\n${d.message}\n\nTo enable, open Setup → Optional AI add-ons, install pyannote.audio, and set a Hugging Face token.`, 'Speaker Diarization (optional)');
    }
  } catch (e) {
    showAlert(`Error: ${e.message}`);
  } finally {
    setBtnIdle(btn);
  }
}

const MULTI_ASPECT_RATIOS = [
  { ratio: '9:16', label: 'Vertical 9:16', tag: 'TikTok / Reels / Shorts' },
  { ratio: '1:1', label: 'Square 1:1', tag: 'Feed' },
  { ratio: '4:5', label: 'Portrait 4:5', tag: 'IG feed' },
  { ratio: '16:9', label: 'Landscape 16:9', tag: 'YouTube / X' },
];
let multiAspectClip = null;

// Opens a preview-before-export modal (OpenClipper-style): shows the clip framed
// in each aspect ratio, lets the user pick which to render, then choose a folder.
// Active-speaker crop offsets computed by the preview, reused on export so the
// rendered file matches exactly what the preview showed.
let maCropOffsets = {};

function exportMultiAspectPack(idx) {
  const clip = generatedClips[idx];
  if (!clip || !clip.output_file) {
    showAlert('No rendered clip to export yet.');
    return;
  }
  multiAspectClip = clip;
  maCropOffsets = {};
  const wrap = document.getElementById('multi-aspect-previews');
  if (wrap) {
    const fallbackSrc = fileUrl(clip.output_file);
    wrap.innerHTML = MULTI_ASPECT_RATIOS.map(({ ratio, label, tag }) => `
      <div class="ma-card">
        <label class="ma-card-head">
          <input type="checkbox" class="ma-check" value="${ratio}" checked />
          <span class="ma-label">${label}</span>
        </label>
        <div class="ma-frame" style="aspect-ratio:${ratio.replace(':', ' / ')}">
          <div class="ma-loading" data-ratio="${ratio}">⏳</div>
          <img class="ma-img" data-ratio="${ratio}" alt="${label} preview" hidden />
          <video class="ma-fallback" data-ratio="${ratio}" src="${escapeHtml(fallbackSrc)}" muted playsinline preload="metadata" hidden></video>
        </div>
        <span class="muted small">${tag}</span>
        <button class="btn btn-small btn-secondary ma-export-one" data-ratio="${ratio}">⬇️ Export ${ratio}</button>
      </div>`).join('');
    // Individual per-aspect export (OpenClipper-style): export just this ratio.
    wrap.querySelectorAll('.ma-export-one').forEach((b) => {
      b.addEventListener('click', () => runMultiAspectExport([b.dataset.ratio], b));
    });
    // Lazily render a REAL cropped still per ratio (active-speaker framing).
    MULTI_ASPECT_RATIOS.forEach(({ ratio }) => loadAspectPreview(clip, ratio));
  }
  document.getElementById('multi-aspect-modal')?.classList.remove('hidden');
}

// Fetch a real cropped preview frame for one ratio and swap it in. Stores the
// computed crop offset so the export reuses the exact same framing. Falls back
// to the plain CSS-cover video if the still can't be rendered.
async function loadAspectPreview(clip, ratio) {
  const wrap = document.getElementById('multi-aspect-previews');
  if (!wrap) return;
  const loadingEl = wrap.querySelector(`.ma-loading[data-ratio="${ratio}"]`);
  const imgEl = wrap.querySelector(`.ma-img[data-ratio="${ratio}"]`);
  const fbEl = wrap.querySelector(`.ma-fallback[data-ratio="${ratio}"]`);
  try {
    const res = await fetch(`${serverUrl}/export/aspect-preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        clip_path: clip.output_file,
        source_video: selectedVideo,
        start_seconds: clip.start_time,
        end_seconds: clip.end_time,
        aspect_ratio: ratio,
      }),
    });
    const data = await res.json();
    if (!res.ok || !data.image_path) throw new Error(data.detail || 'preview failed');
    maCropOffsets[ratio] = (data.crop_x_offset ?? null);
    if (imgEl) {
      // The still can come back as a valid path that the renderer still can't
      // load (write race / file access), which showed as a broken-image icon.
      // Swap to the video fallback on a load error instead of leaving it broken.
      imgEl.addEventListener('error', () => {
        imgEl.hidden = true;
        if (fbEl) fbEl.hidden = false;
      }, { once: true });
      imgEl.addEventListener('load', () => { imgEl.hidden = false; }, { once: true });
      imgEl.src = fileUrl(data.image_path, true);
    }
  } catch (_) {
    if (fbEl) fbEl.hidden = false;  // graceful fallback to the CSS-cover video
  } finally {
    if (loadingEl) loadingEl.remove();
  }
}

document.getElementById('multi-aspect-close')?.addEventListener('click', () => {
  document.getElementById('multi-aspect-modal')?.classList.add('hidden');
});
document.getElementById('multi-aspect-cancel')?.addEventListener('click', () => {
  document.getElementById('multi-aspect-modal')?.classList.add('hidden');
});

document.getElementById('multi-aspect-export')?.addEventListener('click', () => {
  const ratios = Array.from(document.querySelectorAll('#multi-aspect-previews .ma-check:checked')).map((c) => c.value);
  if (!ratios.length) {
    showToast('Pick at least one aspect ratio', 'info');
    return;
  }
  runMultiAspectExport(ratios, document.getElementById('multi-aspect-export'));
});

// Render the given aspect ratios for the current clip into a chosen folder,
// then reveal it. Shared by "Export selected" and the per-aspect buttons.
async function runMultiAspectExport(ratios, btn) {
  if (!multiAspectClip || !ratios || !ratios.length) return;
  const exportFolder = await chooseExportFolder(multiAspectClip);
  if (!exportFolder) return;   // cancelled the folder picker
  setBtnBusy(btn, '⏳ Rendering…');
  try {
    const res = await fetch(`${serverUrl}/export/multi-aspect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        clip_path: multiAspectClip.output_file,
        source_video: selectedVideo,
        start_seconds: multiAspectClip.start_time,
        end_seconds: multiAspectClip.end_time,
        title: multiAspectClip.title,
        burn_captions: true,
        subtitle_path: multiAspectClip.ass_path || multiAspectClip.srt_path,
        aspect_ratios: ratios,
        output_dir: exportFolder,
        crop_offsets: maCropOffsets,
      })
    });
    const data = await res.json();
    if (res.ok) {
      playSuccessSound();
      const firstOut = data.exports ? Object.values(data.exports)[0] : null;
      if (firstOut) revealInFolder(firstOut);
      showAlert(`✅ Exported:\n${Object.entries(data.exports).map(([k, v]) => `• ${k}: ${v}`).join('\n')}`, 'Export Complete', firstOut || null);
    } else {
      playErrorSound();
      showAlert(`Multi-aspect export failed: ${data.detail}`);
    }
  } catch (err) {
    playErrorSound();
    showAlert(`Error: ${err.message}`);
  } finally {
    setBtnIdle(btn);
  }
}
