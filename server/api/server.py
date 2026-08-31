"""
FastAPI server exposing Klipzy Studio as a local API.
The Electron UI talks to this server over HTTP on localhost.
"""

import logging
import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from server.models import (
    ChatRequest, ChatResponse, ProcessRequest, ProcessResponse, ClipResult,
    ExportProjectRequest, ExportProjectResponse, SubtitleRegenRequest,
    TrimRequest, TrimResponse, CustomRenderRequest,
    ExportMediaRequest, ExportMediaResponse, ExportCompileRequest, ExportCompileResponse,
    ExportStandaloneRequest, ExportStandaloneResponse, ClipBundleRequest, ClipBundleResponse, DeleteProjectRequest,
    DetectSilenceRequest, DetectSilenceResponse, RemoveSilenceRequest, RemoveSilenceResponse,
    BleepMuteRequest, BleepMuteResponse, CaptionPresetInfo,
    ThumbnailRequest, ThumbnailResponse, SocialMetadataRequest, SocialMetadataResponse,
    MultiAspectExportRequest, MultiAspectExportResponse,
    OverlayRequest, OverlayResponse, EmojiSuggestRequest, EmojiSuggestResponse,
)
from server.core.pipeline import VideoClipperEngine
from server.core.edit_chat import EditChat
from server.core.export_tools import export_fcpxml, export_edl, export_capcut_draft
from server.core.ffmpeg_tools import (
    check_ffmpeg, get_media_info, get_video_duration, detect_hw_encoder, render_clip,
    export_clip_as, concat_clips, export_standalone_audio,
    extract_best_thumbnail,
)
from server.logging_setup import setup_logging
from server.auth import (
    LocalTokenAuthMiddleware, TOKEN_HEADER, auth_disabled, resolve_token,
)

setup_logging()  # logs/server.log (+ console), also used by main.py

app = FastAPI(title="Klipzy Studio Server", version="1.0.0")

# Shared secret for this run. Electron passes one in via KLIPZY_API_TOKEN;
# otherwise one is generated and written to logs/api_token.txt for scripts.
API_TOKEN = resolve_token()

# The renderer runs from a file:// page, which Chromium reports as the literal
# origin "null". Everything else is an explicit localhost entry. A wildcard here
# would let any website in any browser drive this API, including the endpoints
# that launch installers - so it is deliberately NOT "*". Credentials are off
# because auth travels in a header, not a cookie.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "null",
        "file://",
        "http://localhost",
        "http://127.0.0.1",
        f"http://localhost:{os.environ.get('KLIPZY_PORT', '8765')}",
        f"http://127.0.0.1:{os.environ.get('KLIPZY_PORT', '8765')}",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", TOKEN_HEADER],
)

# Registered after CORS so preflight still gets a proper CORS response.
app.add_middleware(LocalTokenAuthMiddleware, token=API_TOKEN)

if auth_disabled():
    _startup_logger = logging.getLogger("klipzy")
    _startup_logger.warning(
        "KLIPZY_DISABLE_AUTH is set - the local API is UNAUTHENTICATED. "
        "Any website open in a browser can reach it. Unset it for normal use."
    )

# In-memory job store. To avoid unbounded growth over a long session (each
# /process stores full clip metadata + words), keep only the most recent jobs
# and drop the oldest completed/failed ones.
JOBS: Dict[str, dict] = {}
_JOBS_ORDER: List[str] = []
MAX_JOBS = 60

# A single background worker. Rendering clips from a long video is CPU/GPU
# heavy, so parallel jobs would thrash Whisper + FFmpeg + YOLO and produce
# confusing progress. New /process requests are queued and run serially; each
# job stays independently addressable via /job/{job_id} and cancelable.
_JOB_LOCK = threading.Lock()
_ACTIVE_JOB_ID: Optional[str] = None
_WORKER_THREAD: Optional[threading.Thread] = None
_QUEUE_JOBS: List[str] = []  # FIFO of job_ids waiting on the single worker

# Request + cancellation state kept separate from the serializable JOBS payload
# so /job/{job_id} can return the job dict as-is (no Event/model objects leak).
_JOB_REQUESTS: Dict[str, ProcessRequest] = {}
_JOB_CANCEL: Dict[str, threading.Event] = {}

# Where generated clips, captions, transcripts and temp files are saved.
# Mirrors VideoClipperEngine's repo-anchored default so the folder can be
# switched at runtime (see /output-folder) without touching the source video.
DEFAULT_OUTPUT_DIR = str(Path(__file__).resolve().parents[2] / "output")
DEFAULT_OUTPUT_ROOT = Path(DEFAULT_OUTPUT_DIR).resolve()

ENGINE = VideoClipperEngine()
# KLIPZY_OUTPUT_DIR is respected at startup only; runtime changes go through
# the /output-folder endpoint and mutate OUTPUT_ROOT below.
_env_output = os.environ.get("KLIPZY_OUTPUT_DIR", "").strip()
if _env_output and Path(_env_output).expanduser().is_absolute():
    OUTPUT_ROOT = Path(_env_output).expanduser().resolve()
else:
    OUTPUT_ROOT = Path(ENGINE.output_dir).resolve()

CHAT = EditChat()


def _ensure_output_root():
    """Return the current runtime output folder, creating it if missing."""
    global OUTPUT_ROOT
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    return OUTPUT_ROOT.resolve()


def _sync_output_root(root: Path):
    """Point the global ENGINE and OUTPUT_ROOT at a new output folder.

    Only affects where *new* jobs are written; previously generated clips keep
    their original paths. The job store is left intact so running jobs continue
    to write to their own output_dir referenced at creation time.
    """
    global OUTPUT_ROOT
    if not root.is_absolute():
        root = _ensure_output_root()
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    OUTPUT_ROOT = root
    if ENGINE is not None:
        ENGINE.output_dir = root


def _prune_jobs():
    """Drop oldest terminal jobs when the ring buffer exceeds MAX_JOBS.
    Active/queued jobs are kept; we only stop scanning once we've removed what
    we can (bounded to MAX_JOBS work so a pathological session can't spin).
    """
    scanned = 0
    while len(JOBS) > MAX_JOBS and _JOBS_ORDER and scanned < MAX_JOBS:
        oldest = _JOBS_ORDER.pop(0)
        scanned += 1
        if oldest in JOBS and JOBS[oldest].get("status") in ("completed", "failed", "cancelled"):
            del JOBS[oldest]
            _JOB_REQUESTS.pop(oldest, None)
            _JOB_CANCEL.pop(oldest, None)
        elif oldest in JOBS:
            # Still running - move to back so it eventually gets pruned after
            # it completes, and stop scanning to avoid a spin.
            _JOBS_ORDER.append(oldest)
            break


class _CancelledError(Exception):
    """Raised inside a background job when cancellation is requested."""


def _run_job(job_id: str) -> None:
    """Execute one queued job, recording per-job status in JOBS."""
    req = _JOB_REQUESTS.get(job_id)
    cancel_ev = _JOB_CANCEL.get(job_id)
    if req is None:
        JOBS[job_id]["status"] = "failed"
        JOBS[job_id]["error"] = "Request no longer present in job store."
        return

    def progress_callback(step: str, pct: int):
        if cancel_ev is not None and cancel_ev.is_set():
            raise _CancelledError()
        JOBS[job_id]["progress"] = pct
        JOBS[job_id]["step"] = step

    try:
        JOBS[job_id]["status"] = "processing"
        clips = ENGINE.process_video(
            video_path=req.video_path,
            vertical_crop=req.vertical_crop,
            aspect_ratio=req.aspect_ratio,
            max_clips=req.max_clips,
            min_duration=req.min_duration,
            max_duration=req.max_duration,
            whisper_model=req.whisper_model,
            language=req.language,
            use_audio_energy=req.use_audio_energy,
            use_llm=req.use_llm,
            burn_captions=req.burn_captions,
            caption_style=req.caption_style or "viral_yellow",
            font_size=req.font_size,
            font_name=req.font_name,
            primary_color=req.primary_color,
            highlight_color=req.highlight_color,
            outline_color=req.outline_color,
            outline_width=req.outline_width,
            chunk_size=req.chunk_size,
            uppercase=req.uppercase,
            bold=req.bold,
            italic=req.italic,
            position=req.position,
            intro_caption=req.intro_caption,
            intro_caption_duration=req.intro_caption_duration,
            remove_silence=req.remove_silence,
            bleep_profanity=req.bleep_profanity,
            mute_profanity=req.mute_profanity,
            progress_callback=progress_callback,
        )
        if cancel_ev is not None and cancel_ev.is_set():
            raise _CancelledError()
        JOBS[job_id]["clips"] = [c.model_dump() for c in clips]
        JOBS[job_id]["status"] = "completed"
    except _CancelledError:
        JOBS[job_id]["status"] = "cancelled"
        JOBS[job_id]["error"] = "Job cancelled by user."
    except Exception as e:
        JOBS[job_id]["status"] = "failed"
        JOBS[job_id]["error"] = str(e)


def _drain_job_queue() -> None:
    """Run queued jobs serially until the FIFO is empty."""
    global _ACTIVE_JOB_ID
    while True:
        with _JOB_LOCK:
            if not _QUEUE_JOBS:
                return
            job_id = _QUEUE_JOBS.pop(0)
            _ACTIVE_JOB_ID = job_id
        try:
            _run_job(job_id)
        except Exception:
            # _run_job records per-job status itself; keep the worker alive.
            pass
        finally:
            with _JOB_LOCK:
                if _ACTIVE_JOB_ID == job_id:
                    _ACTIVE_JOB_ID = None


class HealthResponse(BaseModel):
    status: str
    ffmpeg_available: bool
    hw_encoder: str
    whisper_loaded: bool
    ollama_available: bool


@app.get("/health", response_model=HealthResponse)
def health():
    ffmpeg_ok, _ = check_ffmpeg()
    encoder, _ = detect_hw_encoder()
    ollama_ok = False
    try:
        import ollama
        ollama_ok = True
    except ImportError:
        pass
    whisper_ok = False
    try:
        import whisper  # noqa: F401
        whisper_ok = True
    except ImportError:
        pass
    return HealthResponse(
        status="ok",
        ffmpeg_available=ffmpeg_ok,
        hw_encoder=encoder,
        whisper_loaded=whisper_ok,
        ollama_available=ollama_ok,
    )


@app.post("/process", response_model=ProcessResponse)
def process_video(req: ProcessRequest):
    if not os.path.exists(req.video_path):
        raise HTTPException(status_code=400, detail=f"Video file not found: {req.video_path}")
    if not (1 <= req.max_clips <= 20):
        raise HTTPException(status_code=400, detail="max_clips must be between 1 and 20")
    if req.max_duration <= req.min_duration:
        raise HTTPException(status_code=400, detail="max_duration must be greater than min_duration")

    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {"status": "queued", "clips": [], "error": None, "progress": 0}
    _JOB_REQUESTS[job_id] = req
    _JOB_CANCEL[job_id] = threading.Event()
    _JOBS_ORDER.append(job_id)
    _prune_jobs()

    # Enqueue serially behind a single worker so a burst of /process calls
    # doesn't spawn N ffmpeg/Whisper processes at once.
    global _WORKER_THREAD
    with _JOB_LOCK:
        _QUEUE_JOBS.append(job_id)
        if _WORKER_THREAD is None or not _WORKER_THREAD.is_alive():
            _WORKER_THREAD = threading.Thread(target=_drain_job_queue, daemon=True)
            _WORKER_THREAD.start()

    return ProcessResponse(job_id=job_id, status="queued")


@app.get("/job/{job_id}")
def get_job(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="Job not found")
    return JOBS[job_id]


@app.post("/job/{job_id}/cancel")
def cancel_job(job_id: str):
    """Request cancellation of a queued or active job."""
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="Job not found")
    job = JOBS[job_id]
    status = job.get("status")
    if status in ("completed", "failed", "cancelled"):
        return {"job_id": job_id, "status": status, "message": "Already finished."}
    # Signal cancellation; the worker checks this at the next progress callback.
    ev = _JOB_CANCEL.get(job_id)
    if ev is not None:
        ev.set()
        job["status"] = "cancelling"
    return {"job_id": job_id, "status": job["status"], "message": "Cancellation requested."}


@app.get("/jobs")
def list_jobs():
    """Lightweight view of all known jobs (id, status, progress, step)."""
    items = []
    for job_id in _JOBS_ORDER:
        job = JOBS.get(job_id)
        if job is None:
            continue
        items.append({
            "job_id": job_id,
            "status": job.get("status"),
            "progress": job.get("progress", 0),
            "step": job.get("step"),
            "error": job.get("error"),
        })
    return {"jobs": items}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    return CHAT.chat(
        message=req.message,
        conversation_history=req.conversation_history,
        clip_context=req.clip_context,
    )


@app.post("/inspect")
def inspect_video(req: ProcessRequest):
    """Get video metadata without processing."""
    try:
        info = get_media_info(req.video_path)
        return info
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/export/project", response_model=ExportProjectResponse)
def export_project(req: ExportProjectRequest):
    """
    Exports clip cuts into Premiere Pro / DaVinci Resolve (FCPXML/EDL) or CapCut Draft JSON.
    """
    video_dir = os.path.dirname(os.path.abspath(req.video_path))
    stem = os.path.splitext(os.path.basename(req.video_path))[0]

    if req.format.lower() == "fcpxml":
        out_path = os.path.join(video_dir, f"{stem}_shorts.xml")
        export_fcpxml(req.video_path, req.clips, out_path, fps=req.fps)
        msg = "Exported Premiere Pro / DaVinci Resolve XML successfully"
    elif req.format.lower() == "edl":
        out_path = os.path.join(video_dir, f"{stem}_shorts.edl")
        export_edl(req.video_path, req.clips, out_path, fps=req.fps)
        msg = "Exported EDL timeline successfully"
    elif req.format.lower() == "capcut":
        out_path = os.path.join(video_dir, f"{stem}_capcut_draft.json")
        export_capcut_draft(req.video_path, req.clips, out_path)
        msg = "Exported CapCut Draft project structure successfully"
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {req.format}")

    return ExportProjectResponse(export_path=out_path, format=req.format, message=msg)


@app.post("/export/media", response_model=ExportMediaResponse)
def export_media(req: ExportMediaRequest):
    """
    Re-encode an existing rendered clip to a chosen container/codec:
      mp4 / mov / mkv / webm / av1 / gif (animated).
    Writes the new file beside the source (or to req.output_path if given).
    """
    if not os.path.exists(req.video_path):
        raise HTTPException(status_code=400, detail=f"Clip file not found: {req.video_path}")

    fmt = req.format.lower().lstrip(".")
    if fmt not in ("mp4", "mov", "mkv", "webm", "av1", "gif"):
        raise HTTPException(status_code=400, detail=f"Unsupported export format: {fmt}")

    video_dir = os.path.dirname(os.path.abspath(req.video_path))
    stem = os.path.splitext(os.path.basename(req.video_path))[0]
    out_suffix = "mp4" if fmt == "av1" else fmt
    out_path = req.output_path or os.path.join(video_dir, f"{stem}_export.{out_suffix}")

    try:
        exported_path, msg = export_clip_as(req.video_path, out_path, fmt)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return ExportMediaResponse(
        export_path=exported_path,
        format=fmt,
        duration=round(float(get_media_info(exported_path).get("format", {}).get("duration", 0.0)), 3),
        message=msg,
    )


@app.post("/export/clip-bundle", response_model=ClipBundleResponse)
def export_clip_bundle(req: ClipBundleRequest):
    """Save a chosen clip and matching MP3/SRT/ASS files in one named folder."""
    if not os.path.isfile(req.video_path):
        raise HTTPException(status_code=400, detail=f"Clip file not found: {req.video_path}")
    fmt = req.format.lower().lstrip(".")
    if fmt not in ("mp4", "mov", "mkv", "webm", "av1", "gif"):
        raise HTTPException(status_code=400, detail=f"Unsupported export format: {fmt}")
    safe = "".join(c for c in req.title if c.isalnum() or c in " _-").strip().replace(" ", "_")[:70] or "clip"
    export_dir = Path(req.output_dir).expanduser().resolve() / safe
    export_dir.mkdir(parents=True, exist_ok=True)
    out_suffix = "mp4" if fmt == "av1" else fmt
    video_out = export_dir / f"{safe}.{out_suffix}"
    try:
        export_clip_as(req.video_path, str(video_out), fmt)
        audio_out = export_dir / f"{safe}.mp3"
        export_standalone_audio(req.video_path, str(audio_out), "mp3")
        copied = {}
        for key, source, suffix in (("srt_path", req.srt_path, ".srt"), ("ass_path", req.ass_path, ".ass")):
            if source and os.path.isfile(source):
                target = export_dir / f"{safe}{suffix}"
                shutil.copy2(source, target)
                copied[key] = str(target)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return ClipBundleResponse(export_dir=str(export_dir), video_path=str(video_out),
                              audio_path=str(audio_out), srt_path=copied.get("srt_path"),
                              ass_path=copied.get("ass_path"), message="Clip bundle exported successfully")


@app.post("/export/thumbnail", response_model=ThumbnailResponse)
def export_thumbnail_endpoint(req: ThumbnailRequest):
    """Extract a thumbnail cover image from a clip at a given timestamp."""
    if not os.path.isfile(req.video_path):
        raise HTTPException(status_code=400, detail=f"Clip file not found: {req.video_path}")
    video_dir = os.path.dirname(os.path.abspath(req.video_path))
    stem = os.path.splitext(os.path.basename(req.video_path))[0]
    out_path = req.output_path or os.path.join(video_dir, f"{stem}_thumb.jpg")
    try:
        thumb = extract_best_thumbnail(req.video_path, out_path, timestamp=req.timestamp)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return ThumbnailResponse(thumbnail_path=thumb, message="Thumbnail extracted successfully")


@app.post("/social/metadata", response_model=SocialMetadataResponse)
def generate_social_metadata(req: SocialMetadataRequest):
    """
    Generate optimized social media titles, descriptions, and hashtags for a clip.
    Works locally using smart rule-based heuristic synthesis or local LLM if active.
    """
    hook = (req.hook_text or req.title or "Viral Moment").strip()
    raw_desc = (req.full_text or req.hook_text or "").strip()
    
    # Catchy titles formatted for Shorts / TikTok
    clean_hook = hook.replace('"', '').replace("'", "").strip()
    title_opt = f"🔥 {clean_hook[:60]}" if clean_hook else "🔥 Must Watch Moment"
    
    # Generate contextual hashtags
    base_tags = ["#shorts", "#viral", "#reels", "#tiktok", "#fyp", "#contentcreator"]
    text_lower = (raw_desc + " " + hook).lower()
    
    keyword_tags = []
    topics = {
        "podcast": ["#podcast", "#podcasthighlights", "#interview"],
        "gaming": ["#gaming", "#gamer", "#gameplay", "#streamer"],
        "money": ["#finance", "#moneytips", "#business", "#success"],
        "tech": ["#technology", "#tech", "#ai", "#coding"],
        "fitness": ["#fitness", "#workout", "#gym", "#motivation"],
        "mindset": ["#motivation", "#mindset", "#growth", "#inspiration"],
        "story": ["#storytime", "#mindblowing", "#truestory"],
    }
    for key, tags in topics.items():
        if key in text_lower or any(w in text_lower for w in ["crypto", "cash", "invest", "earn"] if key == "money"):
            keyword_tags.extend(tags)
            
    combined_tags = list(dict.fromkeys(base_tags + keyword_tags))[:10]
    hashtag_str = " ".join(combined_tags)
    
    formatted_post = (
        f"{title_opt}\n\n"
        f"👉 Watch this highlight: {clean_hook}\n\n"
        f"Drop your thoughts in the comments! 👇\n\n"
        f"{hashtag_str}"
    )
    
    return SocialMetadataResponse(
        title=title_opt,
        description=f"Highlight: {clean_hook}\nDuration: {req.duration:.1f}s",
        hashtags=combined_tags,
        formatted_post=formatted_post,
        disclaimer="AI-generated metadata — accuracy and tone may vary. Review before publishing."
    )


@app.post("/export/compile", response_model=ExportCompileResponse)
def export_compile(req: ExportCompileRequest):
    """
    Concatenate all generated clips into one highlights-reel media file
    (mp4 / mov / mkv / webm / gif animated).
    """
    if not req.clip_paths:
        raise HTTPException(status_code=400, detail="No clip paths provided for compile")

    fmt = req.format.lower().lstrip(".")
    if fmt not in ("mp4", "mov", "mkv", "webm", "av1", "gif"):
        raise HTTPException(status_code=400, detail=f"Unsupported export format: {fmt}")

    stem = "".join(c for c in req.title if c.isalnum() or c in "-_" ).strip() or "highlights_reel"
    out_path = req.output_path or os.path.join(
        os.path.dirname(os.path.abspath(req.clip_paths[0])),
        f"{stem}_reel.{fmt}",
    )

    try:
        compiled_path, msg, total_dur = concat_clips(req.clip_paths, out_path, fmt)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return ExportCompileResponse(
        export_path=compiled_path,
        format=fmt,
        clip_count=len(req.clip_paths),
        duration=round(total_dur, 3),
        message=msg,
    )


@app.post("/export/standalone", response_model=ExportStandaloneResponse)
def export_standalone(req: ExportStandaloneRequest):
    """
    Export standalone companion assets from the video or clips:
    - Audio only: audio_mp3, audio_wav, audio_flac, audio_aac, audio_m4a
    - Subtitles / Transcripts: sub_srt, sub_vtt, transcript_txt, transcript_json
    """
    if not req.video_path or not os.path.exists(req.video_path):
        raise HTTPException(status_code=400, detail=f"Source video not found: {req.video_path}")

    video_dir = os.path.dirname(os.path.abspath(req.video_path))
    stem = os.path.splitext(os.path.basename(req.video_path))[0]
    asset = req.asset_type.lower()

    if asset.startswith("audio_"):
        fmt = asset.split("_", 1)[1]
        out_path = req.output_path or os.path.join(video_dir, f"{stem}_audio.{fmt}")
        try:
            saved = export_standalone_audio(req.video_path, out_path, fmt)
            return ExportStandaloneResponse(
                export_path=saved,
                asset_type=asset,
                message=f"Exported standalone {fmt.upper()} audio track successfully",
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    elif asset in ("sub_srt", "sub_vtt", "transcript_txt", "transcript_json"):
        # Look for companion caption/transcript files in the video's folder or extract
        ext_map = {
            "sub_srt": ".srt",
            "sub_vtt": ".vtt",
            "transcript_txt": ".txt",
            "transcript_json": ".json",
        }
        target_ext = ext_map[asset]
        out_path = req.output_path or os.path.join(video_dir, f"{stem}_standalone{target_ext}")

        # Check if already generated in folder
        existing_candidate = os.path.join(video_dir, f"captions{target_ext}")
        if not os.path.exists(existing_candidate):
            existing_candidate = os.path.join(video_dir, f"{stem}{target_ext}")

        if os.path.exists(existing_candidate) and existing_candidate != out_path:
            import shutil
            shutil.copy2(existing_candidate, out_path)
            return ExportStandaloneResponse(
                export_path=out_path,
                asset_type=asset,
                message=f"Exported standalone {target_ext.upper().lstrip('.')} subtitle/transcript file",
            )
        elif os.path.exists(out_path):
            return ExportStandaloneResponse(
                export_path=out_path,
                asset_type=asset,
                message=f"Standalone {target_ext.upper().lstrip('.')} file ready at destination",
            )
        else:
            raise HTTPException(status_code=404, detail=f"No transcript/subtitle file generated yet for this project. Please run clipping first.")

    else:
        raise HTTPException(status_code=400, detail=f"Unsupported asset type: {req.asset_type}")


@app.post("/export/subtitles", response_model=ExportProjectResponse)
def regenerate_subtitles(req: SubtitleRegenRequest):
    """
    Regenerates per-clip subtitle files (SRT + animated karaoke captions) from edited words.
    Optionally re-renders the clip video to burn the updated subtitles in place.
    Returns the paths to the rewritten files so the renderer can apply them.
    """
    from server.core.caption_styler import generate_karaoke_captions
    from server.core.ffmpeg_tools import generate_srt, render_clip
    from server.models import TranscriptSegment, WordTimestamp

    words = [
        WordTimestamp(word=w.get("word", ""), start=float(w.get("start", 0)), end=float(w.get("end", 0)))
        for w in req.words if w.get("word")
    ]
    if not words:
        raise HTTPException(status_code=400, detail="No words provided for subtitle regeneration")

    seg = TranscriptSegment(
        id=0,
        start=words[0].start,
        end=words[-1].end,
        text=" ".join(w.word for w in words),
        words=words,
    )
    generate_srt([seg], req.output_path)

    ass_path = os.path.splitext(req.output_path)[0] + ".ass"
    generate_karaoke_captions(
        [seg],
        ass_path,
        style_preset=req.style_preset,
        font_size=req.font_size,
        font_name=req.font_name,
        primary_color=req.primary_color,
        highlight_color=req.highlight_color,
        outline_color=req.outline_color,
        outline_width=req.outline_width,
        chunk_size=req.chunk_size,
        uppercase=req.uppercase,
        bold=req.bold,
        italic=req.italic,
        position=req.position,
        intro_caption=req.intro_caption,
        intro_caption_duration=req.intro_caption_duration,
    )

    # If requested and video context is provided, re-render the clip to burn updated captions.
    re_rendered = False
    if req.re_render and req.clip_output_file and os.path.exists(req.clip_output_file):
        try:
            target_file = str(Path(req.clip_output_file).expanduser().resolve())
            temp_target = str(Path(target_file).with_suffix(f".new.{uuid.uuid4().hex[:6]}.mp4"))

            # Determine input source & segment bounds
            input_video = req.source_video if (req.source_video and os.path.exists(req.source_video)) else target_file
            if input_video == target_file or req.start_seconds is None or req.end_seconds is None:
                # Re-rendering the rendered clip itself: timeline starts at 0
                render_clip(
                    input_video=target_file,
                    output_video=temp_target,
                    start_time=0.0,
                    end_time=words[-1].end - words[0].start + 1.0,
                    aspect_ratio=req.aspect_ratio or "9:16",
                    burn_captions=True,
                    subtitle_path=ass_path,
                    layout=req.layout or "",
                    cam_video=req.cam_video,
                    cam_scale=req.cam_scale,
                    cam_position=req.cam_position,
                )
            else:
                # Re-rendering from original source video with preserved layout/cam
                render_clip(
                    input_video=input_video,
                    output_video=temp_target,
                    start_time=req.start_seconds,
                    end_time=req.end_seconds,
                    aspect_ratio=req.aspect_ratio or "9:16",
                    burn_captions=True,
                    subtitle_path=ass_path,
                    layout=req.layout or "",
                    cam_video=req.cam_video,
                    cam_scale=req.cam_scale,
                    cam_position=req.cam_position,
                    crop_x_offset=req.crop_x_offset,
                )

            if os.path.isfile(temp_target) and os.path.getsize(temp_target) > 0:
                shutil.move(temp_target, target_file)
                re_rendered = True
        except Exception as e:
            # Fall back gracefully to saving subtitle files without crashing the response
            pass

    msg = f"Regenerated subtitles at {req.output_path}"
    if re_rendered:
        msg += " and updated burned clip"

    return ExportProjectResponse(
        export_path=req.output_path,
        format="srt",
        message=msg,
        re_rendered=re_rendered,
    )

@app.get("/caption-presets")
def get_caption_presets():
    """Returns the list of 20+ creator caption style presets."""
    from server.core.caption_presets import get_available_presets
    return get_available_presets()


@app.post("/tools/detect-silence", response_model=DetectSilenceResponse)
def api_detect_silence(req: DetectSilenceRequest):
    """Find dead-air / silent intervals in video/audio."""
    from server.core.silence_cutter import detect_silence_intervals
    try:
        intervals = detect_silence_intervals(
            req.media_path,
            noise_threshold_db=req.noise_threshold_db,
            min_silence_duration=req.min_silence_duration,
        )
        total_sil = round(sum(i["duration"] for i in intervals), 2)
        return DetectSilenceResponse(
            intervals=intervals,
            total_silence=total_sil,
            silence_count=len(intervals),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/tools/remove-silence", response_model=RemoveSilenceResponse)
def api_remove_silence(req: RemoveSilenceRequest):
    """Auto-cut dead air silence from a video clip."""
    from server.core.silence_cutter import remove_silence
    try:
        out_path = req.output_path
        if not out_path:
            p = Path(req.video_path)
            out_path = str(p.parent / f"{p.stem}_tight{p.suffix}")
        
        result = remove_silence(
            input_video=req.video_path,
            output_video=out_path,
            noise_threshold_db=req.noise_threshold_db,
            min_silence_duration=req.min_silence_duration,
            pad_seconds=req.pad_seconds,
        )
        return RemoveSilenceResponse(
            output_path=result["output_path"],
            original_duration=result["original_duration"],
            cut_duration=result["cut_duration"],
            time_saved=result["time_saved"],
            silence_intervals=result.get("silence_intervals", []),
            message=result["message"],
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/tools/bleep-mute", response_model=BleepMuteResponse)
def api_bleep_mute(req: BleepMuteRequest):
    """Censor audio profanity or custom words with 1000Hz bleep or mute."""
    from server.core.word_filter import apply_bleep_or_mute
    try:
        out_path = req.output_path
        if not out_path:
            p = Path(req.video_path)
            out_path = str(p.parent / f"{p.stem}_censored{p.suffix}")
        
        ts = req.timestamps or []
        result = apply_bleep_or_mute(
            input_video=req.video_path,
            output_video=out_path,
            timestamps=ts,
            mode=req.mode,
            beep_freq=req.beep_freq,
        )
        return BleepMuteResponse(
            output_path=result["output_path"],
            censored_count=result["censored_count"],
            mode=result["mode"],
            message=result["message"],
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/export/multi-aspect", response_model=MultiAspectExportResponse)
def export_multi_aspect(req: MultiAspectExportRequest):
    """
    Renders a clip in multiple aspect ratios (e.g. 9:16 vertical, 1:1 square, 4:5 portrait, 16:9 landscape)
    in a single coordinated pass.
    """
    if not os.path.exists(req.clip_path) and not (req.source_video and os.path.exists(req.source_video)):
        raise HTTPException(status_code=400, detail=f"Source clip or video not found: {req.clip_path}")

    source = req.source_video if (req.source_video and os.path.exists(req.source_video)) else req.clip_path
    stem = "".join(c for c in (req.title or Path(req.clip_path).stem) if c.isalnum() or c in " _-").strip().replace(" ", "_") or "clip"
    out_dir = Path(req.output_dir).expanduser().resolve() if req.output_dir else Path(req.clip_path).parent / f"{stem}_multi_aspect"
    out_dir.mkdir(parents=True, exist_ok=True)

    exports = {}
    valid_aspects = ["9:16", "1:1", "4:5", "16:9"]
    requested_aspects = [a for a in req.aspect_ratios if a in valid_aspects] or valid_aspects

    start = req.start_seconds if (req.start_seconds is not None and req.source_video) else 0.0
    duration = (req.end_seconds - req.start_seconds) if (req.end_seconds is not None and req.start_seconds is not None and req.source_video) else get_video_duration(source)
    end = start + duration

    for aspect in requested_aspects:
        slug = aspect.replace(":", "x")
        out_file = str(out_dir / f"{stem}_{slug}.mp4")
        try:
            render_clip(
                input_video=source,
                output_video=out_file,
                start_time=start,
                end_time=end,
                aspect_ratio=aspect,
                burn_captions=req.burn_captions,
                subtitle_path=req.subtitle_path,
            )
            if os.path.exists(out_file) and os.path.getsize(out_file) > 0:
                exports[aspect] = out_file
        except Exception as e:
            continue

    if not exports:
        raise HTTPException(status_code=500, detail="Failed to render any multi-aspect exports")

    return MultiAspectExportResponse(
        exports=exports,
        message=f"Successfully exported {len(exports)} aspect ratios to {out_dir}",
    )


@app.post("/tools/overlay", response_model=OverlayResponse)
def api_apply_overlay(req: OverlayRequest):
    """Overlay an image or B-roll video clip onto a video segment.
    Optionally burns animated karaoke captions simultaneously in a single FFmpeg pass."""
    from server.core.overlay_manager import apply_broll_overlay
    try:
        out_path = req.output_path
        if not out_path:
            p = Path(req.video_path)
            out_path = str(p.parent / f"{p.stem}_broll{p.suffix}")

        saved = apply_broll_overlay(
            input_video=req.video_path,
            output_video=out_path,
            broll_path=req.broll_path,
            start_time=req.start_time,
            duration=req.duration,
            scale=req.scale,
            position=req.position,
            opacity=req.opacity,
            subtitle_path=req.subtitle_path,
        )
        return OverlayResponse(
            output_path=saved,
            message="Applied B-roll overlay successfully",
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/tools/suggest-emojis", response_model=EmojiSuggestResponse)
def api_suggest_emojis(req: EmojiSuggestRequest):
    """Analyze transcript segments and return contextual emoji suggestions."""
    from server.core.overlay_manager import suggest_emojis_for_segments
    try:
        suggestions = suggest_emojis_for_segments(req.segments)
        return EmojiSuggestResponse(suggestions=suggestions)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

        raise HTTPException(status_code=400, detail=str(e))


@app.post("/trim", response_model=TrimResponse)
def create_manual_trim(req: TrimRequest):
    """
    Manual clip trimming: renders a user-selected in/out range from the source video
    into its own clip file (no aspect crop unless requested).
    Also returns word timestamps intersecting the trim window for emoji injection.
    """
    from server.models import TranscriptSegment, WordTimestamp
    import json

    if not os.path.exists(req.video_path):
        raise HTTPException(status_code=400, detail=f"Video file not found: {req.video_path}")
    if req.start_seconds < 0 or req.end_seconds <= req.start_seconds:
        raise HTTPException(status_code=400, detail="end_seconds must be greater than a non-negative start_seconds")
    if req.end_seconds - req.start_seconds > 600:
        raise HTTPException(status_code=400, detail="Trim range too long (max 10 minutes per clip)")

    video_dir = os.path.dirname(os.path.abspath(req.video_path))
    stem = os.path.splitext(os.path.basename(req.video_path))[0]
    title_slug = "".join(c if c.isalnum() or c in " -_" else "" for c in req.title).strip().replace(" ", "_")
    if not title_slug:
        title_slug = "trim"
    out_path = os.path.join(video_dir, f"{stem}_{title_slug}_{int(req.start_seconds)}-{int(req.end_seconds)}.mp4")

    render_clip(
        input_video=req.video_path,
        output_video=out_path,
        start_time=req.start_seconds,
        end_time=req.end_seconds,
        layout="full",
        burn_captions=req.burn_captions,
        subtitle_path=req.subtitle_path,
    )

    # Try to load transcript/words from companion files in the video directory
    words = []
    transcript_json = os.path.join(video_dir, f"{stem}_transcript.json")
    if not os.path.exists(transcript_json):
        transcript_json = os.path.join(video_dir, "captions.json")
    if not os.path.exists(transcript_json):
        transcript_json = os.path.join(video_dir, f"{stem}.json")

    if os.path.exists(transcript_json):
        try:
            data = json.loads(open(transcript_json, encoding="utf-8").read())
            for seg_data in data:
                seg = TranscriptSegment(**seg_data)
                for w in getattr(seg, "words", []):
                    if w.end > req.start_seconds and w.start < req.end_seconds:
                        # Rebase to clip-local time (0-based)
                        ws = max(0.0, w.start - req.start_seconds)
                        we = max(ws, w.end - req.start_seconds)
                        words.append(WordTimestamp(word=w.word, start=ws, end=we, probability=w.probability))
        except Exception:
            pass

    return TrimResponse(
        clip_path=out_path,
        title=req.title or title_slug,
        start_seconds=req.start_seconds,
        end_seconds=req.end_seconds,
        duration=round(req.end_seconds - req.start_seconds, 3),
        words=words,
    )


@app.post("/render/custom", response_model=TrimResponse)
def render_custom_selection(req: CustomRenderRequest):
    """
    Re-renders a selected source range with a chosen output layout:
      layout = 'vertical'       -> 9:16 smart crop
              'full'            -> original full-frame
              'game_reaction'   -> full-frame gameplay + camera PiP overlay
    """
    if not os.path.exists(req.video_path):
        raise HTTPException(status_code=400, detail=f"Video file not found: {req.video_path}")
    if req.start_seconds < 0 or req.end_seconds <= req.start_seconds:
        raise HTTPException(status_code=400, detail="end_seconds must be greater than a non-negative start_seconds")
    if req.end_seconds - req.start_seconds > 600:
        raise HTTPException(status_code=400, detail="Trim range too long (max 10 minutes per clip)")

    if req.layout not in ("vertical", "full", "game_reaction"):
        raise HTTPException(status_code=400, detail=f"Unsupported layout: {req.layout}")
    if req.aspect_ratio not in ("9:16", "1:1", "4:5", "16:9", "full", None):
        raise HTTPException(status_code=400, detail=f"Unsupported aspect ratio: {req.aspect_ratio}")

    out_path = req.output_path or str(
        _ensure_output_root() / f"manual_{uuid.uuid4().hex[:8]}" / "clip.mp4"
    )
    if not Path(out_path).suffix:
        out_path += ".mp4"
    out_path = str(Path(out_path).expanduser().resolve())

    render_clip(
        input_video=req.video_path,
        output_video=out_path,
        start_time=req.start_seconds,
        end_time=req.end_seconds,
        aspect_ratio=req.aspect_ratio,
        crop_x_offset=req.crop_x_offset,
        burn_captions=req.burn_captions,
        subtitle_path=req.subtitle_path,
        layout=req.layout,
        cam_video=req.cam_video,
        cam_scale=req.cam_scale,
        cam_position=req.cam_position,
    )

    if not os.path.isfile(out_path):
        raise HTTPException(status_code=500, detail=f"Render completed without creating output file: {out_path}")

    return TrimResponse(
        clip_path=out_path,
        title="Custom render",
        start_seconds=req.start_seconds,
        end_seconds=req.end_seconds,
        duration=round(req.end_seconds - req.start_seconds, 3),
    )

@app.post("/project/delete")
def delete_project_files(req: DeleteProjectRequest):
    """Delete generated files and folders belonging to a project.

    Only paths inside the engine output directory are removed, so the user's
    original source video can never be deleted. Generated clip folders are
    removed wholesale (they contain the rendered clips plus temp audio,
    caption/transcript files, and re-render temp versions), and empty parents
    are pruned up to the output root.
    """
    if not req.paths:
        return {"deleted": []}
    root = _ensure_output_root()
    deleted = []
    seen = set()
    for raw_path in req.paths:
        try:
            candidate = Path(raw_path).expanduser().resolve()
            candidate.relative_to(root)
        except (OSError, ValueError):
            continue
        if candidate == root or candidate in seen:
            continue
        if candidate.exists():
            if candidate.is_dir():
                shutil.rmtree(candidate)
            else:
                candidate.unlink(missing_ok=True)
            seen.add(candidate)
            deleted.append(str(candidate))
        # Remove now-empty generated job folders, but never the output root.
        parent = candidate.parent
        while parent != root and parent.is_relative_to(root):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    return {"deleted": deleted}


# ----------------------------------------------------------------------
# Output folder (CapCut-style "save everything to my chosen folder")
# ----------------------------------------------------------------------
class OutputFolderRequest(BaseModel):
    folder: str = ""


@app.get("/output-folder")
def get_output_folder():
    """Return the folder where generated clips are saved."""
    return {"folder": str(_ensure_output_root()), "is_default": str(_ensure_output_root()) == str(DEFAULT_OUTPUT_ROOT)}


@app.post("/output-folder")
def set_output_folder(req: OutputFolderRequest):
    """Switch where generated clips/captions/temp files are written.

    The folder is created if it doesn't exist. The original source video is
    never moved or touched.
    """
    folder = req.folder.strip() if req.folder else ""
    if folder:
        root = Path(folder).expanduser()
        if not root.is_absolute():
            # A relative path is resolved against the repo root so we never
            # accidentally resolve against a server cwd that could change.
            root = Path(DEFAULT_OUTPUT_DIR) / root
        try:
            root.mkdir(parents=True, exist_ok=True)
            root = root.resolve()
        except OSError as e:
            raise HTTPException(status_code=400, detail=f"Cannot use that output folder: {e}")
        _sync_output_root(root)
    else:
        # Empty folder resets back to the default repo-anchored ./output.
        _sync_output_root(DEFAULT_OUTPUT_ROOT)
    return {"folder": str(OUTPUT_ROOT), "is_default": str(OUTPUT_ROOT) == str(DEFAULT_OUTPUT_ROOT)}


# ----------------------------------------------------------------------
# Setup / System panel
# ----------------------------------------------------------------------
class SetupInstallRequest(BaseModel):
    component: str  # "ffmpeg" | "ollama" | "pytorch" | "whisper" | "librosa" | "ultralytics"


@app.get("/api/setup/status")
def setup_status():
    """Full system + dependency status for the Setup panel."""
    from server.core import system_check as sc

    return {
        "os": sc.detect_os(),
        "python": sc.detect_python(),
        "ffmpeg": sc.detect_ffmpeg(),
        "ollama": sc.detect_ollama(),
        "lmstudio": sc.detect_lmstudio(),
        "whisper": sc.detect_whisper(),
        "ultralytics": sc.detect_ultralytics(),
        "gpu": sc.detect_gpu(),
        "cpu": sc.detect_cpu(),
        "torch": sc.detect_torch(),
        "install_commands": sc.get_install_commands(),
        "recommendations": sc.recommend_models(),
        "output_dir": str(_ensure_output_root()),
    }


@app.post("/api/setup/install")
def setup_install(req: SetupInstallRequest):
    """Run the click-to-install command for a missing component."""
    from server.core import system_check as sc

    commands = sc.get_install_commands()
    cmd = commands.get(req.component)
    if not cmd:
        raise HTTPException(status_code=400, detail=f"Unknown component: {req.component}")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return {
            "component": req.component,
            "command": " ".join(cmd),
            "returncode": result.returncode,
            "stdout": result.stdout[-2000:],
            "stderr": result.stderr[-2000:],
        }
    except subprocess.TimeoutExpired:
        return {"component": req.component, "error": "Install timed out after 10 minutes"}
    except Exception as e:
        return {"component": req.component, "error": str(e)}


@app.post("/api/setup/ollama/pull")
def ollama_pull(model: str = "gemma2:2b"):
    """One-click download of an Ollama GGUF model (e.g. gemma2:2b for clip suggestions)."""
    ollama_exe = shutil.which("ollama") or "ollama"
    try:
        result = subprocess.run(
            [ollama_exe, "pull", model], capture_output=True, text=True, timeout=1800,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return {"model": model, "returncode": result.returncode, "stdout": result.stdout[-1500:], "stderr": result.stderr[-1500:]}
    except subprocess.TimeoutExpired:
        return {"model": model, "error": "Model download timed out after 30 minutes"}
    except Exception as e:
        return {"model": model, "error": str(e)}


@app.get("/api/setup/estimate")
def setup_estimate(duration: float = 30.0, layout: str = "vertical"):
    """Rough render-time estimate for a clip of the given duration."""
    from server.core import system_check as sc

    return sc.estimate_render_time(duration, layout)


@app.get("/api/setup/support")
def setup_support():
    """Support / social links for the app footer."""
    return {
        "paypal": "https://www.paypal.me/techfreq",
        "beacons": "https://beacons.ai/techfreq",
        "github": "https://github.com/techfreq",
        "issues": "https://github.com/techfreq",
        "star": "https://github.com/techfreq",
    }


def start_server(host: str = "127.0.0.1", port: int = 8765):
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    start_server()