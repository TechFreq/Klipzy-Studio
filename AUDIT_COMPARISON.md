# Klipzy Studio — Full Audit & Competitive Comparison

> **Audit date:** 2026-08-28 · **Repo:** `clippy-studio` (local-first AI video clipper)
> This document is a **feature-level audit of the Klipzy Studio codebase** plus a
> **gap analysis against ClipsKitty, OpenClipper (OpenClips), and OpusClip**.

---

## 1. What was audited

| Area | Files |
|------|-------|
| FastAPI server | `server/api/server.py` (19 endpoints) |
| Data models | `server/models.py` (13 Pydantic models) |
| Processing pipeline | `server/core/pipeline.py`, `transcriber.py`, `highlight_detector.py`, `audio_energy.py`, `llm_detector.py`, `face_tracker.py` |
| Export/FFmpeg | `server/core/ffmpeg_tools.py`, `export_tools.py`, `caption_styler.py` |
| System/Setup | `server/core/system_check.py`, `edit_chat.py` |
| UI | `ui/index.html`, `ui/src/renderer.js`, `ui/src/styles.css`, `ui/electron/main.js`, `ui/electron/preload.js` |
| Pack & launch | `ui/package.json` (electron-builder), `main.py`, `scripts/*`, `assets/*` |
| Tests | `tests/test_core.py` |

**Validation run in this audit:** all 13 Python modules `py_compile` OK, `node --check ui/src/renderer.js` OK.
A live smoke test previously confirmed `POST /export/standalone` (mp3/wav/flac/aac/m4a/srt/vtt) returns 200 + files.

---

## 2. Feature inventory — Klipzy Studio (what EXISTS)

### Core pipeline
- ✅ Import local video (drop-zone, drag & drop, file dialog via Electron IPC)
- ✅ Whisper transcription with word-level timestamps; `tiny/base/small/medium`, optional language, auto CUDA/MPS/CPU
- ✅ Heuristic virality scoring + hook-keyword detection (secret / never / why / mistake / truth / crazy…)
- ✅ Audio-energy highlight detection (librosa RMS loudness spikes → mapped to transcript)
- ✅ Optional LLM highlight discovery (Ollama, JSON prompt)
- ✅ Speaker-aware 9:16 smart crop via YOLOv8 person tracking + OpenCV
- ✅ Dedup + sort candidates, configurable min/max duration & clip count
- ✅ GPU hardware encoding (NVENC / VideoToolbox / VAAPI, fallback x264)
- ✅ Manual clip trimmer (drag handles, in/out points, preview, custom layout)
- ✅ Gaming/reaction layout (full-frame gameplay + webcam PiP in 4 corners, scalable)

### Captions
- ✅ Animated karaoke `.ass` captions (word-by-word `\k`) with OpusClip-style presets
- ✅ SRT / VTT / ASS generation per clip + full-project
- ✅ Caption burn-in via libass subtitles filter (Windows-safe path handling)
- ✅ Interactive caption editor (edit word timings, style presets: Opus Yellow, Neon Green, Bold White)

### Exports
- ✅ Single-clip export-as: MP4 / MOV / MKV / WebM / GIF
- ✅ Compile-all reel export: MP4 / MOV / MKV / WebM / GIF (concat demuxer)
- ✅ NLE exports: Premiere Pro (FCPXML/XML), DaVinci Resolve (EDL), CapCut (Draft)
- ✅ **Standalone assets:** audio-only MP3/WAV/FLAC/AAC/M4A + subtitles SRT/VTT, transcript TXT/JSON
- ✅ Reveal in OS folder (Electron shell)

### Other
- ✅ AI Edit Chat (Ollama + rule-based fallback)
- ✅ Setup/system panel (deps, GPU/CPU/hardware detect, install cmds, model recommendations, render-time estimator)
- ✅ Health check, job queue + progress polling, CORS
- ✅ Desktop packaging: NSIS with desktop + Start Menu shortcuts, macOS DMG/icns, Linux AppImage
- ✅ Success / error / click audio feedback (WebAudio synthesized)
- ✅ 100% local & private (no uploads; Ollama/LM Studio optional)

---

## 3. Competitor comparison

> **Source note:** OpusClip data from `opus.pro`; OpenClipper data from the
> `GrepCut/OpenClipper` GitHub README. **ClipsKitty's site (`clipskitty.com`) was unreachable**
> from this environment (fetch failures; no GitHub/App Store/Play store presence found),
> so it is compared at **"assumed category parity"** (AI desktop clipper) and flagged as unverified.
> The gap recommendations are identical to the OpusClip-class gaps either way.

| Capability | **Klipzy Studio** | **ClipsKitty** (unverified) | **OpenClipper (GrepCut)** | **OpusClip (web)** |
|---|---|---|---|---|
| Platform | Win/mac/Linux desktop | macOS/iOS (per category) | **Windows only** | Web (any) |
| Local transcription | ✅ Whisper | ✅ | ✅ Whisper v3 Turbo + Parakeet + OpenRouter/Groq | ❌ cloud |
| Transcript import | ❌ | n/a | ✅ | ✅ |
| Scene-aware autoreframe | ⚠️ single center speaker | — | ✅ paths + split view | ✅ |
| Multi-aspect export | ❌ only 9:16 + full | ✅ | ✅ 9:16/16:9/1:1/4:5 | ✅ |
| Caption styles | ✅ 3 presets | ✅ | ✅ **20+ presets** | ✅ animated |
| Caption editor | ✅ word times | ✅ | ✅ | ✅ |
| B-roll / stock | ❌ | ❌ | ❌ | ✅ AI B-Roll |
| AI reframe (track moving subject) | ❌ static center | ✅ | ✅ | ✅ ReframeAnything |
| Social **auto-publish** | ❌ manual only | ✅ | ⚠️ roadmap (pending) | ✅ |
| Cloud URL import | ❌ | ✅ | ❌ | ✅ |
| Social scheduler | ❌ | n/a | ❌ | ✅ |
| Brand kit / templates | ❌ | ❌ | ❌ | ✅ |
| Team workspace | ❌ | ❌ | ❌ | ✅ |
| MCP / API | ❌ | ❌ | ✅ MCP topic | ✅ API + MCP |
| Voice-over / AI audio | ❌ | ❌ | ❌ | ✅ AI voiceover |
| Multilingual | ⚠️ language option | ✅ 20+ | ✅ | ✅ 20+ |
| Thumbnails/titles | ⚠️ basic title | ❌ | ❌ | ✅ thumbnail gen |
| Virality scoring | ✅ heuristic | ✅ | ✅ | ✅ Opus Score |
| NLE export | ✅ FCPXML/EDL/CapCut | n/a | ✅ | ✅ Export to XML |
| Pricing | **Free OSS (perpetual)** | ? | Free OSS | **Paid tiers** |

---

## 4. Gaps — what we're missing vs the benchmark trio

### 🔴 High impact (recommend next)

1. **Social sharing / one-click publish**
   - ClipsKitty (assumed) and OpusClip publish directly to TikTok/YouTube/Instagram/Facebook/X + batch queue/scheduler.
   - Open Clipper lists this as the headline differentiator too (their social OAuth is still pending).
   - *Gap:* we only have manual local-file export.
2. **Cloud link import (YouTube / Vimeo / Twitch / Google Drive)**
   - OpusClip's whole flow is "drop a link"; OpenClips supports URL import. We are local-file only.
   - *Gap:* add yt-dlp-based import → download → pipeline.
3. **Scene-aware / multi-aspect auto-reframe**
   - OpenClips does GPU face/subject detection in the same pass, plans a camera path per format at scene cuts, split view when 2+ people, supports 1:1/4:5/9:16/16:9.
   - *Gap:* our `FaceTracker` returns a single center crop offset for 9:16 only.
4. **20+ caption preset library**
   - OpenClips advertises 20+ styles (karaoke, kinetic, podcast, gaming) + positional/size/brand control.
   - *We have 3 presets + fixed alignment/style.*
5. **Multi-language auto-caption translation** (OpusClip: 20+ languages; our Whisper transcribes many but cannot translate).

### 🟠 Medium
6. Brand kit / brand template + title & thumbnail generator (OpusClip).
7. AI B-Roll / stock clips (OpusClip).
8. Auto voice-over / audio mixing & music ducking (OpusClip).
9. MCP / agentic API layer (both OpusClip & OpenClips expose MCP; we have an HTTP API but no MCP).
10. Batch render queue UI (we render sequentially, no queue UI).

### 🟡 Nice-to-have
11. Background removal (common in the AI-clip category).
12. Timeline/trim preview at frame level inside the app.
13. Saved projects / draft persistence (OpusClip team workspace; local can do saved JSON projects).
14. Simple analytics dashboard (OpusClip Pro).

---

## 5. Internal audit: small improvements & observations

- `ui/src/renderer.js` uses `alert()` for almost all feedback — switch to in-app toasts for a nicer UX.
- GIF export path is fixed at 15fps / 720px — could add fps/size options.
- `sub_srt`/`sub_vtt` standalone depends on an existing `captions.srt` next to the video (404 with a clear message otherwise). Could auto-generate a fallback for a single clip.
- `transcript_txt` / `transcript_json` are wired in `models.py` + server but not exposed in the UI dropdown (only audio + srt/vtt). Minor.
- Face tracker falls back gracefully when YOLO/OpenCV missing — good.
- `concat_clips` cleans up its `.concat-list.txt` — good.
- **Security:** Electron runs `nodeIntegration: true, contextIsolation: false` in production — recommend `contextIsolation: true` + preload-only IPC.
- Packaging: desktop + Start Menu shortcuts configured (`createDesktopShortcut: always`), matching installer already in `ui/dist`.

---

## 6. Suggested roadmap (by version)

| Version | Feature |
|---------|---------|
| v1.2 (next) | 20+ caption presets (4-5 quick wins) · in-app toasts · cloud-link import via yt-dlp |
| v1.3 | Multi-aspect export menu (9:16, 4:5, 1:1) · smoother scene-aware re-frame (YOLO track → FFmpeg path) |
| v1.4 | Social API publish (TikTok/YouTube OAuth) + scheduler / render queue |
| v1.5 | AI B-Roll util · brand templates · thumbnail generator |
| v1.6 | MCP + agentic API (align with OpusClip / OpenClips) |

---

## 7. Bottom line

**Klipzy Studio is feature-rich on the core clipper → caption → export axis** and unique among
the three as a **free, offline, MIT-licensed desktop tool**. The biggest product gaps are:

1. URL-based import (YouTube / Twitch / Drive)
2. Multi-aspect & scene-aware reframing
3. 20+ caption presets + brand styling
4. Direct social publishing / scheduler
5. API / MCP / agent hooks

Secondary: B-roll, translation, thumbnail/title generation, team sync, audio mixing.