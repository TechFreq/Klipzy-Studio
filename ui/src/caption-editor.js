/*
 * Caption presets, the live previews (modal + portrait phone + intro hook) and the Interactive Caption Editor modal.
 *
 * Split out of renderer.js, which had grown past 5,800 lines and made bugs easy
 * to hide. This is a CLASSIC script (not an ES module), loaded after
 * renderer.js in index.html, so it shares one global scope with it: top-level
 * functions and state declared there are available here and vice versa. Nothing
 * here runs work at load time beyond registering listeners, so load order only
 * needs renderer.js to come first.
 */

// ------------------------------------------------------------------
// Caption preset preview
// Converts ASS colors (&HB BG R R) to CSS and mirrors the preset's
// primary + highlight so the style bar shows a live sample.
// ------------------------------------------------------------------
const CAPTION_PREVIEW = {
  viral_yellow:  { text: '#ffffff', accent: '#ffd21e', back: '#000000', font: 'Arial Black' },
  neon_green:    { text: '#ffffff', accent: '#1bff3a', back: '#111111', font: 'Impact' },
  bold_white:    { text: '#e0e0e0', accent: '#ffffff', back: '#000000', font: 'Montserrat, Arial' },
  cyberpunk_cyan:{ text: '#64e6ff', accent: '#ff40d0', back: '#050515', font: 'Arial Black' },
  tiktok_pop:    { text: '#ffd21e', accent: '#ff2a3a', back: '#000000', font: 'Arial Black' },
  fire_red:      { text: '#ffffff', accent: '#ff4530', back: '#000000', font: 'Impact' },
  retro_vaporwave:{ text: '#e0b0ff', accent: '#ffff59', back: '#330033', font: 'Trebuchet MS' },
  mrbeast_impact:{ text: '#ffffff', accent: '#ffd21e', back: '#000000', font: 'Impact' },
  pastel_pink:   { text: '#ffffff', accent: '#ffa4d8', back: '#2e1b33', font: 'Arial' },
  minimalist_dark:{ text: '#f0f0f0', accent: '#ff8a4d', back: '#000000', font: 'Helvetica' },
  comic_punch:   { text: '#ffd21e', accent: '#ffffff', back: '#000000', font: 'Impact' },
  golden_hour:   { text: '#fff0e6', accent: '#ffa510', back: '#1a1005', font: 'Arial Black' },
  electric_purple:{ text: '#ffffff', accent: '#b833ff', back: '#1b0324', font: 'Arial Black' },
  sunset_orange: { text: '#ffffff', accent: '#ff7b14', back: '#050905', font: 'Impact' },
  matrix_green:  { text: '#00cc33', accent: '#80ff80', back: '#001500', font: 'Courier New' },
  deep_blue:     { text: '#ffffff', accent: '#33aaff', back: '#051024', font: 'Arial Black' },
  boxed_karaoke: { text: '#dddddd', accent: '#ffe500', back: '#000000', font: 'Arial' },
  glitch_shadow: { text: '#ffffff', accent: '#ff30e0', back: '#000000', font: 'Arial Black' },
  elegant_serif: { text: '#f5f5f5', accent: '#ff6bd3', back: '#1c1c1c', font: 'Georgia' },
  high_contrast: { text: '#000000', accent: '#ffcc00', back: '#000000', font: 'Arial Black' },
  monochrome_chic:{ text: '#888888', accent: '#ffffff', back: '#111111', font: 'Helvetica' },
  gaming_rgb:    { text: '#80ffaa', accent: '#ff20b0', back: '#000000', font: 'Impact' },
};

function captionFontSize() {
  // The generated-clips toolbar is the single source of truth for both the
  // initial render and the caption editor preview.
  const slider = document.getElementById('generated-caption-font-size');
  const n = parseFloat(slider ? slider.value : '40');
  return Number.isFinite(n) ? n : 40;
}

function globalCaptionFontSize() {
  const slider = document.getElementById('generated-caption-font-size');
  const n = parseFloat(slider ? slider.value : '40');
  return Number.isFinite(n) ? n : 40;
}

function collectCaptionOptions() {
  const preset = document.getElementById('generated-caption-preset')?.value || 'viral_yellow';
  const fontSize = globalCaptionFontSize();
  const fontName = document.getElementById('caption-font-name')?.value || null;
  const primaryColor = document.getElementById('caption-primary-color')?.value || null;
  const highlightColor = document.getElementById('caption-highlight-color')?.value || null;
  const outlineColor = document.getElementById('caption-outline-color')?.value || null;
  const outlineWidthVal = document.getElementById('caption-outline-width')?.value;
  const outlineWidth = outlineWidthVal !== undefined && outlineWidthVal !== '' ? parseInt(outlineWidthVal, 10) : null;
  const positionVal = document.getElementById('caption-position')?.value;
  const position = positionVal ? parseInt(positionVal, 10) : null;
  const chunkSizeVal = document.getElementById('caption-chunk-size')?.value;
  const chunkSize = chunkSizeVal && chunkSizeVal !== '0' ? parseInt(chunkSizeVal, 10) : null;
  const uppercase = document.getElementById('caption-uppercase')?.checked || false;
  const bold = document.getElementById('caption-bold')?.checked || false;
  const italic = document.getElementById('caption-italic')?.checked || false;
  const introEnabled = document.getElementById('caption-intro-enabled')?.checked || false;
  const introCaption = introEnabled ? (document.getElementById('caption-intro-text')?.value || '').trim() : null;
  const introCaptionDuration = parseFloat(document.getElementById('caption-intro-duration')?.value || '3');

  return {
    caption_style: preset,
    style_preset: preset,
    font_size: fontSize,
    font_name: fontName || undefined,
    primary_color: primaryColor || undefined,
    highlight_color: highlightColor || undefined,
    outline_color: outlineColor || undefined,
    outline_width: Number.isFinite(outlineWidth) ? outlineWidth : undefined,
    position: Number.isFinite(position) ? position : undefined,
    chunk_size: Number.isFinite(chunkSize) ? chunkSize : undefined,
    uppercase,
    bold,
    italic,
    intro_caption: introCaption || undefined,
    intro_caption_duration: Number.isFinite(introCaptionDuration) ? introCaptionDuration : 3,
    // Flag so the backend auto-generates a hook per clip when the toggle is on
    // but the optional custom text is left blank.
    intro_enabled: introEnabled,
    intro_style: collectIntroStyle(),
    // Optional bigger font for the intro hook (from the caption editor's slider).
    intro_font_size: parseInt(document.getElementById('generated-intro-font-size')?.value || '', 10) || undefined,
  };
}

// Live preview of the intro hook at the top of the caption stage, styled with
// the current preset/colors and sized by the hook font-size slider.
function updateHookPreview() {
  const el = document.getElementById('hook-preview');
  if (!el) return;
  const raw = (document.getElementById('edit-intro-hook')?.value || '').trim();
  if (!raw) { el.classList.add('hidden'); el.textContent = ''; return; }
  el.classList.remove('hidden');
  const isUpper = document.getElementById('caption-uppercase')?.checked;
  el.textContent = isUpper ? raw.toUpperCase() : raw;

  const presetId = document.getElementById('generated-caption-preset')?.value || 'viral_yellow';
  const base = CAPTION_PREVIEW[presetId] || CAPTION_PREVIEW.viral_yellow;
  const primary = document.getElementById('caption-primary-color')?.value || base.text;
  const accent = document.getElementById('caption-highlight-color')?.value || base.accent;
  const stroke = document.getElementById('caption-outline-color')?.value || base.back;
  const font = document.getElementById('caption-font-name')?.value || base.font;
  const hookSize = parseInt(document.getElementById('intro-hook-font-size')?.value || '64', 10);

  const stage = el.parentElement;
  const ratio = document.getElementById('clip-aspect-ratio')?.value || '9:16';
  const M = KlipzyCaptionPreviewMath;
  const canvasWidth = M.canvasWidthForRatio(ratio);
  const stageStyle = stage ? getComputedStyle(stage) : null;
  const padX = stageStyle
    ? (parseFloat(stageStyle.paddingLeft) || 0) + (parseFloat(stageStyle.paddingRight) || 0)
    : 28;
  const previewWidth = M.usableWidth(stage ? stage.clientWidth : 0, padX, 540);
  const scale = M.scaleFor(previewWidth, canvasWidth);
  // Keep the hook overlay bounded so a long title used as a hook can't flood
  // the top of the stage and reach the caption.
  const stageMinHeight = stageStyle ? (parseFloat(stageStyle.minHeight) || 132) : 132;
  const maxHookFont = Math.max(12, stageMinHeight / 4);

  el.style.color = primary;
  el.style.fontFamily = font;
  el.style.fontSize = `${M.boundedPx(hookSize, scale, maxHookFont, 10)}px`;
  el.style.fontWeight = document.getElementById('caption-bold')?.checked ? '900' : '800';
  const w = parseInt(document.getElementById('caption-outline-width')?.value || '3', 10);
  const o = stroke || '#000000';
  el.style.textShadow = w > 0
    ? `${w}px 0 0 ${o}, -${w}px 0 0 ${o}, 0 ${w}px 0 ${o}, 0 -${w}px 0 ${o}, 0 0 14px ${accent}55`
    : `0 0 14px ${accent}55`;
}

function applyCaptionPreviewStyle() {
  const preview = document.getElementById('caption-preview');
  if (!preview) return;
  const presetId = document.getElementById('generated-caption-preset')?.value || 'viral_yellow';
  const baseStyle = CAPTION_PREVIEW[presetId] || CAPTION_PREVIEW.viral_yellow;

  const fontName = document.getElementById('caption-font-name')?.value;
  const primaryColor = document.getElementById('caption-primary-color')?.value;
  const highlightColor = document.getElementById('caption-highlight-color')?.value;
  const outlineColor = document.getElementById('caption-outline-color')?.value;
  const outlineWidth = parseInt(document.getElementById('caption-outline-width')?.value || '3', 10);
  const isUppercase = document.getElementById('caption-uppercase')?.checked;
  const isBold = document.getElementById('caption-bold')?.checked;
  const isItalic = document.getElementById('caption-italic')?.checked;

  const font = fontName || baseStyle.font;
  const textColor = primaryColor || baseStyle.text;
  const accentColor = highlightColor || baseStyle.accent;
  const strokeColor = outlineColor || baseStyle.back;

  preview.style.color = textColor;
  preview.style.fontFamily = font;
  const previewStage = preview.parentElement;
  const ratio = document.getElementById('clip-aspect-ratio')?.value || '9:16';
  const M = KlipzyCaptionPreviewMath;
  const canvasWidth = M.canvasWidthForRatio(ratio);
  // Map the caption to the stage's *usable* width (minus horizontal padding) so
  // it lands in the same 1080/1920-px coordinate space the exporter uses.
  const stageStyle = previewStage ? getComputedStyle(previewStage) : null;
  const padX = stageStyle
    ? (parseFloat(stageStyle.paddingLeft) || 0) + (parseFloat(stageStyle.paddingRight) || 0)
    : 28;
  const previewWidth = M.usableWidth(previewStage ? previewStage.clientWidth : 0, padX, 540);
  const previewScale = M.scaleFor(previewWidth, canvasWidth);
  // The stage is a short landscape box, so a purely width-based scale can look
  // oversized. Clamp to a fraction of the stage's *fixed* min-height (not its
  // live height, which would grow as text wraps and defeat the clamp) so the
  // preview reads like one or two caption lines and never balloons.
  const stageMinHeight = stageStyle ? (parseFloat(stageStyle.minHeight) || 90) : 90;
  const padY = stageStyle
    ? (parseFloat(stageStyle.paddingTop) || 0) + (parseFloat(stageStyle.paddingBottom) || 0)
    : 28;
  const maxPreviewFont = Math.max(14, (stageMinHeight - padY) / 1.5);
  preview.style.fontSize = `${M.boundedPx(captionFontSize(), previewScale, maxPreviewFont, 1)}px`;
  preview.style.fontWeight = isBold ? '900' : 'normal';
  preview.style.fontStyle = isItalic ? 'italic' : 'normal';
  preview.style.textTransform = isUppercase ? 'uppercase' : 'none';

  if (outlineWidth > 0) {
    const o = strokeColor || '#000000';
    const w = outlineWidth;
    preview.style.textShadow = `${w}px 0 0 ${o}, -${w}px 0 0 ${o}, 0 ${w}px 0 ${o}, 0 -${w}px 0 ${o}, ${w}px ${w}px 0 ${o}, -${w}px -${w}px 0 ${o}, ${w}px -${w}px 0 ${o}, -${w}px ${w}px 0 ${o}, 0 0 16px ${accentColor}55`;
  } else {
    preview.style.textShadow = `0 0 16px ${accentColor}55`;
  }

  const highlights = preview.querySelectorAll('mark');
  highlights.forEach((m) => {
    m.style.color = accentColor;
    m.style.background = 'transparent';
  });
  applyPortraitCaptionPreviewStyle();
  updateHookPreview();  // keep the hook preview in sync with style changes
}

function refreshCaptionPreview() {
  const preview = document.getElementById('caption-preview');
  if (!preview) return;
  const rawWords = preview.dataset.words || 'Your caption appears here';
  const isUppercase = document.getElementById('caption-uppercase')?.checked;
  const words = isUppercase ? rawWords.toUpperCase() : rawWords;


  // Render words, highlighting every few so the accent shows like a karaoke lead.

  const list = words.split(' ').map((w, i) => (i % 3 === 0 ? `<mark>${escapeHtml(w)}</mark>` : escapeHtml(w)));
  preview.innerHTML = list.join(' ');
  preview.dataset.words = rawWords;
  refreshPortraitCaptionPreview();
  applyCaptionPreviewStyle();
}

function applyPortraitCaptionPreviewStyle() {
  const preview = document.getElementById('portrait-caption-preview');
  if (!preview) return;
  const presetId = document.getElementById('generated-caption-preset')?.value || 'viral_yellow';
  const baseStyle = CAPTION_PREVIEW[presetId] || CAPTION_PREVIEW.viral_yellow;

  const fontName = document.getElementById('caption-font-name')?.value;
  const primaryColor = document.getElementById('caption-primary-color')?.value;
  const highlightColor = document.getElementById('caption-highlight-color')?.value;
  const outlineColor = document.getElementById('caption-outline-color')?.value;
  const outlineWidth = parseInt(document.getElementById('caption-outline-width')?.value || '3', 10);
  const isBold = document.getElementById('caption-bold')?.checked;
  const isItalic = document.getElementById('caption-italic')?.checked;
  const isUppercase = document.getElementById('caption-uppercase')?.checked;

  const font = fontName || baseStyle.font;
  const textColor = primaryColor || baseStyle.text;
  const accentColor = highlightColor || baseStyle.accent;
  const strokeColor = outlineColor || baseStyle.back;

  const positionVal = document.getElementById('caption-position')?.value || '2';
  const alignClass = ['1'].includes(positionVal) ? 'caption-align-left' : (['3'].includes(positionVal) ? 'caption-align-right' : 'caption-align-center');
  const verticalClass = ['8'].includes(positionVal) ? 'caption-pos-top' : (['2', '1', '3'].includes(positionVal) ? 'caption-pos-bottom' : 'caption-pos-middle');
  const screenEl = preview.parentElement;
  if (screenEl) {
    screenEl.classList.remove('caption-align-left', 'caption-align-right', 'caption-align-center');
    screenEl.classList.remove('caption-pos-top', 'caption-pos-bottom', 'caption-pos-middle');
    screenEl.classList.add(alignClass, verticalClass);
  }

  // Calculate proportional font size matching the export canvas (1080-wide for
  // vertical/square, 1920 for 16:9).
  const containerWidth = (screenEl && screenEl.clientWidth > 0) ? screenEl.clientWidth : 200;
  const rawSize = captionFontSize();
  const ratio = document.getElementById('clip-aspect-ratio')?.value || '9:16';
  const M = KlipzyCaptionPreviewMath;
  const portraitScale = M.scaleFor(containerWidth, M.canvasWidthForRatio(ratio));
  const scaledFontSize = M.scaledPx(rawSize, portraitScale, 1);
  const scaledOutline = M.scaledPx(outlineWidth, portraitScale, 0);

  preview.style.color = textColor;
  preview.style.fontFamily = font;
  preview.style.fontSize = `${scaledFontSize}px`;
  preview.style.fontWeight = isBold ? '900' : 'normal';
  preview.style.fontStyle = isItalic ? 'italic' : 'normal';
  preview.style.textTransform = isUppercase ? 'uppercase' : 'none';

  if (outlineWidth > 0) {
    const o = strokeColor || '#000000';
    const w = scaledOutline;
    preview.style.textShadow = `${w}px 0 0 ${o}, -${w}px 0 0 ${o}, 0 ${w}px 0 ${o}, 0 -${w}px 0 ${o}, ${w}px ${w}px 0 ${o}, -${w}px -${w}px 0 ${o}, ${w}px -${w}px 0 ${o}, -${w}px ${w}px 0 ${o}, 0 0 14px ${accentColor}55`;
  } else {
    preview.style.textShadow = `0 0 14px ${accentColor}55`;
  }

  const highlights = preview.querySelectorAll('mark');
  highlights.forEach((m) => {
    m.style.color = accentColor;
    m.style.background = 'transparent';
  });
}


function refreshPortraitCaptionPreview() {
  const preview = document.getElementById('portrait-caption-preview');
  if (!preview) return;
  const rawWords = preview.dataset.words || 'Your caption appears here';
  const isUppercase = document.getElementById('caption-uppercase')?.checked;
  const words = isUppercase ? rawWords.toUpperCase() : rawWords;


  // Mirror the modal preview: highlight every 3rd word with the accent color.
  const list = words.split(' ').map((w, i) => (i % 3 === 0 ? `<mark>${escapeHtml(w)}</mark>` : escapeHtml(w)));
  preview.innerHTML = list.join(' ');
  preview.dataset.words = rawWords;
  applyPortraitCaptionPreviewStyle();
  refreshIntroPreview();
}

// Separate live preview for the INTRO HOOK (top-center, bigger font) shown in
// the same phone frame. Reuses the caption preset + color controls, but with
// its own font size so the user can size the hook independently of the captions.
function refreshIntroPreview() {
  const el = document.getElementById('portrait-intro-preview');
  if (!el) return;
  const enabled = document.getElementById('caption-intro-enabled')?.checked;
  if (!enabled) { el.classList.add('hidden'); return; }
  el.classList.remove('hidden');

  const raw = (document.getElementById('caption-intro-text')?.value || '').trim() || 'Your hook here';
  const isUppercase = document.getElementById('intro-uppercase')?.checked;
  el.textContent = isUppercase ? raw.toUpperCase() : raw;

  const presetId = document.getElementById('intro-preset')?.value || 'viral_yellow';
  const baseStyle = CAPTION_PREVIEW[presetId] || CAPTION_PREVIEW.viral_yellow;
  const fontName = document.getElementById('intro-font-name')?.value || baseStyle.font;
  const accentColor = document.getElementById('intro-color')?.value || baseStyle.accent;
  const strokeColor = document.getElementById('intro-outline-color')?.value || baseStyle.back;
  const outlineWidth = parseInt(document.getElementById('intro-outline-width')?.value || '3', 10);

  const screenEl = el.parentElement;
  const containerWidth = (screenEl && screenEl.clientWidth > 0) ? screenEl.clientWidth : 200;
  const ratio = document.getElementById('clip-aspect-ratio')?.value || '9:16';
  const canvasWidth = ratio === '16:9' ? 1920 : 1080;
  const rawSize = parseInt(document.getElementById('generated-intro-font-size')?.value || '64', 10);
  const scaled = Math.max(1, Math.round(rawSize * (containerWidth / canvasWidth)));
  const w = Math.max(0, Math.round(outlineWidth * (containerWidth / canvasWidth)));

  el.style.color = accentColor;        // hooks pop in the highlight color
  el.style.fontFamily = fontName;
  el.style.fontSize = `${scaled}px`;
  el.style.fontWeight = document.getElementById('intro-bold').checked ? '900' : '400';
  el.style.fontStyle = document.getElementById('intro-italic').checked ? 'italic' : 'normal';
  const position = document.getElementById('intro-position').value;
  el.style.top = position === '8' ? '12%' : position === '5' ? '45%' : '78%';
  el.style.animation = document.getElementById('intro-animation').value === 'none' ? 'none' : 'headline-' + document.getElementById('intro-animation').value + ' 1.5s infinite';
  el.style.textShadow = w > 0
    ? `${w}px 0 0 ${strokeColor}, -${w}px 0 0 ${strokeColor}, 0 ${w}px 0 ${strokeColor}, 0 -${w}px 0 ${strokeColor}, 0 0 14px ${accentColor}55`
    : `0 0 14px ${accentColor}55`;
}

const INTRO_STYLE_FIELDS = {preset:'intro-preset',font_name:'intro-font-name',primary_color:'intro-color',outline_color:'intro-outline-color',outline_width:'intro-outline-width',position:'intro-position',animation:'intro-animation',bold:'intro-bold',italic:'intro-italic',uppercase:'intro-uppercase'};
function collectIntroStyle() {
  const style = {};
  Object.entries(INTRO_STYLE_FIELDS).forEach(([key,id]) => {
    const el = document.getElementById(id);
    style[key] = el.type === 'checkbox' ? el.checked : el.value;
  });
  return style;
}
function restoreIntroStyle(style) {
  Object.entries(INTRO_STYLE_FIELDS).forEach(([key,id]) => {
    const el = document.getElementById(id);
    if (el.type === 'checkbox') el.checked = style?.[key] ?? el.defaultChecked;
    else el.value = style?.[key] ?? (el.tagName === 'SELECT' ? el.options[0].value : el.defaultValue);
  });
}
Object.values(INTRO_STYLE_FIELDS).forEach(id => document.getElementById(id)?.addEventListener('input', refreshIntroPreview));
document.getElementById('intro-preset')?.addEventListener('change', event => {
  const preset = CAPTION_PREVIEW[event.target.value] || CAPTION_PREVIEW.viral_yellow;
  document.getElementById('intro-color').value = preset.text;
  document.getElementById('intro-outline-color').value = preset.back;
  document.getElementById('intro-font-name').value = '';
  refreshIntroPreview();
});

// Wire the intro-hook controls to the live preview (runs once at load).
(function wireIntroHookPreview() {
  const introSize = document.getElementById('generated-intro-font-size');
  const introSizeLabel = document.getElementById('generated-intro-font-size-label');
  document.getElementById('caption-intro-text')?.addEventListener('input', refreshIntroPreview);
  document.getElementById('caption-intro-enabled')?.addEventListener('change', refreshIntroPreview);
  introSize?.addEventListener('input', () => {
    if (introSizeLabel) introSizeLabel.textContent = introSize.value;
    refreshIntroPreview();
  });
})();

// ------------------------------------------------------------------
// Interactive Caption Editor
// ------------------------------------------------------------------
window.openCaptionEditor = function(clipIndex) {
  const clip = generatedClips[clipIndex];
  if (!clip) return;
  currentEditingClip = clip;

  // Seed the intro-hook editor with this clip's current hook and reset the
  // rotation cache so "Suggest another" pulls fresh candidates for this clip.
  const titleInput = document.getElementById('edit-clip-title');
  if (titleInput) titleInput.value = clip.title || clip.hook_text || '';
  const hookInput = document.getElementById('edit-intro-hook');
  if (hookInput) hookInput.value = clip.intro_caption || clip.hook_text || '';
  const hookSizeInput = document.getElementById('intro-hook-font-size');
  if (hookSizeInput) {
    hookSizeInput.value = clip.intro_font_size || 64;
    const lbl = document.getElementById('intro-hook-font-size-label');
    if (lbl) lbl.textContent = hookSizeInput.value;
  }
  hookCandidates = [];
  hookCandidateIdx = -1;
  const vbox = document.getElementById('hook-variants');
  if (vbox) { vbox.classList.add('hidden'); vbox.innerHTML = ''; }
  updateHookPreview();

  const modal = document.getElementById('caption-modal');
  const chipsContainer = document.getElementById('word-chips');
  chipsContainer.innerHTML = '';

  const words = clip.words && clip.words.length ? clip.words : (clip.hook_text || '').split(' ').map((w, i) => ({ word: w, start: i * 0.4, end: (i + 1) * 0.4 }));

  words.forEach((w) => {
    // Coerce timings defensively: backend word entries occasionally omit
    // start/end, which used to throw on .toFixed and leave the editor half-built.
    const start = Number.isFinite(w.start) ? w.start : 0;
    const end = Number.isFinite(w.end) ? w.end : start;
    const chip = document.createElement('div');
    chip.className = 'word-chip';
    chip.innerHTML =
      `<span class="word-text" contenteditable="true">${escapeHtml(w.word || String(w))}</span> ` +
      `<small class="word-time" data-start="${escapeHtml(start)}" data-end="${escapeHtml(end)}" style="color:var(--text-muted);">[${start.toFixed(1)}s]</small>`;
    chipsContainer.appendChild(chip);
  });

  // Seed the preview with a SHORT snippet that mimics one on-screen caption
  // line. Using the whole transcript (or the full hook_text title) floods the
  // preview box and collides with the hook overlay, so cap it to a few words
  // drawn from the actual spoken words.
  const preview = document.getElementById('caption-preview');
  const sampleWords = (clip.words || [])
    .map((x) => (x && x.word ? String(x.word) : ''))
    .filter(Boolean);
  let sample = sampleWords.slice(0, 6).join(' ').trim();
  if (!sample) {
    // No word-level data: fall back to the first few words of any available text.
    sample = (clip.hook_text || '').trim().split(/\s+/).slice(0, 6).join(' ');
  }
  if (preview) {
    preview.dataset.words = sample.length ? sample : 'Your caption appears here';
    refreshCaptionPreview();
  }
  const previewFontSize = document.getElementById('caption-preview-font-size');
  const generatedFontSize = document.getElementById('generated-caption-font-size');
  const previewFontLabel = document.getElementById('caption-preview-font-size-label');
  if (previewFontSize && generatedFontSize) {
    previewFontSize.value = generatedFontSize.value;
    if (previewFontLabel) previewFontLabel.textContent = generatedFontSize.value;
  }

  modal.classList.remove('hidden');
};

document.getElementById('close-caption-modal')?.addEventListener('click', () => {
  document.getElementById('caption-modal').classList.add('hidden');
});

// Rotate through hook suggestions drawn from THIS clip's transcript. Fetches
// once, then cycles on each click (wrapping around).
document.getElementById('suggest-hook-btn')?.addEventListener('click', async () => {
  if (!currentEditingClip) return;
  const input = document.getElementById('edit-intro-hook');
  const btn = document.getElementById('suggest-hook-btn');
  if (!hookCandidates.length) {
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Thinking…'; }
    try {
      const res = await fetch(`${serverUrl}/tools/suggest-hooks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: currentEditingClip.full_text || currentEditingClip.reason || currentEditingClip.hook_text || '',
          words: currentEditingClip.words || [],
          count: 8,
        }),
      });
      const data = await res.json();
      hookCandidates = (data.hooks || []).filter(Boolean);
    } catch (_) {
      hookCandidates = [];
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '🔄 Suggest another'; }
    }
  }
  if (!hookCandidates.length) {
    showToast('No alternative hooks found for this clip', 'info');
    return;
  }
  hookCandidateIdx = (hookCandidateIdx + 1) % hookCandidates.length;
  if (input) input.value = hookCandidates[hookCandidateIdx];
  updateHookPreview();
});

// A/B hook variants: fetch several options at once (AI if Ollama is running,
// else transcript heuristics) and show them as clickable chips to compare/pick.
async function showHookVariants() {
  if (!currentEditingClip) return;
  const box = document.getElementById('hook-variants');
  const btn = document.getElementById('hook-variants-btn');
  if (!box) return;
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Generating…'; }
  noteAiColdStart();
  let hooks = [];
  let usedAi = false;
  try {
    const res = await fetch(`${serverUrl}/tools/rewrite-hook`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: currentEditingClip.full_text || currentEditingClip.reason || currentEditingClip.hook_text || '',
        words: currentEditingClip.words || [],
        current_hook: document.getElementById('edit-intro-hook')?.value || '',
        count: 6,
        preset: document.getElementById('generated-caption-preset')?.value || '',
      }),
    });
    const data = await res.json();
    hooks = (data.hooks || []).filter(Boolean);
    usedAi = !!data.used_ai;
  } catch (_) { hooks = []; }
  if (btn) { btn.disabled = false; btn.textContent = '⚖️ Variants'; }
  if (!hooks.length) { showToast('No hook variants found for this clip', 'info'); return; }
  box.classList.remove('hidden');
  box.innerHTML = `<div class="hook-variants-head muted small">${usedAi ? '✨ AI' : 'Suggested'} variants — click one to use it:</div>` +
    hooks.map((h) => `<button type="button" class="hook-variant-chip">${escapeHtml(h)}</button>`).join('');
  box.querySelectorAll('.hook-variant-chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      const input = document.getElementById('edit-intro-hook');
      if (input) input.value = chip.textContent;
      box.querySelectorAll('.hook-variant-chip').forEach((c) => c.classList.remove('chosen'));
      chip.classList.add('chosen');
      updateHookPreview();
    });
  });
}

// Optional AI rewrite: punch up the hook with the user's local Ollama model.
// Falls back to the offline suggestions when Ollama isn't running.
document.getElementById('ai-rewrite-hook-btn')?.addEventListener('click', async () => {
  if (!currentEditingClip) return;
  const input = document.getElementById('edit-intro-hook');
  const titleInput = document.getElementById('edit-clip-title');
  const btn = document.getElementById('ai-rewrite-hook-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Rewriting…'; }
  noteAiColdStart();
  try {
    // Full copywriter pass: regenerate the hook AND the title (and a description)
    // together, so clicking "AI rewrite" visibly refreshes both fields.
    const res = await fetch(`${serverUrl}/tools/rewrite-copy`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: currentEditingClip.full_text || currentEditingClip.reason || currentEditingClip.hook_text || '',
        words: currentEditingClip.words || [],
        current_hook: input?.value || '',
        preset: document.getElementById('generated-caption-preset')?.value || '',
      }),
    });
    const data = await res.json();
    const newHook = (data.hook || '').trim();
    const newTitle = (data.title || '').trim();
    if (!newHook && !newTitle) { showToast('No copy generated for this clip', 'info'); return; }
    if (input && newHook) { input.value = newHook; }
    if (titleInput && newTitle) { titleInput.value = newTitle; }
    if (data.description) currentEditingClip.description = data.description;
    updateHookPreview();
    if (data.used_ai) showToast(`✨ AI hook + title from ${data.model} — edit or Save & Apply to keep`, 'success');
    else showToast('Ollama not running — used offline suggestions. Start Ollama in Setup for AI rewrites.', 'info');
  } catch (e) {
    showToast('AI rewrite failed — try again', 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '✨ AI rewrite'; }
  }
});

document.getElementById('save-captions-btn')?.addEventListener('click', async () => {
  if (!currentEditingClip) {
    document.getElementById('caption-modal').classList.add('hidden');
    return;
  }

  const saveBtn = document.getElementById('save-captions-btn');
  const originalText = saveBtn ? saveBtn.textContent : '💾 Save & Apply Subtitles';
  if (saveBtn) {
    saveBtn.disabled = true;
    saveBtn.textContent = '⏳ Saving & Burning Captions…';
  }

  // Read edited word chips: [word] [start] now editable
  const chips = document.querySelectorAll('#word-chips .word-chip');
  const editedWords = [];
  chips.forEach((chip) => {
    const textEl = chip.querySelector('.word-text');
    const timeEl = chip.querySelector('.word-time');
    const word = (textEl ? textEl.textContent : '').trim();
    if (!word) return;
    let start = parseFloat((timeEl?.dataset.start || '0').replace(/[^0-9.]/g, ''));
    let end = parseFloat((timeEl?.dataset.end || '0').replace(/[^0-9.]/g, ''));
    if (isNaN(start)) start = 0;
    if (isNaN(end) || end <= start) end = start + 1;
    editedWords.push({ word, start, end });
  });

  if (!editedWords.length) {
    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.textContent = originalText;
    }
    showAlert('No editable words found.');
    return;
  }

  const captionOpts = collectCaptionOptions();
  const outputPath = currentEditingClip.ass_path
    ? currentEditingClip.ass_path.replace(/\.ass$/i, '.srt')
    : `${currentEditingClip.output_file}.srt`;

  try {
    const res = await fetch(`${serverUrl}/export/subtitles`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        output_path: outputPath,
        words: editedWords,
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
        clip_output_file: currentEditingClip.output_file,
        start_seconds: currentEditingClip.start_time,
        end_seconds: currentEditingClip.end_time,
        aspect_ratio: document.getElementById('clip-aspect-ratio')?.value || '9:16',
        // Burn the (possibly edited/rotated) intro hook for this clip.
        intro_caption: (document.getElementById('edit-intro-hook')?.value || '').trim() || undefined,
        intro_enabled: !!(document.getElementById('edit-intro-hook')?.value || '').trim(),
        intro_caption_duration: parseFloat(document.getElementById('caption-intro-duration')?.value || '3') || 3,
        intro_font_size: parseInt(document.getElementById('intro-hook-font-size')?.value || '', 10) || undefined,
        re_render: true,
      })
    });
    const data = await res.json();
    if (res.ok) {
      // Update in-memory clip state
      currentEditingClip.words = editedWords;
      if (data.export_path) currentEditingClip.srt_path = data.export_path;
      // Persist the chosen intro hook so it's reflected on the card and re-used.
      const newHook = (document.getElementById('edit-intro-hook')?.value || '').trim();
      currentEditingClip.intro_caption = newHook;
      if (newHook) currentEditingClip.hook_text = newHook;
      currentEditingClip.intro_font_size = parseInt(document.getElementById('intro-hook-font-size')?.value || '', 10) || undefined;
      // Editable clip title (used for the export file name + the card label).
      const newTitle = (document.getElementById('edit-clip-title')?.value || '').trim();
      if (newTitle) currentEditingClip.title = newTitle;

      // Reload the matching clip card so it plays the freshly burned captions.
      // Match on the card's own index rather than fuzzy src string-matching,
      // which was both fragile and mis-grouped (&& binds tighter than ||, so
      // the old condition reloaded the wrong card or none at all).
      const targetIdx = generatedClips.indexOf(currentEditingClip);
      if (targetIdx !== -1) {
        const card = document.querySelector(`.clip-card[data-clip-idx="${targetIdx}"]`);
        const vid = card && card.querySelector('video');
        if (vid) {
          vid.src = fileUrl(currentEditingClip.output_file, true);
          vid.load();
        }
        // Reflect the edited hook + title on the card immediately. Prefer the
        // AI-written description for the card blurb (matches buildClipCard),
        // falling back to the hook line when there's no description.
        const descEl = card && card.querySelector('.clip-desc');
        const cardBlurb = (currentEditingClip.description || newHook || '').trim();
        if (descEl && cardBlurb) descEl.textContent = cardBlurb;
        const titleEl = card && card.querySelector('.clip-title');
        if (titleEl && newTitle) titleEl.textContent = newTitle;
      }
      saveCurrentProjectSilently();
      playSuccessSound();
      showAlert(`✅ Captions updated and applied to clip!`);
    } else {
      showAlert(`Save failed: ${data.detail || 'Unknown error'}`);
    }
  } catch (err) {
    showAlert(`Save error: ${err.message}`);
  } finally {
    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.textContent = originalText;
    }
    document.getElementById('caption-modal').classList.add('hidden');
  }
});

function revealInFolder(filePath) {
  // Reveal in OS file manager via Electron IPC (fallback: copy path).
  if (window.clipperAPI && window.clipperAPI.revealInFolder) {
    window.clipperAPI.revealInFolder(filePath);
  } else {
    navigator.clipboard.writeText(filePath).catch(() => {});
    showAlert(`Path copied to clipboard:\n${filePath}`);
  }
}

window.quickCutSilence = async function(clipIndex) {
  const clip = generatedClips[clipIndex];
  if (!clip) return;
  try {
    const res = await fetch(`${serverUrl}/tools/remove-silence`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_path: clip.output_file }),
    });
    const data = await res.json();
    if (res.ok) {
      playSuccessSound();
      showAlert(`✂️ Dead air removed! Saved ${data.time_saved}s (${data.original_duration}s -> ${data.cut_duration}s)`);
      clip.output_file = data.output_path;
      clip.duration = data.cut_duration;
      showResults(generatedClips);
    } else {
      throw new Error(data.detail || 'Silence removal failed');
    }
  } catch (e) {
    showError(e.message);
  }
};

window.quickBleepClip = async function(clipIndex) {
  const clip = generatedClips[clipIndex];
  if (!clip) return;
  try {
    const res = await fetch(`${serverUrl}/tools/bleep-mute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_path: clip.output_file,
        mode: 'bleep',
        // Rebase to the clip's 0-based timeline (words carry absolute source
        // times) and let the server keep only profanity, so the bleep lands on
        // the right moments instead of the whole clip.
        timestamps: wordsClipRelative(clip.words || [], clip),
        profanity_only: true,
      }),
    });
    const data = await res.json();
    if (res.ok) {
      playSuccessSound();
      showAlert(`🔇 Bleep filter applied! (${data.message})`);
      clip.output_file = data.output_path;
      showResults(generatedClips);
    } else {
      throw new Error(data.detail || 'Bleeping failed');
    }
  } catch (e) {
    showError(e.message);
  }
};


function showError(message) {
  playErrorSound();
  const btn = document.getElementById('start-clipping');
  if (btn) {
    btn.disabled = false;
    btn.textContent = '🚀 Start Clipping & Transcribing';
  }
  setWizardStep(2);
  showAlert(`Error: ${message}`);
}
