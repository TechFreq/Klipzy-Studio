"""
Main processing pipeline orchestrator.
Coordinates: Audio Extraction -> Transcription -> Highlight Finding -> Smart Crop -> Rendering.
"""

import hashlib
import json
import os
import uuid
from pathlib import Path
import re
from typing import List, Optional

from server.core.ffmpeg_tools import (
    check_ffmpeg, extract_audio, render_clip,
    generate_srt, generate_vtt, get_video_duration,
    extract_best_thumbnail,
)
from server.core.processing_trace import pipeline_scope
from server.core.transcriber import Transcriber
from server.core.highlight_detector import HighlightDetector
from server.core.audio_energy import detect_highlights_audio_energy
from server.core.face_tracker import FaceTracker
from server.models import ClipCandidate, ClipResult, TranscriptSegment


class VideoClipperEngine:
    def __init__(self, output_dir: str = "output", whisper_model: str = "base"):
        # Always anchor generated media to the repository, not the process cwd.
        # This keeps returned paths valid when FFmpeg temporarily changes cwd for subtitles.
        configured_dir = Path(output_dir)
        if not configured_dir.is_absolute():
            configured_dir = Path(__file__).resolve().parents[2] / configured_dir
        self.output_dir = configured_dir.resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.transcriber = Transcriber(model_size=whisper_model)
        self.detector = HighlightDetector()
        self.face_tracker = FaceTracker()
        # Derive Whisper model lazily / per-run: the engine may be created with
        # one model but /process can request a different size per job.
        self.whisper_model = whisper_model

    # ------------------------------------------------------------------
    # Transcript cache: avoids re-running audio extraction + Whisper when a
    # user re-processes the same video (the most expensive step, often minutes).
    # Keyed by (size, mtime) of the source + the whisper model name, so editing
    # the source or switching models always busts the cache.
    # ------------------------------------------------------------------
    @staticmethod
    def _transcript_cache_path() -> Path:
        cache_dir = Path(__file__).resolve().parents[2] / "output" / ".cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    @staticmethod
    def _video_fingerprint(video_path: str) -> str:
        try:
            st = os.stat(video_path)
            raw = f"{Path(video_path).resolve()}:{st.st_size}:{st.st_mtime_ns}"
        except OSError:
            raw = str(video_path)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def _cached_segments(self, video_path: str, model: str) -> Optional[List[TranscriptSegment]]:
        key = f"{self._video_fingerprint(video_path)}_{model}"
        cache_file = self._transcript_cache_path() / f"{key}.json"
        if not cache_file.exists():
            return None
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError("Transcript cache must contain a segment list")
            return [TranscriptSegment(**s) for s in data]
        except Exception as exc:
            from server.core import processing_trace as trace
            trace.event("transcript.cache_rejected", path=str(cache_file), error_type=type(exc).__name__, error=str(exc))
            return None

    def _save_cache(self, video_path: str, model: str, segments: List[TranscriptSegment]) -> None:
        key = f"{self._video_fingerprint(video_path)}_{model}"
        cache_file = self._transcript_cache_path() / f"{key}.json"
        temporary = cache_file.with_name(cache_file.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_text(json.dumps([s.model_dump() for s in segments], ensure_ascii=False), encoding="utf-8")
            temporary.replace(cache_file)
        except Exception as exc:
            from server.core import processing_trace as trace
            trace.event("transcript.cache_write_failed", path=str(cache_file), error_type=type(exc).__name__, error=str(exc))
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # Cache cleanup must not abort successful processing.

    def _free_gpu_memory(self, unload_llm_model: Optional[str] = None) -> None:
        """Release GPU memory once a job is done so VRAM returns to ~idle instead
        of staying reserved. Drops the per-job model references (Whisper + YOLO —
        they reload lazily on the next run), empties PyTorch's CUDA allocator
        cache, and unloads the Ollama LLM from VRAM. Best-effort: never raises,
        so cleanup can't break a finished render.
        """
        # Drop the face-tracking (YOLO) weights.
        try:
            if getattr(self, "face_tracker", None) is not None:
                self.face_tracker.model = None
        except Exception:
            pass
        # Drop the Whisper weights (reloads lazily next transcribe; the transcript
        # cache still avoids re-transcribing the same video).
        try:
            if getattr(self, "transcriber", None) is not None:
                self.transcriber._model = None
                self.transcriber._backend = None
        except Exception:
            pass
        # Return PyTorch's cached CUDA blocks to the OS.
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
        except Exception:
            pass
        # Unload the Ollama model from VRAM immediately (keep_alive=0) rather than
        # letting it linger for its ~5-minute idle timeout.
        if unload_llm_model:
            # No-op for a remote endpoint, which manages its own memory.
            try:
                from server.core import llm_client
                llm_client.unload(unload_llm_model)
            except Exception:
                pass
        try:
            import gc
            gc.collect()
        except Exception:
            pass

    @pipeline_scope
    def process_video(
        self,
        video_path: str,
        vertical_crop: bool = True,
        aspect_ratio: Optional[str] = "9:16",
        max_clips: int = 5,
        auto_clip_count: bool = False,
        include_unreviewed_action: bool = False,
        min_duration: float = 20.0,
        max_duration: float = 60.0,
        whisper_model: str = "base",
        language: Optional[str] = None,
        use_audio_energy: bool = True,
        use_llm: bool = False,
        # Optional speaker-diarization features (#16). Both need pyannote.audio +
        # an HF token; they no-op gracefully when diarization is unavailable.
        speaker_aware_selection: bool = False,  # prefer single-speaker / clean back-and-forth windows
        speaker_aware_crop: bool = False,       # follow the active speaker's face over time
        llm_model: str = "gemma2:2b",
        burn_captions: bool = True,
        caption_style: str = "viral_yellow",
        font_size: Optional[int] = None,
        remove_silence: bool = False,
        bleep_profanity: bool = False,
        mute_profanity: bool = False,
        # ---- audio/visual polish (all optional, off by default) ----
        normalize_audio: bool = False,   # EBU R128 loudness (~-14 LUFS) for platform-consistent volume
        auto_zoom: bool = False,         # gentle continuous push-in
        music_path: Optional[str] = None,  # background track mixed under speech
        music_volume: float = 0.12,
        duck_music: bool = True,         # sidechain-duck music under speech
        # ---- CapCut-style caption fine-tuning (optional; fallback to preset)----
        font_name: Optional[str] = None,
        primary_color: Optional[str] = None,
        highlight_color: Optional[str] = None,
        outline_color: Optional[str] = None,
        outline_width: Optional[int] = None,
        chunk_size: Optional[int] = None,
        uppercase: Optional[bool] = None,
        bold: Optional[bool] = None,
        italic: Optional[bool] = None,
        position: Optional[int] = None,  # ASS alignment 1-9
        intro_caption: Optional[str] = None,
        intro_caption_duration: float = 3.0,
        intro_enabled: Optional[bool] = None,
        intro_font_size: Optional[int] = None,
        intro_style: Optional[dict] = None,
        progress_callback=None,
        audio_tracks: str = "default",
        analysis_audio_tracks: str = "same",
        audio_track_gains: Optional[List[float]] = None,
        visual_review: bool = False,
        diagnostic_job_id: Optional[str] = None,
    ) -> List[ClipResult]:
        """
        End-to-end pipeline: long video -> rendered shorts.
        """
        requested_settings = {k: v for k, v in locals().items() if k not in ("self", "progress_callback")}
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        available, ffmpeg_err = check_ffmpeg()
        if not available:
            raise RuntimeError(ffmpeg_err)

        if auto_clip_count:
            min_duration, max_duration = 8.0, 120.0

        self.detector.min_duration = min_duration
        self.detector.max_duration = max_duration

        video_name = Path(video_path).stem
        job_id = uuid.uuid4().hex[:8]
        job_dir = self.output_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        from server.core import processing_trace as trace
        trace.bind(diagnostic_job_id or job_id)
        trace.attach(job_dir / "processing-debug.jsonl")
        trace.event("pipeline.start", settings=requested_settings, output_dir=str(job_dir),
                    effective_min_duration=min_duration, effective_max_duration=max_duration)
        temp_audio = str(job_dir / "temp_audio.wav")

        current_stage = ["initializing"]
        def trace_filter(stage, before, after):
            remaining = {id(c) for c in after}
            trace.event("selection.filter", stage=stage, before=len(before), after=len(after),
                        rejected=[{"id": c.id, "start": c.start_time, "end": c.end_time,
                                   "score": c.score, "ai_score": c.ai_score} for c in before if id(c) not in remaining])

        def report(step: str, pct: int):
            current_stage[0] = step
            trace.event("progress", percent=pct, message=step)
            if progress_callback:
                progress_callback(step, pct)

        cache_video_path = video_path
        action_audio = str(job_dir / "action_audio.wav")
        def extract_analysis_audio():
            if analysis_audio_tracks == "same":
                return extract_audio(video_path, temp_audio)
            from server.core.ffmpeg_tools import extract_selected_audio
            return extract_selected_audio(cache_video_path, temp_audio, analysis_audio_tracks)

        if audio_tracks != "default" or audio_track_gains:
            from server.core.media_cache import materialize
            report(f"Preparing export audio tracks {audio_tracks}...", 10)
            video_path = materialize(video_path, str(job_dir / "selected_audio_source.mkv"), audio_tracks, audio_track_gains)
        else:
            try:
                from server.core.ffmpeg_tools import get_media_info
                tracks = [s for s in get_media_info(video_path).get("streams", []) if s.get("codec_type") == "audio"]
                if len(tracks) > 1:
                    report(f"Found {len(tracks)} audio tracks. Default uses one track. Select audio tracks in Audio & Cleanup to include separate commentary.", 10)
            except Exception as stage_error:
                trace.event("stage.failed", stage=current_stage[0], error_type=type(stage_error).__name__, error=str(stage_error))
                pass
        report("Transcribing with Whisper...", 25)
        effective_model = whisper_model or self.whisper_model or "base"
        cache_model = effective_model + "_audio_" + (audio_tracks if analysis_audio_tracks == "same" else analysis_audio_tracks).replace(",", "-") + "_lang_" + (language or "auto")
        if analysis_audio_tracks == "same" and audio_track_gains:
            cache_model += "_mix_" + hashlib.sha1(json.dumps(audio_track_gains).encode()).hexdigest()[:10]
        # Reuse transcript if we've already transcribed this exact video with
        # this model — makes caption/format tweaks near-instant.
        segments = self._cached_segments(cache_video_path, cache_model)
        trace.event("transcript.cache", hit=segments is not None, cache_key=cache_model,
                    fingerprint=self._video_fingerprint(cache_video_path))
        if segments is None:
            report("Extracting audio...", 12)
            extract_analysis_audio()
            # Keep self.transcriber in sync for backend reporting / GPU cleanup,
            # even though the transcription itself runs via transcribe_cancellable.
            if self.transcriber.model_size != effective_model:
                self.transcriber = Transcriber(model_size=effective_model)
            # Run transcription in a killable child process so a job cancel can
            # actually interrupt Whisper (it used to run in-process and ignore
            # the cancel until it finished). Falls back to in-process when a
            # subprocess isn't viable (packaged app, launch error).
            from server.core.transcribe_runner import transcribe_cancellable
            segments = transcribe_cancellable(temp_audio, effective_model, language)
            self._save_cache(cache_video_path, cache_model, segments)

        # VAD can return a segment spanning separated speech islands. Split on
        # observed word gaps, including cached transcripts from older runs.
        from server.core.transcriber import split_transcript_gaps
        repaired = split_transcript_gaps(segments)
        if len(repaired) != len(segments):
            report("Repaired transcript segments spanning long speech gaps", 34)
            segments = repaired
            self._save_cache(cache_video_path, cache_model, segments)

        trace.event("transcript.summary", segments=len(segments), words=sum(len(s.words or []) for s in segments),
                    first_start=segments[0].start if segments else None, last_end=segments[-1].end if segments else None)
        for segment in segments:
            trace.event("transcript.segment", segment=segment.model_dump())

        # Optional speaker diarization ("who spoke when"), shared by both the
        # speaker-aware selection (#16.2) and active-speaker crop (#16.3). Run it
        # ONCE here and reuse. Fully optional: no pyannote/token -> diar_segments
        # stays None and both features silently fall back to the defaults.
        diar_segments: Optional[List[dict]] = None
        if speaker_aware_selection or speaker_aware_crop:
            try:
                from server.core.diarizer import diarization_available, diarize
                if diarization_available():
                    report("Diarizing speakers...", 36)
                    if not os.path.exists(temp_audio):
                        extract_analysis_audio()
                    dres = diarize(temp_audio)
                    if dres.get("available"):
                        diar_segments = dres.get("segments", []) or None
            except Exception as stage_error:
                trace.event("stage.failed", stage=current_stage[0], error_type=type(stage_error).__name__, error=str(stage_error))
                diar_segments = None

        report("Finding highlight moments...", 38)
        if auto_clip_count:
            from server.core.highlight_detector import detect_highlights_auto
            report("Auto: finding complete speech moments and audio highlights...", 38)
            candidates = detect_highlights_auto(segments)
        else:
            candidates = self.detector.detect_highlights_heuristic(segments)

        if use_audio_energy:
            report("Scanning audio energy peaks...", 42)
            if not os.path.exists(temp_audio):
                extract_analysis_audio()
            energy_clips = detect_highlights_audio_energy(
                temp_audio, segments, min_duration=min_duration, max_duration=max_duration,
                max_speech_gap=5.0 if auto_clip_count else None
            )
            candidates.extend(energy_clips)

        speech_count = len(candidates)
        if use_audio_energy:
            report("Scanning audio + visual action moments alongside speech...", 44)
            from server.core.audio_energy import detect_action_highlights
            if not os.path.exists(temp_audio):
                extract_analysis_audio()
            # Action detection needs game sound even when ASR uses only the mic.
            if analysis_audio_tracks != "same":
                extract_audio(video_path, action_audio)
            action_clips = detect_action_highlights(
                action_audio if analysis_audio_tracks != "same" else temp_audio, min_duration=max(15.0, min_duration) if auto_clip_count else min_duration, max_duration=max_duration,
                top_k=None if auto_clip_count else max_clips, video_path=video_path,
            )
            # Action windows may contain speech too. Preserve those words for
            # captions, transcript review, and grounded AI ranking.
            from server.core.highlight_detector import align_action_window
            for clip in action_clips:
                align_action_window(clip, segments, max_shift=max_duration if auto_clip_count else 2.0, max_duration=max_duration)
                matching = [seg for seg in segments if seg.end > clip.start_time and seg.start < clip.end_time]
                clip.words = [word for seg in matching for word in (seg.words or [])
                              if word.start >= clip.start_time and word.end <= clip.end_time]
                clip.full_text = " ".join(word.word for word in clip.words) if clip.words else " ".join(seg.text for seg in matching)
            candidates.extend(action_clips)
            report(f"Found {speech_count} speech/audio candidates and {len(action_clips)} action candidates; combining overlapping moments", 45)

        # Local models can attach good titles to unrelated invented windows, even
        # when prompted with segment IDs. Rank actual detector windows below instead.
        if use_llm and not segments:
            report("No transcript: skipping text-only AI; using available action candidates", 46)

        for candidate in candidates:
            trace.event("candidate.proposed", candidate=candidate.model_dump())
        proposed_candidates = list(candidates)

        # Sanity floor: never emit a degenerate sub-clip regardless of source.
        # (A loud one-word segment must not survive as a fraction-of-a-second clip.)
        floor = min(min_duration, 5.0)
        before_filter = list(candidates)
        candidates = [c for c in candidates if c.duration >= floor]
        trace_filter("minimum duration", before_filter, candidates)

        # Semantic "rank-and-refine": let the local LLM SCORE the real candidate
        # windows (and write hook/title) rather than invent timestamps — grounded
        # selection, no hallucinated cuts. Only the top pool is sent to keep the
        # prompt tight; degrades to the heuristic ordering if Ollama is absent.
        if use_llm and any(c.full_text for c in candidates):
            try:
                from server.core.llm_detector import rank_candidates_llm
                report("Ranking clips with local AI...", 48)
                spoken = [c for c in candidates if c.full_text]
                unspoken = [c for c in candidates if not c.full_text]
                ordered = sorted(spoken, key=lambda c: c.start_time)
                ranked = []
                # Cover the full timeline, not just the twelve highest keyword scores.
                for offset in range(0, len(ordered), 6):
                    from server.core import proc
                    if proc.cancelled():
                        break
                    report(f"Ranking candidates {offset+1}–{min(offset+6,len(ordered))}/{len(ordered)}...", 48)
                    batch = ordered[offset:offset+6]
                    ranked.extend(rank_candidates_llm(batch, model=llm_model,
                        preset=caption_style, status_callback=lambda msg: report(msg, 48)))
                    if not any(c.ai_score is not None for c in batch):
                        ranked.extend(ordered[offset+6:])
                        report("No usable AI scores; remaining candidates keep heuristic scores", 48)
                        break
                candidates = ranked + unspoken

            except Exception as stage_error:
                trace.event("stage.failed", stage=current_stage[0], error_type=type(stage_error).__name__, error=str(stage_error))
                pass

        from server.core import proc
        proc.raise_if_cancelled()

        if auto_clip_count:
            before_filter = list(candidates)
            candidates = filter_auto_evidence(candidates, require_ai=use_llm,
                                              include_unreviewed_action=include_unreviewed_action)
            trace_filter("Auto evidence: incomplete speech, rejected AI or unreviewed action", before_filter, candidates)
            if not candidates:
                report("Auto found no supported highlights. Check Game/Chat/Mic speech tracks, or enable unreviewed action suggestions to inspect activity peaks.", 49)

        # #16.2 Speaker-aware selection: gently boost candidates that stay on one
        # speaker or a clean two-way exchange, and dampen messy 3+ speaker /
        # talk-over windows. Mutates scores in place; the sort below picks it up.
        if speaker_aware_selection and diar_segments:
            try:
                from server.core.diarizer import rank_clips_by_speaker
                rank_clips_by_speaker(candidates, diar_segments)
            except Exception as stage_error:
                trace.event("stage.failed", stage=current_stage[0], error_type=type(stage_error).__name__, error=str(stage_error))
                pass

        # Deduplicate overlapping moments. Repeated titles are repaired after copywriting;
        # they must not discard distinct footage.
        # Deduplication keeps the first overlapping window in either mode.
        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        before_filter = list(candidates)
        candidates = self.detector._deduplicate(candidates)
        trace_filter("overlapping windows", before_filter, candidates)
        candidates.sort(key=lambda c: c.score, reverse=True)
        unique = candidates
        if auto_clip_count:
            candidates = select_auto_candidates(unique)
            report(f"Auto selected {len(candidates)} of {len(unique)} distinct moments from this footage", 49)
        else:
            candidates = unique[:max_clips]

        trace_filter("Auto quality threshold" if auto_clip_count else "manual clip count", unique, candidates)
        selected_ids = {id(c) for c in candidates}
        for candidate in proposed_candidates:
            trace.event("candidate.decision", candidate_id=candidate.id, start=candidate.start_time,
                        end=candidate.end_time, score=candidate.score, ai_score=candidate.ai_score,
                        selected=id(candidate) in selected_ids,
                        reason=candidate.reason)
        trace.event("selection.summary", proposed=len(proposed_candidates), selected=len(candidates))

        if visual_review and candidates:
            from server.core.visual_reviewer import review_candidate
            for index, clip in enumerate(candidates):
                proc.raise_if_cancelled()
                report(f"Reviewing visual evidence {index+1}/{len(candidates)} (sampled frames)...", 49)
                try:
                    clip.visual_review = review_candidate(video_path, clip)
                    trace.event("visual.result", clip_id=clip.id, result=clip.visual_review)
                except Exception as stage_error:
                    trace.event("stage.failed", stage=current_stage[0], error_type=type(stage_error).__name__, error=str(stage_error))
                    proc.raise_if_cancelled()
                    report("Visual review unavailable; keeping existing selections. Check gemma3:4b in Ollama.", 49)
                    break

        # Write and check final clip copy. Unavailable or unsupported generation
        # falls back to this clip's source wording, not unverified ranking copy.
        if use_llm and any(c.full_text for c in candidates):
            report("Writing and checking clip titles & descriptions with local AI...", 49)
            try:
                from server.core.hook_writer import generate_clip_copy_llm, source_clip_copy
                for clip in candidates:
                    if not clip.full_text:
                        continue
                    try:
                        copy = generate_clip_copy_llm(
                            clip.full_text or clip.hook_text,
                            current_hook=clip.hook_text,
                            model=llm_model,
                        )
                    except Exception as stage_error:
                        trace.event("stage.failed", stage=current_stage[0], error_type=type(stage_error).__name__, error=str(stage_error))
                        copy = None
                    if not copy:
                        copy = source_clip_copy(clip.full_text)
                    trace.event("copy.result", clip_id=clip.id, copy=copy,
                                source_fallback=copy.get("description", "").startswith("From the clip:"))
                    if copy.get("hook"):
                        clip.hook_text = copy["hook"]
                    if copy.get("title"):
                        clip.title = copy["title"]
                    if copy.get("description"):
                        clip.description = copy["description"]
            except Exception as stage_error:
                trace.event("stage.failed", stage=current_stage[0], error_type=type(stage_error).__name__, error=str(stage_error))
                pass

        from server.core.hook_writer import ensure_distinct_clip_titles
        ensure_distinct_clip_titles(candidates)
        for clip in candidates:
            trace.event("clip.final", clip=clip.model_dump())

        # Generate subtitle files
        srt_path = str(job_dir / "captions.srt")
        vtt_path = str(job_dir / "captions.vtt")
        ass_path = str(job_dir / "captions.ass")
        report("Writing transcript & caption files...", 50)
        generate_srt(segments, srt_path)
        generate_vtt(segments, vtt_path)
        try:
            from server.core.caption_styler import generate_karaoke_captions
            generate_karaoke_captions(
                segments,
                ass_path,
                style_preset=caption_style,
                font_size=font_size,
                font_name=font_name,
                primary_color=primary_color,
                highlight_color=highlight_color,
                outline_color=outline_color,
                outline_width=outline_width,
                chunk_size=chunk_size,
                uppercase=uppercase,
                bold=bold,
                italic=italic,
                position=position,
                intro_caption=intro_caption,
                intro_caption_duration=intro_caption_duration,
                intro_font_size=intro_font_size,
                intro_style=intro_style,
            )
        except Exception as stage_error:
            trace.event("stage.failed", stage=current_stage[0], error_type=type(stage_error).__name__, error=str(stage_error))
            ass_path = None

        results: List[ClipResult] = []
        total = len(candidates)
        for idx, clip in enumerate(candidates, start=1):
            # Use the detected hook as the human-readable clip name. Keep the
            # job folder for project cleanup, while each clip gets its own
            # title folder containing its rendered assets.
            raw_title = clip.title or clip.hook_text or f"highlight_{idx}"
            title_slug = re.sub(r"[^A-Za-z0-9 _-]+", "", raw_title).strip()
            title_slug = re.sub(r"\s+", "_", title_slug)[:70].strip("._- ") or f"highlight_{idx}"
            clip_dir = job_dir / f"{idx:02d}_{title_slug}"
            clip_dir.mkdir(parents=True, exist_ok=True)
            clip_filename = f"{title_slug}.mp4"
            output_clip_path = str(clip_dir / clip_filename)

            clip_progress = 55 + int(35 * (idx - 1) / max(1, total))
            crop_offset = None
            crop_expr = None
            if vertical_crop:
                report(f"Tracking speaker for clip {idx}/{total}...", clip_progress)
                # #16.3 Diarization-driven active-speaker crop: build a trajectory
                # that follows whoever is talking. Falls back to the visual
                # head-motion crop when it can't (no diar, single position, etc).
                if speaker_aware_crop and diar_segments:
                    try:
                        from server.core.ffmpeg_tools import build_crop_x_expression
                        traj = self.face_tracker.get_diarized_speaker_trajectory(
                            video_path, clip.start_time, clip.end_time, diar_segments,
                            aspect_ratio=aspect_ratio or "9:16",
                        )
                        if traj:
                            # Trajectory crop_x values are already clamped to
                            # [0, width-crop_width] per keyframe, so linear
                            # interpolation can't overshoot; the clip() bound is
                            # just a wide safety net.
                            crop_expr = build_crop_x_expression(
                                traj, min_x=0.0, max_x=100000.0,
                            )
                    except Exception as stage_error:
                        trace.event("stage.failed", stage=current_stage[0], error_type=type(stage_error).__name__, error=str(stage_error))
                        crop_expr = None
                if crop_expr is None:
                    crop_offset = self.face_tracker.get_speaker_center_x(
                        video_path, clip.start_time, clip.end_time
                    )

            # Generate clip-specific animated karaoke subtitle
            clip_ass = str(clip_dir / f"{title_slug}.ass")
            clip_srt = str(clip_dir / f"{title_slug}.srt")
            # Segments that overlap the clip window (partial overlap counts so a
            # word straddling the boundary is still captioned).
            clip_segs = [s for s in segments if s.start < clip.end_time and s.end > clip.start_time]
            # Auto-generate the intro hook from THIS clip's own hook/title when
            # the intro toggle is on but the optional custom text was left blank.
            effective_intro = resolve_intro_caption(clip, intro_caption, intro_enabled)
            # Keep the editable word list consistent with the captions actually rendered.
            clip.words = [word for seg in clip_segs for word in (seg.words or [])
                          if word.end > clip.start_time and word.start < clip.end_time]
            if clip_segs:
                generate_srt(clip_segs, clip_srt)
                try:
                    from server.core.caption_styler import generate_karaoke_captions
                    generate_karaoke_captions(
                        clip_segs,
                        clip_ass,
                        style_preset=caption_style,
                        font_size=font_size,
                        font_name=font_name,
                        primary_color=primary_color,
                        highlight_color=highlight_color,
                        outline_color=outline_color,
                        outline_width=outline_width,
                        chunk_size=chunk_size,
                        uppercase=uppercase,
                        bold=bold,
                        italic=italic,
                        position=position,
                        intro_caption=effective_intro,
                        intro_caption_duration=intro_caption_duration,
                        intro_font_size=intro_font_size,
                        intro_style=intro_style,
                    )
                except Exception as stage_error:
                    trace.event("stage.failed", stage=current_stage[0], error_type=type(stage_error).__name__, error=str(stage_error))
                    clip_ass = None
            else:
                # Silent windows must not reuse the full-video captions or its hook.
                generate_srt([], clip_srt)
                from server.core.caption_styler import generate_karaoke_captions
                generate_karaoke_captions([], clip_ass, style_preset=caption_style,
                    intro_caption=effective_intro, intro_caption_duration=intro_caption_duration,
                    intro_font_size=intro_font_size, intro_style=intro_style)

            report(f"Rendering clip {idx}/{total}...", clip_progress + int(35 / max(1, total)))
            sub_to_burn = clip_ass if (clip_ass and os.path.exists(clip_ass)) else (clip_srt if os.path.exists(clip_srt) else None)
            
            render_clip(
                input_video=video_path,
                output_video=output_clip_path,
                start_time=clip.start_time,
                end_time=clip.end_time,
                aspect_ratio=aspect_ratio or ("9:16" if vertical_crop else None),
                crop_x_offset=crop_offset,
                crop_x_expr=crop_expr,
                burn_captions=burn_captions,
                subtitle_path=sub_to_burn,
                normalize_audio=normalize_audio,
                auto_zoom=auto_zoom,
                music_path=music_path,
                music_volume=music_volume,
                duck_music=duck_music,
            )

            trace.event("render.completed", clip_id=clip.id, output_file=output_clip_path,
                        bytes=os.path.getsize(output_clip_path) if os.path.exists(output_clip_path) else 0)

            # Optional profanity bleep/mute
            if (bleep_profanity or mute_profanity) and os.path.exists(output_clip_path):
                try:
                    from server.core.word_filter import find_profanity_timestamps, apply_bleep_or_mute
                    swear_ts = find_profanity_timestamps(clip_segs)
                    if swear_ts:
                        censored_path = str(job_dir / f"clip_{idx}_censored.mp4")
                        mode = "mute" if mute_profanity else "bleep"
                        apply_bleep_or_mute(output_clip_path, censored_path, swear_ts, mode=mode)
                        if os.path.exists(censored_path):
                            import shutil
                            shutil.move(censored_path, output_clip_path)
                except Exception as stage_error:
                    trace.event("stage.failed", stage=current_stage[0], error_type=type(stage_error).__name__, error=str(stage_error))
                    pass

            # Optional dead air removal
            if remove_silence and os.path.exists(output_clip_path):
                try:
                    from server.core.silence_cutter import remove_silence as cut_dead_air
                    tight_path = str(job_dir / f"clip_{idx}_tight.mp4")
                    cut_dead_air(output_clip_path, tight_path)
                    if os.path.exists(tight_path):
                        import shutil
                        shutil.move(tight_path, output_clip_path)
                except Exception as stage_error:
                    trace.event("stage.failed", stage=current_stage[0], error_type=type(stage_error).__name__, error=str(stage_error))
                    pass

            # Generate high quality video poster / thumbnail
            thumb_path = str(clip_dir / f"{title_slug}_thumb.jpg")
            try:
                if os.path.exists(output_clip_path):
                    extract_best_thumbnail(output_clip_path, thumb_path, timestamp=0.5)
            except Exception as stage_error:
                trace.event("stage.failed", stage=current_stage[0], error_type=type(stage_error).__name__, error=str(stage_error))
                pass

            # Determine layout string for potential re-render
            if vertical_crop and aspect_ratio == "9:16":
                layout_str = "vertical"
            elif aspect_ratio == "full" or aspect_ratio is None:
                layout_str = "full"
            else:
                layout_str = "vertical"

            results.append(
                ClipResult(
                    clip_id=clip.id,
                    visual_review=clip.visual_review,
                    ai_score=clip.ai_score,
                    title=clip.title,
                    score=clip.score,
                    start_time=clip.start_time,
                    end_time=clip.end_time,
                    duration=clip.duration,
                    hook_text=clip.hook_text,
                    output_file=output_clip_path,
                    source_file=video_path,
                    intro_caption=effective_intro or "",
                    captions_burned=bool(burn_captions and sub_to_burn and clip_segs),
                    aspect_ratio=aspect_ratio,
                    description=getattr(clip, "description", "") or "",
                    full_text=getattr(clip, "full_text", "") or "",
                    virality=clip.virality,
                    words=clip.words,
                    thumbnail_path=thumb_path if os.path.exists(thumb_path) else None,
                    srt_path=clip_srt if os.path.exists(clip_srt) else None,
                    vtt_path=vtt_path if os.path.exists(vtt_path) else None,
                    ass_path=clip_ass if (clip_ass and os.path.exists(clip_ass)) else None,
                    layout=layout_str,
                    cam_video=None,
                    cam_scale=0.3,
                    cam_position="bottom-right",
                    crop_x_offset=crop_offset,
                )
            )

        # Cleanup temp audio
        if os.path.exists(temp_audio):
            os.remove(temp_audio)

        if os.path.exists(action_audio):
            os.remove(action_audio)

        # Free GPU memory now that the job is done, so VRAM drops back to ~idle
        # instead of staying reserved by Whisper/YOLO/PyTorch cache + the LLM.
        self._free_gpu_memory(unload_llm_model=llm_model if use_llm else None)

        report("Done!" if results else "Analysis finished: no clips selected. Review speech tracks or enable unreviewed gameplay suggestions, then retry.", 100)
        trace.event("pipeline.completed", outputs=[c.output_file for c in results])
        if diagnostic_job_id is None:
            trace.unbind()
        return results

def select_auto_candidates(candidates, limit=None):
    """Keep strong distinct moments without a fixed clip quota."""
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda candidate: candidate.score, reverse=True)
    threshold = max(5.0, ordered[0].score * 0.65)
    selected = [candidate for candidate in ordered if candidate.score >= threshold]
    return selected if limit is None else selected[:limit]


def filter_auto_evidence(candidates, require_ai=False, include_unreviewed_action=False):
    """Avoid filling Auto results with unreviewed motion peaks or tiny speech fragments."""
    selected=[]
    for candidate in candidates:
        spoken=len(re.findall(r"\w+", candidate.full_text or '')) >= 6
        action=candidate.id.startswith('action_')
        if candidate.ai_score is not None and candidate.ai_score < 5:
            continue  # Action-origin windows do not bypass an explicit AI rejection.
        if not spoken:
            if action and include_unreviewed_action:
                selected.append(candidate)
            continue
        if require_ai and candidate.ai_score is None:
            if not (action and include_unreviewed_action):
                continue
        selected.append(candidate)
    return selected


def resolve_intro_caption(clip, custom, enabled):
    """Detector labels are metadata, never publishable opening copy."""
    if not enabled:
        return None
    if custom and custom.strip():
        return custom.strip()
    hook = (clip.hook_text or '').strip()
    generic = r"^(?:(?:motion|action|high.energy) (?:highlight|moment|peak)|audio\s*/\s*motion peak)\b"
    if hook and not re.match(generic, hook, re.I):
        return hook
    # A silent action candidate has no grounded automatic headline.
    text = (clip.full_text or '').strip()
    return re.split(r'(?<=[.!?])\s+', text)[0][:90] or None
