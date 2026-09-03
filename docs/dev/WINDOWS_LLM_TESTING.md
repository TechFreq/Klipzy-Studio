# Windows LLM Quality Testing — Session Handoff

Saved from the Mac session so it's ready to run on the Windows machine (RTX 3060 12GB, 64GB RAM, Ryzen 9).

## Last three commits (local on `master`)

- `94c7998` — stronger LLM hooks + grounded semantic selection
- `a8d742c` — editing polish (loudness, music, zoom, batch)
- `52881a0` — speaker diarization

## Testing prompt — Klipzy Studio LLM quality on Windows

> I'm on my Windows machine (RTX 3060 12GB, 64GB RAM, Ryzen 9) with the latest Klipzy Studio code. I want to measure how much better clip selection and hook writing get with a strong local model versus the tiny default, using real footage. Everything is local (Ollama); no cloud.

### Do this

1. **Confirm the environment.** Check Ollama is running and list installed models. Then pull a strong model that fits a 12GB GPU — try `qwen2.5:14b` first (the card can handle it), and also pull `qwen2.5:7b` as a faster fallback. Confirm `resolve_default_ollama_model()` now auto-selects the 14B (it should, since it'll be the strongest installed model that fits).

2. **Pick a test clip.** Use a real interview/talking clip you have locally. Prefer one with a few distinct topics so clip selection has real choices to make.

3. **Run an A/B comparison — this is the point.** For the same clip, transcribe once, build the heuristic candidates, then compare:
   - **Hooks:** run `generate_hooks_llm` with `gemma2:2b` vs `qwen2.5:14b` and print both sets side by side.
   - **Selection:** run `rank_candidates_llm` with `gemma2:2b` vs `qwen2.5:14b` and show how each reorders/scores the candidates and what titles/hooks it writes. Print it as a clean before/after table to judge the quality jump.
   - Then a full render through `/process` with `use_llm` on and the 14B model, on that clip, and show the resulting clip titles + hooks the pipeline actually chose.

4. **Report honestly:** did the bigger model produce genuinely better hooks and smarter clip choices, or marginal? Note speed (how long the LLM calls took). Recommend which model to set as the default for this hardware based on the quality/speed tradeoff. Don't oversell — if it's only slightly better, say so.

### Context

On the Mac with only `gemma2:2b`, the improved hook prompt already produced decent hooks, but the clip ranking barely changed because the 2B model is too weak to add judgment. The whole point of this test is to see how much a 14B closes that gap. The rank-and-refine is grounded (the LLM only scores real candidate windows, never invents timestamps), so it can't produce bad cuts — we're purely measuring judgment/writing quality.
