# 🎬 Klipzy Studio

A **local-first Long Form to Shorts studio by TechFreq Developments** for Windows, macOS, and Linux. Turn long videos, podcasts, and streams into ready-to-post vertical Shorts — entirely on your own machine. **No cloud AI, no fees.**

> ⚖️ Licensed under the **TechFreq Developments Open-Attribution License** (see [LICENSE](LICENSE)). Free to use and modify, provided you credit TechFreq Developments as the original author.

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🎯 **AI Virality & Hook Detection** | Finds the most engaging moments with virality scores, hook strength, and trend breakdowns |
| ⚡ **Hardware Acceleration** | Auto GPU video encoding (NVIDIA NVENC, Apple Silicon VideoToolbox, x264 CPU fallback) |
| 🎨 **Viral Dynamic Captions** | 22 word-by-word karaoke caption presets (`.ass`) with dynamic color styling |
| ✏️ **Interactive Caption Editor** | Live word-timestamp adjusting and subtitle customization |
| 🎬 **NLE Project Exports** | Export timelines to **Adobe Premiere Pro** (XML), **DaVinci Resolve** (EDL), or **CapCut** (Draft) |
| 🔊 **Audio Energy Detection** | Detects excitement spikes and loudness peaks to catch dramatic moments |
| ✂️ **Silence / Dead-Air Cutter** | FFmpeg `silencedetect` jump-cuts to keep energy high |
| 🔇 **Profanity Filter** | Word-level bleep / mute / caption masking |
| 🎮 **Gaming / Reaction Layout** | Full gameplay + scalable webcam PiP in any corner |
| 🤖 **Optional LLM Discovery** | Uses local Ollama (Gemma) to pick viral moments from the transcript |
| 🗣️ **Speaker-Aware Face Tracking** | Tracks the active speaker so 9:16 crops stay centered |
| 💬 **AI Edit Chat** | Chat with local AI for hook ideas, captions, hashtags, and edits |
| 📦 **100% Local & Private** | Whisper, YOLO, Ollama, FFmpeg all run on your machine — no cloud |

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────┐
│  Electron Desktop App (UI)                        │
│  - Windows / macOS / Linux                        │
│  - Drag & drop, options, progress, previews       │
└────────────────────┬──────────────────────────────┘
                     │ localhost HTTP (token-authenticated)
┌────────────────────▼──────────────────────────────┐
│  Python FastAPI Server                            │
│  - Job queue & progress polling                   │
│  - AI Edit Chat                                   │
└────────────────────┬──────────────────────────────┘
                     │
┌────────────────────▼──────────────────────────────┐
│  Processing Pipeline                              │
│  FFmpeg → Whisper → Highlights → Face Track →     │
│  Render 9:16 + captions                           │
└─────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### Prerequisites

1. **Python 3.10+**
2. **FFmpeg** (with `ffprobe` and `libass`)
   - Windows: `winget install Gyan.FFmpeg`
   - macOS: `brew install ffmpeg`
   - Linux: `sudo apt install ffmpeg libass-dev`
3. **Node.js 18+** (for the Electron desktop UI — the server also runs standalone)

### Option A — one-click launchers (recommended)

- **Windows:** double-click `start_klipzy.bat`, or run `scripts\run_windows.bat`
- **macOS / Linux:** run `./scripts/run_macos.sh`

These set up the Python virtual environment and UI dependencies on first run, then launch the desktop app (which starts the backend for you).

### Option B — manual

**1. Python server (core engine)**

```bash
python -m venv .venv

# Windows
.\.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt

# Start the API server
python scripts/main.py
```

Then open <http://127.0.0.1:8765/docs> for the interactive API docs.

**2. Electron desktop app**

```bash
cd ui
npm install
npm start
```

---

## 🔒 Local API Security

The backend listens on `127.0.0.1` but is **token-authenticated**: the desktop app
generates a per-launch secret and sends it in the `X-Klipzy-Token` header. This
prevents other web pages in your browser from reaching the local API (which can
launch installers and touch the filesystem).

- `/health` and the `/docs` pages are the only unauthenticated routes.
- For scripted / headless use, the token is written to `logs/api_token.txt`.
- Set `KLIPZY_DISABLE_AUTH=1` to turn enforcement off (test suite / at your own risk).

---

## 🧠 AI Components

| Component | Tool | License | Purpose |
|-----------|------|---------|---------|
| Transcription | OpenAI **Whisper** | MIT | Speech → text with word timestamps |
| Face tracking | **YOLOv8** (ultralytics) | AGPL-3.0 | Speaker-aware 9:16 crop |
| Local LLM | **Ollama** + Gemma | MIT | AI edit chat + highlight discovery |
| Video processing | **FFmpeg** | LGPL/GPL | Extract, cut, crop, burn captions |

> **Note on YOLO/ultralytics:** Ultralytics is AGPL-3.0. Using it as a dependency is fine; if you distribute a modified version of *their* library you must share it. For permissive licensing, swap in OpenCV's `cv2.CascadeClassifier` or MediaPipe (Apache-2.0).

---

## 🧪 Tests

```bash
pytest tests/ -v
```

Covers highlight detection, subtitle generation, NLE exports, aspect-ratio
reframing, silence detection, profanity filtering, logging, and the API token guard.

---

## 📦 Packaging

See [docs/dev/PACKAGING.md](docs/dev/PACKAGING.md) for building desktop installers
and the notes on bundling a Python runtime.

---

## 🗺️ Roadmap

**Done**
- [x] Local transcription + word timestamps
- [x] Heuristic + audio-energy highlight detection
- [x] Speaker-aware 9:16 smart crop
- [x] Caption export (SRT/VTT/ASS) + burn-in
- [x] Interactive caption editor
- [x] Manual clip trimmer UI
- [x] Gaming / reaction layouts
- [x] AI edit chat (Ollama + fallback, Ollama optional)
- [x] NLE exports (Premiere / DaVinci / CapCut)
- [x] Silence cutter + profanity filter
- [x] Electron desktop app (Win/macOS/Linux)
- [x] Token-authenticated local API

**Coming soon**
- [ ] URL / stream import — YouTube, Twitch, Kick (`yt-dlp`)
- [ ] **Apple Silicon + Intel Mac acceleration** — hardware-aware model selection
  (the app runs its own server and downloads the model that best fits your chip)
- [ ] **MLX acceleration** on Apple Silicon (M-series) — optional, auto-selected
  when available; Ollama stays optional
- [ ] Multi-speaker split-screen
- [ ] Multilingual subtitles / dubbing
- [ ] Auto-posting to TikTok / YouTube / Reels

---

## 📄 License

Licensed under the **TechFreq Developments Open-Attribution License**. Free to use
and modify, provided credit is given to **TechFreq Developments** as the original
author. See [LICENSE](LICENSE) for full details.

**Third-party notices:** Whisper (MIT), FFmpeg (LGPL/GPL), Ollama (MIT), ultralytics (AGPL-3.0).
