"""
Cancellable subprocess execution for the job pipeline.

Background
----------
Before this module, job cancellation was cooperative only: the server set a
``threading.Event`` that was checked at a couple of progress callbacks. Nothing
ever killed a subprocess, so once FFmpeg (or any long ffmpeg render) was running
the job could not be interrupted — it ran to completion and only *then* noticed
the cancel flag. On the project's ~530s source videos that meant "Cancel" did
effectively nothing for minutes at a time.

What this provides
------------------
A tiny ``subprocess.run`` work-alike, :func:`run`, that keeps a handle on every
child process it spawns for the *current* job. :func:`request_cancel` hard-kills
those children immediately, and :func:`run` then surfaces that as
:class:`CancelledError` rather than a generic non-zero-exit failure, so the
pipeline unwinds as a clean cancellation.

Only one job runs at a time (the server drains jobs serially through a single
worker), so a process-wide registry is sufficient — no per-job keying needed.
Setup/dependency and Ollama-pull subprocesses deliberately keep using
``subprocess.run`` directly; they are not job work and must not be killed by a
job cancel.
"""

import os
import subprocess
import threading


class CancelledError(BaseException):
    """Raised when a running job is force-cancelled.

    Deliberately subclasses ``BaseException`` (like ``KeyboardInterrupt``) rather
    than ``Exception`` so it cuts straight through the pipeline's broad
    ``except Exception: pass`` guards around optional stages (bleep, silence,
    thumbnail) instead of being silently swallowed and letting the job carry on.
    """


_LOCK = threading.Lock()
_PROCS = set()      # live Popen handles belonging to the current job
_CANCEL = False     # set by request_cancel(), reset by begin_job()/end_job()


def begin_job() -> None:
    """Reset cancel state at the start of a job. Any stale flag or handle from a
    previous job is cleared so a fresh job never starts already-cancelled."""
    global _CANCEL
    with _LOCK:
        _CANCEL = False
        _PROCS.clear()


def end_job() -> None:
    """Clear cancel state when a job finishes (success, failure or cancel)."""
    global _CANCEL
    with _LOCK:
        _CANCEL = False
        _PROCS.clear()


def cancelled() -> bool:
    with _LOCK:
        return _CANCEL


def raise_if_cancelled() -> None:
    """Cooperative checkpoint for callers to invoke between stages."""
    if cancelled():
        raise CancelledError()


def request_cancel() -> None:
    """Flag the current job cancelled and hard-kill any subprocess it is running.

    Safe to call from another thread (the HTTP handler) while the worker thread
    is blocked inside ``communicate()`` — killing the child makes that call
    return promptly and :func:`run` then raises :class:`CancelledError`.
    """
    global _CANCEL
    with _LOCK:
        _CANCEL = True
        procs = list(_PROCS)
    for p in procs:
        try:
            p.kill()
        except Exception:
            # Already exited, or platform refused — nothing more to do.
            pass


def run(cmd, **kwargs):
    """A killable stand-in for ``subprocess.run`` used across the video pipeline.

    Supports the kwargs the pipeline actually uses (``stdout``, ``stderr``,
    ``text``, ``cwd``); on Windows it also hides the child console window by
    default. Returns a ``subprocess.CompletedProcess`` so existing callers can
    keep reading ``.returncode`` / ``.stdout`` / ``.stderr`` unchanged.

    Raises :class:`CancelledError` if cancellation was requested before the
    process started or while it was running (i.e. the process was killed).
    """
    # Don't even launch new work once cancellation has been requested.
    if cancelled():
        raise CancelledError()

    if os.name == "nt":
        kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)

    proc = subprocess.Popen(cmd, **kwargs)
    with _LOCK:
        _PROCS.add(proc)
    try:
        stdout, stderr = proc.communicate()
    finally:
        with _LOCK:
            _PROCS.discard(proc)

    # A kill triggered by cancellation must surface as cancellation, not as a
    # generic ffmpeg error the caller would report as a failure.
    if cancelled():
        raise CancelledError()

    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
