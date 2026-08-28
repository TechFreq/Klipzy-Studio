"""
FastAPI server exposing the AI Video Clipper as a local API.
The Electron UI talks to this server over HTTP on localhost.
"""

import os
import threading
import uuid
from typing import Dict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from server.models import (
    ChatRequest, ChatResponse, ProcessRequest, ProcessResponse, ClipResult,
    ExportProjectRequest, ExportProjectResponse
)
from server.core.pipeline import VideoClipperEngine
from server.core.edit_chat import EditChat
from server.core.ffmpeg_tools import check_ffmpeg, get_media_info, detect_hw_encoder
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
    return HealthResponse(
        status="ok",
        ffmpeg_available=ffmpeg_ok,
        hw_encoder=encoder,
        whisper_loaded=True,
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


def start_server(host: str = "127.0.0.1", port: int = 8765):
    import uvicorn
    uvicorn.run(app, host=host, port=port)