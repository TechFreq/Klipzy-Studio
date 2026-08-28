# 🎬 Klipzy Studio

A **local-first, open-source AI video clipper by TechFreq** for Windows and macOS. Turn long videos, podcasts, and streams into ready-to-post vertical Shorts — entirely on your own PC. **No cloud AI, no fees.**

> ⚖️ **100% original code.** This project is inspired by the *features* of similar open-source tools but is built from scratch with its own architecture, UI, and implementation. It is licensed under **MIT** (you can use, modify, and distribute it freely).

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🎯 **AI Virality & Hook Detection** | Finds the most engaging moments with Virality Scores, Hook strength, and Trend breakdowns |
| ⚡ **Hardware Acceleration** | Auto GPU video encoding acceleration (NVIDIA NVENC, Apple Silicon VideoToolbox, CPU x264 fallback) |
| 🎨 **OpusClip-Style Captions** | Word-by-word active karaoke highlights (`.ass` format) with dynamic color styling |
| ✏️ **Interactive Caption Editor** | Live word timestamp adjusting and subtitle customization |
| 🎬 **NLE Project Exports** | Export timeline directly to **Adobe Premiere Pro** (XML), **DaVinci Resolve** (EDL), or **CapCut** (Draft) |
| 🔊 **Audio Energy Detection** | Detects excitement spikes and loudness peaks to catch dramatic moments |
| 🤖 **Optional LLM Discovery** | Uses local Ollama (Gemma) to pick viral moments from the transcript |
| 🗣️ **Speaker-Aware Face Tracking** | Tracks the active speaker so 9:16 vertical crops stay centered on them |
| 💬 **AI Edit Chat** | Chat with local AI for hook ideas, captions, hashtags, and edit adjustments |
| 📦 **100% Local & Private** | Everything runs on your machine — Whisper, YOLO, Ollama, FFmpeg — no cloud fees |

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────┐
│  Electron Desktop App (UI)                      │
│  - Windows / macOS / Linux                      │
│  - Drag & drop, options, progress, previews     │
└────────────────────┬────────────────────────────┘
                     │ localhost HTTP
┌────────────────────▼────────────────────────────┐
│  Python FastAPI Server                          │
│  - Job queue & progress polling                 │
│  - AI Edit Chat                                 │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│  Processing Pipeline                            │
│  FFmpeg → Whisper → Highlights → Face Track →   │
│  Render 9:16 + captions                         │
└─────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### Prerequisites

1. **Python 3.10+**
2. **FFmpeg**
   - Windows: `winget install Gyan.FFmpeg`
   - macOS: `brew install ffmpeg`
3. **Node.js 18+** (for Electron UI — optional, server works standalone)

### 1. Python Server (core engine)

```bash
python -m venv venv

# Windows
.\venv\Scripts\activate
# macOS/Linux
source venv/bin/activate

pip install -r requirements.txt

# Start the API server
python -m server.api.server
```

Then open `http://127.0.0.1:8765/docs` for the API docs.

### 2. Electron Desktop App (optional)

```bash
cd ui
npm install
npm start
```

### 3. One-click launchers

- **Windows**: `scripts\run_windows.bat`
- **macOS**: `scripts\run_macos.sh`

---

## 🧠 AI Components

| Component | Tool | License | Purpose |
|-----------|------|---------|---------|
| Transcription | OpenAI **Whisper** | MIT | Speech → text with word timestamps |
| Face tracking | **YOLOv8** (ultralytics) | AGPL-3.0 | Speaker-aware 9:16 crop |
| Local LLM | **Ollama** + Gemma | MIT | AI edit chat + highlight discovery |
| Video processing | **FFmpeg** | LGPL/GPL | Extract, cut, crop, burn captions |

> **Note on YOLO/ultralytics**: Ultralytics is AGPL-3.0. If you distribute a modified version of *their* library code you must share it — using it as a dependency is fine. If you prefer permissive licensing, swap in OpenCV's built-in face detector (`cv2.CascadeClassifier`) or MediaPipe (Apache-2.0).

---

## 🗺️ Roadmap (feature parity with commercial tools)

- [x] Local transcription + word timestamps
- [x] Heuristic + audio-energy highlight detection
- [x] Speaker-aware 9:16 smart crop
- [x] Caption export (SRT/VTT) + burn-in
- [x] AI edit chat (Ollama + fallback)
- [x] Electron desktop app (Win/macOS)
- [ ] Twitch/Kick stream import
- [ ] Manual clip trimmer UI
- [ ] Gaming/reaction layouts
- [ ] Auto-posting to TikTok/YouTube

---

## 📄 License

This project is **MIT licensed** — free to use, modify, and distribute, including commercially. See [LICENSE](LICENSE).

**Third-party notices:** Whisper (MIT), FFmpeg (LGPL/GPL), Ollama (MIT), ultralytics (AGPL-3.0).
