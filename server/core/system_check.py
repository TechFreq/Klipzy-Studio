"""
System & dependency check for the Klipzy Studio desktop app.

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


def ffmpeg_has_subtitles() -> bool:
    """Does the ffmpeg on PATH actually support burning subtitles?

    A bare 'ffmpeg exists' check is not enough: some builds (e.g. the modular
    homebrew-ffmpeg tap, or minimal static builds) ship WITHOUT libass, so the
    'subtitles'/'ass' filters are missing. Caption burning then fails at render
    time with a confusing 'No option name' / 'No such filter: subtitles' error.
    We probe the actual filter list so the Setup panel can flag it up front.
    """
    if shutil.which("ffmpeg") is None:
        return False
    out = _run(["ffmpeg", "-hide_banner", "-filters"], timeout=8) or ""
    # Filter rows look like: " T.. subtitles         V->V       Render text ..."
    return any(
        f" {name} " in out or f" {name}\t" in out
        for name in ("subtitles", "ass")
    )


def detect_ffmpeg() -> Dict[str, bool]:
    """Detect ffmpeg/ffprobe presence AND subtitle-burning capability.

    'subtitles' is True only when the installed ffmpeg was built with libass.
    Without it the app can cut/crop clips but cannot burn captions.
    """
    has_ffmpeg = shutil.which("ffmpeg") is not None
    return {
        "ffmpeg": has_ffmpeg,
        "ffprobe": shutil.which("ffprobe") is not None,
        "subtitles": ffmpeg_has_subtitles() if has_ffmpeg else False,
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
    # 4) AMD / Intel GPUs (no CUDA): name them so the UI + recommender are
    #    accurate. These don't accelerate PyTorch on Windows, but the FFmpeg
    #    encoder (AMD AMF / Intel QSV) still speeds up rendering.
    name = _detect_gpu_name_fallback()
    if name:
        return {"name": name, "vram_gb": None}
    return {"name": None, "vram_gb": None}


def _detect_gpu_name_fallback() -> Optional[str]:
    """Best-effort GPU name for non-NVIDIA cards (AMD Radeon / Intel Arc/UHD)."""
    system = platform.system()
    try:
        if system == "Windows":
            out = _run([
                "powershell", "-NoProfile", "-Command",
                "(Get-CimInstance Win32_VideoController).Name",
            ], timeout=6)
        elif system == "Linux":
            out = _run(["bash", "-lc", "lspci | grep -Ei 'vga|3d|display'"], timeout=6)
        else:
            out = None
    except Exception:
        out = None
    if not out:
        return None
    for line in out.splitlines():
        low = line.lower()
        if any(k in low for k in ("radeon", "amd", "rx ", "vega", "arc", "intel", "uhd", "iris", "nvidia", "geforce", "rtx", "gtx")):
            return line.strip()
    return out.splitlines()[0].strip() or None


def detect_cpu() -> Dict[str, str]:
    cores = os.cpu_count() or 0
    try:
        import psutil
        ram_gb = round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except ImportError:
        ram_gb = None
    return {"cores": cores, "ram_gb": ram_gb, "name": _detect_cpu_name()}


def _detect_cpu_name() -> Optional[str]:
    """Human-readable CPU brand (e.g. 'AMD Ryzen 7 5800X', 'Intel Core i7')."""
    system = platform.system()
    try:
        if system == "Windows":
            out = _run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"], timeout=6)
            if out:
                return out.splitlines()[0].strip()
        elif system == "Darwin":
            out = _run(["sysctl", "-n", "machdep.cpu.brand_string"], timeout=6)
            if out:
                return out.strip()
        elif system == "Linux":
            out = _run(["bash", "-lc", "grep -m1 'model name' /proc/cpuinfo | cut -d: -f2"], timeout=6)
            if out:
                return out.strip()
    except Exception:
        pass
    return platform.processor() or None


def detect_torch() -> Dict[str, bool]:
    try:
        import torch
        cuda_ok = torch.cuda.is_available() or bool(getattr(torch.version, "cuda", None))
        return {
            "installed": True,
            "cuda": cuda_ok,
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
        # IMPORTANT: homebrew-core's plain "ffmpeg" is now a slim build WITHOUT
        # libass, so it CANNOT burn subtitles. "ffmpeg-full" bundles libass.
        # If the slim ffmpeg is already installed it conflicts with ffmpeg-full
        # (both provide the `ffmpeg` binary), so remove it first. We route this
        # through `bash -lc` because:
        #   1) the install runs as a plain arg list (no shell), and we need two
        #      steps (uninstall-then-install), and
        #   2) a login shell picks up Homebrew's PATH (/opt/homebrew/bin) even
        #      when the app is launched from Finder rather than a terminal.
        # `;` (not `&&`) lets the install proceed when ffmpeg wasn't installed.
        commands["ffmpeg"] = [
            "bash", "-lc",
            "brew uninstall ffmpeg 2>/dev/null; brew install ffmpeg-full",
        ]
        commands["ollama"] = ["brew", "install", "--cask", "ollama"]
    else:
        commands["ffmpeg"] = ["sudo", "apt", "install", "-y", "ffmpeg"]
        commands["ollama"] = ["curl", "-fsSL", "https://ollama.com/install.sh", "|", "sh"]

    # CRITICAL: use THIS interpreter's pip (sys.executable -m pip), not a bare
    # "pip" off PATH. The server runs inside the app's venv; a bare "pip" can
    # resolve to a different Python (e.g. the Windows Store Python), which
    # installs packages the running app can never import — so a dependency would
    # look "installed successfully" yet still show as missing.
    pip = [sys.executable, "-m", "pip"]

    # PyTorch: install the accelerated build for this machine so Whisper & YOLO use the GPU.
    # CUDA 12.6 wheels cover NVIDIA GPUs (RTX 3060 etc) — falls back to CPU for non-NVIDIA.
    if os_name == "windows":
        commands["pytorch"] = pip + ["install", "--index-url", "https://download.pytorch.org/whl/cu126", "torch", "torchvision"]
    elif os_name == "macos":
        commands["pytorch"] = pip + ["install", "torch", "torchvision"]
    else:
        commands["pytorch"] = pip + ["install", "--index-url", "https://download.pytorch.org/whl/cu126", "torch", "torchvision"]
    commands["whisper"] = pip + ["install", "openai-whisper"]
    # faster-whisper (CTranslate2) is cross-platform and 3-5x faster than
    # openai-whisper; the transcriber prefers it when present.
    commands["faster-whisper"] = pip + ["install", "faster-whisper"]
    # mlx-whisper is Apple-Silicon-only native acceleration; offer it just on M-series.
    if os_name == "macos" and platform.machine() == "arm64":
        commands["mlx-whisper"] = pip + ["install", "mlx-whisper"]
    commands["librosa"] = pip + ["install", "librosa", "soundfile"]
    commands["ultralytics"] = pip + ["install", "ultralytics"]
    return commands


def get_uninstall_commands() -> Dict[str, List[str]]:
    """Uninstall commands for the pip-managed Python packages only.

    Deliberately excludes ffmpeg / ollama / lmstudio: those are system or GUI
    apps the user installs via their OS package manager, and auto-removing them
    (winget/brew/apt) is riskier and out of scope. These pip removals are safe
    and reversible via the matching install command.
    """
    # Same interpreter-pip rule as installs: target the venv the server runs in.
    pip = [sys.executable, "-m", "pip"]
    return {
        "whisper": pip + ["uninstall", "-y", "openai-whisper"],
        "faster-whisper": pip + ["uninstall", "-y", "faster-whisper"],
        "mlx-whisper": pip + ["uninstall", "-y", "mlx-whisper"],
        "ultralytics": pip + ["uninstall", "-y", "ultralytics"],
        "librosa": pip + ["uninstall", "-y", "librosa", "soundfile"],
        "pytorch": pip + ["uninstall", "-y", "torch", "torchvision"],
    }


def _spec_installed(module: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(module) is not None


def component_installed(key: str) -> bool:
    """Is a given install-command component already present?"""
    if key == "ffmpeg":
        # Require BOTH the binary AND libass (subtitle) support. A build without
        # libass can't burn captions, so treat it as "not fully installed" and
        # let the Setup panel offer to install a capable ffmpeg.
        ff = detect_ffmpeg()
        return ff.get("ffmpeg", False) and ff.get("subtitles", False)
    if key == "ollama":
        return detect_ollama().get("installed", False)
    if key == "pytorch":
        return detect_torch().get("installed", False)
    if key == "whisper":
        return detect_whisper().get("installed", False)
    if key == "ultralytics":
        return detect_ultralytics().get("installed", False)
    if key == "faster-whisper":
        return _spec_installed("faster_whisper")
    if key == "mlx-whisper":
        return _spec_installed("mlx_whisper")
    if key == "librosa":
        return _spec_installed("librosa")
    return False


def missing_components() -> List[str]:
    """Install-command keys for this OS that are not yet installed."""
    return [k for k in get_install_commands() if not component_installed(k)]


# ----------------------------------------------------------------------
# Curated Ollama model catalog (Clips-Kitty-style download menu)
# ----------------------------------------------------------------------
# Small-to-large local LLMs the app knows how to recommend + one-click pull.
# size_gb is the approximate download size; min_ram_gb is a comfortable floor.
OLLAMA_MODEL_CATALOG = [
    {"name": "gemma2:2b",   "label": "Gemma 2 · 2B",   "params": "2B",   "size_gb": 1.6, "min_ram_gb": 8,  "tier": "Light",
     "note": "Fast and light — great default for hooks & chat on most machines."},
    {"name": "llama3.2:3b", "label": "Llama 3.2 · 3B", "params": "3B",   "size_gb": 2.0, "min_ram_gb": 8,  "tier": "Light",
     "note": "Balanced quality and speed; solid all-rounder."},
    {"name": "qwen2.5:3b",  "label": "Qwen2.5 · 3B",   "params": "3B",   "size_gb": 1.9, "min_ram_gb": 8,  "tier": "Light",
     "note": "Strong small model for punchy copywriting."},
    {"name": "phi3:mini",   "label": "Phi-3 Mini",     "params": "3.8B", "size_gb": 2.2, "min_ram_gb": 8,  "tier": "Light",
     "note": "Compact but capable; good on modest hardware."},
    {"name": "gemma2:9b",   "label": "Gemma 2 · 9B",   "params": "9B",   "size_gb": 5.4, "min_ram_gb": 16, "tier": "Quality",
     "note": "Noticeably sharper hooks. Wants 16GB+ RAM or a decent GPU."},
    {"name": "llama3.1:8b", "label": "Llama 3.1 · 8B", "params": "8B",   "size_gb": 4.7, "min_ram_gb": 16, "tier": "Quality",
     "note": "High-quality writing; 16GB+ recommended."},
    {"name": "qwen2.5:7b",  "label": "Qwen2.5 · 7B",   "params": "7B",   "size_gb": 4.7, "min_ram_gb": 16, "tier": "Quality",
     "note": "Excellent for short-form copy; 16GB+."},
    {"name": "mistral:7b",  "label": "Mistral · 7B",   "params": "7B",   "size_gb": 4.1, "min_ram_gb": 16, "min_vram_gb": 6,  "tier": "Quality",
     "note": "Reliable 7B all-rounder."},
    # --- Larger models: great on a strong GPU (>=12GB VRAM) or 32GB+ RAM ---
    {"name": "qwen2.5:14b", "label": "Qwen2.5 · 14B",  "params": "14B",  "size_gb": 9.0,  "min_ram_gb": 16, "min_vram_gb": 12, "tier": "Pro",
     "note": "Sweet spot for sharp hooks/titles; fits a 12GB GPU (RTX 3060) nicely."},
    {"name": "gemma2:27b",  "label": "Gemma 2 · 27B",  "params": "27B",  "size_gb": 16.0, "min_ram_gb": 32, "min_vram_gb": 20, "tier": "Pro",
     "note": "Excellent quality. Splits across a 12GB GPU + system RAM; wants 32GB+ RAM."},
    {"name": "qwen2.5:32b", "label": "Qwen2.5 · 32B",  "params": "32B",  "size_gb": 20.0, "min_ram_gb": 32, "min_vram_gb": 24, "tier": "Pro",
     "note": "Top-tier copywriting; runs on 32GB+ RAM (partly on CPU below 24GB VRAM)."},
    {"name": "mixtral:8x7b","label": "Mixtral · 8x7B",  "params": "8x7B MoE", "size_gb": 26.0, "min_ram_gb": 32, "min_vram_gb": 24, "tier": "Pro",
     "note": "Fast mixture-of-experts; needs 32GB+ RAM."},
    {"name": "llama3.1:70b","label": "Llama 3.1 · 70B", "params": "70B",  "size_gb": 40.0, "min_ram_gb": 64, "min_vram_gb": 48, "tier": "Max",
     "note": "Best quality here, but heavy: needs 64GB RAM and runs slowly without a big GPU."},
]


def recommend_ollama_model() -> str:
    """Best default Ollama model for this machine, by GPU VRAM + system RAM.

    Bigger local models write noticeably better hooks/titles but need memory.
    A ~14B is the sweet spot on a 12GB GPU (e.g. RTX 3060) or 48GB+ RAM; step
    down for lighter machines so it still runs comfortably.
    """
    gpu = detect_gpu()
    cpu = detect_cpu()
    vram = gpu.get("vram_gb") or 0
    ram = cpu.get("ram_gb") or 0
    if vram >= 12 or ram >= 48:
        return "qwen2.5:14b"
    if (vram and vram >= 8) or ram >= 32:
        return "gemma2:9b"
    if ram >= 16:
        return "llama3.2:3b"
    return "gemma2:2b"


def ollama_model_catalog() -> Dict:
    """Catalog + which models are installed + the hardware-recommended pick.

    Recommends by hardware; the caller may further nudge by preset. Marks each
    model installed/recommended and whether the machine likely has enough RAM.
    """
    recommended = recommend_ollama_model()
    installed = list_ollama_models()
    installed_set = set(installed) | {m.split(":")[0] for m in installed}
    ram = detect_cpu().get("ram_gb") or 0
    vram = detect_gpu().get("vram_gb") or 0
    models = []
    for m in OLLAMA_MODEL_CATALOG:
        models.append({
            **m,
            "installed": (m["name"] in installed) or (m["name"] in installed_set),
            "recommended": m["name"] == recommended,
            "fits_ram": (not ram) or (ram >= m["min_ram_gb"]),
            # Fully GPU-accelerated when there's enough VRAM; otherwise it still
            # runs by splitting onto CPU/RAM (slower), which the note explains.
            "fits_vram": bool(vram) and vram >= m.get("min_vram_gb", 0),
        })
    return {"models": models, "recommended": recommended, "installed": installed,
            "ram_gb": ram, "vram_gb": vram}


def list_ollama_models() -> List[str]:
    """Names of locally-installed Ollama models (via `ollama list`), best-effort."""
    exe = shutil.which("ollama")
    if not exe:
        info = detect_ollama()
        exe = info.get("executable") or "ollama"
    out = _run([exe, "list"], timeout=6)
    if not out:
        return []
    models = []
    for line in out.splitlines()[1:]:  # skip the header row
        parts = line.split()
        if parts and parts[0] and parts[0].upper() != "NAME":
            models.append(parts[0])
    return models
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

    # A GPU showing up in nvidia-smi does NOT mean the ML stack can use it: the
    # user may have CPU-only PyTorch, or (for CTranslate2) be missing the CUDA
    # runtime DLLs. Recommend based on what will ACTUALLY run, and if a card is
    # present but dormant, tell the user how to unlock it instead of promising
    # GPU speed they won't get.
    cuda_usable = bool(torch_info.get("cuda"))
    mps_usable = bool(torch_info.get("mps"))
    gpu_name = gpu.get("name") or ""
    gpu_present = bool(vram) or bool(gpu_name)
    dormant_gpu = gpu_present and not cuda_usable and not mps_usable
    _is_nvidia = any(k in gpu_name.lower() for k in ("nvidia", "geforce", "rtx", "gtx", "quadro", "tesla"))
    if _is_nvidia:
        unlock_note = f"{gpu_name} detected but PyTorch can't use it yet — install the CUDA build to unlock GPU speed"
    elif gpu_name:
        # AMD/Intel GPUs don't accelerate PyTorch on Windows, but FFmpeg still
        # uses them (AMF/QSV) to speed up rendering. Be honest, don't push CUDA.
        unlock_note = f"{gpu_name}: used for fast video encoding; transcription/AI run on CPU (faster-whisper recommended)"
    else:
        unlock_note = "No usable GPU for AI — running on CPU (install faster-whisper for a 3-5x speedup)"

    # --- Whisper ---
    if cuda_usable and vram and vram >= 8:
        whisper = {"model": "medium", "realtime_factor": "~4-8x", "note": "Great accuracy/speed balance on your GPU"}
    elif cuda_usable and vram and vram >= 4:
        whisper = {"model": "small", "realtime_factor": "~3-6x", "note": "Good accuracy, fits your VRAM comfortably"}
    elif mps_usable:
        whisper = {"model": "base", "realtime_factor": "~2-4x", "note": "Apple Silicon — install mlx-whisper for native MLX acceleration"}
    elif dormant_gpu:
        whisper = {"model": "base", "realtime_factor": "~1-3x", "note": unlock_note}
    elif ram >= 16:
        whisper = {"model": "base", "realtime_factor": "~1-3x", "note": "CPU-only: base keeps transcription fast (install faster-whisper for a 3-5x speedup)"}
    else:
        whisper = {"model": "tiny", "realtime_factor": "~1-2x", "note": "Low-RAM CPU: tiny is fastest"}

    # --- YOLO ---
    if cuda_usable and vram and vram >= 6:
        yolo = {"model": "yolov8m.pt", "realtime_factor": "~30-60fps", "note": "Good accuracy for face tracking"}
    elif cuda_usable and vram and vram >= 2:
        yolo = {"model": "yolov8n.pt", "realtime_factor": "~60-120fps", "note": "Lightweight — perfect for tracking"}
    elif dormant_gpu:
        yolo = {"model": "yolov8n.pt", "realtime_factor": "~10-30fps", "note": unlock_note}
    else:
        yolo = {"model": "yolov8n.pt", "realtime_factor": "~10-30fps", "note": "CPU: nano model only"}

    # --- Ollama --- (optional; used only for AI chat + LLM highlight picks)
    if cuda_usable and vram and vram >= 8:
        ollama = {"model": "llama3.2", "note": "3B — fast, smart, fits your GPU"}
    elif ram >= 16:
        ollama = {"model": "gemma2:2b", "note": "2B — runs comfortably on CPU/RAM"}
    else:
        ollama = {"model": "gemma2:2b", "note": "2B — smallest reliable option"}

    whisper["engine"] = "GPU (CUDA)" if cuda_usable else ("GPU (MPS)" if mps_usable else ("CPU (GPU idle)" if dormant_gpu else "CPU"))
    yolo["engine"] = whisper["engine"]
    ollama["engine"] = "GPU (CUDA)" if (cuda_usable and vram and vram >= 8) else "CPU"

    # Keep the recommendation cards actionable: the first model is the best
    # fit, followed by lighter fallbacks that are still compatible.
    whisper_choices = [
        {"model": whisper["model"], "tier": "Best fit", "note": whisper["note"]},
        {"model": "small", "tier": "Balanced", "note": "Lower VRAM/RAM use with very good accuracy"},
        {"model": "base", "tier": "Light", "note": "Fast and dependable on most machines"},
        {"model": "tiny", "tier": "Fastest", "note": "Smallest memory footprint"},
    ]
    # Remove duplicates while preserving the hardware-ranked order.
    seen = set()
    whisper_choices = [c for c in whisper_choices if not (c["model"] in seen or seen.add(c["model"]))]
    ollama_choices = [
        {"model": ollama["model"], "tier": "Best fit", "note": ollama["note"]},
        {"model": "gemma2:2b", "tier": "Light", "note": "Small and responsive for clip suggestions and chat"},
        {"model": "llama3.2:3b", "tier": "Quality", "note": "Stronger answers; uses more memory"},
    ]
    seen = set()
    ollama_choices = [c for c in ollama_choices if not (c["model"] in seen or seen.add(c["model"]))]
    whisper["choices"] = whisper_choices
    ollama["choices"] = ollama_choices

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