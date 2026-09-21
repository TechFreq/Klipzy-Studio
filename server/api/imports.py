"""Authenticated local direct-URL import jobs; media stays on this machine."""
import logging
import threading
import time
import uuid
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from server.core import url_import, proc

router = APIRouter(prefix="/imports", tags=["imports"])
ROOT = Path(__file__).resolve().parents[2] / "output" / "imports"
_LOCK = threading.RLock()
_JOBS = {}
_ACTIVE = False
_LOG = logging.getLogger("klipzy.imports")

class ImportRequest(BaseModel):
    url: str = Field(min_length=1, max_length=8192, repr=False)
    rights_confirmed: bool = False
    policy_version: str = ""
    project_id: str = Field(default="", max_length=150)

def snapshot(job):
    return {k: v for k, v in job.items() if k not in ("url", "cancel")}

@router.get("")
def list_imports():
    with _LOCK:
        return [snapshot(j) for j in _JOBS.values()]

@router.post("")
def start_import(req: ImportRequest):
    if not req.rights_confirmed or req.policy_version != url_import.POLICY_VERSION:
        raise HTTPException(400, "Confirm your download/edit permissions and the current import notice before importing.")
    try:
        _, host = url_import.parse_url(req.url.strip())
    except url_import.ImportFailure as exc:
        raise HTTPException(400, str(exc)) from None
    global _ACTIVE
    with _LOCK:
        if sum(j["status"] in ("queued", "downloading", "verifying") for j in _JOBS.values()) >= 5:
            raise HTTPException(409, "The import queue is full. Wait for an import to finish.")
        for key in list(_JOBS):
            if len(_JOBS) < 30:
                break
            if _JOBS[key]["status"] in ("ready", "failed", "cancelled"):
                del _JOBS[key]
        job_id = uuid.uuid4().hex
        job = dict(id=job_id, status="queued", host=host, bytes=0, total=None,
                   speed=0, path=None, error=None, project_id=req.project_id,
                   policy_version=url_import.POLICY_VERSION, accepted_at=time.time(),
                   url=req.url.strip(), cancel=threading.Event())
        _JOBS[job_id] = job
        if not _ACTIVE:
            _ACTIVE = True
            threading.Thread(target=_drain, name="media-import", daemon=True).start()
        return snapshot(job)

@router.post("/{job_id}/cancel")
def cancel_import(job_id: str):
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "Import not found; it may belong to a previous app session.")
        if job["status"] in ("queued", "downloading", "verifying"):
            job["cancel"].set()
            proc.request_cancel("import:" + job_id)
        return snapshot(job)

def _drain():
    global _ACTIVE
    while True:
        with _LOCK:
            job = next((j for j in _JOBS.values() if j["status"] == "queued"), None)
            if job is None:
                _ACTIVE = False
                return
            job["status"] = "downloading"
        key = job["id"]
        proc.begin_job("import:" + key)
        _LOG.info("Import %s started from %s (policy %s)", key, job["host"], job["policy_version"])
        def progress(status, received, total, speed):
            with _LOCK:
                job.update(status=status, bytes=received, total=total, speed=speed)
        try:
            path = url_import.download(job["url"], ROOT / key, job["cancel"], progress)
            with _LOCK:
                job.update(status="ready", path=path, speed=0)
            _LOG.info("Import %s ready: %s bytes", key, job["bytes"])
        except (url_import.ImportCancelled, proc.CancelledError):
            with _LOCK:
                job.update(status="cancelled", speed=0)
            _LOG.info("Import %s cancelled", key)
        except Exception as exc:
            # Transport exceptions can embed signed URLs. Only display known safe messages.
            message = str(exc) if isinstance(exc, url_import.ImportFailure) else "Download or verification failed. Check the connection, free space and FFmpeg installation; retry with a fresh link."
            with _LOCK:
                job.update(status="failed", error=message, speed=0)
            _LOG.warning("Import %s failed (%s): %s", key, type(exc).__name__, message)
        finally:
            with _LOCK:
                job.pop("url", None)
            proc.end_job()
