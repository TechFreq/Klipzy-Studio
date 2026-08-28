"""
FastAPI server exposing the AI Video Clipper as a local API.
The Electron UI talks to this server over HTTP on localhost.
"""

import os
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from server.models import (
    ChatRequest, ChatResponse, ProcessRequest, ProcessResponse, ClipResult,
    ExportProjectRequest, ExportProjectResponse, SubtitleRegenRequest,
    TrimRequest, TrimResponse, CustomRenderRequest
)
from server.core.pipeline import VideoClipperEngine
from server.core.edit_chat import EditChat
from server.core.ffmpeg_tools import check_ffmpeg, get_media_info, detect_hw_encoder, render_clip
from server.core.export_tools import export_fcpxml, export_edl, export_capcut_draft

app = FastAPI(title="AI Video Clipper Server", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job store
JOBS: Dict[str, dict] = {}
ENGINE = VideoClipperEngine()
CHAT = EditChat()


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

    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {"status": "queued", "clips": [], "error": None, "progress": 0}

    def run():
        def progress_callback(step: str, pct: int):
            JOBS[job_id]["progress"] = pct
            JOBS[job_id]["step"] = step

        try:
            JOBS[job_id]["status"] = "processing"
            clips = ENGINE.process_video(
                video_path=req.video_path,
                vertical_crop=req.vertical_crop,
                max_clips=req.max_clips,
                min_duration=req.min_duration,
                max_duration=req.max_duration,
                whisper_model=req.whisper_model,
                language=req.language,
                use_audio_energy=req.use_audio_energy,
                use_llm=req.use_llm,
                progress_callback=progress_callback,
            )
            JOBS[job_id]["clips"] = [c.model_dump() for c in clips]
            JOBS[job_id]["status"] = "completed"
        except Exception as e:
            JOBS[job_id]["status"] = "failed"
            JOBS[job_id]["error"] = str(e)

    threading.Thread(target=run, daemon=True).start()

    return ProcessResponse(job_id=job_id, status="queued")


@app.get("/job/{job_id}")
def get_job(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="Job not found")
    return JOBS[job_id]


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


@app.post("/export/subtitles", response_model=ExportProjectResponse)
def regenerate_subtitles(req: SubtitleRegenRequest):
    """
    Regenerates per-clip subtitle files (SRT + karaoke ASS) from edited words.
    Returns the paths to the rewritten files so the renderer can apply them.
    """
    from server.core.caption_styler import generate_animated_ass
    from server.core.ffmpeg_tools import generate_srt
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

    preset_colors = {
        "opus_yellow": ("Arial Black", "&H00FFFFFF", "&H0000FFFF", "&H00000000", 3),
        "neon_green": ("Arial Black", "&H00FFFFFF", "&H0000FF00", "&H00000000", 3),
        "bold_white": ("Arial", "&H00FFFFFF", "&H00FFD700", "&H00000000", 2),
    }
    font, primary, highlight, outline, border = preset_colors.get(
        req.style_preset, preset_colors["opus_yellow"]
    )
    ass_path = os.path.splitext(req.output_path)[0] + ".ass"
    generate_animated_ass(
        [seg], ass_path,
        font_name=font, primary_color=primary,
        highlight_color=highlight, outline_color=outline, outline_width=border,
    )

    return ExportProjectResponse(
        export_path=req.output_path,
        format="srt",
        message=f"Regenerated subtitles at {req.output_path} (and {ass_path})",
    )

@app.post("/trim", response_model=TrimResponse)
def create_manual_trim(req: TrimRequest):
    """
    Manual clip trimming: renders a user-selected in/out range from the source video
    into its own clip file (no aspect crop unless requested).
    """
    if not os.path.exists(req.video_path):
        raise HTTPException(status_code=400, detail=f"Video file not found: {req.video_path}")
    if req.end_seconds <= req.start_seconds:
        raise HTTPException(status_code=400, detail="end_seconds must be greater than start_seconds")

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

    return TrimResponse(
        clip_path=out_path,
        title=req.title or title_slug,
        start_seconds=req.start_seconds,
        end_seconds=req.end_seconds,
        duration=round(req.end_seconds - req.start_seconds, 3),
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
    if req.end_seconds <= req.start_seconds:
        raise HTTPException(status_code=400, detail="end_seconds must be greater than start_seconds")

    out_path = req.output_path or os.path.join(
        os.path.dirname(os.path.abspath(req.video_path)),
        f"custom_render_{uuid.uuid4().hex[:8]}.mp4",
    )
    if not Path(out_path).suffix:
        out_path += ".mp4"

    if req.layout not in ("vertical", "full", "game_reaction"):
        raise HTTPException(status_code=400, detail=f"Unsupported layout: {req.layout}")

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

    return TrimResponse(
        clip_path=out_path,
        title="Custom render",
        start_seconds=req.start_seconds,
        end_seconds=req.end_seconds,
        duration=round(req.end_seconds - req.start_seconds, 3),
    )

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