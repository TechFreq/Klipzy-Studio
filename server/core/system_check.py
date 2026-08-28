"""
System & dependency check for the Clippy Studio desktop app.

Detects what's installed, recommends hardware-appropriate models,
and provides click-to-install commands (winget / Homebrew / pip).
"""

import os
import platform
import shutil
import subprocess
import sys
from typing import Dict, List, Optional


# ----------------------------------------------------------------------
# Detection helpers
# ----------------------------------------------------------------------
def _run(cmd: List[str], timeout: int = 8) -> Optional[str]:
    """Run a command, return trimmed stdout, or None on failure."""
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def detect_os() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    return system


def detect_python() -> Dict[str, str]:
    return {
        "version": platform.python_version(),
        "executable": sys.executable,
        "pip": shutil.which("pip") or shutil.which("pip3") or "pip",
    }


def detect_ffmpeg() -> Dict[str, bool]:
    return {
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "ffprobe": shutil.which("ffprobe") is not None,
    }


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    """Check whether a local service is listening on a port (e.g. Ollama :11434, LM Studio :1234)."""
    try:
        import socket
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _find_first(candidates) -> Optional[str]:
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return None


def detect_ollama() -> Dict[str, bool]:
    installed = shutil.which("ollama") is not None
    exe = shutil.which("ollama")
    if not installed:
        # Ollama often is NOT on PATH — check common install locations.
        if detect_os() == "windows":
            exe = _find_first([
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"),
                "C:\\Program Files\\Ollama\\ollama.exe",
            ])
        elif detect_os() == "macos":
            exe = _find_first([
                "/Applications/Ollama.app/Contents/Resources/ollama",
                os.path.expanduser("~/Applications/Ollama.app/Contents/Resources/ollama"),
            ])
        installed = exe is not None
    running = _port_open(11434)  # Ollama's HTTP API port
    return {"installed": installed, "running": running, "executable": exe}


def detect_lmstudio() -> Dict[str, bool]:
    """LM Studio is a common alternative GUI for local LLMs."""
    exe = shutil.which("lmstudio") or shutil.which("lm-studio") or shutil.which("lmstudiod")
    if not exe:
        if detect_os() == "windows":
            exe = _find_first([
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "LM Studio", "LM Studio.exe"),
                os.path.expanduser(r"~\AppData\Local\Programs\LM Studio\LM Studio.exe"),
                "C:\\Program Files\\LM Studio\\LM Studio.exe",
            ])
        elif detect_os() == "macos":
            exe = _find_first([
                "/Applications/LM Studio.app/Contents/MacOS/LM Studio",
                os.path.expanduser("~/Applications/LM Studio.app/Contents/MacOS/LM Studio"),
            ])
    installed = exe is not None
    running = _port_open(1234)  # LM Studio's OpenAI-compatible API port
    return {"installed": installed, "running": running, "executable": exe}


def detect_whisper() -> Dict[str, bool]:
    try:
        import whisper  # noqa: F401
        return {"installed": True}
    except ImportError:
        return {"installed": False}


def detect_ultralytics() -> Dict[str, bool]:
    try:
        import ultralytics  # noqa: F401
        return {"installed": True}
    except ImportError:
        return {"installed": False}


def detect_gpu() -> Dict[str, str]:
    """Return GPU name + VRAM (GB) where detectable."""
    # 1) nvidia-smi (works on Windows + Linux)
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    if out:
        try:
            name, vram_mb = out.splitlines()[0].split(",")
            return {"name": name.strip(), "vram_gb": round(int(vram_mb.strip()) / 1024, 1)}
        except Exception:
            return {"name": out.splitlines()[0], "vram_gb": None}
    # 2) torch CUDA (works when torch is installed)
    try:
        import torch
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            vram = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
            return {"name": name, "vram_gb": round(vram, 1)}
    except Exception:
        pass
    # 3) macOS Metal
    if platform.system() == "Darwin":
        return {"name": "Apple Silicon (Metal)", "vram_gb": None}
    return {"name": None, "vram_gb": None}


def detect_cpu() -> Dict[str, str]:
    cores = os.cpu_count() or 0
    try:
        import psutil
        ram_gb = round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except ImportError:
        ram_gb = None
    return {"cores": cores, "ram_gb": ram_gb}


def detect_torch() -> Dict[str, bool]:
    try:
        import torch
        return {
            "installed": True,
            "cuda": torch.cuda.is_available(),
            "mps": getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available(),
        }
    except ImportError:
        return {"installed": False, "cuda": False, "mps": False}


def get_install_commands() -> Dict[str, List[str]]:
    """Click-to-install commands for the current OS."""
    os_name = detect_os()
    commands: Dict[str, List[str]] = {}

    if os_name == "windows":
        commands["ffmpeg"] = ["winget", "install", "Gyan.FFmpeg", "--accept-source-agreements", "--accept-package-agreements"]
        commands["ollama"] = ["winget", "install", "Ollama.Ollama", "--accept-source-agreements", "--accept-package-agreements"]
    elif os_name == "macos":
        commands["ffmpeg"] = ["brew", "install", "ffmpeg"]
        commands["ollama"] = ["brew", "install", "--cask", "ollama"]
    else:
        commands["ffmpeg"] = ["sudo", "apt", "install", "-y", "ffmpeg"]
        commands["ollama"] = ["curl", "-fsSL", "https://ollama.com/install.sh", "|", "sh"]

    commands["pytorch"] = ["pip", "install", "torch", "torchvision"]
    commands["whisper"] = ["pip", "install", "openai-whisper"]
    commands["librosa"] = ["pip", "install", "librosa", "soundfile"]
    commands["ultralytics"] = ["pip", "install", "ultralytics"]
    return commands
# ----------------------------------------------------------------------
# Hardware-aware model recommendations
# ----------------------------------------------------------------------
def recommend_models() -> Dict[str, Dict]:
    """Pick Whisper / YOLO / Ollama models based on detected hardware."""
    gpu = detect_gpu()
    vram = gpu.get("vram_gb")
    torch_info = detect_torch()
    cpu = detect_cpu()
    ram = cpu.get("ram_gb") or 0
    cores = cpu.get("cores") or 0

    # --- Whisper ---
    if vram and vram >= 8:
        whisper = {"model": "medium", "realtime_factor": "~4-8x", "note": "Great accuracy/speed balance on your GPU"}
    elif vram and vram >= 4:
        whisper = {"model": "small", "realtime_factor": "~3-6x", "note": "Good accuracy, fits your VRAM comfortably"}
    elif torch_info.get("mps"):
        whisper = {"model": "base", "realtime_factor": "~2-4x", "note": "Apple Silicon MPS — base is the sweet spot"}
    elif ram >= 16:
        whisper = {"model": "base", "realtime_factor": "~1-3x", "note": "CPU-only: base keeps transcription fast"}
    else:
        whisper = {"model": "tiny", "realtime_factor": "~1-2x", "note": "Low-RAM CPU: tiny is fastest"}

    # --- YOLO ---
    if vram and vram >= 6:
        yolo = {"model": "yolov8m.pt", "realtime_factor": "~30-60fps", "note": "Good accuracy for face tracking"}
    elif vram and vram >= 2:
        yolo = {"model": "yolov8n.pt", "realtime_factor": "~60-120fps", "note": "Lightweight — perfect for tracking"}
    else:
        yolo = {"model": "yolov8n.pt", "realtime_factor": "~10-30fps", "note": "CPU: nano model only"}

    # --- Ollama ---
    if vram and vram >= 8:
        ollama = {"model": "llama3.2", "note": "3B — fast, smart, fits your GPU"}
    elif ram >= 16:
        ollama = {"model": "gemma2:2b", "note": "2B — runs comfortably on CPU/RAM"}
    else:
        ollama = {"model": "gemma2:2b", "note": "2B — smallest reliable option"}

    return {
        "whisper": whisper,
        "yolo": yolo,
        "ollama": ollama,
        "hardware": {"gpu": gpu, "cpu": cpu, "torch": torch_info},
    }


def estimate_render_time(duration_seconds: float, layout: str = "vertical") -> Dict[str, str]:
    """Rough render-time estimate for a clip of the given duration."""
    # Heuristic: hardware-dependent realtime factor for ffmpeg x264 encode.
    gpu = detect_gpu()
    torch_info = detect_torch()
    if gpu.get("vram_gb"):
        rtf = 0.35  # ~3x realtime with hardware encode
        engine = "GPU (NVENC/QSV)"
    elif torch_info.get("mps"):
        rtf = 0.5
        engine = "Apple Silicon (VideoToolbox)"
    else:
        rtf = 0.9
        engine = "CPU (x264)"

    if layout == "game_reaction":
        rtf *= 1.6  # PiP overlay adds decode+filter cost

    est = duration_seconds * rtf
    return {
        "engine": engine,
        "estimated_seconds": round(est, 1),
        "estimated_text": f"~{int(est // 60)}m {int(est % 60)}s",
        "realtime_factor": f"{round(1 / rtf, 1)}x",
    }