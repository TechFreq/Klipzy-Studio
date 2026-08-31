# 🎬 Klipzy Studio — Complete Instructions & User Guide

Welcome to **Klipzy Studio**, a 100% local-first, privacy-focused AI video clipping and formatting studio. This guide provides comprehensive instructions for installing, configuring, running, using, and developing Klipzy Studio.

---

## Table of Contents

1. [System Prerequisites](#1-system-prerequisites)
2. [Installation & Setup](#2-installation--setup)
3. [Running Klipzy Studio](#3-running-klipzy-studio)
4. [Using the Studio (Workflow Guide)](#4-using-the-studio-workflow-guide)
   - [Reframing & Aspect Ratios](#reframing--aspect-ratios)
   - [Gaming & Reaction PiP Mode](#gaming--reaction-pip-mode)
   - [Caption Presets & Styling](#caption-presets--styling)
   - [Silence & Dead-Air Auto-Cutter](#silence--dead-air-auto-cutter)
   - [Word-Level Profanity Filter & Bleeper](#word-level-profanity-filter--bleeper)
   - [Interactive Caption Editor](#interactive-caption-editor)
   - [Standalone Asset & NLE Project Exports](#standalone-asset--nle-project-exports)
5. [Using the REST API & Headless Mode](#5-using-the-rest-api--headless-mode)
6. [Running Tests & Quality Checks](#6-running-tests--quality-checks)
7. [Building Desktop Installers](#7-building-desktop-installers)
8. [Troubleshooting & FAQs](#8-troubleshooting--faqs)

---

## 1. System Prerequisites

Before running Klipzy Studio, ensure you have the following installed on your machine:

| Component | Required Version | Purpose |
|---|---|---|
| **Python** | 3.10, 3.11, 3.12, or 3.13 | Core processing pipeline & FastAPI server |
| **FFmpeg** | 5.0+ (with `ffprobe` & `libass`) | Video transcoding, audio analysis, filter rendering |
| **Node.js & npm** | Node 18+ (LTS recommended) | Electron Desktop UI |
| **Ollama** *(Optional)* | Latest | Local LLM intelligence & edit chat (`gemma:2b` or `mistral`) |
| **GPU / CUDA** *(Optional)* | NVIDIA NVENC / Apple Silicon VideoToolbox | Hardware-accelerated video rendering & Whisper |

### Installing FFmpeg

- **Windows:**
  ```powershell
  winget install Gyan.FFmpeg
  ```
  *(Or download from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) and add its `bin` folder to your Windows system `PATH`.)*

- **macOS:**
  ```bash
  brew install ffmpeg
  ```

- **Linux (Ubuntu/Debian):**
  ```bash
  sudo apt update && sudo apt install -y ffmpeg libass-dev
  ```

---

## 2. Installation & Setup

### Step 1: Clone the repository
```bash
git clone https://github.com/TechFreq/clippy-studio.git
cd clippy-studio
```

### Step 2: Set up Python virtual environment
```bash
# Create the virtual environment
python -m venv .venv

# Activate it:
# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
# Windows (Command Prompt):
.\.venv\Scripts\activate.bat
# macOS / Linux:
source .venv/bin/activate

# Install dependencies:
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 3: Install Electron UI dependencies
```bash
cd ui
npm install
cd ..
```

---

## 3. Running Klipzy Studio

### Option A: One-Click Launchers (Recommended)

- **Windows:** Double-click `scripts\run_windows.bat`
- **macOS:** Run `./scripts/run_macos.sh`

### Option B: Manual Launch (Two Terminals)

**Terminal 1 — Start Python FastAPI Backend:**
```bash
# Ensure your virtual environment is active
python scripts/main.py
```
*The backend starts at `http://127.0.0.1:8765`.*

**Terminal 2 — Start Electron Desktop App:**
```bash
cd ui
npm start
```
*The desktop window connects to the backend on `http://127.0.0.1:8765`. It sends a per-launch API token automatically (see the security note in the README).*

---

## 4. Using the Studio (Workflow Guide)

### 1. Ingesting Video
- Drag and drop any video file (`.mp4`, `.mov`, `.mkv`, `.avi`, `.webm`, `.flv`) into the Klipzy Studio window, or click **"Browse Video"**.
- The video will automatically load in the player with duration, resolution, and audio waveform metrics.

### 2. Reframing & Aspect Ratios
Select the output dimension suited to your destination platform:
- **`9:16` Vertical:** Optimized for TikTok, YouTube Shorts, and Instagram Reels (uses YOLOv8 speaker tracking or center-crop).
- **`4:5` Portrait:** Ideal for Instagram feed posts and LinkedIn video.
- **`1:1` Square:** Ideal for Twitter/X and Instagram carousel posts.
- **`16:9` Landscape:** Keeps widescreen framing with highlights cut.
- **`original`:** Preserves source input dimensions untouched.

### 3. Gaming & Reaction PiP Mode
For game streams, tutorials, or reaction videos:
- Toggle **Gaming / Reaction Layout** in the pipeline settings.
- Select your **Webcam Position**: `top-left`, `top-right`, `bottom-left`, or `bottom-right`.
- Set your **Webcam Scale** (default 32% of frame).
- Klipzy will automatically split the game canvas and overlay the webcam into a stacked vertical format.

### 4. Caption Presets & Styling
Klipzy Studio comes with **22 built-in animated caption presets** (`.ass` word-by-word karaoke formatting):

- **High-Engagement & Kinetic:** `viral_yellow`, `neon_green`, `bold_white`, `hormozi_bold`, `beast_mode`, `kinetic_pop`
- **Podcasts & Conversations:** `podcast_subtle`, `minimal_clean`, `cinematic_gold`, `documentary`
- **Modern & Boxed Styles:** `boxed_inverted`, `cyberpunk`, `retro_vaporwave`, `soft_aesthetic`
- **Gamer & Streamer Styles:** `twitch_purple`, `gradient_sunset`, `comic_book`

You can also customize the primary highlight color, font size, stroke outline width, and line placement directly from the UI.

### 5. Silence & Dead-Air Auto-Cutter
- Enable **"Remove Dead Air"** to activate automated silence truncation.
- Customize the **Silence Threshold** (e.g., `-32 dB`) and **Minimum Silence Duration** (e.g., `0.5s`).
- Klipzy analyzes speech boundaries and creates jump-cuts to keep the energy high.

### 6. Word-Level Profanity Filter & Bleeper
- Enable **"Censor Profanities"** to automatically sanitize output.
- Choose your **Censorship Mode**:
  - **`bleep`:** Overlays a standard broadcast 1000 Hz sine-wave tone over flagged words.
  - **`mute`:** Silences the audio track strictly during the offending word timestamps.
  - **`mask_only`:** Keeps audio untouched but censors the visual captions (e.g., `f***`).

### 7. Interactive Caption Editor
- Click any generated clip to open the **Caption Editor**.
- Click individual words to adjust their start/end timestamps or correct spelling.
- Real-time preview shows updated styles and alignments immediately.

### 8. Standalone Asset & NLE Project Exports
In the Clip Detail / Export panel, you can generate:
- **Audio Stems:** Export audio in `MP3`, `WAV`, `FLAC`, `AAC`, or `M4A` without transcoding the video.
- **Subtitles:** Download raw `.ass`, `.srt`, `.vtt`, or `.json` subtitle files.
- **NLE Timelines:**
  - **Adobe Premiere Pro:** `.xml` (Final Cut Pro 7 / Premiere XML format).
  - **DaVinci Resolve:** `.edl` (Edit Decision List with timecodes).
  - **CapCut:** `draft_content.json` (drag directly into your local CapCut projects directory).

---

## 5. Using the REST API & Headless Mode

Klipzy Studio runs as an open REST API powered by FastAPI. You can integrate Klipzy Studio into automated scripts, external tools, or custom pipelines.

- **Interactive API Documentation (Swagger UI):** `http://127.0.0.1:8765/docs`
- **Alternative Documentation (ReDoc):** `http://127.0.0.1:8765/redoc`
- **Health Check Endpoint:** `GET http://127.0.0.1:8765/health`

### Key Endpoints:
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/process` | Submit video path and pipeline configuration options to process clips |
| `GET` | `/api/status/{job_id}` | Poll background processing job status and progress percentage |
| `GET` | `/api/presets` | Get metadata for all 22 caption presets |
| `POST` | `/api/regenerate-subtitles` | Re-burn or restyle subtitles on an existing clip |
| `POST` | `/api/export-audio` | Extract audio in any supported format |
| `POST` | `/api/export-nle` | Generate Premiere XML, DaVinci EDL, or CapCut Draft files |
| `POST` | `/api/chat` | Send a prompt to the local AI assistant for hook and title ideas |

---

## 6. Running Tests & Quality Checks

Klipzy Studio includes a comprehensive unit test suite covering highlight detection, subtitle generation, NLE exports, aspect ratio reframing, silence detection, and profanity filtering.

### Run tests:
```bash
# Run the test suite
pytest tests/ -v

# Run with stdout output enabled
pytest tests/ -v -s
```

### Validate Python syntax:
```bash
python -c "import py_compile, glob; [py_compile.compile(f, doraise=True) for f in glob.glob('**/*.py', recursive=True) if '.venv' not in f and 'node_modules' not in f]; print('All files valid!')"
```

### Validate Electron / JS syntax:
```bash
node --check ui/src/renderer.js
node --check ui/electron/main.js
```

---

## 7. Building Desktop Installers

To package Klipzy Studio into a standalone desktop application executable:

```bash
cd ui

# Windows (.exe installer via NSIS):
npm run dist:win

# macOS (.dmg installer):
npm run dist:mac

# Linux (.AppImage):
npm run dist:linux
```

The compiled binaries will be output to the `ui/dist/` directory.

---

## 8. Troubleshooting & FAQs

### Q: Why is FFmpeg not found?
**A:** Ensure `ffmpeg` and `ffprobe` are in your operating system's PATH. You can verify this by opening a terminal and running `ffmpeg -version`.

### Q: Why is AI transcription taking a long time?
**A:** By default, OpenAI Whisper runs on CPU if CUDA is not detected. If you have an NVIDIA GPU, make sure you have installed PyTorch with CUDA support:
```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
```

### Q: Why does the AI Edit Chat say Ollama is unavailable?
**A:** Klipzy Studio will automatically use a rules-based fallback if Ollama is not installed or running. To enable local LLM chat:
1. Install Ollama from [ollama.com](https://ollama.com/).
2. Run `ollama run gemma:2b` or `ollama run mistral`.
3. Restart Klipzy Studio.

### Q: Where are processed clips saved?
**A:** Output clips, subtitle tracks, and audio stems are saved in your system temporary folder or the custom output directory selected in the UI under **Output Folder**.

---

*Enjoy creating viral shorts with Klipzy Studio!*
