# Session handoff — resume notes (Windows, Sept 2026)

Quick-start context so a fresh session can pick up without re-deriving everything.

## Environment (this Windows machine)
- Hardware: RTX 3060 12GB, Ryzen 9 5900XT (16-core / 32 threads), 63.9GB RAM.
- App venv: `.venv` (Python 3.13.14), created by `start_klipzy.bat`. Full deps installed
  (torch **2.14.0+cu126 — CUDA build**, torchvision cu126, faster-whisper, ultralytics,
  librosa, ollama, fastapi, etc.). `torch.cuda.is_available() == True`.
- ffmpeg/ffprobe at `c:\ffmpeg\bin`.
- Ollama models: qwen2.5:14b, qwen2.5:7b, gemma2:2b, llama3:latest. Default = **qwen2.5:7b**
  (auto-resolved; now persisted — see below).
- Launch: double-click `start_klipzy.bat` (self-heals a foreign/copied venv now).
- Tests: **115 pass.** pytest collection chokes walking up to the Downloads folder (a stale
  path) — run scoped: `.venv\Scripts\python.exe -m pytest tests -o addopts="" --rootdir .`.
  Heavy commands sometimes get ^C'd in nested shells — run long jobs as background processes.
- Verify edits: `node --check ui\src\renderer.js` and `py_compile` for Python; clean up any
  temp `_*` scripts after.

## Dependency / install model (how "fresh" installs behave — for packaging & GitHub)
- **Everything installs LOCALLY into the project folder, once.** `start_klipzy.bat`:
  1. creates `.venv` in the repo root and `pip install -r requirements.txt` into it — only if
     `import fastapi, uvicorn, whisper` fails (so it's a **one-time** install per folder);
  2. `npm install` into `ui/node_modules` — only if missing / built for another OS.
- On later launches both checks pass and it goes straight to running. You do **not** reinstall
  every launch.
- A fresh `git clone` has **no** `.venv` / `node_modules` / models (all gitignored), so the first
  `start_klipzy.bat` run rebuilds them locally. That's the intended "fresh thing" behaviour.
- Model weights: `models/yolov8n.pt` (gitignored `*.pt`) downloads on first face-track use;
  Whisper models download to the faster-whisper/HF cache in the user profile; **Ollama models
  live in Ollama's own store** (managed by Ollama, not this folder). None of these are committed.
- The venv is **not relocatable** and is never bundled (see PACKAGING.md). Copying the folder to
  another machine still triggers a rebuild on first launch (the launcher detects a foreign venv).

## What was done THIS session (2026-09-02 continued) — all on `master`, local commit only
UI polish + AI copy quality + several real bug fixes. `node --check` clean, `py_compile` clean,
**115/115 tests pass**. No secrets/artifacts tracked. NOT pushed to any remote.

1. **UI polish pass (foundation + step-2 Clipping Options).** Stronger button system (`inline-flex`,
   hover/active/`:focus-visible`, primary glow); de-duped the two conflicting `.step-actions` /
   `.panel-header-row` rules. Flat wall of 11 toggles → four labeled `.toggle-group` cards
   (🎬 Video & Framing / 🔊 Audio & Cleanup / 🧠 Highlight Detection / 🧪 Experimental[dashed]),
   with `:has(input:checked)` accent. **Added the MISSING tooltip CSS** (`.tooltip-toggle` /
   `.tooltip-text`) — the 5 `❓` help blurbs used to render inline as walls of text; now hover/focus
   bubbles. Files: `ui/index.html`, `ui/src/styles.css`.
2. **Phone preview fix.** A duplicate `.portrait-preview-screen { position: relative; }` overrode the
   earlier `position:absolute; inset:0`, collapsing the preview so captions floated to the top and the
   hook clipped under the notch. Removed the override (left a warning comment). `ui/src/styles.css`.
3. **"ASS" removed from UI.** The only user-facing spot (`Font Size (…px in ASS)`) → `Font Size (…px)`.
   Remaining `ASS` mentions are dev code comments about the .ass subtitle format (not shown in-app).
4. **AI clip copywriting (the big one).** The `use_llm` toggle only ever drove clip *selection*, never
   the hook/title/description text, and there was **no per-clip description** — the card blurb was just
   the short hook sentence. Added `generate_clip_copy_llm()` (`server/core/hook_writer.py`) → a
   CapCut/OpusClips-style prompt returning `{hook, title, description}` (format=json, generous caps
   hook≤120 / title≤90 / desc≤400, graceful `None` fallback). Wired into `pipeline.process_video` after
   final selection (per-clip, gated on `use_llm`, uses `llm_model=PREFERRED_OLLAMA_MODEL`). Added
   `description` to `ClipCandidate` and `description`+`full_text` to `ClipResult` (`server/models.py`;
   flow via `model_dump()`). Loosened old short caps in `hook_writer`/`llm_detector` (3-9 words → 4-12).
   Clip card now shows `clip.description` (`ui/src/renderer.js buildClipCard`).
5. **Selected LLM model is now persisted.** `set_ai_model` only set an in-memory global, so every
   restart auto-resolved a default and forgot the user's pick. Added `logs/preferred_ollama_model.txt`
   persistence (same gitignored pattern as the HF token): `_preferred_model_path` / `_save_preferred_model`,
   and `_resolve_startup_ollama_model` now loads it first (auto-resolve only when nothing saved).
   So rehook, pipeline, chat, translate all use the model picked from the AI Models list, across
   restarts. `server/api/server.py`. (gitignored the new file.)
6. **AI-rewrite-hook prompt upgraded** to elite/OpusClips caliber (distinct angles, front-loaded
   tension, extra grounded example). `server/core/hook_writer.py`.
7. **"New Hook" card button fixed (was re-picking the SAME text).** `quickRerollHook` built candidates
   from only the short `hook_text` (full_text/reason undefined on existing clips) → `rank_hook_candidates`
   returned a single sentence → rotation never changed. Now reconstructs the clip transcript from
   `clip.words`, calls `/tools/rewrite-hook` for several distinct AI hooks, and skips a candidate equal
   to the current one. Works on the running server (frontend-only + existing endpoint). `ui/src/renderer.js`.
8. **Editor "AI rewrite" now regenerates hook + title (+ description) together** via a new
   `POST /tools/rewrite-copy` (uses `generate_clip_copy_llm`; offline heuristic fallback). Updates both
   the hook and title fields in the caption editor; Save reflects the description on the card.
   `server/api/server.py`, `ui/src/renderer.js`.
9. **Intro-hook only showed on one clip — FIXED (root cause).** The intro is authored in clip-relative
   time `0:00–0:03`, but `_shift_subtitle_times` rebased **every** Dialogue line by the clip's start
   offset, so for clips not starting at 0 the intro collapsed to zero length (`max(0, t-offset)`), i.e.
   only the clip starting near t=0 kept it. Fix: the intro Dialogue line is now tagged with `Name=intro`
   (`server/core/caption_styler.py`) and `_shift_subtitle_times` skips rebasing any line whose Name is
   `intro` (`server/core/ffmpeg_tools.py`). Reproduced (clip @320s) + verified; existing tests unaffected.

### ⚠️ To actually SEE the backend changes: restart the server
Items 4, 5, 6, 8, 9 are backend — the running FastAPI server must be relaunched. Items 4 & 9 also burn
in at render time, so **regenerate clips** (or use New Hook / Save & Apply, which re-render) after
restart to get the AI copy and the intro hook on every clip. Item 7 works without restart.

## Key files (touched this session)
- Backend: `server/api/server.py` (PREFERRED_OLLAMA_MODEL persistence, `/tools/rewrite-copy`),
  `server/core/pipeline.py` (LLM copywriting step + description/full_text on results),
  `server/core/hook_writer.py` (`generate_clip_copy_llm`, upgraded hook prompt),
  `server/core/llm_detector.py` (loosened caps), `server/core/caption_styler.py` (intro Name tag),
  `server/core/ffmpeg_tools.py` (`_shift_subtitle_times` skips the intro line), `server/models.py`.
- Frontend: `ui/index.html`, `ui/src/styles.css`, `ui/src/renderer.js`
  (buildClipCard, quickRerollHook, ai-rewrite-hook handler, save handler).

## Notes / answers
- **VRAM stays high after a render — normal, not a leak.** Ollama keeps the LLM resident ~5min
  (keep_alive); PyTorch's CUDA allocator caches freed memory. Releases on idle / app close.
- CUDA torch is CPU-swappable via the Setup GPU card (commands shown there).
- Intro hook + captions never overlap in the render: intro is pinned top-center (`{\an8}`) for its
  first few seconds; body captions sit at the chosen position.

## PENDING / next
- Full end-to-end run on real footage AFTER a server restart to confirm items 4/8/9 visually (AI
  hooks+titles+descriptions on the cards, intro hook on every clip).
- Optionally route `/social/metadata` description through `generate_clip_copy_llm` too (still the old
  heuristic template).
- Packaging: still a **dev artifact** only — `ui/dist/ClippyStudio-win-portable.zip` needs Python on
  the target. Real "double-click and go" needs PyInstaller (see docs/dev/PORTABLE_BUILD.md &
  PACKAGING.md). Not started.

## GitHub prep (do this under YOUR OTHER account — not the current git identity)
Repo is clean: 70 tracked files, no `.venv`/`node_modules`/`output`/`*.pt`/tokens/logs committed.
This session's work is a **local commit on `master`**; nothing has been pushed.

Suggested flow (run these yourself so the commits/remote use your other account):
```bash
# 1. Make sure git will author as your OTHER account (repo-local, not global):
git config user.name  "Your Other Name"
git config user.email "your-other@email"

# 2. Private dev branch (your working branch):
git branch develop            # or: git checkout -b develop
# create an EMPTY private repo on your other GitHub account first, then:
git remote add origin git@github.com:<your-other-user>/<repo>.git
git push -u origin develop    # private repo = private branch

# 3. Public release branch for users to fork/download (only when it looks correct):
git checkout -b main          # or push master as the public branch
git push -u origin main
```
- Keep the GitHub repo **Private** for the dev copy; make a **separate Public** repo (or flip to
  public) for the release branch when ready.
- Before publishing: skim `LICENSE` (TechFreq Open-Attribution) and the README "AI Components"
  license table (ultralytics is AGPL-3.0). Nothing secret is tracked, but double-check no absolute
  local paths leak in docs (STATUS.md lists test-footage paths — fine for private, consider trimming
  for the public repo).
