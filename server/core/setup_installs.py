"""Serialized, observable dependency installs. Never unload live Torch libraries."""
import copy
import json
import logging
import os
import subprocess
import sys
import threading
import uuid

_LOCK = threading.RLock()
_JOBS = {}
_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def snapshot():
    with _LOCK:
        return copy.deepcopy(list(_JOBS.values()))


def _update(job, **values):
    with _LOCK:
        job.update(values)


def _log(job, text):
    logging.getLogger("klipzy.install").info("[%s] %s", job["component"], text.rstrip())
    with _LOCK:
        job["output"] = (job["output"] + text)[-24000:]


def _check(component):
    # A fresh interpreter sees newly installed packages; the server may have an
    # older Torch DLL loaded until restart. Also validates before downloading.
    if component == "gpu":
        code = """
import json, importlib.util
from server.core.system_check import _pytorch_accel_plan
import torch
label = _pytorch_accel_plan()['label']
if 'CUDA' in label:
    installed = bool(torch.version.cuda)
elif 'ROCm' in label:
    installed = bool(torch.version.hip)
elif 'Metal' in label:
    installed = torch.backends.mps.is_built()
elif 'DirectML' in label:
    installed = importlib.util.find_spec('torch_directml') is not None
elif 'XPU' in label:
    installed = importlib.util.find_spec('intel_extension_for_pytorch') is not None
else:
    installed = True
print(json.dumps(installed))
"""
    elif component == "diarization":
        code = "import pyannote.audio; print('true')"
    else:
        code = "from server.core.system_check import component_installed; import json; print(json.dumps(component_installed(" + repr(component) + ")))"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            timeout=120, creationflags=_FLAGS)
    if result.returncode:
        return False
    return result.stdout.strip().splitlines()[-1:] == ["true"]


def start(component):
    from server.core import system_check as sc
    if component not in {*sc.get_install_commands(), "gpu", "diarization"}:
        raise ValueError("Unknown component")
    with _LOCK:
        for previous in _JOBS.values():
            if previous["state"] not in ("complete", "failed"):
                if previous["component"] == component:
                    return copy.deepcopy(previous)
                raise RuntimeError("Another dependency install is running. Wait for it to finish.")
            if previous["state"] == "complete" and previous["restart_required"]:
                if previous["component"] == component or {component, previous["component"]} <= {"gpu", "pytorch"}:
                    return copy.deepcopy(previous)
        job = dict(id=uuid.uuid4().hex, component=component, state="checking", output="",
                   command="", restart_required=False, ok=False)
        _JOBS[job["id"]] = job
        threading.Thread(target=_worker, args=(job,), daemon=True).start()
        return copy.deepcopy(job)


def _worker(job):
    from server.core import system_check as sc
    component = job["component"]
    try:
        _log(job, "Checking the selected Python environment before installing...\n")
        if _check(component):
            _log(job, "Already installed and verified. No download needed.\n")
            _update(job, state="complete", ok=True, already_installed=True, restart_required=component in ("gpu", "pytorch"))
            return
        command = (sc._pytorch_accel_plan()["command"] if component == "gpu" else
                   [sys.executable, "-m", "pip", "install", "pyannote.audio"] if component == "diarization" else
                   sc.get_install_commands()[component])
        _update(job, state="installing", command=subprocess.list2cmdline(command))
        _log(job, "Running: " + job["command"] + "\n")
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PIP_PROGRESS_BAR": "off", "PIP_NO_INPUT": "1"}
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, errors="replace", env=env, creationflags=_FLAGS) as process:
            expired = threading.Event()
            def timeout():
                expired.set()
                process.kill()
            timer = threading.Timer(2400, timeout)
            timer.start()
            try:
                for line in process.stdout:
                    _log(job, line)
                code = process.wait()
            finally:
                timer.cancel()
            if expired.is_set():
                raise RuntimeError("Install timed out after 40 minutes")
            if code:
                raise RuntimeError("Installer exited with code " + str(code) + ". See output below.")
        _update(job, state="verifying")
        _log(job, "Verifying in a fresh Python process...\n")
        if not _check(component):
            raise RuntimeError("Installer finished, but verification failed. Review the output; acceleration may also require a working GPU driver.")
        _update(job, state="complete", ok=True, restart_required=True)
        _log(job, "Verified. Restart Klipzy to load the updated dependencies.\n")
    except Exception as error:
        _log(job, str(error) + "\n")
        _update(job, state="failed", error=str(error))
