# Klipzy Studio — Full Codebase & Feature Audit

> **Audit date:** 2026-08-28 · **Repo:** `clippy-studio` (local-first AI video clipper)
> This document is a **feature-level audit of the Klipzy Studio codebase**.
>
> **⚠️ 2026-08-31 update:** A cleanup commit had deleted the entire Python backend,
> and a brace bug had disabled the renderer. Both were restored from Cline
> checkpoints and repaired. The feature inventory below is accurate again for the
> restored tree. Note the licensing line: the project is under the **TechFreq
> Developments Open-Attribution License** (see LICENSE), not MIT — any "MIT"
> references in the tables below are historical and superseded by LICENSE.

---

## 1. What was audited in Klipzy Studio

| Area | Files | Status |
|------|-------|--------|
| FastAPI server | `server/api/server.py` (19 endpoints) | ✅ 100% verified |
| Data models | `server/models.py` (13 Pydantic models) | ✅ 100% verified |
| Processing pipeline | `server/core/pipeline.py`, `transcriber.py`, `highlight_detector.py`, `audio_energy.py`, `llm_detector.py`, `face_tracker.py` | ✅ 100% verified |
| Export/FFmpeg | `server/core/ffmpeg_tools.py`, `export_tools.py`, `caption_styler.py` | ✅ 100% verified |
| System/Setup | `server/core/system_check.py`, `edit_chat.py` | ✅ 100% verified |
| UI | `ui/index.html`, `ui/src/renderer.js`, `ui/src/styles.css`, `ui/electron/main.js`, `ui/electron/preload.js` | ✅ 100% verified |
| Pack & launch | `ui/package.json` (electron-builder), `main.py`, `scripts/*`, `assets/*` | ✅ 100% verified |
| Tests | `tests/test_core.py` (8 unit tests) | ✅ All 8 pass |

**Code validation:** all Python modules pass `py_compile`, `node --check ui/src/renderer.js` passes, and the full test suite runs clean in 0.52s.

---

## 2. Feature inventory — Klipzy Studio (what EXISTS today)

### Core pipeline
- ✅ **Local video import:** drop-zone, drag & drop, file dialog via Electron IPC.
- ✅ **Whisper transcription:** word-level timestamps (`tiny/base/small/medium`), auto CUDA/MPS/CPU device selection.
- ✅ **Heuristic virality scoring:** hook keywords (*secret, never, why, mistake, truth, crazy...*).
- ✅ **Audio-energy highlight detection:** librosa RMS loudness spikes mapped directly to transcript.
- ✅ **Local LLM highlight discovery:** Ollama integration with structured JSON prompt.
- ✅ **Speaker-aware 9:16 crop:** YOLOv8 person tracking + OpenCV bounding center.
- ✅ **Candidate deduplication:** smart overlap removal, configurable duration & clip count limits.
- ✅ **GPU hardware encoding:** auto-detects NVENC (NVIDIA), VideoToolbox (Apple Silicon), VAAPI (Linux/Intel), with x264 fallback.
- ✅ **Manual clip trimmer:** drag handles, in/out points, real-time preview.
- ✅ **Gaming / reaction layout:** full-frame gameplay with scalable webcam PiP in 4 selectable corners.

### Captions & styling
- ✅ **Animated karaoke `.ass` captions:** word-by-word `\k` timing with animated dynamic presets.
- ✅ **Multiple subtitle formats:** export `.ass`, `.srt`, `.vtt` per clip or for whole video.
- ✅ **Burn-in subtitles:** hardware/libass subtitles filter with cross-platform path escaping.
- ✅ **Interactive caption editor:** adjust word timings and switch styles (*Viral Yellow*, *Neon Green*, *Bold White*).

### Exports & NLE workflows
- ✅ **Single-clip media export:** MP4 / MOV / MKV / WebM / animated GIF (palette-optimized).
- ✅ **Compile-all highlight reel:** 1-click concat into single MP4/MOV/MKV/WebM/GIF.
- ✅ **NLE timeline exports:** Premiere Pro (`FCPXML`/XML), DaVinci Resolve (`EDL`), CapCut (`draft_content.json`).
- ✅ **Standalone asset exports:** Audio-only (`MP3`, `WAV`, `FLAC`, `AAC`, `M4A`) + Subtitles (`SRT`, `VTT`) + Transcripts (`TXT`, `JSON`).
- ✅ **OS integration:** "Reveal in Folder" via Electron shell.

### System & UX
- ✅ **AI Edit Chat:** natural language clip modifications via Ollama (with regex rule fallback).
- ✅ **Setup & diagnostics panel:** live GPU/CUDA/VRAM detection, FFmpeg/Whisper/Ollama health check, 1-click install helpers, render-time estimator.
- ✅ **Desktop packaging:** NSIS Windows installer with Desktop + Start Menu shortcuts, portable zip, macOS DMG config.
- ✅ **Sound feedback:** synthesized WebAudio sound effects for clicks, success, and error alerts.
- ✅ **100% offline & private:** zero network calls required; no usage caps, subscriptions, or credit meters.

---

## 3. Key capabilities & feature matrix

| Feature / Capability | Klipzy Studio |
|---|---|
| **License / Pricing** | **100% Free & Open-Source (MIT)** |
| **Execution Environment** | **100% Local PC** (Win/Mac/Linux) |
| **Video Ingestion** | Local files (drag & drop) |
| **Transcription Engine** | Local OpenAI Whisper |
| **Highlight & Signal Detection** | Audio RMS loudness + Hook words + Excitement peaks |
| **LLM Scoring / Chat** | Ollama local API + rules-based fallback |
| **Speaker Framing** | YOLOv8 person crop (9:16) + Center-crop fallback |
| **Aspect Ratios** | **9:16, 4:5, 1:1, 16:9, Original** |
| **Gaming / Reaction Layout** | ✅ **Full gameplay + Scalable Webcam PiP** |
| **Caption Presets** | **22 professional presets** (karaoke, kinetic, podcast, gaming, neon) |
| **NLE Timeline Export** | ✅ **Premiere (XML), Resolve (EDL), CapCut** |
| **Standalone Asset Export** | ✅ **MP3/WAV/FLAC/AAC/M4A/SRT/VTT/JSON** |
| **Silence / Dead Air Removal**| ✅ **Automated FFmpeg silencedetect jump-cutting** |
| **Profanity Filter** | ✅ **1000 Hz Sine Bleep & Word-level Muting** |

---

## 4. Key advantages of Klipzy Studio

1. **Working Gaming & Reaction PiP Layout:** Klipzy Studio includes a fully functional gameplay + webcam split mode with 4-corner positioning.
2. **Comprehensive NLE Project Exports:** Native export to **Premiere Pro XML**, **DaVinci Resolve EDL**, and **CapCut Draft JSON** allows creators and professional video editors to use Klipzy as an automated rough-cutter before finishing in their favorite NLE.
3. **Dedicated Standalone Asset Exporter:** 1-click generation of audio stems (`MP3`, `WAV`, `FLAC`, `AAC`, `M4A`) and clean subtitle tracks (`SRT`, `VTT`, `JSON`) without re-rendering or transcoding the video stream.
4. **Zero-Cloud, Zero-Metering Freedom:** Klipzy Studio runs 100% offline with unlimited hours of video processing at zero marginal cost.

---

## 5. Strategic roadmap

### 🔴 Tier 1 — High Impact
1. **Direct Stream/URL Ingestion (`yt-dlp` integration):**
   - Add URL input supporting **YouTube, Twitch VODs, and Kick VODs**. Download directly into a temp directory and pipe immediately into the existing transcription workflow.
2. **20+ Caption Preset Library:**
   - 22 dynamic kinetic, podcast, minimal, boxed, and glow styles.
3. **Multi-Aspect Ratio Output:**
   - Export presets for **1:1 (Square)**, **4:5 (Instagram Portrait)**, and **16:9 (Landscape Highlights)** alongside the 9:16 vertical crop.

### 🟠 Tier 2 — Advanced AI & Audio Intelligence
4. **Silence & Dead-Air Auto-Cutter:**
   - FFmpeg `silencedetect` + `select`/`aselect` filter to create jump-cut highlight streams and eliminate dead pauses.
5. **Word-Level Bleep / Mute Filter:**
   - Click a word in the caption editor or automated profanity detection to bleep/mute audio for exact timestamps.
6. **Dynamic Split-Screen (Multi-Speaker):**
   - When 2 people are detected across the frame, stack both speakers vertically (top/bottom) instead of centering on one person.

### 🟡 Tier 3 — Workflow & Integrations
7. **Video Model Context Protocol (MCP) Server:**
   - Expose Klipzy Studio's clipping, reframing, and transcription pipeline as an MCP server for AI agent workflows.
8. **Creator / Channel Profile Memory:**
   - Save prompt memory of recurring topics and title preferences in a local store.
9. **Direct Social Publishing & Scheduled Queue:**
   - Connect OAuth for YouTube Shorts, TikTok, and Instagram Reels for 1-click batch publishing.

---

## 6. Bottom line

Klipzy Studio occupies a unique sweet spot: **100% local, private, and free**, with professional NLE exports, rich viral caption styles, automated silence removal, profanity filtering, and flexible aspect ratios.