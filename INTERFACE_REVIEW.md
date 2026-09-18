# Screenshot review and interface adoption plan

Reviewed 2026-09-16: all 28 PNG screenshots in the supplied `inspiraiton` folder, individually. These are observations of the supplied images, not claims about the products' current capabilities.

## Direction

Keep Klipzy's guided Import → Options → Processing → Results flow. Use a dedicated editing workspace for precision work: media/clip navigation on the left, a large central source/output preview, a contextual inspector on the right, and the timeline underneath. Keep the charcoal surfaces and cyan selection accent consistent across both workflows.

## Implemented in this pass

- Clip Editor in the main sidebar opens a library of generated clips for the current source video, with a useful empty state. It does not silently create or switch projects.
- Editor tools grouped into Layout, Text & media, Audio, Captions, and Effects. Existing controls and values are retained when switching categories. Arrow keys, Home and End navigate the tabs.
- Selected timeline item properties appear above the category controls, so they do not get lost at the bottom of the rail.
- Wider editor workspace, visible clip title, responsive stacked layout on smaller windows.

This pass reorganizes existing capabilities. It does not implement every feature pictured below or add a general multi-video assembly editor.

## Implementation update — 2026-09-16

Implemented across the follow-up editor passes:

- Device-local drafts keyed by project, source and clip; autosave, explicit Save draft, and visible save-failure status. Restores trim, framing, text, images, audio and caption controls after reopening. Drafts live in app local storage, not portable project files.
- Undo/redo for edit snapshots, with keyboard shortcuts; current-source clip switching preserves each clip's draft.
- Searchable timed transcript with word-to-playhead seeking. Existing source-relative word timings still need a mapping for renders that removed silence.
- Searchable visual caption preset gallery in processing settings, applying the represented colors/font; clip-library search and device-local favorites.
- Independent editor text/opening-headline items with font, bold, italic, outline, shadow distance and square fill/padding. The compositor exports these styles; a real-video pixel test verifies the background.
- Timeline snapping (Alt bypass), source thumbnails, source audio waveform, and stepping based on detected average frame rate. This is frame-sized seeking, not guaranteed decoded-frame stepping on variable-frame-rate media.
- Thumbnail/waveform extraction is bounded to clips of at most 10 minutes, cached for eight source/range combinations, and invalidated by file size/modification time. Late responses cannot update a different source. Preview extraction failure leaves editing/export available.
- Visible export destination and filename, automatic unique filenames, refusal to replace existing output files, and actionable caption-preparation errors.
- Text/image/SFX export timing rebased after trim and playback-speed changes. Fully excluded overlays are omitted.

Validation includes UI interaction tests, draft reopen/undo tests, stale-thumbnail response tests, export request validation, and actual FFmpeg rendering. A hidden Electron visual fixture now verifies the editor at large and smaller window sizes and the generated clip/transcript rows using test footage. This is a layout check, not a live Ollama shutdown test.

Still to finish: portable drafts and draft migration/recovery UI; all-project/source navigation inside the editor; shared advanced caption/headline style model and preset favorites; variable-frame-rate precision; silence-removal transcript mapping; fully composed output preview with effects/overlays; richer text glow/shadow/background geometry; export jobs integrated into the shared queue; larger media-bin and assembly workflows.

## Priorities and remaining gaps

| Priority | Area | Adoption | Current gap / completion condition |
| --- | --- | --- | --- |
| 1 | Editing drafts | Save edits per project, source and clip; show saved/unsaved state | Device-local restoration is implemented. Remaining: portable project drafts, recovery/migration controls, and broader restart testing. |
| 1 | Source and output | Full source with movable crop beside the output; explicit Original/Rendered labels | Existing crop and facecam tools are present. Improve presentation and ensure every preview matches export, especially captions and baked effects. |
| 1 | Text inspector | Separate Spoken captions, Opening hook and Added text; Basic / Style / Animation groups | Controls differ between setup and editor. Share a style model, use explicit selected-item vs apply-to-all scope, and preserve independent hook settings. |
| 1 | Preset browsing | Actual visual tiles, selected border, preview, favorites and reset | Searchable caption tiles and headline tiles exist. Remaining: favorites/reset and shared style controls inside the editor; previews remain representative. |
| 1 | Timeline | Thumbnail strip, audio waveform, snapping, clear trim handles, frame stepping, undo/redo | Zoom, handles, cached thumbnails/waveforms, snapping, undo/redo and trim/speed timing fixes are implemented. Remaining: variable-frame-rate precision and more complete timeline editing. |
| 2 | Clip navigation | Project/source picker and clip strip within the editing workspace | New sidebar library is for the active source. Switching clips must first preserve drafts; do not mutate processing/project context accidentally. |
| 2 | Text backgrounds | Independent fill, box border, corner radius, width/height padding, offset | Square headline fill exists. A hollow rectangle border is different from glyph stroke and needs separate preview/export support. |
| 2 | Text effects | Shadow blur/distance/angle, glow and richer entrance animations | Add only with matching render support; do not present CSS-only effects as exportable. Curved text and keyframes come later. |
| 2 | Results | Large playable cards, title/duration, obvious Edit/Export, search, favorites | Keep scores labelled as estimates; do not label a clip Trending without real trend evidence. |
| 2 | Export center | Destination and filename visible, settings summary, progress, retry/open-folder | Consolidate existing export and queue controls. Errors should state a usable recovery action. |
| 2 | Transcript navigation | Click words to seek, search transcript and mark requested moments | Search/click-to-seek is implemented for source-relative word timings. Remaining: silence-removal mapping and requested-moment selection. |
| 3 | Media and audio | Searchable asset bin; independent audio streams if present in source | Current text/image/SFX/music tools are a start. A mixed soundtrack cannot expose original game/chat/mic tracks independently without separation. |
| 3 | Additional sidebar tools | Image generation, capture, montage and publishing | Keep as future work until each has a complete usable flow. Screenshots do not establish implementation or provider availability. |

## Conventions to keep

- One primary action per stage. Batch actions name the actual project video count.
- App sidebar switches workspaces; editor tabs switch tools within the current edit.
- Use familiar labels and icons together, visible selection states, keyboard focus, and consistent field spacing.
- Collapse advanced sections; keep preview, playback and export easy to reach.
- Auto discovery chooses scored moments; manual duration/count controls clearly indicate when they are inactive.
- Background jobs remain visible through Queue and completion badges without forcing navigation.
- CPU/GPU status belongs in Setup and job details; editing navigation must not require a GPU.

## Screenshot-by-screenshot observations

| Screenshot (relative to inspiration folder) | Observed pattern |
| --- | --- |
| `capcut interfaces/Screenshot 2026-08-28 165237.png` | Portrait result cards with title and duration; enough preview space to judge each clip. |
| `capcut interfaces/Screenshot 2026-08-28 165250.png` | Per-card playback with clear Edit and Export actions. |
| `capcut interfaces/Screenshot 2026-08-29 023657.png` | Single obvious import/drop target and compact guidance. |
| `capcut interfaces/Screenshot 2026-08-29 023729.png` | Native video picker; selection remains part of the existing workflow. |
| `capcut interfaces/Screenshot 2026-08-29 023751.png` | Source preview, visual caption templates, duration choices, optional moment prompt. |
| `capcut interfaces/Screenshot 2026-08-29 023824.png` | One result still uses the same card interaction as many results. |
| `capcut interfaces/Screenshot 2026-08-29 023853.png` | Clip strip left, central playback, caption template panel on the right. |
| `capcut interfaces/Screenshot 2026-08-29 023907.png` | Text effects separate from templates; previews communicate appearance. |
| `capcut interfaces/Screenshot 2026-08-29 023952.png` | Project dashboard and separate creation tools; recent projects retain thumbnails. |
| `capcuteditor/Screenshot 2026-09-04 202134.png` | Media library left, player center, inspector right, layered timeline below. |
| `capcuteditor/Screenshot 2026-09-04 202216.png` | Caption text editing, font, case, alignment, preset swatches, apply-to-all scope. |
| `capcuteditor/Screenshot 2026-09-04 202231.png` | Transform, opacity and outline in separate collapsible inspector sections. |
| `capcuteditor/Screenshot 2026-09-04 202242.png` | Background independent of stroke; opacity, corner radius, dimensions and offset. |
| `capcuteditor/Screenshot 2026-09-04 202257.png` | Glow separated from shadow, with intensity and range controls. |
| `capcuteditor/Screenshot 2026-09-04 202307.png` | Shadow opacity, blur, distance and angle; optional curved text. |
| `capcuteditor/Screenshot 2026-09-04 202321.png` | Searchable template grid, favorites, selection border and save/reset actions. |
| `clipskitty interface/Screenshot 2026-09-02 095729.png` | Generate action separate from queue, editor, activity and model setup. |
| `clipskitty interface/Screenshot 2026-09-02 095750.png` | Font names displayed in their typeface alongside a caption preview. |
| `clipskitty interface/Screenshot 2026-09-02 095815.png` | Output intent expressed through understandable duration/format choices. |
| `clipskitty interface/Screenshot 2026-09-02 095903.png` | Hardware-aware model guidance with installed/active status; screenshot claims not verified. |
| `open clipper interfaces/Screenshot 2026-08-29 024015.png` | Named projects, progress/status and explicit Open/Edit actions. |
| `open clipper interfaces/Screenshot 2026-08-29 024036.png` | Minimal project creation: name and optional description. |
| `open clipper interfaces/Screenshot 2026-08-29 024102.png` | Transcript-led selection alongside output preview and multiple ratio previews. |
| `open clipper interfaces/Screenshot 2026-08-29 024121.png` | Renaming/describing a project is separate from replacing its source. |
| `Screenshot 2026-08-27 214152.png` | Export settings grouped beside job results; visible failure needs an actionable recovery message. |
| `steelseriesinterface/Screenshot 2026-09-10 005537.png` | Full source visible outside movable portrait crop, timeline waveforms and separate audio lanes. |
| `steelseriesinterface/Screenshot 2026-09-10 005622.png` | Searchable clip library with duration, favorites and event markers. |
| `steelseriesinterface/Screenshot 2026-09-10 005708.png` | Capture settings kept in their own panel, separate from editing. |

## Layout and shutdown refinement

- Editor now fills the window with a left clip bin, central player, right tool inspector, and bottom timeline. Export stays in the header; undo/redo sit above the timeline. Output framing is an expandable preview beside the player.
- Generated clips use wide rows with selectable transcript text to the right of the video, falling back to timed words when full transcript text is absent.
- Quit detects local Ollama processes and asks whether to leave them running, stop them and quit, or cancel. Stopping requires the explicit choice and explains its effect on other apps. Shutdown failures offer Quit Klipzy anyway or Cancel. No running Ollama processes were stopped during tests.
- Visual fixture checks caught and fixed collapsing inspector tabs; separate unit tests cover each shutdown choice.


## Startup and quit reliability

- Keep the project Python environment rather than falling back to system Python after 30 seconds. Allow up to three minutes for startup with bounded readiness requests.
- Use an authenticated lightweight readiness route; model recommendations run in the background and health checks no longer import Whisper/Torch.
- Replay backend status to newly initialized windows, defer setup checks until ready, and refresh them automatically.
- Windows Ollama detection handles absent processes normally. Shutdown failures permit quitting without stopping Ollama, and dialog errors cannot trap the desktop window.
- Verified the local project environment has PyTorch 2.14.0+cu126 with CUDA available. Tests do not stop live Ollama processes.


## Settings install feedback and model selection

- One Settings entry remains at the bottom of the sidebar, with an active highlight.
- Dependency, Install All, optional diarization, and accelerated PyTorch buttons now use a serialized background installer. Each operation has its own activity entry, live output (also logged to the terminal), checking/installing/verifying states, and an indeterminate progress indicator. Installer percentages are not fabricated.
- Preflight and post-install checks run in a fresh project Python interpreter. Verified installs show restart-required status rather than trusting stale imported libraries. Repeated clicks reuse in-progress or restart-pending installs; other package installs cannot overlap.
- Restart buttons close the owned backend before relaunching and refuse while installation or processing jobs are active. Externally managed backends receive manual restart guidance. Restart leaves shared Ollama running.
- Ollama recommendations now save the active choice and synchronize the highlighted recommendation, model dropdown, and catalog. Undownloaded selections are explicitly labeled.
- YOLO currently uses yolov8n.pt automatically; alternate model selection is not implemented, and the recommendations no longer imply otherwise.
- Regression tests mock installers; no packages are installed or uninstalled during validation.


## Wrong-environment reconnect repair

- Confirmed a legacy backend (system Python, PID 28348) was serving the app despite CUDA PyTorch working in the project .venv. It lacked the new readiness and install activity routes.
- Readiness now reports backend protocol, project root, and Python interpreter. Electron only reuses a matching backend; otherwise it starts the project engine on a free local port and updates the renderer URL before loading Settings.
- GPU Settings displays the actual interpreter, PyTorch version, and CUDA build. An installed CUDA wheel without GPU availability is distinguished from a CPU-only wheel, avoiding repeated downloads for driver/runtime problems.


## Model controls and highlight reliability

- Sidebar local model and transcription selectors, immediate model selection feedback, and rollback on save failure.
- Burn captions defaults off; manually selected and saved project values remain authoritative.
- Dynamic commands now quote interpreter paths for PowerShell/POSIX shells.
- Fixed Auto action candidate limit crash, combined action and speech discovery, and preserved action-window transcript words.
- See [CLIPPING_QUALITY_REVIEW.md](CLIPPING_QUALITY_REVIEW.md) for the sourced product comparison and benchmark-first accuracy plan.


## Codebase audit

See [CODEBASE_AUDIT.md](CODEBASE_AUDIT.md) for the audit scope, repaired concurrency and validation bugs, verification results, and remaining release checks.
