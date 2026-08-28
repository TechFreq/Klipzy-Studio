"""
Main processing pipeline orchestrator.
Coordinates: Audio Extraction -> Transcription -> Highlight Finding -> Smart Crop -> Rendering.
"""

import os
import uuid
from pathlib import Path
from typing import List, Optional

from server.core.ffmpeg_tools import (
    check_ffmpeg, extract_audio, render_clip,
    generate_srt, generate_vtt, get_video_duration,
)
from server.core.transcriber import Transcriber
from server.core.highlight_detector import HighlightDetector
from server.core.audio_energy import detect_highlights_audio_energy
from server.core.llm_detector import detect_highlights_llm
from server.core.face_tracker import FaceTracker
from server.models import ClipCandidate, ClipResult, TranscriptSegment


class VideoClipperEngine:
    def __init__(self, output_dir: str = "output", whisper_model: str = "base"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.transcriber = Transcriber(model_size=whisper_model)
        self.detector = HighlightDetector()
        self.face_tracker = FaceTracker()

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
        burn_captions: bool = True,
        caption_style: str = "viral_yellow",
        remove_silence: bool = False,
        bleep_profanity: bool = False,
        mute_profanity: bool = False,
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

        report("Extracting audio...", 10)
        extract_audio(video_path, temp_audio)

        report("Transcribing with Whisper...", 25)
        segments = self.transcriber.transcribe(temp_audio, language=language)

        report("Finding highlight moments...", 50)
        candidates: List[ClipCandidate] = self.detector.detect_highlights_heuristic(segments)

        if use_audio_energy:
            energy_clips = detect_highlights_audio_energy(temp_audio, segments)
            candidates.extend(energy_clips)

        if use_llm:
            llm_clips = detect_highlights_llm(segments)
            candidates.extend(llm_clips)

        # Deduplicate + sort by score + limit
        candidates = self.detector._deduplicate(candidates)
        candidates.sort(key=lambda c: c.score, reverse=True)
        candidates = candidates[:max_clips]

        # Generate subtitle files
        srt_path = str(job_dir / "captions.srt")
        vtt_path = str(job_dir / "captions.vtt")
        ass_path = str(job_dir / "captions.ass")
        generate_srt(segments, srt_path)
        generate_vtt(segments, vtt_path)
        try:
            from server.core.caption_styler import generate_animated_ass
            generate_animated_ass(segments, ass_path)
        except Exception:
            ass_path = None

        results: List[ClipResult] = []
        total = len(candidates)
        for idx, clip in enumerate(candidates, start=1):
            clip_filename = f"clip_{idx}.mp4"
            output_clip_path = str(job_dir / clip_filename)

            crop_offset = None
            if vertical_crop:
                report(f"Tracking speaker for clip {idx}/{total}...", 55 + int(35 * (idx - 1) / max(1, total)))
                crop_offset = self.face_tracker.get_speaker_center_x(
                    video_path, clip.start_time, clip.end_time
                )

            # Generate clip-specific animated karaoke subtitle
            clip_ass = str(job_dir / f"clip_{idx}.ass")
            clip_srt = str(job_dir / f"clip_{idx}.srt")
            clip_segs = [s for s in segments if s.start >= clip.start_time and s.end <= clip.end_time]
            if clip_segs:
                generate_srt(clip_segs, clip_srt)
                try:
                    from server.core.caption_styler import generate_animated_ass
                    generate_animated_ass(clip_segs, clip_ass, style_preset=caption_style)
                except Exception:
                    clip_ass = None
            else:
                clip_srt = srt_path
                clip_ass = ass_path

            report(f"Rendering clip {idx}/{total}...", 55 + int(35 * idx / max(1, total)))
            sub_to_burn = clip_ass if (clip_ass and os.path.exists(clip_ass)) else (clip_srt if os.path.exists(clip_srt) else None)
            
            render_clip(
                input_video=video_path,
                output_video=output_clip_path,
                start_time=clip.start_time,
                end_time=clip.end_time,
                aspect_ratio=aspect_ratio or ("9:16" if vertical_crop else None),
                crop_x_offset=crop_offset,
                burn_captions=burn_captions,
                subtitle_path=sub_to_burn,
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
                    virality=clip.virality,
                    words=clip.words,
                    srt_path=clip_srt if os.path.exists(clip_srt) else None,
                    vtt_path=vtt_path if os.path.exists(vtt_path) else None,
                    ass_path=clip_ass if (clip_ass and os.path.exists(clip_ass)) else None,
                )
            )

        # Cleanup temp audio
        if os.path.exists(temp_audio):
            os.remove(temp_audio)

        report("Done!", 100)
        return results