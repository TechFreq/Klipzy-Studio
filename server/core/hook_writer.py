"""
Optional AI hook rewriting via Ollama.

Turns a clip's transcript into punchy short-form hooks using the user's chosen
local LLM. Fully optional: callers fall back to the heuristic
`rank_hook_candidates` when Ollama isn't running or the call fails.
"""

from typing import List


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
        "You write scroll-stopping hooks for vertical short-form videos "
        "(TikTok / Reels / YouTube Shorts).\n"
        f"From the transcript of ONE clip below, write {count} punchy hook options "
        "a viewer sees in the first 2 seconds.\n"
        "Rules: each hook 3-9 words, 60 characters max, no hashtags, no emojis, no "
        "surrounding quotes, no numbering. Ground them in what's actually said, and "
        f"make them curiosity-driving.{tone_line}{current_line}\n"
        "Return ONLY the hooks, one per line.\n\n"
        f"Transcript:\n{text[:1800]}"
    )

    try:
        import ollama

        resp = ollama.chat(model=model, messages=[{"role": "user", "content": prompt}])
        content = (resp.get("message", {}) or {}).get("content", "") or ""
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
        if len(s) > 90:
            s = s[:87].rsplit(" ", 1)[0] + "…"
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        hooks.append(s)
        if len(hooks) >= count:
            break
    return hooks
