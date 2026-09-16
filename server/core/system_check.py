"""
System & dependency check for the Klipzy Studio desktop app.

Detects what's installed, recommends hardware-appropriate models,
and provides click-to-install commands (winget / Homebrew / pip).
"""

import os
import platform
import re
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
    #    accurate, and best-effort read their dedicated VRAM so the model
    #    recommendation can scale for AMD/Intel cards too (not just NVIDIA).
    name = _detect_gpu_name_fallback()
    if name:
        return {"name": name, "vram_gb": _detect_nonnvidia_vram_gb()}
    return {"name": None, "vram_gb": None}


def _detect_nonnvidia_vram_gb() -> Optional[float]:
    """Best-effort dedicated VRAM (GB) for AMD/Intel GPUs. NVIDIA is read via
    nvidia-smi/torch above. Returns None when it can't be determined.

    ⚠️ UNTESTED: written without access to an AMD GPU or a Linux machine. The
    Windows registry read and the Linux sysfs read are best-effort and may need
    tweaking on real hardware. Failure is non-fatal — it returns None and the
    recommender falls back to the RAM-based pick.

    Windows: Win32_VideoController.AdapterRAM caps at 4GB for larger cards, so
    read the reliable 64-bit `qwMemorySize` from the display-class registry key.
    Linux: read AMD's sysfs `mem_info_vram_total` (bytes).
    """
    system = platform.system()
    try:
        if system == "Windows":
            ps = (
                "$k=Get-ChildItem 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Class\\"
                "{4d36e968-e325-11ce-bfc1-08002be10318}' -ErrorAction SilentlyContinue;"
                "$m=0;foreach($i in $k){$v=(Get-ItemProperty $i.PSPath -Name "
                "'HardwareInformation.qwMemorySize' -ErrorAction SilentlyContinue)."
                "'HardwareInformation.qwMemorySize';if($v -and $v -gt $m){$m=$v}};$m"
            )
            out = _run(["powershell", "-NoProfile", "-Command", ps], timeout=8)
        elif system == "Linux":
            out = _run([
                "bash", "-lc",
                "cat /sys/class/drm/card*/device/mem_info_vram_total 2>/dev/null | sort -n | tail -1",
            ], timeout=6)
        else:
            out = None
    except Exception:
        out = None
    if not out:
        return None
    digits = re.sub(r"[^0-9]", "", out.splitlines()[0] if out.splitlines() else "")
    if not digits:
        return None
    gb = round(int(digits) / (1024 ** 3), 1)
    # Guard against the bogus 4GB AdapterRAM cap or absurd values.
    return gb if 0.5 <= gb <= 256 else None


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


def live_resources() -> Dict:
    """Live system usage for the persistent resource footer.

    Returns CPU %, RAM used/total/%, GPU utilization %, VRAM used/total, and free
    disk on the app's drive. EVERY field degrades to None on any problem (missing
    psutil, no NVIDIA GPU, etc.) so the footer simply hides what it can't read —
    this must never raise, it's polled every couple of seconds.
    """
    out: Dict = {
        "cpu_percent": None,
        "ram_used_gb": None, "ram_total_gb": None, "ram_percent": None,
        "gpu_name": None, "gpu_percent": None,
        "vram_used_gb": None, "vram_total_gb": None,
        "disk_free_gb": None, "disk_total_gb": None,
    }
    try:
        import psutil
        # interval=None is non-blocking: returns %CPU since the previous call,
        # which is exactly right for a footer that polls on a timer.
        out["cpu_percent"] = round(psutil.cpu_percent(interval=None))
        vm = psutil.virtual_memory()
        out["ram_used_gb"] = round(vm.used / (1024 ** 3), 1)
        out["ram_total_gb"] = round(vm.total / (1024 ** 3), 1)
        out["ram_percent"] = round(vm.percent)
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        du = psutil.disk_usage(root)
        out["disk_free_gb"] = round(du.free / (1024 ** 3), 1)
        out["disk_total_gb"] = round(du.total / (1024 ** 3), 1)
    except Exception:
        pass
    # NVIDIA live GPU util + VRAM. AMD/Intel/Apple have no cheap equivalent, so
    # they just leave the GPU fields as None (the footer omits the GPU chip).
    try:
        smi = _run([
            "nvidia-smi",
            "--query-gpu=utilization.gpu,memory.used,memory.total,name",
            "--format=csv,noheader,nounits",
        ])
        if smi:
            util, used, total, name = smi.splitlines()[0].split(",")
            out["gpu_percent"] = round(float(util.strip()))
            out["vram_used_gb"] = round(float(used.strip()) / 1024, 1)
            out["vram_total_gb"] = round(float(total.strip()) / 1024, 1)
            out["gpu_name"] = name.strip()
    except Exception:
        pass
    return out


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
        cuda_ok = torch.cuda.is_available()
        return {
            "installed": True,
            "cuda": cuda_ok,
            "mps": getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available(),
        }
    except (ImportError, OSError):
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


def _gpu_vendor(name: str, is_apple: bool) -> str:
    """Classify the GPU by vendor from its name: nvidia / amd / intel / apple / none."""
    if is_apple:
        return "apple"
    low = (name or "").lower()
    if any(k in low for k in ("nvidia", "geforce", "rtx", "gtx", "quadro", "tesla")):
        return "nvidia"
    if any(k in low for k in ("radeon", "amd", "rx ", "vega", "instinct", "firepro")):
        return "amd"
    if any(k in low for k in ("intel", "arc", "iris", "uhd", "hd graphics")):
        return "intel"
    return "none"


def _directml_available() -> bool:
    """torch-directml gives AMD/Intel GPUs acceleration on Windows via DirectX."""
    try:
        import torch_directml  # type: ignore
        return bool(torch_directml.is_available())
    except Exception:
        return False


def _xpu_available() -> bool:
    """Intel XPU (via intel-extension-for-pytorch) exposes torch.xpu."""
    try:
        import torch
        return hasattr(torch, "xpu") and torch.xpu.is_available()
    except Exception:
        return False


def _pytorch_accel_plan() -> Dict:
    """The best-effort accelerated PyTorch install for THIS machine's GPU vendor
    + OS. Returns {command, label, experimental, note}. NVIDIA (CUDA) and Apple
    (Metal/MPS) are proven; AMD (ROCm on Linux, DirectML on Windows) and Intel
    (DirectML/XPU) are best-effort and flagged experimental so the UI stays honest.
    Every path targets THIS interpreter's pip so it lands in the app's venv."""
    os_name = detect_os()
    gpu = detect_gpu()
    name = gpu.get("name") or ""
    is_apple = os_name == "macos" and platform.machine() == "arm64"
    vendor = _gpu_vendor(name, is_apple)
    pip = [sys.executable, "-m", "pip"]

    # --force-reinstall + --no-deps is REQUIRED when swapping the CPU wheel for a
    # GPU wheel: the CUDA/ROCm build carries the SAME version number as the CPU
    # build (e.g. 2.14.0), so a plain `pip install torch` sees the requirement
    # already satisfied and does nothing. --no-deps keeps the existing shared
    # deps (numpy/sympy/…) which aren't hosted on the pytorch index.
    if vendor == "nvidia":
        return {"command": pip + ["install", "--index-url", "https://download.pytorch.org/whl/cu126",
                                  "--force-reinstall", "--no-deps", "torch", "torchvision"],
                "label": "CUDA (NVIDIA) build", "experimental": False,
                "note": "Official NVIDIA CUDA 12.6 wheels."}
    if vendor == "apple":
        return {"command": pip + ["install", "--force-reinstall", "--no-deps", "torch", "torchvision"],
                "label": "Apple Metal (MPS) build", "experimental": False,
                "note": "Default wheels include Metal (MPS) on Apple Silicon."}
    if vendor == "amd":
        if os_name == "linux":
            return {"command": pip + ["install", "--index-url", "https://download.pytorch.org/whl/rocm6.2",
                                      "--force-reinstall", "--no-deps", "torch", "torchvision"],
                    "label": "AMD ROCm build (Linux)", "experimental": True,
                    "note": "Official ROCm 6.2 wheels — supported AMD cards on Linux only."}
        if os_name == "windows":
            return {"command": pip + ["install", "torch-directml"],
                    "label": "AMD via DirectML (Windows)", "experimental": True,
                    "note": "DirectML gives AMD GPUs partial acceleration on Windows; YOLO/ultralytics coverage varies."}
    if vendor == "intel":
        if os_name == "windows":
            return {"command": pip + ["install", "torch-directml"],
                    "label": "Intel via DirectML (Windows)", "experimental": True,
                    "note": "DirectML covers Intel Arc / iGPU on Windows; support varies."}
        return {"command": pip + ["install", "intel-extension-for-pytorch"],
                "label": "Intel XPU (IPEX)", "experimental": True,
                "note": "Intel Extension for PyTorch (XPU); best on Linux."}
    # No discrete GPU we can target.
    return {"command": pip + ["install", "torch", "torchvision"],
            "label": "CPU build", "experimental": False,
            "note": "No supported GPU detected — CPU build."}


def _transcription_accel() -> Dict:
    """Which transcription backend is active + the best one to install for this
    machine. MLX is the fastest on Apple Silicon; faster-whisper (CTranslate2
    int8) is the cross-platform CPU/GPU accelerator vs plain openai-whisper."""
    os_name = detect_os()
    is_apple = os_name == "macos" and platform.machine() == "arm64"
    pip = [sys.executable, "-m", "pip"]
    try:
        from server.core.transcriber import detect_active_backend
        tb = detect_active_backend()
    except Exception:
        tb = {"active": None, "available": [], "note": ""}
    available = tb.get("available") or []
    if is_apple and "mlx" not in available:
        cmd, reco = pip + ["install", "mlx-whisper"], "Install mlx-whisper for native MLX acceleration (fastest on Apple Silicon)."
    elif "faster-whisper" not in available:
        cmd, reco = pip + ["install", "faster-whisper"], "Install faster-whisper for 3–5× faster transcription (CTranslate2 int8)."
    else:
        cmd, reco = [], ""
    return {"active": tb.get("active"), "note": tb.get("note", ""),
            "recommend": reco, "command": " ".join(cmd)}


def gpu_acceleration_status() -> Dict:
    """Hardware-aware ACCELERATION status + the exact install/uninstall commands
    for THIS machine, covering every path: NVIDIA CUDA, AMD (ROCm/DirectML),
    Intel (DirectML/XPU), Apple Metal (MPS) + MLX transcription, and CPU
    (faster-whisper). Guides the user — or anyone who forks/copies the repo onto
    different hardware — to turn on the best acceleration their machine supports.

    The gap this closes: ``component_installed('pytorch')`` is True whenever torch
    is present, even the CPU-only wheel — so a machine with a capable GPU but the
    CPU build looks "done" while the GPU sits idle. This surfaces that "dormant"
    state (for ANY GPU vendor) as an actionable step.

    state: 'active'        -> a GPU backend (CUDA/MPS/DirectML/XPU) is working
           'dormant'       -> a GPU exists but torch can't use it yet (fixable)
           'cpu_only'      -> no discrete GPU; CPU (faster-whisper) is the path
           'not_installed' -> torch isn't installed yet
    """
    os_name = detect_os()
    gpu = detect_gpu()
    torch_info = detect_torch()
    name = gpu.get("name") or ""
    is_apple = os_name == "macos" and platform.machine() == "arm64"
    vendor = _gpu_vendor(name, is_apple)

    installed = bool(torch_info.get("installed"))
    cuda = bool(torch_info.get("cuda"))
    mps = bool(torch_info.get("mps"))
    directml = _directml_available()
    xpu = _xpu_available()
    accelerated = cuda or mps or directml or xpu
    # We can offer *some* accelerated path for any discrete GPU vendor.
    can_accelerate = vendor in ("nvidia", "apple", "amd", "intel")

    if not installed:
        state = "not_installed"
    elif accelerated:
        state = "active"
    elif can_accelerate:
        state = "dormant"
    else:
        state = "cpu_only"

    if cuda:
        engine = "GPU (CUDA)"
    elif mps:
        engine = "GPU (Apple MPS)"
    elif directml:
        engine = "GPU (DirectML)"
    elif xpu:
        engine = "GPU (Intel XPU)"
    else:
        engine = "CPU"

    plan = _pytorch_accel_plan()
    install_cmd = " ".join(plan["command"])
    uninstall_cmd = " ".join(get_uninstall_commands().get("pytorch", []))
    cpu_cmd = f"{os.path.basename(sys.executable)} -m pip install torch torchvision"
    transcription = _transcription_accel()

    vendor_label = {"nvidia": "NVIDIA", "amd": "AMD", "intel": "Intel",
                    "apple": "Apple Silicon", "none": "No discrete GPU"}[vendor]

    if state == "active":
        headline = f"Acceleration is ON — {engine}."
        detail = f"{name or vendor_label} is powering transcription and face-tracking."
    elif state == "dormant":
        exp = " (experimental)" if plan["experimental"] else ""
        headline = f"{name or vendor_label} found, but PyTorch is running on the CPU."
        detail = (f"Install the {plan['label']}{exp} to use your GPU for faster "
                  f"Whisper transcription and YOLO face-tracking. {plan['note']} "
                  "It replaces the current CPU build; a restart is needed after.")
    elif state == "cpu_only":
        headline = f"{vendor_label} — running on CPU (accelerated)."
        detail = ("No discrete GPU to target, so AI runs on the CPU. That's fully "
                  "supported: faster-whisper (CTranslate2 int8) keeps transcription "
                  "quick, and FFmpeg still uses hardware encoding.")
    else:
        headline = "PyTorch isn't installed."
        detail = f"Install the {plan['label']} to enable face-tracking and acceleration."

    return {
        "os": os_name,
        "gpu_name": name or None,
        "vram_gb": gpu.get("vram_gb"),
        "vendor": vendor,
        "torch_installed": installed,
        "cuda": cuda, "mps": mps, "directml": directml, "xpu": xpu,
        "accelerated": accelerated,
        "can_accelerate": can_accelerate,
        "state": state,
        "engine": engine,
        "headline": headline,
        "detail": detail,
        "plan_label": plan["label"],
        "experimental": plan["experimental"],
        "install_command": install_cmd,
        "uninstall_command": uninstall_cmd,
        "cpu_command": cpu_cmd,
        "transcription": transcription,
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
    # --- Current-gen flagships (2026). Tags follow Ollama's library and may
    #     change; a bad pull just surfaces a 'download failed' toast. ---
    {"name": "phi4",            "label": "Phi-4 · 14B",       "params": "14B", "size_gb": 9.1,  "min_ram_gb": 16, "min_vram_gb": 12, "tier": "Flagship",
     "note": "Microsoft Phi-4 — excellent quality that fits a 12GB GPU (great on an RTX 3060)."},
    {"name": "qwen3:14b",       "label": "Qwen3 · 14B",       "params": "14B", "size_gb": 9.3,  "min_ram_gb": 16, "min_vram_gb": 12, "tier": "Flagship",
     "note": "Newer Qwen generation; strong all-round writing. Fits a 12GB GPU."},
    {"name": "deepseek-r1:14b", "label": "DeepSeek-R1 · 14B", "params": "14B", "size_gb": 9.0,  "min_ram_gb": 16, "min_vram_gb": 12, "tier": "Flagship",
     "note": "Visible step-by-step reasoning; very capable, can be overkill for short hooks."},
    {"name": "gpt-oss:20b",     "label": "GPT-OSS · 20B",     "params": "20B", "size_gb": 13.0, "min_ram_gb": 24, "min_vram_gb": 16, "tier": "Flagship",
     "note": "OpenAI's open model; strong general quality. Best on 16GB+ GPUs."},
    {"name": "gemma3:27b",      "label": "Gemma 3 · 27B",     "params": "27B", "size_gb": 17.0, "min_ram_gb": 32, "min_vram_gb": 20, "tier": "Flagship",
     "note": "Newer Gemma 3 — high quality + multilingual. Splits across a 12GB GPU + RAM."},
    {"name": "gpt-oss:120b",    "label": "GPT-OSS · 120B",    "params": "120B","size_gb": 65.0, "min_ram_gb": 96, "min_vram_gb": 80, "tier": "Extreme",
     "note": "Frontier-class open model; workstation / multi-GPU only."},
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
     "note": "Heavy but excellent; needs 64GB RAM and runs slowly without a big GPU."},
    # --- Workstation / high-end rigs (big multi-GPU or 64GB+ / 128GB+ RAM) ---
    {"name": "llama3.3:70b","label": "Llama 3.3 · 70B", "params": "70B",   "size_gb": 43.0, "min_ram_gb": 64,  "min_vram_gb": 48, "tier": "Max",
     "note": "Meta's latest 70B — sharpest hooks/titles. Wants a 48GB GPU or 64GB+ RAM."},
    {"name": "qwen2.5:72b", "label": "Qwen2.5 · 72B",  "params": "72B",   "size_gb": 47.0, "min_ram_gb": 64,  "min_vram_gb": 48, "tier": "Max",
     "note": "Elite short-form copywriting; 64GB+ RAM or a 48GB GPU."},
    {"name": "mixtral:8x22b","label":"Mixtral · 8x22B", "params": "8x22B MoE", "size_gb": 80.0, "min_ram_gb": 96, "min_vram_gb": 80, "tier": "Extreme",
     "note": "Workstation-class mixture-of-experts; ~96GB RAM or multi-GPU. Overkill for hooks."},
]


# Caption presets with a loud, punchy vibe — these benefit from a stronger LLM
# that writes bolder, higher-energy hooks, so we nudge the recommendation up a
# rung (as long as the machine can still run it).
HIGH_ENERGY_PRESETS = {
    "viral_yellow", "mrbeast_impact", "fire_red", "tiktok_pop", "gaming_rgb",
    "sunset_orange", "electric_purple", "comic_punch", "neon_green",
    "glitch_shadow", "cyberpunk_cyan", "retro_vaporwave", "high_contrast",
}

# Strength ladder (weakest → strongest) used to pick a base by hardware and to
# bump the pick for high-energy presets. Names must exist in OLLAMA_MODEL_CATALOG.
_MODEL_LADDER = [
    "gemma2:2b", "llama3.2:3b", "gemma2:9b", "qwen2.5:7b", "qwen2.5:14b",
    "gemma2:27b", "qwen2.5:32b", "llama3.3:70b", "qwen2.5:72b", "mixtral:8x22b",
]


# Models we've ACTUALLY measured for clip selection on real footage (RTX 3060,
# Sept 2026). Everything else in the catalog should work — it's driven the same
# way — but we haven't benchmarked whether it picks better clips, so the UI is
# honest about that instead of implying every model is vetted.
_TESTED_MODELS = {"gemma2:2b", "qwen2.5:7b", "qwen2.5:14b"}

# Best-effort model licenses, keyed by family (the part before the ':'). Shown in
# the model catalog so users who care about commercial/permissive terms can pick
# accordingly. "Gemma"/"Llama x" are the vendors' own community licenses (usable
# but with their terms); Apache-2.0 / MIT are fully permissive.
_MODEL_LICENSES = {
    "gemma2": "Gemma", "gemma3": "Gemma",
    "qwen2.5": "Apache-2.0", "qwen3": "Apache-2.0",
    "llama3.1": "Llama 3.1", "llama3.2": "Llama 3.2", "llama3.3": "Llama 3.3",
    "mistral": "Apache-2.0", "mistral-nemo": "Apache-2.0", "mixtral": "Apache-2.0",
    "phi3": "MIT", "phi4": "MIT", "deepseek-r1": "MIT", "gpt-oss": "Apache-2.0",
}


def _model_license(name: str) -> str:
    """License label for a model name, matched on its family prefix."""
    fam = name.split(":")[0]
    return _MODEL_LICENSES.get(fam, "")


def _model_fits_ram(name: str, ram_gb: float) -> bool:
    m = next((x for x in OLLAMA_MODEL_CATALOG if x["name"] == name), None)
    if not m:
        return False
    return (not ram_gb) or ram_gb >= m["min_ram_gb"]


def recommend_ollama_model(preset: str = "") -> str:
    """Best default Ollama model for this machine, by GPU VRAM + system RAM, and
    nudged up a rung for high-energy caption presets (which read punchier with a
    stronger model). Never recommends something the machine's RAM can't hold.

    Bigger local models write noticeably better hooks/titles but need memory.
    On a 12GB GPU (e.g. RTX 3060) a 7B is the balanced default — it matches a
    14B's selection judgment at ~half the latency — while 16GB+ cards step up to
    a 14B; scale down for lighter machines so it still runs comfortably.
    """
    gpu = detect_gpu()
    cpu = detect_cpu()
    vram = gpu.get("vram_gb") or 0
    ram = cpu.get("ram_gb") or 0

    # Prefer a model that FITS THE GPU for speed, scaling up with bigger VRAM.
    # Finer-grained tiers so low/mid/high cards each get a sensible pick, not
    # just the coarse 8/12/24/48 steps. For CPU-only (or GPUs whose VRAM we
    # can't read), fall back to RAM but stay conservative — huge models are slow
    # on CPU; the catalog still offers the big ones for those who want them.
    def _pick(name):
        return _MODEL_LADDER.index(name)

    if vram >= 48:        # A6000 / dual-GPU / H100-class
        base = _pick("llama3.3:70b")
    elif vram >= 24:      # 3090 / 4090 / 7900 XTX
        base = _pick("qwen2.5:32b")
    elif vram >= 16:      # 4060 Ti 16GB / 4080 / 7800 XT / A4000
        base = _pick("qwen2.5:14b")
    elif vram >= 11:      # 3060 12GB / 2080 Ti / 6700 XT
        # A/B testing on an RTX 3060 12GB showed qwen2.5:7b matches the 14B's
        # clip-selection judgment and writes equally strong (often punchier)
        # hooks, at ~half the latency. So 7B is the balanced default here; the
        # high-energy-preset bump below (and the Setup catalog) still steps up to
        # the 14B for users who want maximum quality.
        base = _pick("qwen2.5:7b")
    elif vram >= 8:       # 3050/3060 Ti/4060 8GB / RX 6600
        base = _pick("gemma2:9b")
    elif vram >= 6:       # 2060 6GB / 1660 / RX 5500
        base = _pick("llama3.2:3b")
    elif vram >= 4:       # entry GPUs (1650 4GB / RX 6400)
        base = _pick("gemma2:2b")
    elif ram >= 48:       # CPU-only but lots of RAM
        base = _pick("qwen2.5:14b")
    elif ram >= 32:
        base = _pick("gemma2:9b")
    elif ram >= 16:
        base = _pick("llama3.2:3b")
    else:
        base = _pick("gemma2:2b")

    idx = base
    if (preset or "").strip().lower() in HIGH_ENERGY_PRESETS:
        idx = min(base + 1, len(_MODEL_LADDER) - 1)
    # Step back down if the bumped pick won't fit this machine's RAM.
    while idx > 0 and not _model_fits_ram(_MODEL_LADDER[idx], ram):
        idx -= 1
    return _MODEL_LADDER[idx]


def ollama_model_catalog(preset: str = "") -> Dict:
    """Catalog + which models are installed + the recommended pick.

    Recommends by hardware, nudged up for high-energy caption presets. Marks each
    model installed/recommended and whether the machine has enough RAM/VRAM.
    """
    recommended = recommend_ollama_model(preset)
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
            # Transparency: have we actually benchmarked this one, and its license.
            "tested": m["name"] in _TESTED_MODELS,
            "license": _model_license(m["name"]),
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


def resolve_default_ollama_model(preset: str = "") -> str:
    """Pick the app's default local LLM: the STRONGEST model that will actually
    run well on this machine, preferring one that's already installed.

    Order of preference:
      1. The hardware recommendation, if it's already pulled -> use it.
      2. Otherwise the strongest INSTALLED model that fits this machine's RAM,
         ranked by the model ladder (so a user who pulled a big model gets it).
      3. Otherwise the hardware recommendation name (LLM features fall back to
         the heuristic until it's pulled — the Setup catalog nudges the user).
      4. Absolute floor: "gemma2:2b".

    Users can always override via /api/setup/ai-model; this only sets the start
    value so good hardware gets a good model without any manual step.
    """
    try:
        recommended = recommend_ollama_model(preset)
    except Exception:
        recommended = "gemma2:2b"

    try:
        installed = list_ollama_models()
    except Exception:
        installed = []
    if not installed:
        return recommended

    # Exact-name matching only: gemma2:2b and gemma2:9b are DIFFERENT models, so
    # a shared family name must not count a bigger, un-pulled size as installed.
    installed_set = set(installed)

    if recommended in installed_set:
        return recommended

    ram = 0
    try:
        ram = detect_cpu().get("ram_gb") or 0
    except Exception:
        ram = 0

    # Strongest INSTALLED model that fits, by ladder order (strongest last). We
    # never point the default at a model that isn't pulled — using the installed
    # smaller model beats a bigger one that would just fall back to the heuristic.
    best = None
    for name in _MODEL_LADDER:
        if name in installed_set and _model_fits_ram(name, ram):
            best = name  # keep the highest ladder index that fits
    if best:
        return best
    return recommended


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
        {"model": "large-v3", "tier": "Max accuracy",
         "note": "Best transcription for long/complex media; wants a GPU or patience on CPU"},
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