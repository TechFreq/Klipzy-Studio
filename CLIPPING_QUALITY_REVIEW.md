# Clipping quality: current behavior and next improvements

Reviewed 2026-09-17. Product descriptions below are vendor claims, not independently measured accuracy.

| Area | Klipzy after this fix | Reference / remaining gap |
| --- | --- | --- |
| Speech highlights | Transcript heuristics, optional local LLM candidates and ranking | Evaluate coherent setup/payoff boundaries against human selections. |
| Action highlights | Audio loudness plus frame motion, now scanned alongside speech when enabled; overlapping windows deduplicated | Motion is not semantic event understanding. Camera shake and loud music can score highly. |
| Multimodal understanding | Simple signal fusion; action windows retain overlapping words for captions and transcript-based ranking | [Opus ClipAnything](https://www.opus.pro/clipanything) advertises visual, audio, sentiment and prompt-driven clipping. Klipzy does not currently interpret video events with a vision-language model. |
| Caption control | New projects default to no burn-in; saved project settings and explicit choices remain supported | [CapCut auto editing](https://www.capcut.com/tools/auto-video-editor) describes highlight-to-short workflows with caption template choices. Keep detection, caption generation and burned export styling separate. |
| Selection confidence | Existing heuristic scores; activity logs now report speech/audio and action candidate counts | Scores are not probabilities of virality and have not been calibrated against audience outcomes. |

## Prioritized next work

1. Create a labeled benchmark: talking heads, gameplay with commentary, quiet gameplay, sports, music, and camera movement. Record human-selected start/end times, missed moments, duplicates and irrelevant selections. Compare current and changed detectors on exactly the same footage.
2. Add scene-cut and speech boundary refinement so clips preserve the lead-in and payoff. Score clipped sentences and events cut mid-action as failures.
3. Add optional local vision-language review of candidate frames, grounded in exact timestamps. Distinguish meaningful actions from shake, menus, loading screens and transitions. Measure runtime/VRAM on the RTX 3060 before making it a default.
4. Use complementary audio events (laughter, applause, sudden reactions) rather than only amplitude; retain a low-confidence/no-highlights outcome for uniformly noisy footage.
5. Calibrate speech/action ranking per content category. Measure precision among exported clips, recall of human highlights, boundary error, duplicate rate and minutes of processing per source minute. Do not label simple motion peaks as kills/goals without evidence.
6. Add a reviewable explanation and detector provenance to every candidate, then a feedback option (good moment / missed / irrelevant) for benchmark collection with user consent.

## Changes implemented now

- Auto no longer compares its unlimited candidate count with None.
- Speech, audio and motion candidates are combined instead of using action only as a fallback. The audio/action checkbox is respected even in Auto.
- Empty transcripts skip transcript-only AI detection. Speech-free action clips skip AI copywriting to avoid inventing events from a generic hook.
- Action window growth does not shrink the selected interval; silent, motionless signals do not produce action peaks.
- Local model selection highlights immediately with save feedback and failure rollback. Sidebar model/quality selectors reuse the actual saved controls; models load on use, not on selection.
- Commands use the running interpreter on each user's machine, with platform shell quoting. Windows command examples target PowerShell and work with spaces in install paths.

This is not evidence that Klipzy matches another product's selection accuracy. That requires the shared benchmark above.

## Implementation follow-through

The latest AI_ACCURACY_REPORT.md section records the implemented audio routing, candidate ranking, source checking, optional visual evidence, model repair and end-to-end validation. Human publication-quality labels and untouched-interval evaluation remain the next gate. Visual review currently describes frames; it does not provide a proven visual highlight-selection model.
