# Portable Windows build — status & decision notes

Status: **not decided yet** — parked for a later, deliberate session. This doc
captures what the `ui/dist/ClippyStudio-win-portable.zip` artifact actually is,
why it's confusing today, and the concrete options for making a real
"double-click and go" portable Windows version.

## What exists today

`npm run dist:win` (electron-builder, see `docs/dev/PACKAGING.md`) produces in `ui/dist/`:

- `win-unpacked/` — the unpacked Electron app (the `.exe` + resources).
- `ClippyStudio-win-portable.zip` — a zipped, no-installer build of the app.

So the portable zip **is a real build of our own app** (not CapCut, not
clutter). "Portable" = unzip and run the `.exe`, no NSIS installer, no writes to
Program Files.

## Why it's not truly portable *yet*

The Electron bundle deliberately does **not** include a Python interpreter
(see PACKAGING.md → "Why the virtual environment is not bundled"). At runtime
`ui/electron/main.js → findPython()` looks for a dev venv, then a packaged
`venv`, then system `python`. So today the portable zip still requires the user
to already have **Python 3.10+** installed and to have run
`pip install -r requirements.txt` once.

That's fine for us (the developer), but a normal user double-clicking the `.exe`
with no Python will hit the "backend cannot start" state. Hence the confusion:
it looks like a finished portable app but has an invisible dependency.

## Options to make it genuinely self-contained

Ranked by how well they fit a downloadable desktop app:

1. **PyInstaller (recommended).** Freeze `scripts/main.py` + `server/` into a
   standalone backend `.exe`, ship it in `extraResources`, and point
   `findPython()` at it. Clean one-folder/one-file result, no user Python.
   Needs a Windows build runner. This is the usual choice.
2. **Embeddable / python-build-standalone.** Bundle a relocatable interpreter and
   `pip install` into it at build time. More moving parts than PyInstaller.
3. **First-run bootstrap.** On first launch, create a venv under Electron
   `userData` and `pip install -r requirements.txt`. Small installer, but the
   first run is slow and needs network access.

## The real decision: lean vs heavy bundle

The dependency footprint drives the download size, so decide the scope first:

- **Lean portable (recommended default):** `faster-whisper` (CPU, CTranslate2) +
  `ollama` client + core deps. No `torch` / `ultralytics`. Transcription and LLM
  hooks/selection all work; face-tracking / active-speaker crop degrades to
  center-crop. Bundle stays modest (hundreds of MB).
- **Full portable:** add `torch` + `ultralytics` + `opencv` for GPU face
  tracking. Balloons past ~1 GB and pulls in CUDA concerns. Better as an
  **optional add-on** the user installs from Setup, not the default download.

Suggested plan when we pick this up: ship the **lean PyInstaller portable** as
the standard download, keep the heavy CV stack as an optional in-app install
(there's already `/api/setup/install-optional` for heavy deps).

## Windows gotchas already discovered (Sept 2026 testing)

These bit us during LLM testing and will bite a packaged build too — fix before
shipping a portable:

- **faster-whisper VAD** (`vad_filter=True`, ONNX Silero) intermittently stalls
  on first use (model download). Guarded now (retry-without-VAD +
  `KLIPZY_DISABLE_VAD` env var), but confirm on a clean machine.
- **`psutil`** must be present — the model recommender and the new live-resource
  footer need it to read RAM/CPU. It's now in `requirements.txt`.

## When we build it

```bash
cd ui
npm install
npm run dist:win     # NSIS installer + portable zip
```

Then (future) add a PyInstaller step for the backend and re-point `findPython()`.
Until that's done, treat `ClippyStudio-win-portable.zip` as a **dev artifact**,
not a shippable download.
