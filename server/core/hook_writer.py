"""
Optional AI hook rewriting via Ollama.

Turns a clip's transcript into punchy short-form hooks using the user's chosen
local LLM. Fully optional: callers fall back to the heuristic
`rank_hook_candidates` when Ollama isn't running or the call fails.
"""

import json
from typing import List, Optional


def _clean_llm_line(value: object, allow_long: bool = False) -> str:
    """Tidy a single line the model returned: strip list markers, numbering and
    surrounding quotes. Collapses whitespace; keeps sentence punctuation."""
    s = str(value or "").strip()
    if not s:
        return ""
    s = s.lstrip("-•*").strip()
    s = s.lstrip("0123456789.)").strip()
    s = s.strip('"').strip("'").strip()
    s = " ".join(s.split())
    return s


def generate_clip_copy_llm(
    full_text: str,
    current_hook: str = "",
    model: str = "gemma2:2b",
    tone: str = "",
) -> Optional[dict]:
    """Write viral social copy for ONE clip with a local Ollama model.

    Returns ``{"hook", "title", "description"}`` grounded in the clip's own
    transcript, or ``None`` when Ollama is unavailable or the reply can't be
    parsed (so the caller keeps the heuristic text). This is the
    CapCut / OpusClips-style copy the clip cards + intro hook use when the
    "Use Ollama" toggle is on — a real description, not just the raw hook line.
    """
    text = " ".join((full_text or "").split())
    if not text:
        return None

    tone_line = f" Match this tone/style: {tone}." if tone else ""
    current_line = (
        f' The current hook is "{current_hook.strip()}"; make yours clearly stronger.'
        if current_hook and current_hook.strip()
        else ""
    )

    prompt = (
        "You are a short-form video copywriter for TikTok, Instagram Reels and "
        "YouTube Shorts. From ONE clip's transcript below, write social copy that "
        "makes people STOP scrolling and watch to the end.\n\n"
        "Return ONLY a JSON object with EXACTLY these keys:\n"
        '- "hook": the on-screen hook shown in the first 2 seconds. Punchy and '
        "curiosity-driven, roughly 4-12 words. Open a loop the clip pays off. "
        "No hashtags, no emojis, no surrounding quotes.\n"
        '- "title": a specific, catchy title for the clip, roughly 4-10 words.\n'
        '- "description": an engaging 1-2 sentence caption for the post that sets '
        "up the payoff and pulls the viewer in; you may end with 2-4 relevant "
        "hashtags. Up to ~300 characters.\n\n"
        "Rules: be specific, not generic. Ground everything in what is actually "
        "said — do not invent facts. Avoid dead openers like 'In this video' or "
        f"'Today I'.{tone_line}{current_line}\n\n"
        f"Transcript:\n{text[:2000]}"
    )

    try:
        from server.core import llm_client

        content = llm_client.chat(
            [{"role": "user", "content": prompt}], json_mode=True, model=model,
        )
        data = json.loads(content)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    hook = _clean_llm_line(data.get("hook"))
    title = _clean_llm_line(data.get("title"))
    description = _clean_llm_line(data.get("description"), allow_long=True)
    if not (hook or title or description):
        return None

    return {
        "hook": hook[:120],
        "title": title[:90],
        "description": description[:400],
    }


def generate_hooks_llm(
    full_text: str,
    current_hook: str = "",
    count: int = 6,
    model: str = "gemma2:2b",
    tone: str = "",
) -> List[str]:
    """Ask a local Ollama model for punchy hook options grounded in the clip.

    Returns a de-duplicated list of hook strings, or [] if Ollama is unavailable
    or the response can't be parsed (so the caller can fall back gracefully).
    """
    text = (full_text or "").strip()
    if not text:
        return []

    count = max(1, min(int(count or 6), 10))
    tone_line = f" Match this style/tone: {tone}." if tone else ""
    current_line = (
        f' The current hook is "{current_hook.strip()}"; make the alternatives clearly different and stronger.'
        if current_hook and current_hook.strip()
        else ""
    )

    prompt = (
        "You are an elite short-form video copywriter (TikTok / Reels / YouTube "
        "Shorts) — the same caliber as OpusClips / CapCut auto-hooks. The hook is "
        "the on-screen line a viewer reads in the first 1-2 seconds; it decides "
        "whether they keep watching or scroll past.\n\n"
        f"From ONE clip's transcript below, write {count} DISTINCT, scroll-stopping "
        "hook options. Give each a different angle:\n"
        "- a curiosity gap (tease the payoff without revealing it)\n"
        "- a bold or counterintuitive claim\n"
        "- a sharp question the viewer needs answered\n"
        "- a cliffhanger / unresolved tension\n"
        "- a specific number or concrete detail\n"
        "- a high-stakes 'what happens next' setup\n\n"
        "Make them SPECIFIC, not vague. Open a curiosity loop the clip actually "
        "closes, and front-load the tension (most intriguing idea first). Avoid "
        "dead openers ('In this video', 'Today I', 'So basically') and bait the "
        "clip doesn't pay off.\n\n"
        "Examples (spoken line -> hook):\n"
        '"we drove around for hours looking for parking" -> Why nobody warns you about this\n'
        '"I quit my job in 2019 and started selling online" -> Quitting was the easy part\n'
        '"the middleman takes a cut on every sale" -> There\'s always a middleman — here\'s who\n\n'
        f"Rules: each hook punchy, roughly 4-12 words. Ground it in what's actually "
        "said. No hashtags, no emojis, no surrounding quotes, no numbering."
        f"{tone_line}{current_line}\n"
        "Return ONLY the hooks, one per line.\n\n"
        f"Transcript:\n{text[:1800]}"
    )

    try:
        from server.core import llm_client

        content = llm_client.chat([{"role": "user", "content": prompt}], model=model)
    except Exception:
        return []

    hooks: List[str] = []
    seen = set()
    for raw in content.splitlines():
        # Strip common list markers / numbering / quotes the model may add.
        s = raw.strip().lstrip("-•*").strip()
        s = s.lstrip("0123456789.)").strip()
        s = s.strip('"').strip("'").strip()
        if not s or len(s.split()) < 2:
            continue
        if len(s) > 120:
            s = s[:117].rsplit(" ", 1)[0] + "…"
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        hooks.append(s)
        if len(hooks) >= count:
            break
    return hooks
