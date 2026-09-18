# AI accuracy and real-footage review — 2026-09-17

## Result

Real local inference exposed genuine quality defects. Fixes now keep final titles distinct, ground fallback descriptions in source words, fact-check generated copy, preserve transcript words in parsed AI candidates, and use real detector windows for production AI ranking instead of letting the model propose unreliable timestamps.

This is **not a commercial-parity certification**. Highlight quality still needs substantial work, especially multi-track audio and understanding visual gameplay events.

## Footage and method

Source: `C:\Users\ROGPC\Downloads\TEST FOOTAGE`. Originals were only read.

- All four supplied MP4s: Call of Duty, Minecraft for Windows, `chill+sunday+with+wifey`, and `chill+sunday+with+wifeycod`.
- Three 90-second samples per file near 5%, 45%, and 80% of duration, clamped at the end: 18 minutes of sampled playback. Some windows overlap; this is not 18 distinct minutes or a complete review of every file.
- Four additional 60-second track samples: second and third audio tracks of the two three-track recordings.
- Cached Faster-Whisper small, CPU int8, English; real Ollama Gemma 2 2B inference at temperature zero.
- Repeated candidate ranking and copy generation on the same cached transcripts: original prompt, stricter prompt, factual prompt, then factual prompt with source check.
- Real audio/motion detector on all twelve samples, with 320px/4fps video proxies. This approximation can change motion scores relative to full-resolution analysis.
- Four representative video frames inspected. No claim that every generated interval was watched/listened to or independently rated.
- No external AI upload or new model download. Qwen 2.5 7B and Llama 3 returned HTTP 404 from local inference; they were excluded from quality comparisons.

## Measured observations

| Check | Observed result | Interpretation |
|---|---|---|
| Speech-based sample candidates selected for copy review | 15 across twelve samples | Does not establish that those 15 are good clips |
| Original copy responses | 14 usable responses / 15 requests | One missing response; several unsupported stories |
| Final checked copy responses | 15 / 15 fell back to transcript quotes | Conservative source checking, not 100% semantic accuracy |
| Duplicate final titles within each sample | 0 in checked-copy run | Reported repeated-title bug was not reproduced by these samples; independent regression covers it |
| Audio/motion candidate windows | 31 across twelve samples | Intensity candidates, not verified compelling events |
| Separate commentary tracks | Speech found on third tracks of both multi-track recordings | Default-track analysis misses relevant player commentary |

The earlier synthetic ASR check had zero word errors against a 72-word authored reference for both base and small, clean and pink-noise mixed audio; both produced no words on silence. This easy synthetic result must not be generalized to noisy gameplay. There is no human-verified transcript for the real footage, so no real-footage WER is reported.

## Concrete failures and fixes

### Unsupported titles and descriptions

Original output invented a heist and capture-the-flag scenario from routine Call of Duty announcements. Casual whiskey-and-Coke chatter became a secret recipe/tutorial. A factual-only prompt reduced exaggeration but still invented a discount and other details.

The copywriter now asks for a factual clip-specific summary, then performs a separate source check. Acceptance requires a true boolean and a verbatim evidence excerpt. Rejected/malformed/failed checks use an explicitly labeled quote from that clip's transcript. If generation fails in processing, the same source fallback replaces potentially unverified ranker copy.

**Tradeoff:** the small model rejected every tested proposed copy, including potentially acceptable summaries. Current behavior prioritizes grounding over polished social copy. The verifier uses the same model and can still make mistakes; even accepted copy is not independently proven. Source quotes inherit ASR errors and can be awkward or truncated.

### Repeated titles

Action detection previously cycled through four generic titles. Labels now include the peak time and descriptions identify them as audio/motion selections. A final pass repairs duplicate titles using each clip's own transcript and, when needed, a time/index suffix. Distinct moments are no longer discarded merely because their titles match. Meaningful leading numbers such as “6 wins” are preserved during copy cleanup.

This handles backend duplicate output. The supplied samples did not reproduce a separate UI issue showing the first title on every card.

### Unreliable AI windows

On six controlled transcripts, Gemma correctly ranked the two authored useful examples first. Independent discovery was less reliable: malformed timestamp arrays, a backup title attached to the wrong time, and later segment IDs confused with timestamps. An example-derived 0–75s window included unrelated filler.

Production processing now ranks actual heuristic/audio candidate windows rather than adding independently invented LLM windows. The discovery helper also accepts wrapped JSON, validates indices/times, and preserves the actual transcript/word timestamps rather than putting its explanation into transcript text. It is no longer used by the processing path.

**Tradeoff:** semantic ranking cannot recover a great moment that candidate generation never proposed. Existing top-12 ranking and heuristic window coverage still require evaluation on longer labeled footage.

### Steady-noise false positives

A controlled 60-second steady-noise fixture previously yielded three action highlights. Requiring meaningful prominence above the recording baseline reduced that to zero while retaining the known burst; silence remained zero. This is a narrow regression, not proof of calibrated action detection. Sustained action can be harder to detect with a prominence threshold.

## Major unresolved audio issue

Call of Duty and Minecraft recordings each contain three audio streams. At source time 135s, the second track returned no speech, while the third contained player commentary. The default track contained game audio/dialogue and omitted that commentary.

A processing-log diagnostic now reports multi-track inputs and the limitation. **Track selection/mixing is not implemented by this change.** Until it is, a source with the desired commentary and gameplay audio mixed is needed for complete analysis. Automatically mixing every stream can duplicate existing mixes, so it should be a deliberate input control used consistently by transcription, preview, export, and transcript cache identity.

## Review artifacts and reproduction

Open [playable before/after comparison](dev/accuracy/footage/review.html). The twelve proxies contain default-track audio at 320px/4fps; they are review media, not final exports. Candidate times are relative to each sample; the source offset is displayed.

Local scripts and raw JSON live under `dev/accuracy/` (gitignored under the existing development convention):

- `footage_accuracy.py`: ASR cache, real ranking, copy and raw model responses. `before`, `after`, `factual`, `verified` JSON captures preserve the successive runs. Re-running a phase uses current code and overwrites that phase's results; it does not reconstruct historical code.
- `footage_tracks.py`: additional track transcripts and audio/motion results.
- `run_accuracy.py`: controlled ASR, ranking/discovery, and action tests.
- `build_review.py`: local playable comparison.
- `check_models.py`: local model-availability failures.

The footage harness exercises real components, not a complete Electron project/export lifecycle. Its ranking pool is eight candidates per sample and it reviews up to three; production settings can differ. Earlier FFmpeg regression tests cover rendering separately.

## Next quality gates

1. Add explicit audio track selection/mixing, shared by analysis and export, with cache invalidation.
2. Human-label good and bad intervals on complete recordings; measure highlight precision/recall, boundary quality, duplication, and factual copy separately.
3. Restore inference for a stronger local model and compare against the same held-out labels. Do not equate a larger model with guaranteed accuracy.
4. Add visual-event understanding: camera movement and loudness alone cannot distinguish a win, miss, menu transition, or memorable interaction.
5. Calibrate or relabel virality scores. Current heuristic 0–10 scores are not probabilities of success or commercial benchmark results.
6. Improve graceful summaries for ASR fragments and uncertainty; source quotes are safer but not a polished final experience.

## Regression validation

Final backend suite: **549 passed**, no failures, in 42.87 seconds. One existing Starlette/httpx deprecation warning. Focused source-check, duplicate-title, numeric-title, grounded-selection, and action regression checks passed. `git diff --check` passed. Frontend was not changed or rerun for this accuracy pass; earlier frontend audit results are separate.

## Follow-up fixes: selectable source audio and compact copy

The Audio & Cleanup section now includes **Source audio tracks**, saved with project processing options and sent for both individual and batch jobs:

- `default`: existing automatic single-track behavior.
- `3`: third audio stream only (the microphone in the two tested recordings).
- `1,3`: normalized mix of the first and third audio streams.
- `all`: mix all audio streams, with an explicit caution about recordings that already contain a complete mix.

Non-default selections create a persistent `selected_audio_source.mkv` in the job output folder: video is copied without re-encoding; selected audio is encoded to AAC. Analysis, clip rendering, and subsequent editor source playback/re-renders use this same prepared source. Original input preview still plays the original file/audio, as the control explains. Prepared sources use additional disk space (potentially near the original video's size) and must be kept for editor source access. Invalid/nonexistent track choices fail explicitly rather than silently reverting to a different track.

Transcript cache identity includes source path, size, nanosecond modification time, track selection, language and model; default-track results cannot be reused for a microphone-track run. Fallback titles are limited to ten words and hooks to sixteen, shortened on word boundaries.

Live verification at 135–195 seconds in both recordings:

- Track `3` recovered player commentary in Call of Duty and Minecraft, using the actual preparation/extraction functions followed by cached small Whisper inference.
- Mix `1,3` recovered Minecraft commentary. In Call of Duty, louder game dialogue still dominated the mixed transcription. Track inclusion alone is not source separation or a guarantee of intelligibility.
- For these sources, use `3` when prioritizing commentary. That also makes export audio microphone-only. Independent transcription/export track choices and adjustable mix gains remain future work.
- Raw evidence: `dev/accuracy/audio_fix/results.json` (mix) and `dev/accuracy/audio_fix_3/results.json` (microphone).

This replaces the earlier statement that track selection/mixing is unimplemented. The stronger-local-model availability issue, calibrated highlight ranking and visual event understanding remain open; no commercial-parity claim is made.

Validation: full backend suite 557 passed; frontend suite 124 passed. The added audio-payload check passed separately (125 frontend checks covered). Additional focused checks cover final compact headings and cache identity. One existing Starlette/httpx deprecation warning remains.

## Integrated quality upgrade — latest status

### Implemented and tested

- **Separate speech and export audio:** analysis can use microphone track `3`, exports can mix `1,3`, and action detection still receives game audio. Optional export mix balances (for example `0.3,1`) control relative volume. Settings persist with the project; cache identity includes analysis selection, language, and relevant mix settings.
- **Whole-timeline ranking:** all proposed speech candidates are ranked in batches of six, instead of only twelve high keyword-score candidates. Ranking gets up to 1,600 transcript characters per candidate. Missing scores and inference failures produce visible progress messages. A wholly unusable batch stops further AI calls and preserves remaining heuristic candidates.
- **Weak-candidate handling:** Auto excludes AI-reviewed speech candidates below 5/10; visual/action candidates are retained because text alone cannot judge them. This cutoff is provisional, not calibrated against human ratings.
- **Boundary protection:** action windows can expand up to two seconds to avoid cutting a nearby spoken segment, without exceeding the duration cap.
- **Honest score labels:** cards show “Selection score” and “Score band,” not a predicted probability of virality.
- **Model repair:** confirmed Qwen/Llama model manifests pointed to missing weight files. Re-downloaded Qwen 2.5 7B and verified real inference. Model catalog metadata checks now flag missing weights and expose the download/repair path. Llama and untested models were not all re-downloaded. Local chat calls now have a bounded timeout.
- **Local visual evidence:** optional `gemma3:4b` review samples three chronological frames per selected clip and adds a scene description plus uncertainty to the card. Tested on three real excerpts. It does not independently discover events, change ranking, or verify a kill/win; this deliberately remains an experimental review aid.
- **Copy source checking:** faithful paraphrases are allowed while substantive invented claims are rejected. On the same 15 real candidates, Qwen retained generated copy for 8 and fell back for 7, versus 1 retained / 14 fallback with the stricter checker. This is model acceptance, NOT independently measured factual accuracy. Some accepted wording still depends on uncertain ASR (for example a possible “babe”/“vape” confusion).

### What to use on the supplied three-track recordings

Under **Audio & Cleanup → Audio tracks & mix balance**:

- Export tracks: `1,3`
- Export mix balances: `0.3,1` (optional starting point, not a calibrated mix)
- Speech analysis tracks: `3`

Select the repaired `qwen2.5:7b` model for text AI. Visual review uses the separately installed `gemma3:4b`; it is opt-in and remains local. Original input preview uses original audio; generated clips and editor sources use the prepared export mix. Inline track audition controls are not implemented yet.

### Actual end-to-end results

A 60-second Call of Duty excerpt ran through the real pipeline twice:

1. Microphone-only analysis + mixed export + action detection + burned captions: one 19.69-second H.264/AAC export, 1920×1080, completed in 111.9 seconds. Captioned frame visually inspected.
2. Cached microphone transcript + weighted mix + Qwen ranking + Gemma visual review + checked copy + burned captions: one clip completed in 91.2 seconds. Qwen scored all four proposed candidates; the selected clip still used fallback copy. This was a manual-count run, not proof of Auto selection quality.

Times reflect this machine, caching, and concurrent development work; they are not controlled speed benchmarks. Raw outputs: `dev/accuracy/e2e_result.json`, `e2e_ai_result.json`, `visual_results.json`, and `footage/*_qwen_paraphrase.json`.

### Benchmark and human-review gate

Target confirmed by the user: **a mix of funny reactions/conversations and skilled plays/outcomes**.

- [Rate proposed clips and add missed moments](dev/accuracy/footage/label.html): 15 development windows, initially unrated. Playback proxies use default-track audio and low frame rate; check originals for commentary/fast action. Labels are saved locally in that browser and can be exported as JSON.
- `scripts/score_clip_benchmark.py` computes one-to-one interval matching, precision on reviewed predictions, recall on labeled positives, and boundary error. Unrated cases produce no fake accuracy scores.
- `dev/accuracy/footage/heldout_manifest.json` reserves four untouched 90-second ranges from the longer recordings. They have not been processed or used to tune these changes. Human labels and a later held-out run are still required.
- Candidate coverage is still dependent on heuristic proposals. Fine-grained event detection, multi-modal ranking, calibrated thresholds, verified WER, and an independent comparison against commercial tools remain unfinished.

### Final engineering verification

- Backend: **573 passed**, one existing Starlette/httpx deprecation warning.
- Frontend: **126 passed**.
- Hidden Electron screenshots checked at 1100×740, including the expanded audio-routing controls. No user application session was manipulated.
- JavaScript syntax and `git diff --check` passed.
- CPU fallback, rendering, cancellation, auth and editor behaviors retain automated coverage, but packaged installers, macOS/Linux, and alternative GPUs have not been release-certified.

Model implementation reference: [Ollama vision API](https://docs.ollama.com/capabilities/vision) and [Gemma 3 4B model information](https://ollama.com/library/gemma3:4b). Model capability descriptions are not evidence of application-level highlight accuracy.

## Screenshot-driven regression — empty transcripts on action clips

The 20:30 run (`9eac32ec`) used `large-v3_audio_default_lang_auto`. Its first cached segment spanned 2.27–223.05 seconds, with a 212.32-second silence gap between the first and second word. Auto proposed **zero speech candidates** from that transcript. Splitting at real word-timestamp gaps restored **one speech candidate**, without shifting words onto later action clips. The pictured action peaks at 4:07 and later are beyond the detected speech ending at 235.11 seconds.

Pipeline repair now applies to newly transcribed and cached segments before selection/caption generation. No-word-timestamp segments are preserved, and existing rendered clips are untouched. Twelve targeted regression tests passed. For these multi-track recordings, reprocess with analysis `3`, export `1,3`, and optional export balances `0.3,1`; switching Whisper model alone does not change the selected audio track.

## Full processing diagnostics

New processing runs now write detailed structured events to the launcher terminal, `logs/server.log`, and **`processing-debug.jsonl` inside that run's output folder**. The queue's job ID is preserved in each event; the output directory is recorded at pipeline start.

Captured details include:

- Requested settings and effective Auto duration limits; export/analysis track choices, mix balances, model and caption/layout settings.
- Transcript-cache hit/miss and key; repaired transcript summary; every segment and word timestamp/probability.
- Every proposed candidate, pre/post-AI score, selection outcome, and stage-specific filter exclusions (duration, weak Auto speech score, overlap, count/quality threshold).
- Text-AI prompts, raw responses, model/backend, errors and source-copy fallbacks. Visual requests record sampled ranges/frame count and model response; image bytes are omitted.
- Final title, description, transcript, score and timing for each selected clip.
- Subprocess commands, working directory, timeouts, return codes, runtime and captured text stdout/stderr. Binary media bytes are omitted.
- Progress stages, optional-stage failures, output paths/sizes, completion, cancellation and fatal errors with tracebacks.

Normal processing messages use stdout, so Electron labels them `[server]`; warnings/errors use stderr. Credentials and authorization values are redacted. Full transcripts, prompts and local paths are intentionally present in these diagnostic logs. Logs capture application-visible evidence, not private model reasoning or unobserved events. Events from subprocesses are available when their captured output returns; this does not turn FFmpeg output into a live per-frame progress stream.

Verification: backend suite **579 passed** before final routing/cleanup refinements; **27 focused tests passed** afterward. A real export produced a parseable **62-event, 114,362-byte** per-run trace containing settings, transcript, candidate decisions, FFmpeg results and final completion. Original clips remain unchanged. Restart the running app to load the instrumentation; historical runs cannot gain logs retroactively.
