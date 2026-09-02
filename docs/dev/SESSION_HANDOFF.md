# Session handoff — resume notes (Windows, Sept 2026)

Quick-start context so a fresh session can pick up without re-deriving everything.

## Environment (this Windows machine)
- Hardware: RTX 3060 12GB, Ryzen 9 5900XT (16-core / 32 threads), 63.9GB RAM.
- App venv: `.venv` (Python 3.13.14), created by `start_klipzy.bat`. Full deps installed
  (torch **2.14.0+cu126 — CUDA build**, torchvision cu126, faster-whisper, ultralytics,
  librosa, ollama, fastapi, etc.). `torch.cuda.is_available() == True`.
- ffmpeg/ffprobe at `c:\ffmpeg\bin`.
- Ollama models: qwen2.5:14b, qwen2.5:7b, gemma2:2b, llama3:latest. Default = **qwen2.5:7b**.
- Launch: double-click `start_klipzy.bat` (self-heals a foreign/copied venv now).
- Tests: `114→115` pass. pytest collection chokes walking up to the Downloads folder
  (a stale path) — run scoped: `.venv\Scripts\python.exe -m pytest tests -o addopts="" --rootdir .`.
  Heavy commands sometimes get ^C'd in nested shells — run long jobs as background processes.

## What was done this session (commits on master)
- Fixed the LLM clip-ranking merge (models return a single JSON object, not an array;
  added `_coerce_rankings`). Default LLM → qwen2.5:7b on 12GB GPUs. Guarded faster-whisper VAD.
  Added psutil to requirements.
- Clips-Kitty-inspired UI: live resource footer (`/api/setup/resources`), output-mode presets,
  Activity Log lifecycle events, model-catalog transparency (tested/license badges).
- Launcher self-heals a venv copied from another OS (macOS → Windows).
- Output folder now REROUTES rendered clips there (`_sync_output_root`); `./output` is temp scratch.
- Hardware-aware GPU Acceleration card: all vendors (CUDA/ROCm/DirectML/Intel-XPU/Apple-MPS) +
  MLX/faster-whisper transcription line; experimental paths labeled community-supported +
  "report feedback" link. **Fix:** enable used `--force-reinstall --no-deps` (pip was skipping the
  swap because torch was "already satisfied").
- Large Whisper options (large-v3 / large-v2) in Clipping Options + Settings recommendations.
- Generic New Project placeholder ("My podcast — episode 1").
- AI Models UI: in-use banner + accent highlight on the active card, delete button beside the
  action (right), selected-recommendation highlight.

## Key files
- Backend: `server/api/server.py` (endpoints, job store, output routing),
  `server/core/system_check.py` (hardware detect, model catalog, `gpu_acceleration_status`,
  `_pytorch_accel_plan`, `live_resources`, whisper choices), `server/core/pipeline.py`,
  `server/core/transcriber.py` (VAD guard + KLIPZY_DISABLE_VAD).
- Frontend: `ui/index.html`, `ui/src/renderer.js` (renderModelCatalog, renderRecommendations,
  loadGpuAcceleration, buildProcessPayload, collectCaptionOptions, applyPortraitCaptionPreviewStyle),
  `ui/src/styles.css`.

## Notes / answers
- **VRAM stays high after a render — normal, not a leak.** (1) Ollama keeps the LLM resident
  ~5min (keep_alive). (2) PyTorch's CUDA allocator caches freed GPU memory rather than returning
  it to the OS. Releases on idle / app close.
- CUDA torch is CPU-swappable via the GPU card (Revert to CPU) — commands shown there.

## PENDING UI work (this session's asks — in progress / to finish)
1. **Intro-hook controls in Clipping Options (step-2).** Today the step-2 caption area only has
   intro TEXT + enable + duration; the intro FONT SIZE + its own PREVIEW only exist in the per-clip
   Caption Editor (step-4 modal, id `intro-hook-font-size`). Add to step-2:
   - an intro font-size slider (new id, e.g. `generated-intro-font-size` — do NOT reuse
     `intro-hook-font-size` to avoid duplicate IDs),
   - a preview of the intro hook (top-center, bigger font) inside the existing phone frame
     (`portrait-preview-screen`), shown when intro is enabled,
   - wire it into `collectCaptionOptions()` (currently reads `intro-hook-font-size` → make it read
     the step-2 id there), and into the live preview updaters.
2. **Clipping Options buttons / general UI polish** — cleaner button styling & layout across the
   options panel (the `.step-actions`, `.options-grid`, toggles). Make it look more finished.
3. General "make interfaces cleaner" pass per user feedback.
