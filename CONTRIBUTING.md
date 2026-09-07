# Contributing to Klipzy Studio

Thanks for your interest in improving **Klipzy Studio** — a local-first Long Form → Shorts
studio (Electron UI + FastAPI/Python engine). This guide gets you from clone to a verified
change quickly.

New here? The end-user guide is [INSTRUCTIONS.md](INSTRUCTIONS.md) and the feature overview is
in the [README](README.md). This file is for **developers**.

---

## Guiding principles

Please keep changes aligned with what makes this project different:

- **Local-first & private.** Everything runs on the user's machine — Whisper, YOLO, Ollama,
  FFmpeg. No cloud calls, no telemetry, no accounts, no per-minute costs. Don't add features that
  phone home.
- **Degrade gracefully.** Optional/heavy capabilities (Ollama LLM, GPU/CUDA, pyannote diarization)
  must be *opt-in* and fall back cleanly when unavailable. The offline/CPU path is always the
  default and must keep working.
- **Don't download things behind the user's back.** Model downloads and heavy installs are
  surfaced in the Setup panel and initiated by the user.
- **Match the existing style.** Read nearby code first; follow the conventions and libraries already
  in use rather than introducing new ones.

---

## Quick dev setup

**Prerequisites:** Python 3.10–3.13, Node.js 18+, and FFmpeg (with `ffprobe` + `libass`) on PATH.
See INSTRUCTIONS.md §1 for per-OS FFmpeg install.

### Easiest: the one-click launcher
```bash
git clone https://github.com/TechFreq/Klipzy-Studio.git
cd Klipzy-Studio
# Windows:
start_klipzy.bat
# macOS / Linux:
./start_klipzy.command   # or ./scripts/run_macos.sh
```
The launcher creates a local `.venv`, `pip install`s `requirements.txt`, `npm install`s the UI, and
starts the desktop app. It only installs when something is missing, so it's a **one-time** setup per
clone — later launches go straight to running.

### Manual (two terminals)
```bash
python -m venv .venv
# Windows: .\.venv\Scripts\activate    |    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python scripts/main.py          # backend at http://127.0.0.1:8765

cd ui && npm install && npm start   # Electron desktop app (spawns/uses the backend)
```

> Nothing installs globally: the Python venv lives in `.venv/` and UI deps in `ui/node_modules/`,
> both inside the repo and both gitignored. A fresh clone rebuilds them on first run.

---

## Project layout

```
server/          FastAPI backend + processing pipeline
  api/server.py    endpoints, job queue, model/output routing
  core/            pipeline.py, transcriber.py, highlight_detector.py, llm_detector.py,
                   hook_writer.py, caption_styler.py, ffmpeg_tools.py, face_tracker.py, ...
  models.py        Pydantic request/response + ClipCandidate/ClipResult models
scripts/         main.py (backend entry) + launchers/helpers
ui/              Electron app — index.html, src/renderer.js, src/styles.css, electron/main.js
tests/           pytest suite (pure/testable core logic)
```

---

## Verifying a change (please run before opening a PR)

**Python tests** — the suite runs from the repo root:
```bash
pytest tests/ -q
```
> On some Windows setups pytest's collection walks up into parent folders. If that happens, scope it:
> ```
> .venv\Scripts\python.exe -m pytest tests -o addopts="" --rootdir .
> ```

**Syntax checks:**
```bash
python -m py_compile server/core/pipeline.py   # (and any .py files you touched)
node --check ui/src/renderer.js                # after editing renderer.js
```

**Housekeeping:** delete any temporary `_*` scratch scripts you created while testing.

Add or update tests when you change backend logic — prefer small **pure, testable** helpers (see how
`_snap_window`, `_apply_llm_rankings`, `speaker_coherence`, etc. are unit-tested without needing a
GPU, network, or Ollama running).

---

## Branch & PR flow

Klipzy Studio maintains **two release channels** (see the README's *Editions & Release Channels*):

- `main` — the flagship edition (built-in Ollama).
- `main-openai` — the OpenAI-compatible edition (whisper.cpp / `llama-server` / LM Studio / cloud
  via a single "base URL + model" setting).
- `develop` — active development for the flagship line; stabilized here before merging to `main`.

**Where to target your PR:**

- **General fixes / features** (captions, rendering, UI, pipeline) → branch off `develop`, e.g.
  `feat/url-import`, `fix/intro-hook-timing`. These land on `develop` → `main`, and are periodically
  carried into `main-openai` too.
- **OpenAI-compatible / de-Ollama work** → branch off `feature/whisper-openai-compat` (the
  integration branch for that edition). Once proven, it merges into `main-openai` and ships as a
  `-openai` release. Keep the LLM backend behind the same "base URL + model" abstraction rather than
  hardcoding a single provider.

Then:

- Keep commits focused; write a clear message describing the *why*, not just the *what*.
- Make sure `pytest`, `node --check`, and `py_compile` all pass locally.
- Open a PR describing the change, **which edition/branch it targets**, how you tested it, and any
  platform caveats (Windows/macOS/Linux, GPU vs CPU).
- Don't commit generated artifacts or secrets (see below).

---

## Security & things never to commit

The `.gitignore` already excludes these — please keep it that way:

- `.venv/`, `venv/`, `ui/node_modules/`, `ui/dist/` — environments & build output
- `output/` — rendered clips / working files
- `*.pt` — model weights (downloaded on first use)
- `logs/api_token.txt`, `logs/hf_token.txt`, `logs/preferred_ollama_model.txt` — runtime secrets/prefs
- `*.log`, `.DS_Store`

The local API is `127.0.0.1`-only and **token-authenticated** (`X-Klipzy-Token`). If you add
endpoints, keep them behind that auth (only `/health` and `/docs` are unauthenticated), validate
inputs, and never shell out with unsanitized user strings.

---

## License & attribution

Klipzy Studio is under the **TechFreq Developments Open-Attribution License** ([LICENSE.md](LICENSE.md)) —
free to use and modify **provided you credit TechFreq Developments** as the original author. By
contributing, you agree your contributions are licensed under the same terms.

Third-party components keep their own licenses (Whisper — MIT, FFmpeg — LGPL/GPL, Ollama — MIT,
**ultralytics/YOLO — AGPL-3.0**). If you swap YOLO for a permissive tracker (OpenCV / MediaPipe),
note it in your PR.

---

Happy clipping! 🎬
