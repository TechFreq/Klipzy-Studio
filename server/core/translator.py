"""
Local caption translation via Ollama.

Translates an existing SRT's cues into a target language using the user's local
LLM (no cloud), writing translated .srt + .vtt alongside. Line-level (keeps the
original cue timings); word-level karaoke translation is a future step.
"""

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# A small, friendly set for the UI; any language name also works if typed.
COMMON_LANGUAGES = [
    "Spanish", "French", "German", "Portuguese", "Italian", "Hindi",
    "Japanese", "Korean", "Chinese (Simplified)", "Arabic", "Russian", "English",
]


def _parse_srt(path: str) -> List[Dict]:
    """Parse an SRT file into cues: [{index, timing, text}]."""
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        raw = fh.read()
    cues = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip() != ""]
        if len(lines) < 2:
            continue
        idx = lines[0].strip()
        timing = lines[1].strip() if "-->" in lines[1] else ""
        text = " ".join(lines[2:]) if timing else " ".join(lines[1:])
        cues.append({"index": idx, "timing": timing, "text": text})
    return cues


def translate_lines(lines: List[str], target_lang: str, model: str) -> List[str]:
    """Translate a list of short caption lines into target_lang via Ollama,
    preserving count/order. Returns the originals unchanged on any failure."""
    if not lines:
        return []
    try:
        import ollama
    except Exception:
        return list(lines)

    out: List[str] = [None] * len(lines)
    # Batch to keep prompts small and numbering reliable.
    B = 20
    for base in range(0, len(lines), B):
        chunk = lines[base:base + B]
        numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(chunk))
        prompt = (
            f"Translate each numbered subtitle line into {target_lang}.\n"
            "Rules: keep the SAME numbering, exactly one translation per line, "
            "preserve meaning and tone, do NOT add notes or extra lines, do not "
            "merge lines. Output only the numbered translations.\n\n"
            f"{numbered}"
        )
        try:
            resp = ollama.chat(model=model, messages=[{"role": "user", "content": prompt}])
            content = (resp.get("message", {}) or {}).get("content", "") or ""
        except Exception:
            content = ""
        parsed: Dict[int, str] = {}
        for ln in content.splitlines():
            m = re.match(r"\s*(\d+)[.)]\s*(.+)", ln)
            if m:
                parsed[int(m.group(1))] = m.group(2).strip()
        for i, original in enumerate(chunk):
            out[base + i] = parsed.get(i + 1, original)  # fall back to original if missing
    return [o if o is not None else lines[i] for i, o in enumerate(out)]


def _srt_time_to_vtt(t: str) -> str:
    return t.replace(",", ".")


def translate_srt_file(srt_path: str, target_lang: str, model: str,
                       out_dir: Optional[str] = None) -> Dict[str, str]:
    """Translate an SRT into target_lang; write <name>.<lang>.srt and .vtt.
    Returns {"srt": path, "vtt": path, "translated": N, "used_ai": bool}.
    """
    if not os.path.isfile(srt_path):
        raise FileNotFoundError(f"Subtitle file not found: {srt_path}")
    cues = _parse_srt(srt_path)
    texts = [c["text"] for c in cues]
    translated = translate_lines(texts, target_lang, model)
    used_ai = any(a != b for a, b in zip(texts, translated))  # heuristic: something changed

    src = Path(srt_path)
    out = Path(out_dir).expanduser().resolve() if out_dir else src.parent
    out.mkdir(parents=True, exist_ok=True)
    lang_slug = re.sub(r"[^a-z0-9]+", "-", target_lang.lower()).strip("-") or "translated"

    srt_out = out / f"{src.stem}.{lang_slug}.srt"
    vtt_out = out / f"{src.stem}.{lang_slug}.vtt"

    with open(srt_out, "w", encoding="utf-8") as fh:
        for c, t in zip(cues, translated):
            fh.write(f"{c['index']}\n{c['timing']}\n{t}\n\n")
    with open(vtt_out, "w", encoding="utf-8") as fh:
        fh.write("WEBVTT\n\n")
        for c, t in zip(cues, translated):
            fh.write(f"{_srt_time_to_vtt(c['timing'])}\n{t}\n\n")

    return {"srt": str(srt_out), "vtt": str(vtt_out), "translated": len(cues), "used_ai": used_ai}
