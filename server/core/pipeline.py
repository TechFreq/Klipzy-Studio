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
from server.core.transcriber import Transcriber
from server.core.highlight_detector import HighlightDetector
from server.core.audio_energy import detect_highlights_audio_energy
from server.core.llm_detector import detect_highlights_llm
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
            raw = f"{os.path.getsize(video_path)}:{int(st.st_mtime)}"
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
            if not data:
                return None
            return [TranscriptSegment(**s) for s in data]
        except Exception:
            return None

    def _save_cache(self, video_path: str, model: str, segments: List[TranscriptSegment]) -> None:
        key = f"{self._video_fingerprint(video_path)}_{model}"
        cache_file = self._transcript_cache_path() / f"{key}.json"
        try:
            cache_file.write_text(
                json.dumps([s.model_dump() for s in segments], ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

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

    def process_video(
        self,
        video_path: str,
        vertical_crop: bool = True,
        aspect_ratio: Optional[str] = "9:16",
        max_clips: int = 5,
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
        progress_callback=None,
    ) -> List[ClipResult]:
        """
        End-to-end pipeline: long video -> rendered shorts.
        """
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        available, ffmpeg_err = check_ffmpeg()
        if not available:
            raise RuntimeError(ffmpeg_err)

        self.detector.min_duration = min_duration
        self.detector.max_duration = max_duration

        video_name = Path(video_path).stem
        job_id = uuid.uuid4().hex[:8]
        job_dir = self.output_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        temp_audio = str(job_dir / "temp_audio.wav")

        def report(step: str, pct: int):
            if progress_callback:
                progress_callback(step, pct)

        report("Transcribing with Whisper...", 25)
        effective_model = whisper_model or self.whisper_model or "base"
        # Reuse transcript if we've already transcribed this exact video with
        # this model — makes caption/format tweaks near-instant.
        segments = self._cached_segments(video_path, effective_model)
        if segments is None:
            report("Extracting audio...", 12)
            extract_audio(video_path, temp_audio)
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
            self._save_cache(video_path, effective_model, segments)

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
                        extract_audio(video_path, temp_audio)
                    dres = diarize(temp_audio)
                    if dres.get("available"):
                        diar_segments = dres.get("segments", []) or None
            except Exception:
                diar_segments = None

        report("Finding highlight moments...", 38)
        candidates: List[ClipCandidate] = self.detector.detect_highlights_heuristic(segments)

        if use_audio_energy:
            report("Scanning audio energy peaks...", 42)
            energy_clips = detect_highlights_audio_energy(
                temp_audio, segments, min_duration=min_duration, max_duration=max_duration
            )
            candidates.extend(energy_clips)

        if use_llm:
            report("Asking local LLM for viral moments...", 46)
            llm_clips = detect_highlights_llm(segments, model=llm_model)
            candidates.extend(llm_clips)

        # Gameplay fallback: if there's no speech to score (shooters/Warzone,
        # music-only footage), find the loudest action moments straight from the
        # audio envelope — gunfights/killstreaks — NVIDIA-Highlights-style.
        if not candidates:
            report("No speech found — scanning for action moments...", 44)
            from server.core.audio_energy import detect_action_highlights
            candidates = detect_action_highlights(
                temp_audio, min_duration=min_duration, max_duration=max_duration,
                top_k=max_clips, video_path=video_path,
            )

        # Sanity floor: never emit a degenerate sub-clip regardless of source.
        # (A loud one-word segment must not survive as a fraction-of-a-second clip.)
        floor = min(min_duration, 5.0)
        candidates = [c for c in candidates if c.duration >= floor]

        # Semantic "rank-and-refine": let the local LLM SCORE the real candidate
        # windows (and write hook/title) rather than invent timestamps — grounded
        # selection, no hallucinated cuts. Only the top pool is sent to keep the
        # prompt tight; degrades to the heuristic ordering if Ollama is absent.
        if use_llm and candidates:
            try:
                from server.core.llm_detector import rank_candidates_llm
                report("Ranking clips with local AI...", 48)
                ordered = sorted(candidates, key=lambda c: c.score, reverse=True)
                pool, rest = ordered[:12], ordered[12:]
                candidates = rank_candidates_llm(pool, model=llm_model, preset=caption_style) + rest
            except Exception:
                pass

        # #16.2 Speaker-aware selection: gently boost candidates that stay on one
        # speaker or a clean two-way exchange, and dampen messy 3+ speaker /
        # talk-over windows. Mutates scores in place; the sort below picks it up.
        if speaker_aware_selection and diar_segments:
            try:
                from server.core.diarizer import rank_clips_by_speaker
                rank_clips_by_speaker(candidates, diar_segments)
            except Exception:
                pass

        # Deduplicate by time-overlap, then by hook/title: the heuristic and
        # audio-energy detectors often land on the SAME moment with slightly
        # different bounds (so time-overlap alone misses it). Keep the
        # higher-scoring pick per distinct hook.
        candidates = self.detector._deduplicate(candidates)
        candidates.sort(key=lambda c: c.score, reverse=True)
        seen_hooks = set()
        unique: List[ClipCandidate] = []
        for c in candidates:
            key = (c.title or c.hook_text or "").strip().lower()[:40]
            if key and key in seen_hooks:
                continue
            seen_hooks.add(key)
            unique.append(c)
        candidates = unique[:max_clips]

        # Viral copywriting pass (opt-in via use_llm): rewrite each FINAL clip's
        # hook + title and add an engaging 1-2 sentence description — the
        # CapCut / OpusClips-style copy that shows on the clip card and drives the
        # auto intro hook. Runs only on the small final set (<= max_clips) and is
        # grounded in each clip's own transcript. Per-clip try/except means a
        # missing/offline Ollama silently keeps the heuristic hook/title.
        if use_llm and candidates:
            report("Writing viral titles & descriptions with local AI...", 49)
            try:
                from server.core.hook_writer import generate_clip_copy_llm
                for clip in candidates:
                    try:
                        copy = generate_clip_copy_llm(
                            clip.full_text or clip.hook_text,
                            current_hook=clip.hook_text,
                            model=llm_model,
                        )
                    except Exception:
                        copy = None
                    if not copy:
                        continue
                    if copy.get("hook"):
                        clip.hook_text = copy["hook"]
                    if copy.get("title"):
                        clip.title = copy["title"]
                    if copy.get("description"):
                        clip.description = copy["description"]
            except Exception:
                pass

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
            )
        except Exception:
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
                    except Exception:
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
            effective_intro = intro_caption
            if not (intro_caption and str(intro_caption).strip()) and intro_enabled:
                effective_intro = (clip.hook_text or clip.title or raw_title or "").strip() or None
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
                    )
                except Exception:
                    clip_ass = None
            else:
                clip_srt = srt_path
                clip_ass = ass_path

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
                except Exception:
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
                except Exception:
                    pass

            # Generate high quality video poster / thumbnail
            thumb_path = str(clip_dir / f"{title_slug}_thumb.jpg")
            try:
                if os.path.exists(output_clip_path):
                    extract_best_thumbnail(output_clip_path, thumb_path, timestamp=0.5)
            except Exception:
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
                    title=clip.title,
                    score=clip.score,
                    start_time=clip.start_time,
                    end_time=clip.end_time,
                    duration=clip.duration,
                    hook_text=clip.hook_text,
                    output_file=output_clip_path,
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

        # Free GPU memory now that the job is done, so VRAM drops back to ~idle
        # instead of staying reserved by Whisper/YOLO/PyTorch cache + the LLM.
        self._free_gpu_memory(unload_llm_model=llm_model if use_llm else None)

        report("Done!", 100)
        return results