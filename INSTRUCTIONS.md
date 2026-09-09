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
5. [Choosing Models & AI Engines](#5-choosing-models--ai-engines)
   - [Which Whisper model?](#which-whisper-model)
   - [Which local LLM?](#which-local-llm)
   - [AI engine: use your own server](#ai-engine-use-your-own-server)
   - [Subtitle engine: use your own server](#subtitle-engine-use-your-own-server)
6. [Using the REST API & Headless Mode](#6-using-the-rest-api--headless-mode)
7. [Running Tests & Quality Checks](#7-running-tests--quality-checks)
8. [Building Desktop Installers](#8-building-desktop-installers)
9. [Troubleshooting & FAQs](#9-troubleshooting--faqs)

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
git clone https://github.com/TechFreq/Klipzy-Studio.git
cd Klipzy-Studio
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

> **Windows: extract the ZIP first.** Don't run the launcher from inside a ZIP viewer. If Windows
> blocks it, right-click the ZIP → **Properties** → tick **Unblock** before extracting, or see
> [§9](#q-windows-11-blocked-start_klipzybat-from-running).

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

## 5. Choosing Models & AI Engines

**Short version: you don't have to choose.** Open **Setup**, and Klipzy inspects your CPU, RAM
and GPU and marks the best fit with a ⭐. Nothing downloads without you clicking.

The rest of this section is for when you want to decide yourself, or your footage needs
something different from the default.

### Which Whisper model?

Whisper turns speech into the words your captions are built from, so this is the single setting
with the biggest effect on output quality. Set it in **Setup → Whisper size** (applies to the
next clipping run).

| Model | Good for | Needs | Speed |
|---|---|---|---|
| `tiny` | Quick tests, very weak machines | <16GB RAM | Fastest |
| `base` | Clean studio/podcast audio, one speaker | 16GB RAM (CPU) | ~1-3x realtime |
| `small` | **Most people's sweet spot** | 4GB+ VRAM | ~3-6x realtime |
| `medium` | Noisy audio, crosstalk, accents | 8GB+ VRAM | ~4-8x realtime |
| `large-v3` | Long or difficult media, max accuracy | Strong GPU (or patience) | Slowest |

**Pick by your audio, not just your hardware.** This matters more than people expect:

- **Quiet room, one person, good mic** → `base` is genuinely fine.
- **Street interviews, events, crowds, background music, several people talking over each
  other** → go to `small` or `medium`. Tested on real outdoor interview footage, `base` produced
  garbled repeated words ("I'm playing Play, play Play, play"). The bigger model fixes that.
  Since your captions *are* the product, the extra minutes are worth it.
- On **Apple Silicon**, install `mlx-whisper` for native acceleration — Klipzy picks it
  automatically when present.
- On **NVIDIA**, if Setup says **"CPU (GPU idle)"**, your PyTorch is CPU-only. Install the CUDA
  build from Setup → GPU Acceleration to unlock real speed.

### Which local LLM?

This one is **optional**. It writes hooks, titles and descriptions, powers AI Edit Chat, and can
pick highlights. With it off, Klipzy uses built-in keyword/audio heuristics and still works.

Browse **Setup → Local AI model catalog**; the ⭐ is the pick for your machine.

| Tier | Models | Comfortable with |
|---|---|---|
| **Light** | `gemma2:2b`, `llama3.2:3b`, `qwen2.5:3b`, `phi3:mini` | 8GB RAM |
| **Quality** | `gemma2:9b`, `llama3.1:8b`, `qwen2.5:7b`, `mistral:7b` | 16GB RAM |
| **Flagship** | `phi4`, `qwen3:14b`, `deepseek-r1:14b` | 16GB RAM + 12GB VRAM |
| **Flagship XL** | `gpt-oss:20b` | 24GB RAM + 16GB VRAM |

Rules of thumb:

- **8GB RAM / no real GPU** → `gemma2:2b`. Fast and perfectly good for short hooks.
- **16GB RAM** → `qwen2.5:7b` or `llama3.1:8b` for noticeably sharper copy.
- **12GB VRAM (e.g. RTX 3060)** → `phi4` is the sweet spot: near-flagship quality that still
  fits on the card.
- **Reasoning models** like `deepseek-r1:14b` work but are overkill for a 10-word hook; they
  spend effort "thinking" you won't see.

> **First AI request of a session is slow.** The model has to load into memory. Later requests
> hit the warm model and feel instant — this is normal, not a hang.

### AI engine: use your own server

Prefer your own setup over the built-in Ollama? **Setup → AI engine → Custom
(OpenAI-compatible)**. One address covers all of these, because they share the same API:

| Server | Typical address |
|---|---|
| LM Studio | `http://localhost:1234/v1` |
| llama.cpp (`llama-server`) | `http://localhost:8080/v1` |
| vLLM | `http://localhost:8000/v1` |
| Ollama's OpenAI route | `http://localhost:11434/v1` |
| Cloud provider | their base URL + an API key |

Paste the address (with or without `/v1`), add the model name your server uses, then click
**Test connection** before saving. API keys are stored locally and never displayed again.

### Subtitle engine: use your own server

Same idea for speech-to-text. **Setup → Subtitle engine → Custom**:

| Server | Typical address |
|---|---|
| whisper.cpp (`whisper-server`) | `http://localhost:8080/v1` |
| faster-whisper-server / Speaches | `http://localhost:8000/v1` |
| OpenAI | `https://api.openai.com/v1` + API key |

One caveat worth understanding: karaoke captions need to know when **each word** is spoken. Some
servers return that, others only return whole lines. When word timings are missing, Klipzy
spreads the words evenly across each line — it still animates, but it won't match speech exactly.
**Test connection** tells you which you're getting. If the server stops responding mid-run,
Klipzy falls back to local Whisper automatically instead of failing the job.

> **Local-first stays true.** Both engines default to your own machine. Nothing leaves it unless
> you deliberately enter a cloud address.

---

## 6. Using the REST API & Headless Mode

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

## 7. Running Tests & Quality Checks

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

## 8. Building Desktop Installers

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

## 9. Troubleshooting & FAQs

### Q: Windows 11 blocked `start_klipzy.bat` from running
**A:** That's Windows protecting you from an unsigned script downloaded from the internet, not a
fault in Klipzy. Two different guards can fire:

- **"Windows protected your PC" (SmartScreen)** → click **More info** → **Run anyway**.
- **Smart App Control** (on by default on some new Windows 11 PCs) blocks unsigned scripts with
  no "run anyway" option. Either:
  1. Right-click the downloaded ZIP → **Properties** → tick **Unblock** → OK, then extract it
     again and run the launcher; or
  2. Skip the launcher and run the [manual steps](#option-b-manual-launch-two-terminals); or
  3. Turn Smart App Control off in **Windows Security → App & browser control → Smart App
     Control** — note this is a one-way switch, it can't be re-enabled without resetting Windows,
     so try options 1 and 2 first.

Extracting the ZIP **before** running anything also helps: launching from inside a ZIP viewer,
or from a OneDrive-synced folder, causes unrelated path failures.

### Q: "Electron failed to install correctly" when the app tries to start
**A:** npm 12 (July 2026) stopped running dependency install scripts by default, as
supply-chain hardening. Electron's install script is the part that **downloads Electron itself**,
so `npm install` prints "added 310 packages" and succeeds while leaving Electron unusable.

Current versions of Klipzy declare the needed approval in `ui/package.json`, so a fresh install
works. If you already have a broken copy, repair it with:

```bash
cd ui
npm install-scripts approve electron
npm rebuild electron
```

**`npm install` on its own will not fix it** — npm considers the tree complete, prints
"up to date", and skips the download step. `npm rebuild` is what forces it. (Verified: after a
blocked install, `npm install` left it broken and `npm rebuild electron` repaired it.)

Failing that, delete `ui\node_modules` entirely and run the launcher again.

### Q: npm printed deprecation warnings and "10 vulnerabilities". Is that a problem?
**A:** Those come from transitive dependencies of the build tooling (`electron-builder`), not from
Klipzy's own code, and they don't affect a local install. Don't run `npm audit fix --force` — it
will happily upgrade `electron-builder` across a major version and break packaging. They're on
the maintenance list.

### Q: Why is FFmpeg not found?
**A:** Ensure `ffmpeg` and `ffprobe` are in your operating system's PATH. You can verify this by opening a terminal and running `ffmpeg -version`.

### Q: Why is AI transcription taking a long time?
**A:** Two common causes.

1. **Your GPU isn't actually being used.** Check Setup — if it says **"CPU (GPU idle)"**, you
   have an NVIDIA card but CPU-only PyTorch. Install the CUDA build (Setup → GPU Acceleration,
   or manually):
   ```bash
   pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
   ```
2. **The Whisper model is bigger than your hardware wants.** `large-v3` on CPU is genuinely
   slow. See [§5](#which-whisper-model) for picking a size.

### Q: My captions have wrong or repeated words. Why?
**A:** That's transcription accuracy, not a caption bug — the captions faithfully show whatever
Whisper heard. Noisy audio (street interviews, crowds, music, people talking over each other)
overwhelms the smaller models. Move up to `small` or `medium` in Setup and re-run. See
[§5](#which-whisper-model).

### Q: Why is the first AI hook/title slow, then fast after?
**A:** The first request loads the model into memory; later ones reuse it. Expected behaviour.

### Q: Why does the AI Edit Chat say Ollama is unavailable?
**A:** Klipzy falls back to rules-based advice whenever no AI engine is reachable, so nothing
breaks. To enable the local LLM:
1. Install Ollama from [ollama.com](https://ollama.com/).
2. Pull a model — `ollama pull gemma2:2b` (or use Setup → Local AI model catalog).
3. Reopen Setup; the status should turn green.

Already running something else (LM Studio, `llama-server`, a cloud endpoint)? You don't need
Ollama at all — point Klipzy at it via **Setup → AI engine**. See [§5](#ai-engine-use-your-own-server).

### Q: Do I have to use Ollama / can I use whisper.cpp?
**A:** No, and yes. Both AI features are pluggable: **Setup → AI engine** for text generation and
**Setup → Subtitle engine** for speech-to-text. Anything speaking the OpenAI API works, including
whisper.cpp's `whisper-server`. See [§5](#5-choosing-models--ai-engines).

### Q: Some clips show "–" instead of Hook / Flow / Trend scores. Is that broken?
**A:** No — that's deliberate honesty. Only the keyword detector analyses hook wording, so clips
found by audio-energy or gameplay-action detection genuinely have no hook score to report. A dash
means "not measured" rather than showing you a number the app never calculated.

### Q: Where are processed clips saved?
**A:** In the app's `output/` folder by default, or whichever folder you pick under **Output
Folder**. Individual exports always ask where to save. Rendered clips, subtitles (SRT/VTT/ASS),
thumbnails and audio stems land together.

---

*Enjoy creating viral shorts with Klipzy Studio!*
