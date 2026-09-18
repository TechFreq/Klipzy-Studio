"""Cancellable subprocesses scoped to a processing job or editor export.

Worker threads bind an explicit key with begin_job; HTTP cancellation names
that key. Unscoped utility calls retain a separate default context.
"""
import os
import subprocess
import threading


class CancelledError(BaseException):
    """Bypass optional-stage exception handlers when a job is cancelled."""


_LOCK = threading.RLock()
_LOCAL = threading.local()
_JOBS = {}
_DEFAULT = "__default__"


def _key(job_id=None):
    return job_id if job_id is not None else getattr(_LOCAL, "job_id", _DEFAULT)


def _state(key):
    return _JOBS.setdefault(key, {"cancelled": False, "processes": set()})


def begin_job(job_id=None):
    key = job_id if job_id is not None else _DEFAULT
    _LOCAL.job_id = key
    with _LOCK:
        if job_id is None:
            _JOBS[key] = {"cancelled": False, "processes": set()}
        else:
            _state(key)  # Preserve a cancellation that arrived before worker startup.


def end_job():
    with _LOCK:
        _JOBS.pop(_key(), None)
    _LOCAL.job_id = _DEFAULT


def cancelled():
    with _LOCK:
        return _state(_key())["cancelled"]


def raise_if_cancelled():
    if cancelled():
        raise CancelledError()


def request_cancel(job_id=None):
    with _LOCK:
        state = _state(_key(job_id))
        state["cancelled"] = True
        processes = list(state["processes"])
    for child in processes:
        try:
            child.kill()
        except OSError:
            pass


def run(cmd, **kwargs):
    import time
    from server.core import processing_trace as trace
    started = time.monotonic()
    if trace.active():
        trace.event("subprocess.start", command=cmd, cwd=kwargs.get("cwd"), timeout=kwargs.get("timeout"))
    timeout = kwargs.pop("timeout", None)
    check = kwargs.pop("check", False)
    input_data = kwargs.pop("input", None)
    if kwargs.pop("capture_output", False):
        if "stdout" in kwargs or "stderr" in kwargs:
            raise ValueError("capture_output cannot be combined with stdout/stderr")
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if input_data is not None:
        if "stdin" in kwargs:
            raise ValueError("input cannot be combined with stdin")
        kwargs["stdin"] = subprocess.PIPE
    if os.name == "nt":
        kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
    with _LOCK:
        state = _state(_key())
        if state["cancelled"]:
            raise CancelledError()
        # Register atomically with respect to cancellation: no launch/kill race.
        child = subprocess.Popen(cmd, **kwargs)
        state["processes"].add(child)
    try:
        try:
            stdout, stderr = child.communicate(input=input_data, timeout=timeout)
        except subprocess.TimeoutExpired:
            if trace.active():
                trace.event("subprocess.timeout", command=cmd, timeout=timeout)
            child.kill()
            stdout, stderr = child.communicate()
            raise subprocess.TimeoutExpired(cmd, timeout, output=stdout, stderr=stderr)
        except BaseException:
            child.kill()
            child.wait()
            raise
    finally:
        with _LOCK:
            state["processes"].discard(child)
    if trace.active():
        def describe_output(value):
            if isinstance(value, bytes):
                return {"bytes": len(value), "note": "Binary output omitted"}
            return value
        trace.event("subprocess.finished", command=cmd, returncode=child.returncode,
                    seconds=round(time.monotonic()-started,3), cancelled=state["cancelled"],
                    stdout=describe_output(stdout), stderr=describe_output(stderr))
    if state["cancelled"]:
        raise CancelledError()
    if check and child.returncode:
        raise subprocess.CalledProcessError(child.returncode, cmd, stdout, stderr)
    return subprocess.CompletedProcess(cmd, child.returncode, stdout, stderr)
