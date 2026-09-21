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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from server.models import (
    ChatRequest, ChatResponse, ProcessRequest, ProcessResponse, ClipResult,
    ExportProjectRequest, ExportProjectResponse, SubtitleRegenRequest,
    TrimRequest, TrimResponse, TrimPosterRequest, TrimPosterResponse, CustomRenderRequest,
    ExportMediaRequest, ExportMediaResponse, ExportCompileRequest, ExportCompileResponse,
    ExportStandaloneRequest, ExportStandaloneResponse, ClipBundleRequest, ClipBundleResponse, DeleteProjectRequest,
    DetectSilenceRequest, DetectSilenceResponse, RemoveSilenceRequest, RemoveSilenceResponse,
    BleepMuteRequest, BleepMuteResponse, CaptionPresetInfo,
    ThumbnailRequest, ThumbnailResponse, SocialMetadataRequest, SocialMetadataResponse,
    ThumbnailCandidatesRequest, ThumbnailCandidatesResponse, ThumbnailSaveRequest, ThumbnailSaveResponse,
    MultiAspectExportRequest, MultiAspectExportResponse,
    AspectPreviewRequest, AspectPreviewResponse,
    OverlayRequest, OverlayResponse, EmojiSuggestRequest, EmojiSuggestResponse,
)
from server.core.pipeline import VideoClipperEngine
from server.core import proc
from server.core.edit_chat import EditChat
from server.core.export_tools import export_fcpxml, export_edl, export_capcut_draft
from server.core.ffmpeg_tools import (
    check_ffmpeg, get_media_info, get_video_duration, detect_hw_encoder, render_clip,
    export_clip_as, concat_clips, export_standalone_audio,
    extract_best_thumbnail, extract_candidate_thumbnails, extract_audio,
    render_cropped_frame, extract_trim_poster,
)
from server.logging_setup import setup_logging
from server.auth import (
    LocalTokenAuthMiddleware, TOKEN_HEADER, auth_disabled, resolve_token,
)

setup_logging()  # logs/server.log (+ console), also used by main.py

@asynccontextmanager
async def _lifespan(app):
    await _start_model_recommendation()
    yield


app = FastAPI(title="Klipzy Studio Server", version="1.0.0", lifespan=_lifespan)

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

# Built-in clip editor endpoints (/editor/*) live in their own module to keep
# this file manageable. Mounted here; behaviour is identical to before the split.
from server.api import editor as _editor_api  # noqa: E402
app.include_router(_editor_api.router)
from server.api import imports as _imports_api  # noqa: E402
app.include_router(_imports_api.router)
from server.api import media as _media_api
app.include_router(_media_api.router)

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

# The user's chosen OUTPUT folder — where rendered clips (plus their captions /
# thumbnails) are written. When set via POST /output-folder it ALSO re-points
# OUTPUT_ROOT + ENGINE.output_dir there, so new renders land in it directly.
# Empty string = unset, in which case renders go to the disposable built-in
# working dir (DEFAULT_OUTPUT_ROOT, i.e. ./output), which is treated as temp
# scratch space and cleaned up on delete / clear-cache.
EXPORT_DEFAULT_DIR = ""

CHAT = EditChat()


def _hf_token_path() -> Path:
    """Where the optional Hugging Face token is stored (gitignored logs dir).
    Used by speaker diarization (pyannote) to download its pretrained model."""
    base = Path(os.environ["KLIPZY_LOG_DIR"]) if os.environ.get("KLIPZY_LOG_DIR") \
        else (Path(__file__).resolve().parents[2] / "logs")
    return base / "hf_token.txt"


def _load_hf_token_env():
    """Load a saved HF token into the environment at startup so optional
    diarization can use it without the user re-entering it each run."""
    try:
        if os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN"):
            return
        p = _hf_token_path()
        if p.is_file():
            tok = p.read_text(encoding="utf-8").strip()
            if tok:
                os.environ["HF_TOKEN"] = tok
    except Exception:
        pass


_load_hf_token_env()

# App-wide preferred local LLM (Ollama) model. Auto-resolved to the strongest
# model that runs well on this machine (preferring one already installed) so
# good hardware gets sharp hooks/selection out of the box. Users override it in
# the Setup panel (/api/setup/ai-model), and that choice is now remembered
# across restarts so every AI feature (highlight selection, clip copywriting,
# AI-rewrite-hook, chat, translate) keeps using the model picked from the list.
# Whisper is chosen per-job.
def _preferred_model_path() -> Path:
    """Where the user's chosen Ollama model is remembered (gitignored logs dir)."""
    base = Path(os.environ["KLIPZY_LOG_DIR"]) if os.environ.get("KLIPZY_LOG_DIR") \
        else (Path(__file__).resolve().parents[2] / "logs")
    return base / "preferred_ollama_model.txt"


def _save_preferred_model(model: str) -> None:
    """Persist the user's model pick so it survives an app restart."""
    try:
        p = _preferred_model_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text((model or "").strip(), encoding="utf-8")
    except Exception:
        pass


def _resolve_startup_ollama_model() -> str:
    # Honor a model the user explicitly picked from the AI Models list in an
    # earlier session; only auto-resolve when nothing has been saved yet.
    try:
        p = _preferred_model_path()
        if p.is_file():
            saved = p.read_text(encoding="utf-8").strip()
            if saved:
                return saved
    except Exception:
        pass
    return "gemma2:2b"


PREFERRED_OLLAMA_MODEL = _resolve_startup_ollama_model()

async def _start_model_recommendation():
    # Hardware discovery may load Torch slowly on a cold Windows launch.
    # Serve requests first, and never overwrite an explicitly saved selection.
    def resolve():
        global PREFERRED_OLLAMA_MODEL
        try:
            if _preferred_model_path().is_file():
                return
            from server.core.system_check import resolve_default_ollama_model
            model = resolve_default_ollama_model()
            if not _preferred_model_path().is_file():
                PREFERRED_OLLAMA_MODEL = model
        except Exception:
            logging.getLogger("klipzy").exception("Background model recommendation failed")
    threading.Thread(target=resolve, daemon=True).start()



def _active_llm_model() -> str:
    """The model name to report/use for the CURRENT backend.

    A custom OpenAI-compatible server names its models its own way, so the
    configured value wins there; the built-in Ollama backend keeps using the
    model picked in Setup.
    """
    try:
        from server.core import llm_client
        cfg = llm_client.load_config()
        if cfg["backend"] == llm_client.BACKEND_OPENAI:
            return cfg.get("model") or "local-model"
    except Exception:
        pass
    return PREFERRED_OLLAMA_MODEL


def _llm_available() -> bool:
    """Is the configured LLM backend reachable? Backend-aware, so a remote
    endpoint isn't judged by whether localhost:11434 happens to be up."""
    try:
        from server.core import llm_client
        return llm_client.is_available()
    except Exception:
        return False


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


# Cancellation is signalled by proc.CancelledError, which subclasses
# BaseException so it cuts through the pipeline's broad `except Exception`
# guards around optional stages. Aliased here so existing `except _CancelledError`
# handlers keep working and both the cooperative check and a killed subprocess
# raise the SAME type.
_CancelledError = proc.CancelledError


def _enqueue_process_job(req: "ProcessRequest") -> str:
    """Register a job for `req` and ensure the single background worker is draining
    the queue. Returns the new job id. Shared by /process and /process/batch so
    both use the same serial-queue semantics (never N concurrent heavy passes)."""
    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {"status": "queued", "clips": [], "error": None, "progress": 0}
    from server.core import processing_trace as trace
    trace.event("job.queued", job_id=job_id, video_path=req.video_path)
    _JOB_REQUESTS[job_id] = req
    _JOB_CANCEL[job_id] = threading.Event()
    _JOBS_ORDER.append(job_id)
    _prune_jobs()

    global _WORKER_THREAD
    with _JOB_LOCK:
        _QUEUE_JOBS.append(job_id)
        if _WORKER_THREAD is None or not _WORKER_THREAD.is_alive():
            _WORKER_THREAD = threading.Thread(target=_drain_job_queue, daemon=True)
            _WORKER_THREAD.start()
    return job_id


def _run_job(job_id: str) -> None:
    """Execute one queued job, recording per-job status in JOBS."""
    req = _JOB_REQUESTS.get(job_id)
    cancel_ev = _JOB_CANCEL.get(job_id)
    if req is None:
        JOBS[job_id]["status"] = "failed"
        JOBS[job_id]["error"] = "Request no longer present in job store."
        return

    def progress_callback(step: str, pct: int):
        # Cooperative check between stages; a mid-stage subprocess is stopped by
        # the hard kill in proc.request_cancel() (see /job/{job_id}/cancel).
        if (cancel_ev is not None and cancel_ev.is_set()) or proc.cancelled():
            raise _CancelledError()
        JOBS[job_id]["progress"] = pct
        JOBS[job_id]["step"] = step

    from server.core import processing_trace as trace
    trace.bind(job_id)
    trace.event("job.start", settings=req.model_dump())
    proc.begin_job(f"process:{job_id}")  # reset kill-registry + cancel flag for this job
    try:
        JOBS[job_id]["status"] = "processing"
        clips = ENGINE.process_video(
            video_path=req.video_path,
            diagnostic_job_id=job_id,
            audio_tracks=req.audio_tracks,
            audio_track_gains=req.audio_track_gains,
            analysis_audio_tracks=req.analysis_audio_tracks,
            visual_review=req.visual_review,
            vertical_crop=req.vertical_crop,
            aspect_ratio=req.aspect_ratio,
            max_clips=req.max_clips,
            auto_clip_count=req.auto_clip_count,
            include_unreviewed_action=req.include_unreviewed_action,
            min_duration=req.min_duration,
            max_duration=req.max_duration,
            whisper_model=req.whisper_model,
            language=req.language,
            use_audio_energy=req.use_audio_energy,
            use_llm=req.use_llm,
            speaker_aware_selection=req.speaker_aware_selection,
            speaker_aware_crop=req.speaker_aware_crop,
            # Use the model for the ACTIVE backend, not always the Ollama pick.
            # A custom OpenAI-compatible endpoint names its models its own way,
            # so its configured model must win here too — matching every other
            # AI entry point (chat / translate / hooks), which already funnel
            # through _active_llm_model(). Passing PREFERRED_OLLAMA_MODEL sent a
            # meaningless Ollama name to a remote server.
            llm_model=_active_llm_model(),
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
            intro_enabled=req.intro_enabled,
            intro_font_size=req.intro_font_size,
            intro_style=req.intro_style,
            remove_silence=req.remove_silence,
            bleep_profanity=req.bleep_profanity,
            mute_profanity=req.mute_profanity,
            normalize_audio=req.normalize_audio,
            auto_zoom=req.auto_zoom,
            music_path=req.music_path,
            music_volume=req.music_volume,
            duck_music=req.duck_music,
            progress_callback=progress_callback,
        )
        if cancel_ev is not None and cancel_ev.is_set():
            raise _CancelledError()
        JOBS[job_id]["clips"] = [c.model_dump() for c in clips]
        JOBS[job_id]["status"] = "completed"
        trace.event("job.completed", clips=[c.model_dump() for c in clips])
    except _CancelledError:
        trace.event("job.cancelled")
        JOBS[job_id]["status"] = "cancelled"
        JOBS[job_id]["error"] = "Job cancelled by user."
    except Exception as e:
        import traceback
        trace.event("job.failed", error=str(e), traceback=traceback.format_exc())
        JOBS[job_id]["status"] = "failed"
        JOBS[job_id]["error"] = str(e)
    finally:
        trace.unbind()
        proc.end_job()  # clear the kill-registry; next job starts clean


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
    transcription_backend: Optional[str] = None  # mlx | faster-whisper | openai-whisper | None


@app.get("/ready")
def ready():
    """Lightweight readiness; dependency and hardware checks belong to Settings."""
    import sys
    return {"status": "ok", "protocol": "klipzy-settings-v1",
            "python": sys.executable, "root": str(Path(__file__).resolve().parents[2]),
            "pid": os.getpid()}


@app.get("/health", response_model=HealthResponse)
def health():
    from server.core.transcriber import detect_active_backend

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
        from importlib.util import find_spec
        whisper_ok = find_spec("whisper") is not None
    except ImportError:
        pass
    return HealthResponse(
        status="ok",
        ffmpeg_available=ffmpeg_ok,
        hw_encoder=encoder,
        whisper_loaded=whisper_ok,
        ollama_available=ollama_ok,
        transcription_backend=detect_active_backend().get("active"),
    )


@app.get("/transcription-backend")
def transcription_backend():
    """Which transcription backend is active (mlx / faster-whisper / openai-whisper)
    plus what else is available. Handy for confirming MLX is engaged on Apple Silicon."""
    from server.core.transcriber import detect_active_backend
    return detect_active_backend()


@app.post("/process", response_model=ProcessResponse)
def process_video(req: ProcessRequest):
    if not os.path.exists(req.video_path):
        raise HTTPException(status_code=400, detail=f"Video file not found: {req.video_path}")
    if not req.auto_clip_count and not (1 <= req.max_clips <= 20):
        raise HTTPException(status_code=400, detail="max_clips must be between 1 and 20")
    if not req.auto_clip_count and req.max_duration <= req.min_duration:
        raise HTTPException(status_code=400, detail="max_duration must be greater than min_duration")

    job_id = _enqueue_process_job(req)
    return ProcessResponse(job_id=job_id, status="queued")


class BatchProcessResponse(BaseModel):
    job_ids: List[str]
    count: int
    status: str = "queued"


@app.post("/process/batch", response_model=BatchProcessResponse)
def process_video_batch(req: ProcessRequest):
    """Queue several videos at once. Provide the shared settings on the request
    plus `video_paths`; each path becomes its own job, drained serially by the
    same single worker so we never run N Whisper/ffmpeg passes concurrently.
    """
    paths = [p for p in (req.video_paths or []) if p and os.path.exists(p)]
    if not paths:
        raise HTTPException(status_code=400, detail="No existing video files in video_paths")
    if not req.auto_clip_count and not (1 <= req.max_clips <= 20):
        raise HTTPException(status_code=400, detail="max_clips must be between 1 and 20")
    if not req.auto_clip_count and req.max_duration <= req.min_duration:
        raise HTTPException(status_code=400, detail="max_duration must be greater than min_duration")

    job_ids: List[str] = []
    for path in paths:
        # Clone the shared settings, pointing each job at one source file.
        per = req.model_copy(update={"video_path": path, "video_paths": None})
        job_ids.append(_enqueue_process_job(per))
    return BatchProcessResponse(job_ids=job_ids, count=len(job_ids))


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
    # Signal cancellation. Two mechanisms, by design:
    #  - the per-job Event stops a QUEUED job before it starts, and is the
    #    cooperative check between pipeline stages;
    #  - for the ACTIVE job, proc.request_cancel() hard-kills the ffmpeg process
    #    it's running right now so it stops mid-stage instead of running to
    #    completion first. Only the active job owns live subprocesses, so gating
    #    on _ACTIVE_JOB_ID avoids killing anything on behalf of a queued job.
    ev = _JOB_CANCEL.get(job_id)
    if ev is not None:
        ev.set()
        job["status"] = "cancelling"
    if _ACTIVE_JOB_ID == job_id:
        proc.request_cancel(f"process:{job_id}")
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
    CHAT.model = _active_llm_model()  # honor the backend + model chosen in Setup
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

    # Write the timeline into the user-picked folder when provided.
    base_dir = video_dir
    if req.output_dir:
        base_dir = str(Path(req.output_dir).expanduser().resolve())
        os.makedirs(base_dir, exist_ok=True)

    if req.format.lower() == "fcpxml":
        out_path = os.path.join(base_dir, f"{stem}_shorts.xml")
        export_fcpxml(req.video_path, req.clips, out_path, fps=req.fps)
        msg = "Exported Premiere Pro / DaVinci Resolve XML successfully"
    elif req.format.lower() == "edl":
        out_path = os.path.join(base_dir, f"{stem}_shorts.edl")
        export_edl(req.video_path, req.clips, out_path, fps=req.fps)
        msg = "Exported EDL timeline successfully"
    elif req.format.lower() == "capcut":
        out_path = os.path.join(base_dir, f"{stem}_capcut_draft.json")
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
        raise HTTPException(
            status_code=400,
            detail="This clip's working file is no longer available (it may have been "
                   "cleared or the project was deleted). Re-generate the clip, then export.",
        )
    fmt = req.format.lower().lstrip(".")
    if fmt not in ("mp4", "mov", "mkv", "webm", "av1", "gif"):
        raise HTTPException(status_code=400, detail=f"Unsupported export format: {fmt}")
    # Keep human-readable spaces in export names — underscores looked odd. Also
    # fold any underscores already in the title back into spaces, and collapse
    # runs of whitespace so the folder/file read cleanly.
    safe = "".join(c for c in req.title if c.isalnum() or c in " _-").replace("_", " ")
    safe = " ".join(safe.split())[:70].strip() or "clip"
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


@app.post("/export/thumbnail-candidates", response_model=ThumbnailCandidatesResponse)
def export_thumbnail_candidates(req: ThumbnailCandidatesRequest):
    """Generate several scored candidate cover frames so the user can pick the
    best one. Candidates are written to a cache folder under the output root."""
    if not os.path.isfile(req.video_path):
        raise HTTPException(status_code=400, detail=f"Clip file not found: {req.video_path}")
    stem = os.path.splitext(os.path.basename(req.video_path))[0]
    out_dir = req.output_dir or str(_ensure_output_root() / ".cache" / "thumbs" / stem)
    try:
        candidates = extract_candidate_thumbnails(
            req.video_path, out_dir, count=max(1, min(int(req.count or 3), 6)),
            img_format=req.image_format,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))
    if not candidates:
        raise HTTPException(status_code=500, detail="Could not extract any candidate frames")
    return ThumbnailCandidatesResponse(
        candidates=candidates,
        message=f"Generated {len(candidates)} candidate frame(s)",
    )


@app.post("/export/thumbnail-save", response_model=ThumbnailSaveResponse)
def export_thumbnail_save(req: ThumbnailSaveRequest):
    """Save the frame at a chosen timestamp into a folder, in any image format
    (png / jpg / webp / bmp)."""
    if not os.path.isfile(req.video_path):
        raise HTTPException(status_code=400, detail=f"Clip file not found: {req.video_path}")
    fmt = (req.image_format or "png").lower().lstrip(".")
    if fmt not in ("png", "jpg", "jpeg", "webp", "bmp"):
        raise HTTPException(status_code=400, detail=f"Unsupported image format: {fmt}")
    base_dir = Path(req.output_dir).expanduser().resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in (req.title or Path(req.video_path).stem) if c.isalnum() or c in " _-").strip()
    safe = " ".join(safe.split())[:70] or "thumbnail"
    out_path = str(base_dir / f"{safe}.{fmt}")
    try:
        saved = extract_best_thumbnail(req.video_path, out_path, timestamp=req.timestamp)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))
    return ThumbnailSaveResponse(path=saved, message=f"Saved thumbnail to {saved}")


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
    # Prefer an explicit output_path; else a user-picked output_dir; else beside
    # the first clip.
    if req.output_dir:
        base_dir = str(Path(req.output_dir).expanduser().resolve())
        os.makedirs(base_dir, exist_ok=True)
    else:
        base_dir = os.path.dirname(os.path.abspath(req.clip_paths[0]))
    out_path = req.output_path or os.path.join(base_dir, f"{stem}_reel.{fmt}")

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

    # When the user picks a destination folder, write the asset there (with the
    # default filename); otherwise fall back to beside the source video.
    base_dir = video_dir
    if req.output_dir:
        base_dir = str(Path(req.output_dir).expanduser().resolve())
        os.makedirs(base_dir, exist_ok=True)

    if asset.startswith("audio_"):
        fmt = asset.split("_", 1)[1]
        out_path = req.output_path or os.path.join(base_dir, f"{stem}_audio.{fmt}")
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
        out_path = req.output_path or os.path.join(base_dir, f"{stem}_standalone{target_ext}")

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
    if not words and not (req.re_render and req.start_seconds is not None and req.end_seconds is not None):
        raise HTTPException(status_code=400, detail="No timed words available for subtitles")
    seg = TranscriptSegment(
        id=0, start=words[0].start if words else req.start_seconds,
        end=words[-1].end if words else req.end_seconds,
        text=" ".join(w.word for w in words), words=words,
    )
    generate_srt([seg], req.output_path)

    ass_path = os.path.splitext(req.output_path)[0] + ".ass"

    # Auto-generate the intro hook from the clip's opening line when the intro
    # toggle is on but the optional custom text was left blank.
    effective_intro = req.intro_caption
    if not (req.intro_caption and req.intro_caption.strip()) and req.intro_enabled:
        import re as _re
        lead = (seg.text or "").strip()
        first = _re.split(r"(?<=[.!?])\s+", lead)[0] if lead else ""
        effective_intro = (first or lead)[:90].strip() or None

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
        intro_caption=effective_intro,
        intro_caption_duration=req.intro_caption_duration,
        intro_font_size=req.intro_font_size,
        intro_style=req.intro_style,
    )

    # Write a new revision: replacing an MP4 open in Chromium can fail on Windows
    # and a fragment-only cache key does not guarantee a fresh media resource.
    rendered_path = None
    if req.re_render:
        if not req.source_video or not os.path.isfile(req.source_video):
            raise HTTPException(status_code=409, detail="Original source video is unavailable. Restore it before changing burned captions or removing a hook.")
        if not req.clip_output_file or req.start_seconds is None or req.end_seconds is None or req.end_seconds <= req.start_seconds:
            raise HTTPException(status_code=400, detail="Clip path and valid source bounds are required")
        if Path(req.source_video).resolve() == Path(req.clip_output_file).resolve():
            raise HTTPException(status_code=409, detail="A clean original source is required to replace burned text.")
        target = Path(req.clip_output_file).expanduser().resolve()
        revision = str(target.with_name(f"{target.stem}.captions-{uuid.uuid4().hex[:8]}.mp4"))
        try:
            render_clip(input_video=req.source_video, output_video=revision,
                        start_time=req.start_seconds, end_time=req.end_seconds,
                        aspect_ratio=req.aspect_ratio or "full", burn_captions=True,
                        subtitle_path=ass_path, layout=req.layout or "",
                        cam_video=req.cam_video, cam_scale=req.cam_scale,
                        cam_position=req.cam_position, crop_x_offset=req.crop_x_offset)
            if not os.path.isfile(revision) or os.path.getsize(revision) == 0:
                raise RuntimeError("Renderer produced no video")
            rendered_path = revision
        except Exception as exc:
            Path(revision).unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=f"Subtitle files saved, but updating the video failed: {exc}") from exc
    return ExportProjectResponse(export_path=req.output_path, format="srt",
        message="Captions applied to a new video revision" if rendered_path else "Subtitle files saved",
        re_rendered=bool(rendered_path), rendered_path=rendered_path, ass_path=ass_path)

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


class DetectLayoutRequest(BaseModel):
    video_path: str
    start_time: float = 0.0
    end_time: Optional[float] = None


@app.post("/tools/detect-layout")
def api_detect_layout(req: DetectLayoutRequest):
    """Auto-detect whether footage is gameplay + webcam facecam (reaction layout).
    Returns {is_gaming, cam_position, cam_scale, confidence} so the UI can switch
    to the reaction/PiP layout automatically."""
    if not os.path.exists(req.video_path):
        raise HTTPException(status_code=400, detail=f"Video not found: {req.video_path}")
    from server.core.face_tracker import FaceTracker
    try:
        return FaceTracker().detect_gaming_layout(req.video_path, req.start_time, req.end_time)
    except Exception as e:  # noqa: BLE001
        # Detection is best-effort; never block ingest on it.
        return {"is_gaming": False, "cam_position": None, "cam_scale": 0.32, "confidence": 0.0, "error": str(e)}


class SuggestMomentsRequest(BaseModel):
    """Ask for transcript-free "action moments" (gunfights/explosions/big plays)
    fused from audio loudness + visual motion — the semi-automatic gaming path."""
    video_path: str
    min_duration: float = 15.0
    max_duration: float = 45.0
    max_moments: int = 6


class GPUCheckRequest(BaseModel):
    """Preflight check: is torch + a GPU available before starting a job?"""
    pass  # Stateless — reads live system state.


class GPUCheckResponse(BaseModel):
    """Result of the GPU/torch preflight check."""
    ready: bool
    gpu_name: Optional[str] = None
    vram_gb: Optional[float] = None
    torch_installed: bool = False
    cuda_available: bool = False
    accelerated: bool = False
    message: str = ""


@app.post("/tools/trim-poster", response_model=TrimPosterResponse)
def api_trim_poster(req: TrimPosterRequest):
    """Extract a poster thumbnail for the trim preview at a given timestamp."""
    if not os.path.exists(req.video_path):
        raise HTTPException(status_code=400, detail=f"Video not found: {req.video_path}")
    try:
        image_path = extract_trim_poster(req.video_path, timestamp=req.timestamp,
                                         out_dir=str(_ensure_output_root() / ".cache" / "posters"))
        return {"image_path": image_path}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Poster extraction failed: {e}")


@app.post("/tools/gpu-check", response_model=GPUCheckResponse)
def api_gpu_check(req: GPUCheckRequest):
    """Preflight check: is torch + a GPU available before starting a job?
    Reports acceleration separately from readiness. CPU processing is supported;
    readiness depends on an installed/configured transcription backend."""
    from server.core import system_check as sc
    try:
        gpu = sc.detect_gpu()
        torch_info = sc.detect_torch()
        torch_installed = bool(torch_info.get("installed"))
        cuda_available = bool(torch_info.get("cuda"))
        mps_available = bool(torch_info.get("mps"))
        directml = sc._directml_available() if hasattr(sc, "_directml_available") else False
        xpu = sc._xpu_available() if hasattr(sc, "_xpu_available") else False
        accelerated = cuda_available or mps_available or directml or xpu
        from server.core.transcriber import detect_active_backend
        ready = bool(detect_active_backend().get("active"))
        name = gpu.get("name")
        vram = gpu.get("vram_gb")
        if not ready:
            message = "Install Faster-Whisper or Whisper in Setup > AI models to transcribe. CPU mode is supported; GPU acceleration is optional."
        elif not accelerated:
            message = "CPU processing is available. GPU acceleration is optional."
        else:
            message = "GPU acceleration is available."
        return {
            "ready": ready,
            "gpu_name": name,
            "vram_gb": vram,
            "torch_installed": torch_installed,
            "accelerated": accelerated,
            "cuda_available": cuda_available,
            "message": message,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ready": False,
            "message": f"GPU check failed: {e}",
        }


@app.post("/tools/suggest-moments")
def api_suggest_moments(req: SuggestMomentsRequest):
    """Return suggested clippable action moments for gameplay footage so the UI
    can offer one-click "clip this" buttons. Best-effort and fully local: fuses
    audio energy (gunfire/explosions) with visual motion (frame differencing).
    Returns {moments: [{start, end, duration, title, score, reason}]}."""
    if not os.path.exists(req.video_path):
        raise HTTPException(status_code=400, detail=f"Video not found: {req.video_path}")

    from server.core.audio_energy import detect_action_highlights

    # Extract a mono 16kHz WAV into a short-lived temp folder under the output
    # root, then hand both audio + video to the fused detector.
    tmp_dir = _ensure_output_root() / f"_suggest_{uuid.uuid4().hex[:8]}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_audio = str(tmp_dir / "audio.wav")
    try:
        extract_audio(req.video_path, tmp_audio)
        clips = detect_action_highlights(
            audio_path=tmp_audio,
            min_duration=req.min_duration,
            max_duration=req.max_duration,
            top_k=max(1, min(req.max_moments, 12)),
            video_path=req.video_path,
        )
        moments = [
            {
                "start": c.start_time,
                "end": c.end_time,
                "duration": c.duration,
                "title": c.title,
                "score": c.score,
                "reason": c.reason,
            }
            for c in clips
        ]
        return {"moments": moments, "count": len(moments)}
    except Exception as e:  # noqa: BLE001
        # Suggestions are optional; never hard-fail the UI.
        return {"moments": [], "count": 0, "error": str(e)}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


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


class RemoveFillersRequest(BaseModel):
    video_path: str
    words: List[dict] = []
    output_path: Optional[str] = None
    extra_fillers: List[str] = []
    also_remove_silence: bool = True
    remove_phrases: bool = True


@app.post("/tools/remove-fillers")
def api_remove_fillers(req: RemoveFillersRequest):
    """Cut filler words ('um', 'uh', ...) and optionally dead air from a clip,
    using its Whisper word timestamps."""
    if not os.path.isfile(req.video_path):
        raise HTTPException(status_code=400, detail=f"Clip not found: {req.video_path}")
    from server.core.filler_cutter import remove_fillers
    out_path = req.output_path
    if not out_path:
        p = Path(req.video_path)
        out_path = str(p.parent / f"{p.stem}_nofiller{p.suffix}")
    try:
        return remove_fillers(
            input_video=req.video_path,
            output_video=out_path,
            words=req.words,
            extra_fillers=req.extra_fillers,
            also_remove_silence=req.also_remove_silence,
            remove_phrases=req.remove_phrases,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))


class TranslateCaptionsRequest(BaseModel):
    srt_path: str
    target_lang: str
    output_dir: Optional[str] = None
    # Optional: also render a finished clip with the translated captions burned in.
    burn: bool = False
    source_video: Optional[str] = None
    start_seconds: Optional[float] = None
    end_seconds: Optional[float] = None
    aspect_ratio: str = "9:16"
    style_preset: str = "viral_yellow"
    font_size: Optional[int] = None


@app.get("/tools/languages")
def get_languages():
    from server.core.translator import COMMON_LANGUAGES
    return {"languages": COMMON_LANGUAGES}


@app.post("/tools/translate-captions")
def api_translate_captions(req: TranslateCaptionsRequest):
    """Translate a clip's SRT into a target language via the local Ollama model,
    writing translated .srt + .vtt next to it. Requires Ollama running."""
    from server.core import system_check as sc
    if not os.path.isfile(req.srt_path):
        raise HTTPException(status_code=400, detail=f"Subtitle file not found: {req.srt_path}")
    if not _llm_available():
        raise HTTPException(
            status_code=400,
            detail="No AI model is reachable — start Ollama, or configure an "
                   "OpenAI-compatible endpoint in Setup, to translate captions.",
        )
    from server.core.translator import translate_srt_file
    model = _active_llm_model()
    try:
        result = translate_srt_file(req.srt_path, req.target_lang, model, req.output_dir)
        result["model"] = model
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))

    # Optionally render a finished clip with the translated captions burned in.
    # We re-cut from the SOURCE (not the already-captioned clip) so captions
    # don't double up, mirroring the /export/subtitles re-render path.
    if req.burn and req.source_video and os.path.isfile(req.source_video) \
            and req.start_seconds is not None and req.end_seconds is not None:
        try:
            from server.core.caption_styler import generate_karaoke_captions
            from server.core.ffmpeg_tools import render_clip
            from server.models import TranscriptSegment, WordTimestamp

            cues = result.get("cues", [])
            segs = []
            for i, c in enumerate(cues):
                text = (c.get("text") or "").strip()
                if not text:
                    continue
                start, end = float(c["start"]), float(c["end"])
                # Translation loses per-word alignment (word order differs across
                # languages), so spread the translated words evenly across the
                # cue's time span — an approximate karaoke highlight that still
                # animates word-by-word rather than a static line.
                toks = text.split()
                words = []
                if toks and end > start:
                    step = (end - start) / len(toks)
                    words = [
                        WordTimestamp(word=w, start=round(start + j * step, 3),
                                      end=round(start + (j + 1) * step, 3))
                        for j, w in enumerate(toks)
                    ]
                segs.append(TranscriptSegment(id=i, start=start, end=end, text=text, words=words))
            base = Path(result["srt"]).with_suffix("")  # <name>.<lang>
            trans_ass = f"{base}.ass"
            generate_karaoke_captions(
                segs, trans_ass, style_preset=req.style_preset, font_size=req.font_size,
            )
            burned_out = f"{base}.mp4"
            render_clip(
                input_video=req.source_video, output_video=burned_out,
                start_time=float(req.start_seconds), end_time=float(req.end_seconds),
                aspect_ratio=req.aspect_ratio, burn_captions=True, subtitle_path=trans_ass,
            )
            result["burned_video"] = burned_out
        except Exception as e:  # noqa: BLE001
            result["burn_error"] = str(e)

    return result


class DiarizeRequest(BaseModel):
    video_path: str


@app.get("/tools/diarization-available")
def diarization_available_ep():
    from server.core.diarizer import diarization_available
    return {"available": diarization_available()}


@app.post("/tools/diarize")
def api_diarize(req: DiarizeRequest):
    """Speaker diarization (optional; needs pyannote.audio + an HF token).
    Returns availability + segments; never hard-fails for the not-installed case."""
    if not os.path.isfile(req.video_path):
        raise HTTPException(status_code=400, detail=f"File not found: {req.video_path}")
    from server.core.diarizer import diarize
    return diarize(req.video_path)


def _reburn_clip_with_subs(req: SubtitleRegenRequest, ass_path: str,
                           first_word_start: float, last_word_end: float) -> bool:
    """Re-render the clip to burn a freshly-generated .ass, reusing the same
    source/segment logic as /export/subtitles. Returns True on success, and
    degrades gracefully (returns False) on any failure. Shared by the
    speaker-caption path so it matches the manual caption re-render exactly.
    """
    if not (req.re_render and req.clip_output_file and os.path.exists(req.clip_output_file)):
        return False
    try:
        target_file = str(Path(req.clip_output_file).expanduser().resolve())
        temp_target = str(Path(target_file).with_suffix(f".new.{uuid.uuid4().hex[:6]}.mp4"))
        input_video = req.source_video if (req.source_video and os.path.exists(req.source_video)) else target_file
        if input_video == target_file or req.start_seconds is None or req.end_seconds is None:
            render_clip(
                input_video=target_file,
                output_video=temp_target,
                start_time=0.0,
                end_time=last_word_end - first_word_start + 1.0,
                aspect_ratio=req.aspect_ratio or "9:16",
                burn_captions=True,
                subtitle_path=ass_path,
                layout=req.layout or "",
                cam_video=req.cam_video,
                cam_scale=req.cam_scale,
                cam_position=req.cam_position,
            )
        else:
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
            return True
    except Exception:
        pass
    return False


@app.post("/tools/speaker-captions")
def api_speaker_captions(req: SubtitleRegenRequest):
    """Speaker-LABELED captions (optional; needs pyannote.audio + an HF token).

    Runs diarization on the rendered clip, tags each caption line with a
    friendly "Speaker N" label (whoever talks first = Speaker 1), rewrites the
    SRT + animated .ass with the label prefixed to each speaker turn, and
    optionally re-renders the clip to burn them in. Degrades gracefully: when
    pyannote/token is missing it returns available=False with a message and
    changes nothing.
    """
    from server.core.diarizer import diarize, group_words_into_speaker_turns
    from server.core.caption_styler import generate_karaoke_captions
    from server.core.ffmpeg_tools import generate_srt
    from server.models import TranscriptSegment, WordTimestamp

    diar_target = req.clip_output_file or ""
    if not diar_target or not os.path.isfile(diar_target):
        raise HTTPException(status_code=400, detail="A rendered clip (clip_output_file) is required")

    words = [w for w in (req.words or []) if w.get("word")]
    if not words:
        raise HTTPException(status_code=400, detail="No words provided for speaker captioning")

    result = diarize(diar_target)
    if not result.get("available"):
        return {"available": False, "message": result.get("message", "Diarization unavailable"),
                "re_rendered": False}

    # Words carry ABSOLUTE source timestamps; diarization of the rendered clip is
    # clip-local (0-based). Shift the diarization onto the source timeline by the
    # clip's start offset so the two line up.
    first_start = float(words[0].get("start", 0))
    last_end = float(words[-1].get("end", 0))
    diar_offset = float(req.start_seconds) if req.start_seconds is not None else first_start
    turns = group_words_into_speaker_turns(words, result.get("segments", []), diar_offset=diar_offset)

    # Build one TranscriptSegment per speaker turn (carrying the friendly label),
    # so caption_styler prefixes the label onto each turn's first caption chunk.
    turn_segs: List[TranscriptSegment] = []
    for i, turn in enumerate(turns):
        tw = [
            WordTimestamp(word=w.get("word", ""), start=float(w.get("start", 0)), end=float(w.get("end", 0)))
            for w in turn["words"] if w.get("word")
        ]
        if not tw:
            continue
        turn_segs.append(TranscriptSegment(
            id=i,
            start=tw[0].start,
            end=tw[-1].end,
            text=" ".join(w.word for w in tw),
            words=tw,
            speaker=turn.get("speaker"),
        ))

    if not turn_segs:
        return {"available": True, "message": "No speaker turns could be built from the words.",
                "re_rendered": False}

    # SRT with the label prefixed to each turn's text (useful for editors too).
    srt_segs = [
        TranscriptSegment(id=s.id, start=s.start, end=s.end,
                          text=(f"{s.speaker}: {s.text}" if s.speaker else s.text), words=s.words)
        for s in turn_segs
    ]
    generate_srt(srt_segs, req.output_path)

    ass_path = os.path.splitext(req.output_path)[0] + ".ass"
    generate_karaoke_captions(
        turn_segs,
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
        intro_font_size=req.intro_font_size,
        intro_style=req.intro_style,
    )

    re_rendered = _reburn_clip_with_subs(req, ass_path, first_start, last_end)

    speakers = sorted({s.speaker for s in turn_segs if s.speaker})
    msg = f"Labeled captions for {len(speakers)} speaker(s) across {len(turn_segs)} turns."
    if re_rendered:
        msg += " Clip re-rendered with labels burned in."
    return {
        "available": True,
        "message": msg,
        "speakers": speakers,
        "srt_path": req.output_path,
        "ass_path": ass_path,
        "re_rendered": re_rendered,
    }


@app.get("/api/setup/hf-token")
def get_hf_token():
    """Whether a Hugging Face token is configured (never returns the value)."""
    have = bool(os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN") or _hf_token_path().is_file())
    return {"set": have}


class HfTokenRequest(BaseModel):
    token: str = ""


@app.post("/api/setup/hf-token")
def set_hf_token(req: HfTokenRequest):
    """Save (or clear) the Hugging Face token used by optional diarization. Stored
    locally in the gitignored logs dir; also applied to the running process."""
    tok = (req.token or "").strip()
    p = _hf_token_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if tok:
            p.write_text(tok, encoding="utf-8")
            os.environ["HF_TOKEN"] = tok
        else:
            if p.exists():
                p.unlink()
            os.environ.pop("HF_TOKEN", None)
        return {"ok": True, "set": bool(tok)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))


class OptionalInstallRequest(BaseModel):
    component: str


@app.post("/api/setup/install-optional")
def install_optional(req: OptionalInstallRequest):
    """Install a heavy OPTIONAL dependency into the app's venv (kept out of
    Install-All). Currently: 'diarization' -> pyannote.audio."""
    import sys
    pkgs = {"diarization": ["pyannote.audio"]}.get((req.component or "").strip())
    if not pkgs:
        raise HTTPException(status_code=400, detail=f"Unknown optional component: {req.component}")
    cmd = [sys.executable, "-m", "pip", "install", *pkgs]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=1800,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return {"component": req.component, "ok": r.returncode == 0,
                "returncode": r.returncode, "stderr": (r.stderr or "")[-1500:]}
    except subprocess.TimeoutExpired:
        return {"component": req.component, "ok": False, "error": "Install timed out"}
    except Exception as e:  # noqa: BLE001
        return {"component": req.component, "ok": False, "error": str(e)}


@app.post("/tools/bleep-mute", response_model=BleepMuteResponse)
def api_bleep_mute(req: BleepMuteRequest):
    """Censor audio profanity or custom words with 1000Hz bleep or mute."""
    from server.core.word_filter import apply_bleep_or_mute, filter_word_timestamps
    try:
        out_path = req.output_path
        if not out_path:
            p = Path(req.video_path)
            out_path = str(p.parent / f"{p.stem}_censored{p.suffix}")

        ts = req.timestamps or []
        # Narrow a full word list down to just profanity/custom words so the
        # bleep hits the right moments instead of the entire clip.
        if req.profanity_only:
            ts = filter_word_timestamps(ts, req.custom_words)
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


def _compute_speaker_offset(source: str, start: float, end: float, aspect: str):
    """Best-effort active-speaker crop x-offset for a ratio; None => centered.
    16:9 is a letterbox fit (no horizontal crop), so it always stays centered.
    Any detection/OpenCV/YOLO failure degrades quietly to a centered crop."""
    if aspect == "16:9":
        return None
    try:
        from server.core.face_tracker import FaceTracker
        off = FaceTracker().get_speaker_center_x(source, start, end, aspect_ratio=aspect)
        return float(off) if off is not None else None
    except Exception:
        return None


@app.post("/export/aspect-preview", response_model=AspectPreviewResponse)
def export_aspect_preview(req: AspectPreviewRequest):
    """Render one real cropped still (with active-speaker framing) for a ratio so
    the multi-aspect modal previews the ACTUAL export framing, not a CSS guess."""
    src = req.source_video if (req.source_video and os.path.exists(req.source_video)) else req.clip_path
    if not os.path.isfile(src):
        raise HTTPException(status_code=400, detail=f"Source not found: {src}")
    if req.source_video and req.start_seconds is not None and req.end_seconds is not None:
        start, end = float(req.start_seconds), float(req.end_seconds)
    else:
        start, end = 0.0, get_video_duration(src)
    mid = start + max(0.0, end - start) / 2.0
    offset = _compute_speaker_offset(src, start, end, req.aspect_ratio)
    stem = os.path.splitext(os.path.basename(req.clip_path))[0]
    slug = req.aspect_ratio.replace(":", "x")
    out_dir = req.output_dir or str(_ensure_output_root() / ".cache" / "aspect" / stem)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    out_path = str(Path(out_dir) / f"preview_{slug}.jpg")
    try:
        img = render_cropped_frame(src, out_path, timestamp=mid, aspect_ratio=req.aspect_ratio, crop_x_offset=offset)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))
    return AspectPreviewResponse(image_path=img, aspect_ratio=req.aspect_ratio, crop_x_offset=offset)


@app.post("/export/multi-aspect", response_model=MultiAspectExportResponse)
def export_multi_aspect(req: MultiAspectExportRequest):
    """
    Renders a clip in multiple aspect ratios (e.g. 9:16 vertical, 1:1 square, 4:5 portrait, 16:9 landscape)
    in a single coordinated pass.
    """
    if not os.path.exists(req.clip_path) and not (req.source_video and os.path.exists(req.source_video)):
        raise HTTPException(status_code=400, detail=f"Source clip or video not found: {req.clip_path}")

    source = req.source_video if (req.source_video and os.path.exists(req.source_video)) else req.clip_path
    stem = "".join(c for c in (req.title or Path(req.clip_path).stem) if c.isalnum() or c in " _-").replace("_", " ")
    stem = " ".join(stem.split())[:70].strip() or "clip"
    out_dir = Path(req.output_dir).expanduser().resolve() if req.output_dir else Path(req.clip_path).parent / f"{stem} multi aspect"
    out_dir.mkdir(parents=True, exist_ok=True)

    exports = {}
    valid_aspects = ["9:16", "1:1", "4:5", "16:9"]
    requested_aspects = [a for a in req.aspect_ratios if a in valid_aspects] or valid_aspects

    start = req.start_seconds if (req.start_seconds is not None and req.source_video) else 0.0
    duration = (req.end_seconds - req.start_seconds) if (req.end_seconds is not None and req.start_seconds is not None and req.source_video) else get_video_duration(source)
    end = start + duration

    for aspect in requested_aspects:
        slug = aspect.replace(":", "x")
        out_file = str(out_dir / f"{stem} {slug}.mp4")
        # Prefer a preview-computed offset; otherwise compute the active-speaker
        # crop now so the export auto-frames the speaker instead of dead-centering.
        offset = None
        if req.crop_offsets and aspect in req.crop_offsets:
            try:
                offset = float(req.crop_offsets[aspect]) if req.crop_offsets[aspect] is not None else None
            except (TypeError, ValueError):
                offset = None
        else:
            offset = _compute_speaker_offset(source, start, end, aspect)
        try:
            render_clip(
                input_video=source,
                output_video=out_file,
                start_time=start,
                end_time=end,
                aspect_ratio=aspect,
                crop_x_offset=offset,
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


class SuggestHooksRequest(BaseModel):
    text: str = ""
    words: List[dict] = []
    count: int = 6


@app.post("/tools/suggest-hooks")
def suggest_hooks(req: SuggestHooksRequest):
    """Return hook-line candidates drawn from a clip's OWN transcript so the user
    can rotate through alternatives instead of the single auto-picked hook.
    """
    from server.core.highlight_detector import rank_hook_candidates
    text = (req.text or "").strip()
    if not text and req.words:
        text = " ".join(str(w.get("word", "")) for w in req.words if isinstance(w, dict) and w.get("word"))
    hooks = rank_hook_candidates(text, max(1, min(int(req.count or 6), 12)))
    return {"hooks": hooks}


class RewriteHookRequest(BaseModel):
    text: str = ""
    words: List[dict] = []
    current_hook: str = ""
    count: int = 6
    preset: str = ""  # caption preset name, used as a light tone hint


@app.post("/tools/rewrite-hook")
def rewrite_hook(req: RewriteHookRequest):
    """AI-punch-up hooks with the active local Ollama model, grounded in the
    clip's transcript. Degrades gracefully to the offline heuristic when Ollama
    isn't running or the call fails, so the button always returns *something*.
    """
    from server.core.highlight_detector import rank_hook_candidates
    from server.core import system_check as sc

    text = (req.text or "").strip()
    if not text and req.words:
        text = " ".join(str(w.get("word", "")) for w in req.words if isinstance(w, dict) and w.get("word"))
    count = max(1, min(int(req.count or 6), 10))
    heuristic = rank_hook_candidates(text, count)

    model = _active_llm_model()
    if not _llm_available():
        # `ollama_running` is kept for UI back-compat; it now means "the active
        # AI backend is reachable", whichever backend that is.
        return {"used_ai": False, "ollama_running": False, "model": None, "hooks": heuristic}

    from server.core.hook_writer import generate_hooks_llm
    ai_hooks = generate_hooks_llm(text, req.current_hook, count, model, req.preset)
    if ai_hooks:
        return {"used_ai": True, "ollama_running": True, "model": model, "hooks": ai_hooks}
    # Backend is up but returned nothing usable — fall back rather than error.
    return {"used_ai": False, "ollama_running": True, "model": model, "hooks": heuristic}


class RewriteCopyRequest(BaseModel):
    text: str = ""
    words: List[dict] = []
    current_hook: str = ""
    preset: str = ""  # caption preset name, kept for parity with rewrite-hook


@app.post("/tools/rewrite-copy")
def rewrite_copy(req: RewriteCopyRequest):
    """Rewrite a clip's HOOK + TITLE + DESCRIPTION in one shot with the active
    local Ollama model (the CapCut / OpusClips-style copywriter), grounded in the
    clip's transcript. Degrades to offline transcript heuristics when Ollama
    isn't running so the button always returns usable copy.
    """
    from server.core.highlight_detector import rank_hook_candidates, choose_hook_and_title
    from server.core import system_check as sc

    text = (req.text or "").strip()
    if not text and req.words:
        text = " ".join(str(w.get("word", "")) for w in req.words if isinstance(w, dict) and w.get("word"))

    # Offline pieces — used when Ollama is down or the model returns nothing.
    heuristic_hooks = rank_hook_candidates(text, 1)
    h_hook, h_title = choose_hook_and_title(text)
    fallback = {
        "hook": (heuristic_hooks[0] if heuristic_hooks else h_hook) or (req.current_hook or ""),
        "title": h_title or "",
        "description": "",
    }

    model = _active_llm_model()
    if not _llm_available():
        return {"used_ai": False, "ollama_running": False, "model": None, **fallback}

    from server.core.hook_writer import generate_clip_copy_llm
    copy = generate_clip_copy_llm(text, current_hook=req.current_hook, model=model)
    if copy:
        return {
            "used_ai": True,
            "ollama_running": True,
            "model": model,
            "hook": copy.get("hook") or fallback["hook"],
            "title": copy.get("title") or fallback["title"],
            "description": copy.get("description") or "",
        }
    # Backend up but returned nothing usable — fall back rather than error.
    return {"used_ai": False, "ollama_running": True, "model": model, **fallback}


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
    """Return the user's chosen output folder where rendered clips are written.

    Empty string = unset, in which case renders go to the disposable built-in
    ./output working dir (temporary scratch space)."""
    return {"folder": str(_ensure_output_root()), "default_folder": str(DEFAULT_OUTPUT_ROOT), "is_default": EXPORT_DEFAULT_DIR == ""}


@app.post("/output-folder")
def set_output_folder(req: OutputFolderRequest):
    """Choose the folder where rendered clips are saved (created if missing).

    Setting it re-points the engine's output dir there, so NEW renders/jobs land
    in the chosen folder directly. (Previously generated clips keep their paths,
    and any running job finishes in the dir it started with.) Clearing it (empty
    folder) reverts to the disposable built-in ./output working dir, which is
    treated as temporary scratch space.
    """
    global EXPORT_DEFAULT_DIR
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
        EXPORT_DEFAULT_DIR = str(root)
        _sync_output_root(root)                 # send new renders to the chosen folder
    else:
        EXPORT_DEFAULT_DIR = ""
        _sync_output_root(DEFAULT_OUTPUT_ROOT)  # revert to the temp ./output working dir
    return {"folder": str(_ensure_output_root()), "default_folder": str(DEFAULT_OUTPUT_ROOT), "is_default": EXPORT_DEFAULT_DIR == ""}


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
        "faster_whisper": {"installed": sc.component_installed("faster-whisper")},
        "mlx_whisper": {"installed": sc.component_installed("mlx-whisper")},
        "librosa": {"installed": sc.component_installed("librosa")},
        "ultralytics": sc.detect_ultralytics(),
        "gpu": sc.detect_gpu(),
        "cpu": sc.detect_cpu(),
        "torch": sc.detect_torch(),
        "install_commands": sc.get_install_commands(),
        "uninstall_commands": sc.get_uninstall_commands(),
        "uninstall_shell_commands": {key: sc.format_shell_command(cmd) for key, cmd in sc.get_uninstall_commands().items()} if sc.sys.prefix != sc.sys.base_prefix else {},
        "uninstall_environment_isolated": sc.sys.prefix != sc.sys.base_prefix,
        "recommendations": sc.recommend_models(),
        "output_dir": str(_ensure_output_root()),
    }


@app.get("/api/setup/install-jobs")
def setup_install_jobs():
    from server.core.setup_installs import snapshot
    return {"jobs": snapshot()}


@app.post("/api/setup/install-jobs")
def setup_start_install(req: SetupInstallRequest):
    from server.core.setup_installs import start
    try:
        return start(req.component)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error))


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
    from server.core import system_check as sc
    # Resolve the CLI even when it isn't on PATH (e.g. the macOS Ollama.app).
    ollama_exe = shutil.which("ollama") or (sc.detect_ollama().get("executable") or "ollama")
    try:
        result = subprocess.run(
            [ollama_exe, "pull", model], capture_output=True, text=True, timeout=1800,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return {"model": model, "ok": result.returncode == 0, "returncode": result.returncode,
                "stdout": result.stdout[-1500:], "stderr": result.stderr[-1500:]}
    except subprocess.TimeoutExpired:
        return {"model": model, "ok": False, "error": "Model download timed out after 30 minutes"}
    except Exception as e:
        return {"model": model, "ok": False, "error": str(e)}


@app.post("/api/setup/ollama/remove")
def ollama_remove(model: str = ""):
    """Delete a locally-installed Ollama model to free disk space (ollama rm)."""
    if not (model or "").strip():
        raise HTTPException(status_code=400, detail="model is required")
    from server.core import system_check as sc
    ollama_exe = shutil.which("ollama") or (sc.detect_ollama().get("executable") or "ollama")
    try:
        result = subprocess.run(
            [ollama_exe, "rm", model], capture_output=True, text=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return {"model": model, "ok": result.returncode == 0, "returncode": result.returncode,
                "stdout": result.stdout[-800:], "stderr": result.stderr[-800:]}
    except Exception as e:
        return {"model": model, "ok": False, "error": str(e)}


# ----------------------------------------------------------------------
# Streaming model pull with live progress + cancel (for the catalog UI)
# ----------------------------------------------------------------------
_PULL_JOBS: Dict[str, dict] = {}
_PULL_LOCK = threading.Lock()


def _pull_progress_fields(prog) -> tuple:
    """Extract (status, completed, total) from an ollama progress item, which
    may be an object with attributes or a plain dict depending on version."""
    def g(k):
        if isinstance(prog, dict):
            return prog.get(k)
        return getattr(prog, k, None)
    return g("status"), g("completed"), g("total")


def _pull_worker(model: str):
    from server.core.system_check import invalidate_model_health
    invalidate_model_health(model)
    try:
        import ollama
        for prog in ollama.pull(model, stream=True):
            with _PULL_LOCK:
                job = _PULL_JOBS.get(model)
                if not job or job.get("cancel"):
                    break
                status, completed, total = _pull_progress_fields(prog)
                if status:
                    job["status"] = status
                if total:
                    job["total"] = total
                    job["completed"] = completed or 0
                    job["percent"] = round(100.0 * (completed or 0) / total, 1)
    except Exception as e:  # noqa: BLE001
        with _PULL_LOCK:
            job = _PULL_JOBS.get(model)
            if job:
                job["error"] = str(e)
                job["state"] = "error"
                job["done"] = True
        return
    invalidate_model_health(model)
    # Finalize (success vs cancelled).
    with _PULL_LOCK:
        job = _PULL_JOBS.get(model)
        cancelled = bool(job and job.get("cancel"))
        if job:
            job["done"] = True
            if cancelled:
                job["state"] = "cancelled"
            else:
                job["state"] = "success"
                job["percent"] = 100.0
    # A cancelled pull leaves a partial model; remove it so no half-download lingers.
    if cancelled:
        try:
            from server.core import system_check as sc
            exe = shutil.which("ollama") or (sc.detect_ollama().get("executable") or "ollama")
            subprocess.run([exe, "rm", model], capture_output=True, text=True, timeout=60,
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except Exception:
            pass


@app.post("/api/setup/ollama/pull-start")
def ollama_pull_start(model: str = ""):
    """Begin a background model download and track live progress. Returns
    immediately; poll /pull-progress and optionally /pull-cancel."""
    model = (model or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="model is required")
    from server.core import system_check as sc
    if not sc.detect_ollama().get("running"):
        raise HTTPException(status_code=400, detail="Ollama isn't running. Start Ollama, then try again.")
    with _PULL_LOCK:
        existing = _PULL_JOBS.get(model)
        if existing and not existing.get("done"):
            return {"model": model, "started": False, "already_running": True}
        _PULL_JOBS[model] = {"state": "downloading", "status": "starting", "percent": 0.0,
                             "completed": 0, "total": 0, "cancel": False, "done": False, "error": None}
    threading.Thread(target=_pull_worker, args=(model,), daemon=True).start()
    return {"model": model, "started": True}


@app.get("/api/setup/ollama/pull-progress")
def ollama_pull_progress(model: str = ""):
    with _PULL_LOCK:
        job = _PULL_JOBS.get((model or "").strip())
        if not job:
            return {"model": model, "state": "idle", "percent": 0, "done": True}
        return {"model": model, **job}


@app.post("/api/setup/ollama/pull-cancel")
def ollama_pull_cancel(model: str = ""):
    with _PULL_LOCK:
        job = _PULL_JOBS.get((model or "").strip())
        if not job:
            return {"model": model, "ok": False, "error": "no active download"}
        job["cancel"] = True
        job["status"] = "cancelling"
    return {"model": model, "ok": True}


# ----------------------------------------------------------------------
# Whisper model download: pre-flight presence check + background download with
# live progress + cancel. Mirrors the Ollama pull endpoints above so the UI can
# fetch a transcription model as its OWN visible step, instead of the download
# happening implicitly inside transcription (where it looked like the job hung).
# ----------------------------------------------------------------------
_WHISPER_PULL_JOBS: Dict[str, dict] = {}
_WHISPER_PULL_LOCK = threading.Lock()


@app.get("/api/setup/whisper/model-status")
def whisper_model_status(model: str = "base"):
    """Is `model` already downloaded for the backend that would actually run?
    `downloadable` is False when there's nothing to pre-fetch (a remote ASR
    endpoint is configured, or no local backend is installed)."""
    from server.core import whisper_models
    return {"model": (model or "base").strip(), **whisper_models.status(model)}


def _whisper_pull_worker(model: str):
    from server.core import whisper_models

    def progress(done: int, total: int, pct: float):
        with _WHISPER_PULL_LOCK:
            job = _WHISPER_PULL_JOBS.get(model)
            if not job:
                return
            job["completed"] = done
            job["total"] = total
            job["percent"] = pct
            job["status"] = "downloading"

    def should_cancel() -> bool:
        with _WHISPER_PULL_LOCK:
            job = _WHISPER_PULL_JOBS.get(model)
            return bool(job and job.get("cancel"))

    try:
        whisper_models.download(model, progress, should_cancel)
    except whisper_models.DownloadCancelled:
        with _WHISPER_PULL_LOCK:
            job = _WHISPER_PULL_JOBS.get(model)
            if job:
                job["done"] = True
                job["state"] = "cancelled"
        return
    except Exception as e:  # noqa: BLE001
        with _WHISPER_PULL_LOCK:
            job = _WHISPER_PULL_JOBS.get(model)
            if job:
                job["error"] = str(e)
                job["state"] = "error"
                job["done"] = True
        return
    with _WHISPER_PULL_LOCK:
        job = _WHISPER_PULL_JOBS.get(model)
        if job:
            job["done"] = True
            job["state"] = "success"
            job["percent"] = 100.0


@app.post("/api/setup/whisper/pull-start")
def whisper_pull_start(model: str = "base"):
    """Begin downloading a Whisper model in the background. Returns immediately;
    poll /whisper/pull-progress and optionally /whisper/pull-cancel."""
    model = (model or "base").strip()
    from server.core import whisper_models
    st = whisper_models.status(model)
    if not st.get("downloadable"):
        # Remote endpoint or no local backend: nothing to fetch.
        return {"model": model, "started": False, "not_needed": True, "backend": st.get("backend")}
    if st.get("present"):
        return {"model": model, "started": False, "already_present": True, "backend": st.get("backend")}
    with _WHISPER_PULL_LOCK:
        existing = _WHISPER_PULL_JOBS.get(model)
        if existing and not existing.get("done"):
            return {"model": model, "started": False, "already_running": True}
        _WHISPER_PULL_JOBS[model] = {"state": "downloading", "status": "starting", "percent": 0.0,
                                     "completed": 0, "total": 0, "cancel": False, "done": False, "error": None}
    threading.Thread(target=_whisper_pull_worker, args=(model,), daemon=True).start()
    return {"model": model, "started": True, "backend": st.get("backend")}


@app.get("/api/setup/whisper/pull-progress")
def whisper_pull_progress(model: str = "base"):
    with _WHISPER_PULL_LOCK:
        job = _WHISPER_PULL_JOBS.get((model or "base").strip())
        if not job:
            return {"model": model, "state": "idle", "percent": 0, "done": True}
        return {"model": model, **job}


@app.post("/api/setup/whisper/pull-cancel")
def whisper_pull_cancel(model: str = "base"):
    with _WHISPER_PULL_LOCK:
        job = _WHISPER_PULL_JOBS.get((model or "base").strip())
        if not job:
            return {"model": model, "ok": False, "error": "no active download"}
        job["cancel"] = True
        job["status"] = "cancelling"
    return {"model": model, "ok": True}


@app.get("/api/setup/missing")
def setup_missing():
    """The install-command components not yet present, so the UI can install them
    one at a time with a real progress bar."""
    from server.core import system_check as sc
    commands = sc.get_install_commands()
    missing = sc.missing_components()
    return {"missing": missing, "commands": {k: " ".join(commands[k]) for k in missing}}


@app.post("/api/setup/uninstall")
def setup_uninstall(req: SetupInstallRequest):
    """Uninstall a pip-managed package (whisper / faster-whisper / mlx-whisper /
    ultralytics / librosa / pytorch). System apps are not removable here."""
    from server.core import system_check as sc
    cmd = sc.get_uninstall_commands().get(req.component)
    if not cmd:
        raise HTTPException(status_code=400, detail=f"Cannot uninstall '{req.component}' from here")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return {
            "component": req.component,
            "command": " ".join(cmd),
            "returncode": result.returncode,
            "ok": result.returncode == 0,
            "stdout": (result.stdout or "")[-2000:],
            "stderr": (result.stderr or "")[-2000:],
        }
    except subprocess.TimeoutExpired:
        return {"component": req.component, "ok": False, "error": "Uninstall timed out"}
    except Exception as e:  # noqa: BLE001
        return {"component": req.component, "ok": False, "error": str(e)}


@app.post("/api/setup/install-all")
def setup_install_all():
    """Install every missing recommended dependency in one pass (sequential).
    Does NOT pull Ollama models — those are large and handled separately."""
    from server.core import system_check as sc

    commands = sc.get_install_commands()
    missing = sc.missing_components()
    results = []
    for key in missing:
        cmd = commands.get(key)
        if not cmd:
            continue
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=1200,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            results.append({
                "component": key,
                "command": " ".join(cmd),
                "returncode": r.returncode,
                "ok": r.returncode == 0,
                "stderr": (r.stderr or "")[-500:],
            })
        except subprocess.TimeoutExpired:
            results.append({"component": key, "ok": False, "error": "timed out"})
        except Exception as e:  # noqa: BLE001
            results.append({"component": key, "ok": False, "error": str(e)})

    installed_ok = [r["component"] for r in results if r.get("ok")]
    failed = [r["component"] for r in results if not r.get("ok")]
    return {
        "attempted": len(results),
        "installed": installed_ok,
        "failed": failed,
        "already_present": [k for k in commands if k not in missing],
        "results": results,
    }


class AIModelRequest(BaseModel):
    kind: str = "ollama"  # currently only "ollama"
    model: str


@app.get("/api/setup/ai-models")
def get_ai_models():
    """Current preferred LLM model + the Ollama models installed locally."""
    from server.core import system_check as sc
    return {
        "ollama": PREFERRED_OLLAMA_MODEL,
        "installed_ollama_models": sc.list_ollama_models(),
    }


@app.get("/api/setup/model-catalog")
def model_catalog(preset: str = ""):
    """Curated local-LLM catalog (small → large) with a recommendation tuned to
    hardware AND the active caption preset (high-energy presets get a punchier
    pick), plus installed state and the currently active model.
    """
    from server.core import system_check as sc
    data = sc.ollama_model_catalog(preset)
    data["active"] = PREFERRED_OLLAMA_MODEL
    data["ollama"] = sc.detect_ollama()
    return data


@app.post("/api/setup/ai-model")
def set_ai_model(req: AIModelRequest):
    """Change the active local LLM model used by chat + highlight discovery."""
    global PREFERRED_OLLAMA_MODEL
    model = (req.model or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="model is required")
    if req.kind == "ollama":
        from server.core import system_check as sc
        installed = sc.list_ollama_models()
        resolved = next((name for name in installed if name == model or name == model + ":latest"), None)
        if not resolved:
            raise HTTPException(status_code=409, detail="This model is not downloaded or Ollama is unavailable. Download it in Settings, wait for completion, then refresh and select it.")
        model = resolved
        PREFERRED_OLLAMA_MODEL = model
        _save_preferred_model(model)  # remember it across restarts
        return {"ok": True, "ollama": PREFERRED_OLLAMA_MODEL}
    raise HTTPException(status_code=400, detail=f"Unknown model kind: {req.kind}")


class LlmEndpointRequest(BaseModel):
    backend: str = "ollama"          # "ollama" | "openai"
    base_url: str = ""               # e.g. http://localhost:1234/v1
    api_key: str = ""                # optional; only for endpoints that need it
    model: str = ""                  # model name as the endpoint calls it


@app.get("/api/setup/llm-endpoint")
def get_llm_endpoint():
    """Active AI backend settings. Never returns the API key, only whether it's set."""
    from server.core import llm_client
    return llm_client.describe()


@app.post("/api/setup/llm-endpoint")
def set_llm_endpoint(req: LlmEndpointRequest):
    """Point the app's AI features at the built-in Ollama or ANY OpenAI-compatible
    server (llama.cpp llama-server, LM Studio, vLLM, a cloud endpoint...).

    One setting covers all of them because they share the /v1/chat/completions
    contract — which is why this is a config option rather than a separate build.
    """
    from server.core import llm_client
    try:
        llm_client.save_config(
            backend=req.backend, base_url=req.base_url,
            api_key=req.api_key, model=req.model,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Could not save AI backend settings: {e}")
    return llm_client.describe()


@app.post("/api/setup/llm-test")
def test_llm_endpoint(req: LlmEndpointRequest):
    """Try the supplied settings WITHOUT saving them, so the user can verify a
    server before committing to it. Returns the model's own reply on success."""
    from server.core import llm_client

    backend = (req.backend or "ollama").strip().lower()
    if backend == llm_client.BACKEND_OPENAI:
        base = llm_client.normalize_base_url(req.base_url)
        if not base:
            raise HTTPException(status_code=400, detail="A server address is required")
        cfg = {"backend": backend, "base_url": base,
               "api_key": (req.api_key or "").strip(), "model": (req.model or "").strip()}
        if not llm_client.is_available(cfg):
            return {"ok": False, "backend": backend, "base_url": base,
                    "error": "Nothing answered at that address. Is the server running?"}
        try:
            reply = llm_client._chat_openai(
                cfg, [{"role": "user", "content": "Reply with the single word: ready"}],
                json_mode=False, model=cfg["model"] or None, timeout=30.0,
            )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "backend": backend, "base_url": base, "error": str(e)}
        return {"ok": True, "backend": backend, "base_url": base,
                "model": cfg["model"] or "(server default)", "reply": (reply or "")[:200]}

    # Built-in Ollama
    from server.core import system_check as sc
    info = sc.detect_ollama()
    if not info.get("running"):
        return {"ok": False, "backend": "ollama",
                "error": "Ollama isn't running. Start it, then test again."}
    model = (req.model or "").strip() or PREFERRED_OLLAMA_MODEL
    try:
        reply = llm_client._chat_ollama(
            [{"role": "user", "content": "Reply with the single word: ready"}],
            json_mode=False, model=model,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "backend": "ollama", "model": model, "error": str(e)}
    return {"ok": True, "backend": "ollama", "model": model, "reply": (reply or "")[:200]}


class AsrEndpointRequest(BaseModel):
    backend: str = "local"           # "local" | "openai"
    base_url: str = ""               # e.g. http://localhost:8080/v1
    api_key: str = ""
    model: str = ""                  # e.g. whisper-1 / ggml-base.en


@app.get("/api/setup/asr-endpoint")
def get_asr_endpoint():
    """Active speech-to-text backend settings (never returns the API key)."""
    from server.core import asr_client
    return asr_client.describe()


@app.post("/api/setup/asr-endpoint")
def set_asr_endpoint(req: AsrEndpointRequest):
    """Transcribe locally (default) or via any server that speaks the OpenAI
    audio-transcription API — whisper.cpp's whisper-server,
    faster-whisper-server, Speaches, or OpenAI itself."""
    from server.core import asr_client
    try:
        asr_client.save_config(
            backend=req.backend, base_url=req.base_url,
            api_key=req.api_key, model=req.model,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Could not save transcription settings: {e}")
    return asr_client.describe()


@app.post("/api/setup/asr-test")
def test_asr_endpoint(req: AsrEndpointRequest):
    """Probe a transcription endpoint WITHOUT saving it, so the user can verify
    the address before committing. Sends a short generated tone rather than a
    real clip, so the check is fast."""
    from server.core import asr_client

    backend = (req.backend or "local").strip().lower()
    if backend != asr_client.BACKEND_OPENAI:
        from server.core.transcriber import detect_active_backend
        info = detect_active_backend()
        return {"ok": bool(info.get("active")), "backend": "local",
                "active": info.get("active"), "note": info.get("note")}

    base = asr_client.normalize_base_url(req.base_url)
    if not base:
        raise HTTPException(status_code=400, detail="A server address is required")
    cfg = {"backend": backend, "base_url": base,
           "api_key": (req.api_key or "").strip(), "model": (req.model or "").strip()}
    if not asr_client.is_available(cfg):
        return {"ok": False, "backend": backend, "base_url": base,
                "error": "Nothing answered at that address. Is the server running?"}

    # Reachable. Try a real 1-second transcription so auth/model problems show
    # up here rather than midway through a clipping run.
    tmp_dir = _ensure_output_root() / ".cache"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    probe = str(tmp_dir / "asr_probe.wav")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
             "-ar", "16000", "-ac", "1", probe],
            capture_output=True, text=True, timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except Exception:
        return {"ok": True, "backend": backend, "base_url": base,
                "note": "Server is reachable (could not build a local probe clip to test further)."}

    saved = asr_client.load_config()
    try:
        asr_client.save_config(**cfg)          # transcribe_remote reads the config
        segments, real_words = asr_client.transcribe_remote(probe, timeout=90.0)
        return {"ok": True, "backend": backend, "base_url": base,
                "model": cfg["model"] or asr_client.DEFAULT_MODEL,
                "word_timestamps": real_words,
                "note": ("Word-level timings supported." if real_words else
                         "Server returned no word timings — caption timing will be "
                         "approximated by spreading words across each segment.")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "backend": backend, "base_url": base, "error": str(e)}
    finally:
        # Always restore whatever was configured before the test.
        try:
            asr_client.save_config(**saved)
        except Exception:
            pass
        try:
            os.remove(probe)
        except OSError:
            pass


@app.post("/api/setup/clear-cache")
def clear_cache():
    """Delete the transcript cache (output/.cache/*.json). Safe: it only makes
    the next run re-transcribe; it never touches rendered clips."""
    from server.core.pipeline import VideoClipperEngine
    cache_dir = VideoClipperEngine._transcript_cache_path()
    cleared, freed = 0, 0
    try:
        for f in cache_dir.glob("*.json"):
            try:
                freed += f.stat().st_size
                f.unlink()
                cleared += 1
            except OSError:
                continue
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))
    return {"cleared": cleared, "mb_freed": round(freed / (1024 * 1024), 2)}


@app.get("/api/setup/estimate")
def setup_estimate(duration: float = 30.0, layout: str = "vertical"):
    """Rough render-time estimate for a clip of the given duration."""
    from server.core import system_check as sc

    return sc.estimate_render_time(duration, layout)


@app.get("/api/setup/resources")
def setup_resources():
    """Live system resource usage (CPU/RAM/GPU/VRAM/disk) for the persistent
    footer. Polled every couple of seconds by the UI; never raises — each field
    is None when it can't be read."""
    from server.core import system_check as sc
    return sc.live_resources()


@app.get("/api/setup/gpu")
def setup_gpu():
    """Hardware-aware GPU-acceleration status + the right install/uninstall
    commands for this machine (drives the Setup 'GPU Acceleration' card)."""
    from server.core import system_check as sc
    return sc.gpu_acceleration_status()


@app.post("/api/setup/gpu/install")
def setup_gpu_install():
    """Install the best accelerated PyTorch build for this machine's GPU vendor +
    OS (CUDA / ROCm / DirectML / Intel-XPU / Apple-MPS). Heavy (~2.5GB) and
    replaces the current torch build; runs into the app's own venv via
    `sys.executable -m pip`. Returns the command + result so the UI can report
    success or show the copyable command on failure."""
    from server.core import system_check as sc
    cmd = sc._pytorch_accel_plan().get("command")
    if not cmd:
        raise HTTPException(status_code=400, detail="No PyTorch install command for this machine")
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=2400,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return {"ok": r.returncode == 0, "command": " ".join(cmd),
                "returncode": r.returncode, "stderr": (r.stderr or "")[-2000:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "command": " ".join(cmd), "error": "PyTorch install timed out"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "command": " ".join(cmd), "error": str(e)}


@app.get("/api/setup/support")
def setup_support():
    """Support / social links for the app footer."""
    return {
        "paypal": "https://www.paypal.me/techfreq",
        "beacons": "https://beacons.ai/techfreq",
        "github": "https://github.com/TechFreq",
        "issues": "https://github.com/TechFreq",
        "star": "https://github.com/TechFreq",
    }


def start_server(host: str = "127.0.0.1", port: int = 8765):
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    start_server()
