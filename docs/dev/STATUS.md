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
2. **Apple Silicon / MLX** real testing on the Mac (code is in place; unproven on hardware).
3. **Active-speaker on horizontal multi-person footage** — verify the talker-tracking on a
   real landscape interview (both current test clips are already vertical).
4. **Visual kill-feed / event detection** for shooters (beyond audio+motion) — more precise
   gameplay highlights.
5. **URL ingestion (yt-dlp)** — YouTube/Twitch/Kick (user said "not yet", but it's the main
   table-stakes gap vs competitors).
6. **Install-All / big installs** could move to a background job for a nicer UX.
7. Packaging: real installer needs PyInstaller or embeddable Python (see docs/dev/PACKAGING.md).

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
