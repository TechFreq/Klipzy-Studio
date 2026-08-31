# Klipzy Studio — Codebase Audit & Competitive Comparison

> **Klipzy Studio** — a local-first **Long Form to Shorts** studio by TechFreq Developments.
> **Last updated:** 2026-08-31 · **Repo:** `clippy-studio`

> **Restore note (2026-08-31):** a "cleanup temp files" commit (`40909ff`) had
> deleted the entire Python backend, and a brace bug in `renderer.js` had silently
> disabled the UI. The complete working tree was recovered from Cline checkpoint
> `5c8bdc9e` + its untracked companion `2ff3cae` (stamped 2026-08-30 12:40, eleven
> minutes before the deletion), then every audit finding below was fixed. Numbers
> in this document reflect the **restored + repaired** tree.

---

## 1. What was audited (restored tree)

| Area | Files | Status |
|------|-------|--------|
| FastAPI server | `server/api/server.py` (**31 routes**) | ✅ boots, serves |
| Local API auth | `server/auth.py` (token middleware) | ✅ enforced |
| Data models | `server/models.py` | ✅ imports |
| Processing pipeline | `server/core/pipeline.py`, `transcriber.py`, `highlight_detector.py`, `audio_energy.py`, `llm_detector.py`, `face_tracker.py`, `overlay_manager.py` | ✅ compiles |
| Export / FFmpeg | `server/core/ffmpeg_tools.py`, `export_tools.py`, `caption_styler.py`, `silence_cutter.py`, `word_filter.py` | ✅ compiles |
| System / Setup | `server/core/system_check.py`, `edit_chat.py`, `logging_setup.py` | ✅ compiles |
| UI | `ui/index.html`, `ui/src/renderer.js`, `ui/src/styles.css`, `ui/electron/main.js`, `ui/electron/preload.js` | ✅ `node --check` |
| Pack & launch | `ui/package.json` (electron-builder), `scripts/main.py`, `scripts/*`, `assets/*` | ✅ valid |
| Tests | `tests/test_core.py`, `test_v2_features.py`, `test_logging.py`, `test_auth.py` | ✅ **49 pass** |

**Validation:** 36 Python modules pass `py_compile`; `node --check` passes on all
three JS files; `pytest tests/` → **52 passed**; a live boot serves 31 routes
(no duplicates), 22 caption presets, with token auth enforced.

**End-to-end verified (2026-08-31):** a real 1080p HEVC source was run through the
actual pipeline (transcribe → highlight → speaker-crop → render → burn captions).
Output: genuine **1080×1920 H.264 + AAC** clips with burned captions, per-clip
`.ass/.srt`, and thumbnails, produced in ~29s from a 2-min excerpt via
`faster-whisper` on CPU (int8). Two real bugs were found and fixed during this run:

- **faster-whisper CUDA crash** — `device="auto"` let CTranslate2 select a CUDA GPU
  on a machine with CPU-only PyTorch and no CUDA runtime, crashing on
  `cublas64_12.dll`. Now the device is chosen from actual `torch.cuda` availability,
  and a failing accelerator falls back to the next backend instead of aborting.
- **misleading GPU recommendation** — `recommend_models()` claimed GPU/CUDA whenever
  `nvidia-smi` saw a card, even when the ML stack couldn't use it. Now gated on real
  CUDA/MPS usability, with a "CPU (GPU idle)" state + an unlock hint.

**Known quality note:** with Ollama disabled, highlight selection is heuristic
(hook words + audio energy) and can pick weak moments on loosely-structured footage.
Enabling the local LLM (or the planned active-speaker + scene-signal upgrades)
improves picks. Not a correctness bug — a selection-quality tradeoff.

---

## 2. Feature inventory (what exists today)

### Core pipeline
- ✅ **Local video import** — drop-zone, drag & drop, batch multi-file, file dialog via Electron IPC.
- ✅ **Whisper transcription** — word-level timestamps, auto CUDA / MPS / CPU device selection.
- ✅ **Heuristic virality scoring** — hook keywords (*secret, never, why, mistake, truth, crazy…*).
- ✅ **Audio-energy highlight detection** — librosa RMS loudness spikes mapped to transcript.
- ✅ **Optional local LLM discovery** — Ollama, structured JSON prompt (rules-based fallback when absent).
- ✅ **Speaker-aware 9:16 crop** — YOLOv8 person tracking + OpenCV bounding center.
- ✅ **GPU hardware encoding** — auto-detects NVENC (NVIDIA), VideoToolbox (Apple Silicon), VAAPI (Linux/Intel), x264 fallback.
- ✅ **Manual clip trimmer** — drag handles, in/out points, live preview.
- ✅ **Gaming / reaction layout** — full-frame gameplay + scalable webcam PiP in 4 corners.

### Captions & styling
- ✅ **Animated karaoke `.ass` captions** — word-by-word `\k` timing.
- ✅ **22 caption presets** — karaoke, kinetic, podcast, gaming, neon, boxed, glow.
- ✅ **Subtitle formats** — `.ass`, `.srt`, `.vtt` per clip or whole video.
- ✅ **Burn-in subtitles** — libass filter with cross-platform path escaping.
- ✅ **Interactive caption editor** — adjust word timings and switch styles live.

### Exports & NLE workflows
- ✅ **Single-clip media export** — MP4 / MOV / MKV / WebM / GIF.
- ✅ **Compile-all highlight reel** — 1-click concat.
- ✅ **NLE timeline exports** — Premiere Pro (FCP7 XML), DaVinci Resolve (EDL), CapCut (`draft_content.json`).
- ✅ **Standalone asset exports** — audio (MP3/WAV/FLAC/AAC/M4A) + subtitles (SRT/VTT) + transcripts.
- ✅ **OS integration** — "Reveal in Folder" via Electron shell.

### System & UX
- ✅ **AI Edit Chat** — Ollama with regex-rule fallback.
- ✅ **Setup & diagnostics** — GPU/CUDA/VRAM detection, FFmpeg/Whisper/Ollama health, install helpers, render-time estimator.
- ✅ **Token-authenticated local API** — per-launch secret; only `/health` + docs are public.
- ✅ **Silence / dead-air cutter** and **word-level profanity bleep/mute/mask**.
- ✅ **Accessibility** — keyboard-reachable controls, dialog semantics + focus trap, ARIA live regions.
- ✅ **100% offline & private by default** — no usage caps, subscriptions, or credit meters.

---

## 3. Competitive comparison

Klipzy sits in the **local-first "long video → Shorts"** category. The honest
landscape: this is now an active space with several credible open-source entries,
plus the cloud incumbent. Sources are the projects' own public repos/pricing
pages; competitor feature details are "as advertised," not independently tested.
*Content rephrased for licensing compliance.*

| Capability | **Klipzy Studio** | OpusClip (cloud) | OpenClipper (GrepCut) | Clips Kitty (clips-studio) |
|---|---|---|---|---|
| License / cost | Free, Open-Attribution | Freemium, metered | Open-source | Open-source |
| Runs fully local | ✅ by default | ❌ cloud | ✅ (local or cloud ASR) | ✅ by default |
| Desktop app | ✅ Electron, Win/mac/Linux | Browser | ✅ Tauri, Windows-first | ✅ |
| Transcription | Whisper (local) | Cloud | Whisper v3 Turbo / Parakeet | faster-whisper |
| URL ingestion (YT/Twitch/Kick) | ⏳ roadmap | ✅ | ✅ | ✅ |
| Active-speaker detection | YOLOv8 person crop | ✅ | face/subject + split view | YOLOv8 pose + TalkNet |
| Caption presets | ✅ 22 | ✅ | 20+ | ✅ editable |
| **NLE exports (Premiere/DaVinci/CapCut)** | ✅ | ❌ | ↔ jumps to GrepCut Studio | ❌ |
| Silence cutter + profanity filter | ✅ | partial | — | — |
| Gaming / reaction PiP | ✅ | ❌ | split view | reaction signal |
| Multilingual dub/translate | ❌ | ✅ | — | ✅ 19 languages |
| Auto-publish to socials | ❌ | ✅ | ✅ | metadata only |
| MCP / agent API | ⏳ roadmap | — | ✅ | ✅-ish |

**Where Klipzy honestly pulls through:**
- **NLE round-tripping.** Premiere XML / DaVinci EDL / CapCut draft is genuinely
  uncommon here — OpenClipper hands off to its own online editor and Clips Kitty
  doesn't emphasize NLE files. This reframes Klipzy as a *rough-cutter that feeds
  your real editor*, which neither peer targets.
- **Fully local by default with optional LLM.** Some "local" peers still require a
  cloud key (OpenRouter/Groq/Gemini/Claude) for clip selection. Klipzy's highlight
  detection runs on local audio-energy + hook heuristics; Ollama is strictly optional.
- **Cross-platform desktop.** OpenClipper is Windows-first (Tauri); Klipzy targets
  Windows, macOS, and Linux from one Electron codebase.
- **Breadth in one app** — trimmer, caption editor, silence cutter, profanity filter,
  gaming PiP, standalone asset export — versus peers that each do a slice.

**Where Klipzy is behind (honest gaps):**
- **URL ingestion.** Both OpenClipper and Clips Kitty ingest YouTube/Twitch/Kick
  links; Klipzy is local-file-only today (roadmap Tier 1).
- **Active-speaker precision.** Clips Kitty's TalkNet active-speaker + pose tracking
  is more advanced than Klipzy's YOLOv8 person crop.
- **Multilingual** dub/translate (Clips Kitty: 19 languages) and **auto-publishing**
  (OpusClip, OpenClipper) are not implemented.

**Takeaway:** not "nothing like it exists" — it's a crowded, fast-moving category,
and Klipzy holds a defensible spot: desktop-native, fully-local-by-default, with
NLE exports few peers bother with. The clearest table-stakes gap to close is URL
ingestion.

---

## 4. Roadmap

### 🔴 Tier 1 — table stakes
1. **URL / stream ingestion (`yt-dlp`)** — YouTube, Twitch VODs, Kick VODs into the existing pipeline.
2. **Active-speaker upgrade** — add TalkNet-style active-speaker detection on top of YOLOv8.

### 🟠 Tier 2 — hardware acceleration
**Already implemented** (see `server/core/transcriber.py`, `ffmpeg_tools.py`,
`system_check.py`):
- Cross-platform HW **video encoders**: NVENC, Apple **VideoToolbox** (Apple
  Silicon *and* Intel Mac), Intel **QSV**, AMD **AMF**, Linux **VAAPI**, x264 fallback.
- **MLX-accelerated transcription** on Apple Silicon via `mlx-whisper` (auto-preferred
  on M-series), `faster-whisper` on CPU/CUDA, `openai-whisper` fallback — all three
  now shipped in `requirements.txt` (mlx gated to macOS-arm64).
- Whisper device auto-select: CUDA → MPS → CPU.

- **Hardware-aware model recommendation** (`system_check.recommend_models`): the
  Setup panel detects GPU/VRAM/RAM/chip and *recommends* the best Whisper/YOLO/Ollama
  model. It recommends and lets the user choose/install — it does **not** silently
  download anything.
- **Live backend readout**: `/health` + `/transcription-backend` report the active
  engine; the clickable header status opens Setup & diagnostics.

**Still coming soon:**
3. **One-click install of the recommended model** — install the recommended pick
   directly from the Setup recommendation card (detection already done; this is the
   install shortcut). *(Ollama remains optional.)*
4. **MLX beyond transcription** — extend MLX to highlight/LLM stages on M-series.
5. **Multi-speaker split-screen** — stack two detected speakers vertically.

### 🟡 Tier 3 — workflow & integrations
6. **MCP server** — expose clipping / reframing / transcription to AI agents.
7. **Multilingual** subtitle translation and optional dubbing.
8. **Direct social publishing** — OAuth batch publish to Shorts / TikTok / Reels.

---

## 5. Bottom line

Klipzy Studio occupies a real niche: a **100% local, private, free desktop
"Long Form to Shorts" studio** with professional NLE exports, rich caption styles,
silence removal, profanity filtering, and flexible aspect ratios — with hardware-aware
acceleration (Apple Silicon / MLX) on the near-term roadmap.
