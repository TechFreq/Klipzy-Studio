"""
Built-in clip editor API — render an Edit Spec (server/core/edit_spec.py) through
the compositor.

Split out of server.py (which had grown past 2,400 lines) into its own
``APIRouter`` so the editor's endpoints, background worker and job registry live
together. Mounted by server.py via ``app.include_router(editor.router)`` — the
routes and behaviour are unchanged.

The export runs on its own daemon thread with a progress/cancel entry (same
shape as the model-pull workers), so the UI can show a bar and cancel. The spec
is validated synchronously so a bad spec fails the request immediately with 400.
"""
import os
import threading
import uuid
from pathlib import Path
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from server.core import proc
from server.models import EditorExportRequest, EditorExportResponse

router = APIRouter()

_EDITOR_JOBS: Dict[str, dict] = {}
_EDITOR_LOCK = threading.Lock()
_EDITOR_OUTPUTS = set()


def _editor_export_worker(job_id: str, spec: dict, output_path: str,
                          subtitle_path: Optional[str], normalize_audio: bool):
    from server.core import compositor

    def report(step: str, pct: int):
        with _EDITOR_LOCK:
            j = _EDITOR_JOBS.get(job_id)
            if j is not None:
                j["step"] = step
                j["percent"] = pct

    proc.begin_job(f"editor:{job_id}")  # arm the killable-subprocess registry for this render
    try:
        compositor.render(spec, output_path, subtitle_path=subtitle_path,
                          normalize_audio=normalize_audio, progress_callback=report)
        with _EDITOR_LOCK:
            _EDITOR_JOBS[job_id].update(
                {"state": "completed", "percent": 100, "done": True, "output": output_path})
    except proc.CancelledError:
        with _EDITOR_LOCK:
            _EDITOR_JOBS[job_id].update(
                {"state": "cancelled", "done": True, "error": "Export cancelled."})
    except Exception as e:  # noqa: BLE001
        with _EDITOR_LOCK:
            _EDITOR_JOBS[job_id].update({"state": "failed", "done": True, "error": str(e)})
    finally:
        proc.end_job()
        with _EDITOR_LOCK:
            _EDITOR_OUTPUTS.discard(output_path)



@router.post("/editor/export", response_model=EditorExportResponse)
def editor_export(req: EditorExportRequest):
    """Start rendering an edited clip from an Edit Spec. Returns a job id to poll."""
    from server.core.edit_spec import normalize_spec

    # Validate up front so the client gets an immediate, specific error.
    try:
        ns = normalize_spec(req.spec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid edit spec: {e}")

    if not os.path.exists(ns["source"]):
        raise HTTPException(status_code=400, detail=f"Source video not found: {ns['source']}")

    out_dir = Path(req.output_dir) if req.output_dir else Path(ns["source"]).parent
    stem = Path(ns["source"]).stem
    filename = req.filename or f"{stem}_edited_{uuid.uuid4().hex[:8]}.mp4"
    if Path(filename).name != filename or "/" in filename or "\\" in filename or any(c in filename for c in '<>:"|?*') or not filename.lower().endswith(".mp4"):
        raise HTTPException(status_code=400, detail="Use a filename ending in .mp4 with no folders or reserved characters. Choose the folder separately.")
    output_path = str((out_dir / filename).resolve())
    if Path(output_path).exists():
        raise HTTPException(status_code=409, detail="That export already exists. Choose another filename or leave it empty for an automatic name.")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Cannot create the export folder. Choose a writable folder: {exc}")

    with _EDITOR_LOCK:
        if output_path in _EDITOR_OUTPUTS or Path(output_path).exists():
            raise HTTPException(status_code=409, detail="That filename is already being exported or exists. Choose another filename.")
        _EDITOR_OUTPUTS.add(output_path)
    try:
        # If the client asked to burn captions, generate a karaoke ASS from the
        # (already output-timeline-rebased) word timings and burn that. This takes
        # precedence over any prebuilt subtitle_path.
        subtitle_path = req.subtitle_path
        if req.burn_captions and req.caption_words:
            try:
                subtitle_path = _build_caption_ass(req, ns, output_path) or subtitle_path
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Could not prepare captions. Check the caption data and export folder: {e}")

        job_id = uuid.uuid4().hex[:8]
        with _EDITOR_LOCK:
            _EDITOR_JOBS[job_id] = {"state": "rendering", "percent": 0, "step": "Starting…",
                                    "done": False, "error": None, "output": None}
        threading.Thread(
            target=_editor_export_worker,
            args=(job_id, ns, output_path, subtitle_path, req.normalize_audio),
            daemon=True,
        ).start()
        return EditorExportResponse(job_id=job_id, status="started")
    except BaseException:
        with _EDITOR_LOCK:
            _EDITOR_OUTPUTS.discard(output_path)
        raise



# Canvas ratio -> ASS PlayRes (matches the compositor's caption coordinate space).
_ASS_PLAYRES = {
    "9:16": (1080, 1920),
    "4:5": (1080, 1350),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
    "full": (1080, 1920),
}


def _build_caption_ass(req: EditorExportRequest, ns: dict, output_path: str) -> Optional[str]:
    """Turn the request's word timings + style into a karaoke ASS file next to
    the output, returning its path (or None if there are no usable words)."""
    from server.core.caption_styler import generate_karaoke_captions
    from server.models import TranscriptSegment, WordTimestamp

    words = []
    for w in (req.caption_words or []):
        text = str((w or {}).get("word") or "").strip()
        if not text:
            continue
        start = float((w or {}).get("start") or 0.0)
        end = float((w or {}).get("end") or start)
        if end <= start:
            end = start + 0.04
        words.append(WordTimestamp(word=text, start=start, end=end))
    if not words:
        return None

    seg = TranscriptSegment(
        id=0, start=words[0].start, end=words[-1].end,
        text=" ".join(w.word for w in words), words=words,
    )
    style = req.caption_style or {}
    ratio = (ns.get("canvas") or {}).get("ratio", "9:16")
    play_res_x, play_res_y = _ASS_PLAYRES.get(ratio, (1080, 1920))
    ass_path = str(Path(output_path).with_suffix(".captions.ass"))
    generate_karaoke_captions(
        segments=[seg],
        output_ass_path=ass_path,
        primary_color=style.get("primary_color"),
        highlight_color=style.get("highlight_color"),
        font_size=style.get("font_size"),
        chunk_size=int(style.get("chunk_size") or 4),
        position=style.get("position"),
        play_res_x=play_res_x,
        play_res_y=play_res_y,
    )
    return ass_path


@router.get("/editor/exports")
def editor_export_jobs():
    with _EDITOR_LOCK:
        return {"jobs": [{"job_id": key, **value} for key, value in _EDITOR_JOBS.items()]}


@router.get("/editor/export/{job_id}")
def editor_export_status(job_id: str):
    with _EDITOR_LOCK:
        job = _EDITOR_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Unknown editor export job")
        return {"job_id": job_id, **job}


@router.post("/editor/export/{job_id}/cancel")
def editor_export_cancel(job_id: str):
    with _EDITOR_LOCK:
        job = _EDITOR_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Unknown editor export job")
        if job.get("done"):
            return {"job_id": job_id, "ok": False, "error": "already finished"}
        job["state"] = "cancelling"
    proc.request_cancel(f"editor:{job_id}")  # hard-kills the active ffmpeg child (see proc.py)
    return {"job_id": job_id, "ok": True}


class TimelineMediaRequest(BaseModel):
    path: str
    start: float = Field(default=0, ge=0, allow_inf_nan=False)
    duration: float = Field(gt=0, le=600, allow_inf_nan=False)


@router.post("/editor/timeline-media")
def editor_timeline_media(req: TimelineMediaRequest):
    if not Path(req.path).is_file():
        raise HTTPException(status_code=400, detail="The timeline source video could not be found.")
    from server.core.timeline_media import timeline_media
    try:
        return timeline_media(req.path, req.start, req.duration)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Timeline preview unavailable. You can still edit and export: {exc}")
