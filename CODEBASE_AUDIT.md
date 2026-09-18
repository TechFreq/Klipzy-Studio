# Klipzy codebase audit — 2026-09-17

## Scope and approach

Repository-wide automated checks plus focused source review of desktop startup and authentication, Settings and model state, project/queue behavior, processing and cancellation, editor/export validation, media helpers, and persisted endpoint configuration. Existing project changes were preserved; no dependencies were installed or removed.

This is a development audit, not a claim that every possible input, hardware configuration, or release package is bug-free.

## Bugs fixed in this audit

| Severity | Finding | Repair and evidence |
| --- | --- | --- |
| High | Processing and editor exports shared a global subprocess cancellation flag and registry. Starting or cancelling one job could interfere with another. | Job-keyed cancellation contexts, atomic spawn/registration, explicit API targeting. Real concurrent subprocess test proves one job cancels while the other completes. |
| High | Cancellation arriving before an editor worker started could be reset and ignored. | Named cancellation state survives worker startup; regression test rejects work before spawning. |
| High | Concurrent editor exports could pass the existing-file check and write to the same destination. | Locked destination reservation until worker completion, including failure cleanup; duplicate export returns 409. |
| Medium | Restart-after-install checked processing jobs but missed editor exports. | Read-only editor export listing and desktop restart guard; regression test confirms no backend kill during an active export. |
| Medium | NaN/Infinity editor parameters passed numeric validation and could reach FFmpeg; malformed crop values could become server errors. | Finite-number validation and clear crop validation errors; API regression cases return 400. |
| Medium | Installer polling could re-enable controls disabled for unrelated reasons. | Preserve each control's prior disabled state; frontend regression test. |
| Medium | Valid JSON with non-string endpoint fields crashed settings normalization despite the promised corrupt-config fallback. | Only restore string values for string settings; both LLM and ASR configuration tests cover malformed fields. |
| Medium | Endpoint preference files can store credentials but were not excluded from Git. | Ignore the two exact runtime preference paths. No credentials were read or printed. |
| Low | A button labeled Revert to CPU only uninstalled PyTorch. | Renamed it Uninstall PyTorch to match the actual operation and existing confirmation. |
| Test reliability | Cancellation test mocks intercepted metadata/encoder probes and wrote a stray file named -encoders. | Isolate probes from the fake renderer. Removed only the exact verified fixture artifact. |

The subprocess wrapper also now supports timeout, check, input, and capture_output consistently, with child cleanup on timeout and exceptional exits.

## Verification

- Frontend: 123 tests passed in the full suite; the subsequently added active-editor restart guard also passed (124 tests covered in total).
- Backend: 542 passed, no failures or skips, in 46.44 seconds. The isolated cancellation-fixture checks also passed after removing their probe side effect.
- Real FFmpeg tests exercise output geometry, rendering, subtitles, audio operations and cancellation. They do not establish semantic highlight quality.
- Syntax validation: 38 Python files and 18 JavaScript files.
- Python environment: pip check reported no broken requirements.
- Electron visual fixtures inspected at 1440×960 and 1100×740: editor, generated clip/transcript row, Settings activity panel, and sidebar controls. Fixtures use synthetic video/mock install data.
- git diff --check passed after whitespace cleanup.

## Remaining release validation

- Actual multi-gigabyte installer/download success, failure and interrupted-install recovery were mocked; no live reinstall was performed.
- macOS/Linux, AMD/Intel GPU acceleration and signed/packaged installers require their own machine/build checks. This audit ran on Windows.
- A long real project using installed transcription and Ollama models still needs an end-to-end user-footage run; API/model seams are largely mocked in automated tests.
- Semantic highlight precision/recall remains unbenchmarked. See CLIPPING_QUALITY_REVIEW.md; passing software tests does not demonstrate parity with commercial clipping systems.
- Tests remain local/gitignored under the repository's existing convention. A clean checkout needs the local test tooling restored to reproduce this audit.
- The suite emits an existing Starlette/httpx compatibility deprecation warning; it is not a test failure.

## Real-footage follow-up — 2026-09-17

See [AI_ACCURACY_REPORT.md](AI_ACCURACY_REPORT.md) for twelve real 90-second samples, additional commentary-track checks, live local model comparisons, concrete accuracy fixes, and the playable before/after review. This supersedes the earlier statement that semantic behavior had only been mocked, but does not establish full-project end-to-end quality or commercial parity. Multi-track commentary selection remains a significant open issue.

Audio follow-up: selectable source tracks/mixing now flows through processing, prepared editor source and exports. Cache identity separates track/language choices. Real track-3 transcription recovered commentary from both three-track recordings. Full backend suite: 557 passed; frontend: 124 passed plus the new payload test. See the accuracy report for mixed-audio limitations and storage tradeoffs.

## Integrated quality pass

573 backend and 126 frontend tests passed. Separate analysis/export tracks with mix balance, full-timeline AI ranking, weak-speech filtering in Auto, nearby speech-boundary expansion, missing-model-weight diagnostics, optional visual evidence notes, and honest selection-score labels are implemented. Qwen weights were repaired; two real excerpt pipelines rendered successfully, including an AI/vision/captioned run. Expanded audio controls were visually inspected.

See the latest section of AI_ACCURACY_REPORT.md for measured results, the human rating page, held-out intervals, and remaining gates. This is substantial functional progress, not completion of commercial-parity validation.

Processing diagnostics now cover settings, transcript/word timings, candidate decisions, AI requests/responses and fallbacks, subprocess output and failure tracebacks. Per-output `processing-debug.jsonl` complements terminal and server.log. Credentials are redacted; transcripts and paths remain visible for debugging. Verified on a real 62-event export trace; backend suite 579 passed and final focused logging/cancellation checks 27 passed.
