# Klipzy Studio — Session Status & Handoff

_Last updated: 2026-08-31. Local-first "Long Form to Shorts" studio (Electron UI + FastAPI/Python engine)._

## TL;DR
The app was recovered from a near-total loss and is now genuinely working end-to-end,
verified on real footage (talking-head, iPhone HEVC, and Call-of-Duty gameplay).
**67 tests pass. Everything is committed on `master`. Nothing has been pushed to a
remote yet** (holding until Apple Silicon / MLX work is done).

## How to run / verify
```bash
# from repo root, venv active
python scripts/main.py            # backend on 127.0.0.1:8765
cd ui && npm start                # desktop app (spawns backend itself)
pytest tests/ -q                  # 67 tests
```
- Transcription auto-selects: mlx-whisper (Apple Silicon) → faster-whisper → openai-whisper.
- `/health` and the sidebar status show the active backend; click the status to open Setup.

## What works (verified on real clips)
- Full pipeline: transcribe → highlight → speaker-crop → render → burn captions → thumbnail.
- Formats: H.264 + HEVC, vertical + horizontal, 24/30/60fps → correct 9:16 output.
- **Hooks/titles without an LLM**: picks the strongest complete sentence (e.g. "All right,
  where are you headed?" on the street-interview clip). No more "Hi." fragments.
- **Active-speaker crop**: head-region motion picks the talker (unit-verified; not yet
  demonstrated on horizontal multi-person footage — need such a clip).
- **Gaming**: auto-detect gameplay+facecam → Reaction layout; and for no-speech gameplay,
  **action highlights fused from audio loudness + visual motion** (verified on CoD).
- **AMD/Intel aware**: names AMD/Intel GPUs + CPU brand; honest "CPU (GPU idle)" when a
  GPU exists but the ML stack can't use it (no false CUDA promises).
- Setup panel: hardware detection, model recommendations, per-component install,
  **Install All (with progress bar)**, **uninstall** (pip pkgs), **Clear cache** button,
  Whisper/Ollama model switcher.
- UI: per-platform export tiles (TikTok/Reels/Shorts/IG/X), "Ready" badges + results
  summary, CapCut-style Recent Projects grid. Deleting a project removes its footage.
- Security: token-authenticated local API (X-Klipzy-Token), strict CORS.

## Key facts / gotchas
- **Backend was deleted** by a bad "cleanup" commit and recovered from Cline checkpoints
  (`5c8bdc9e` + untracked `2ff3cae`). Safety refs still exist: branch
  `pre-restore-backup-2026-08-30`, tags `recovery-checkpoint-full/untracked`,
  and `_prerestore_backup/` (gitignored).
- `renderer.js` had a brace bug that trapped ~460 lines; fixed. Use a real parser
  (acorn) not brace-counting to check it — multi-line template literals fool counters.
- Install commands use `sys.executable -m pip` (bare `pip` installed into the wrong
  Python on Windows). Don't revert that.
- Model weights (`*.pt`) and `logs/api_token.txt` are gitignored.

## Not done yet (next-session backlog, roughly prioritized)
1. **Push to GitHub + add CI** (run pytest + `node --check` + an endpoint-contract check
   on every push). This is the #1 safety item — the deletion/brace bugs would've been caught.
2. ~~**Apple Silicon / MLX** real testing on the Mac~~ — **DONE (2026-08-31).** Verified on
   M-series hardware: `mlx` + `mlx_whisper` compute on the GPU (Metal), end-to-end
   transcription confirmed. Fixed the MLX model repo IDs (the bare
   `mlx-community/whisper-base` / `whisper-large-v3` names 401'd; switched all sizes to
   the `-mlx` suffixed repos). Also: on macOS the plain Homebrew `ffmpeg` ships WITHOUT
   libass — the app now installs `ffmpeg-full` and the Setup check verifies libass.
3. **Active-speaker on horizontal multi-person footage** — verify the talker-tracking on a
   real landscape interview (both current test clips are already vertical).
4. **Visual kill-feed / event detection** for shooters (beyond audio+motion) — more precise
   gameplay highlights.
5. **URL ingestion (yt-dlp)** — YouTube/Twitch/Kick (user said "not yet", but it's the main
   table-stakes gap vs competitors).
6. **Install-All / big installs** could move to a background job for a nicer UX.
7. Packaging: real installer needs PyInstaller or embeddable Python (see docs/dev/PACKAGING.md).
8. **Linux support — UNVERIFIED (roadmap).** The code paths exist but have never been run on
   a real Linux box: `scripts/run_macos.sh` doubles as the Linux launcher, install commands
   use `apt`/pip, and transcription falls back to faster-whisper (CPU) since MLX is
   Apple-only. Needs a real Linux test pass to confirm: FFmpeg **with libass** from `apt`
   (Debian/Ubuntu builds normally include it), the faster-whisper CPU path, YOLO/ultralytics,
   and that Electron launches. Until someone runs it there, treat Linux as best-effort.
   - **UNTESTED — AMD/Intel VRAM detection + Linux GPU paths.** `system_check._detect_nonnvidia_vram_gb()`
     reads Windows `qwMemorySize` (registry) and Linux AMD sysfs `mem_info_vram_total` to feed the
     model recommender for non-NVIDIA GPUs. Written WITHOUT an AMD GPU or a Linux box to test on, so
     it needs a real-hardware pass. Also note Ollama's AMD GPU acceleration depends on ROCm (Linux)
     / newer Windows builds; on unsupported setups Ollama may run on CPU regardless. Failure is
     non-fatal (falls back to the RAM-based pick).
9. **Optional AI hook rewrite (Ollama).** The intro-hook suggestions today are heuristic
   (transcript sentence ranking in `rank_hook_candidates` — no model, fully offline). Add an
   optional "✨ AI rewrite" in the caption editor's hook field that, when Ollama is installed
   AND running, punches up / rewrites the hook using the user's selected local model (from the
   existing `/api/setup/ai-model` switcher). Must degrade gracefully to the heuristic when
   Ollama is off. Keep it opt-in so the offline path stays the default.
10. **Clips-Kitty-style model manager.** Expand the Setup panel into a curated model catalog:
    several downloadable options (Whisper sizes; Ollama models e.g. gemma2:2b / llama3.2 /
    qwen2.5) with one-click download and, for each, its name + type + size + speed/quality
    tradeoff, plus a recommended pick based on BOTH hardware and the chosen preset. Foundations
    exist: `system_check.recommend_models()` already ranks Whisper/YOLO/Ollama by hardware, and
    there's an Ollama switcher (`/api/setup/ai-model`) + pull (`/api/setup/ollama/pull`); this
    item is the richer catalog UI + tying recommendations to presets.
    NOTE (current model usage, for reference): transcription = MLX-Whisper
    `mlx-community/whisper-<size>-mlx` (size auto-picked by hardware; medium seen in testing);
    face tracking = YOLO `yolov8n.pt`; optional LLM = Ollama `gemma2:2b` default. The hook
    suggester uses NO model.

## UI/UX fixes reported by user (for next session — mostly `ui/`)

> **Session update 2026-08-31:** All 16 UI/UX items below were implemented
> (`ui/renderer.js` + `index.html` + `styles.css`, plus an Electron
> `setWindowOpenHandler` so support links open in the real browser, and a new
> `/tools/suggest-moments` backend endpoint fusing audio energy + visual motion
> for the gaming "suggested moments" buttons). `node --check` clean; 67 tests
> pass. Also moved the loose `yolov8n.pt` into `models/` and pinned
> `FaceTracker` to load weights from there regardless of CWD.
>
> Notable root cause: the "Export undefined" bug was `/export/clip-bundle`
> returning `export_dir`/`video_path` while the renderer read `data.export_path`.

These are observed bugs/rough edges in the running app. Verify each against
`ui/src/renderer.js` + `ui/index.html` + `ui/src/styles.css`.

**Export / saving**
- [ ] **Export button exports "undefined"** — it should first prompt for a *save
  folder* (use the Electron folder picker / `selectOutputFolder`), then export
  the clip there. Wire the chosen path through the export request.
- [ ] Add an **"Open folder"** button next to the OK/Close button on the
  export/dialog screen (small affordance so the user can jump to the output).

**Camera position / gaming layout**
- [ ] Add a **"None"** option to the camera-position select (for when there is no
  facecam), and show/handle it for the gaming-clip path.
- [ ] When **Gaming layout** is selected, surface a few **suggested-moment buttons**
  (from the action/motion detector) so the user isn't guessing what's clippable —
  i.e. make it feel semi-automatic: "here are the detected action moments, clip these".

**Clip preview**
- [ ] **Mute icon shows wrong state**: preview starts muted but the icon doesn't
  reflect it. Show the correct (muted) state *before* the user clicks so it's an
  accurate toggle, and let audio play when unmuted.
- [ ] **Add a duration / playback seek (scrub) bar** to the clip preview.

**Wizard / navigation**
- [ ] **Weird box over the wizard step buttons** — the step-process buttons render
  with an odd box/outline (likely from the div→button change; check focus-outline
  / button default styling in styles.css). Clean it up.
- [ ] **Generated-clips page is empty with no way out** — add a clear **CTA to start
  a new project / go back to step 1** and route there. (There's a `#step4-restart-btn`
  already — verify it's wired and visible; add a prominent empty-state CTA.)
- [ ] **Step 3 spinner keeps spinning after processing is done** — stop the
  processing animation when the job completes/one navigates back to step 3.
- [ ] Add a **"Start New Project"** entry (the projects dropdown / first menu should
  offer "new project" then the rest of the steps flow from there).
- [ ] **Opening a saved project should restore its output layout** (9:16 / gaming /
  aspect it was created with) so the user isn't confused. Project manifest already
  stores captionOptions/outputFolder; also persist + restore the layout + aspect.

**Deletion sync**
- [ ] **Deleting a clip from the generated grid** should also remove it from the
  saved project (currently it only leaves the grid; the confirm text mentions
  "delete from project from grid" — make it actually delete from the project too).

**AI Edit Chat**
- [ ] **Add a loading/typing animation** while the AI is responding (chat currently
  shows nothing until the reply lands).

**Queue manager / sidebar / settings access**
- [ ] **Queue Manager UI needs a rework** — the job-queue modal is functional but
  rough; make it clearer (status, progress, per-job actions, empty state).
- [ ] **Sidebar is missing a "Support TechFreq" button/section** — add it to the
  left sidebar (the support links currently only live in the Setup view).
- [ ] **Add a Settings / manage access point** — a button (near the server
  health-check status / sidebar) to open settings for managing all this, similar
  to how **Clips Kitty** does it. User will share the Clips Kitty settings
  interface screenshots to match the pattern (like the earlier openclipper/capcut
  refs). Until then: consolidate Setup + queue + support + model management behind
  a clear settings entry point.

## Test footage on this machine
- `C:\Users\ROGPC\Downloads\Street interviews day 2 Segment 3  BEN DJ MAYO.mp4` — 9:16 talking-head, ~50s (fast hook/title tests).
- `C:\Users\ROGPC\Downloads\IMG_0499.MOV` — iPhone HEVC vertical, ~24min.
- `C:\Users\ROGPC\Videos\SteelSeries Moments\*.mp4` — Call-of-Duty gameplay, 1080p60 (action-highlight tests).
- `D:\@TechFreq Data\...\Converted Clips for TikTok-YouTube Shorts...` and `...\PRODUCTION` — more real clips (not yet used).

## Commit trail (this work, newest first)
`8c5ccb0` motion+audio fusion · `1c57c56` action highlights + AMD + cache/delete ·
`7de4194` platform tiles/grid · `50ed92e` gaming auto-detect · `a834a27` active speaker ·
`2e3754b` hooks/titles · `66be3a8` docs · `83c7686` transcribe+recommend fixes ·
(earlier) restore + brace fix + security + launchers + packaging + a11y + docs.
