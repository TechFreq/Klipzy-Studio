"""
Grounded LLM clip-selection helpers — pure tests (no Ollama).
Covers timestamp boundary-snapping and the rank-and-refine score merge.
"""

from types import SimpleNamespace

from server.core.llm_detector import _snap_window, _apply_llm_rankings, _coerce_rankings


SEGS = [
    {"start": 0.0, "end": 5.0},
    {"start": 5.0, "end": 12.0},
    {"start": 12.0, "end": 20.0},
    {"start": 20.0, "end": 35.0},
]


def test_snap_window_snaps_to_boundaries():
    assert _snap_window(6.0, 19.0, SEGS) == (5.0, 20.0)


def test_snap_window_extends_short_window_to_min_dur():
    # 1..3 is too short; extend end to reach >= min_dur (8s default).
    out = _snap_window(1.0, 3.0, SEGS, min_dur=8.0)
    assert out is not None
    start, end = out
    assert start == 0.0
    assert end - start >= 8.0


def test_snap_window_trims_overlong_window_to_max_dur():
    out = _snap_window(0.0, 35.0, SEGS, max_dur=20.0)
    assert out == (0.0, 20.0)


def test_snap_window_none_without_segments():
    assert _snap_window(1.0, 5.0, []) is None


def test_snap_window_clamps_out_of_range():
    # Times beyond the transcript get clamped into range, still valid.
    out = _snap_window(-100.0, 999.0, SEGS)
    assert out is not None
    s, e = out
    assert s >= 0.0 and e <= 35.0 and e > s


def _cand(score, hook="", title="", reason=""):
    return SimpleNamespace(score=score, hook_text=hook, title=title, reason=reason)


def test_apply_rankings_blends_scores_and_reorders():
    cands = [_cand(5.0), _cand(5.0)]  # ids 0 and 1
    rankings = [
        {"id": 0, "score": 2.0},
        {"id": 1, "score": 9.0, "hook": "You won't believe this", "title": "Wild moment"},
    ]
    out = _apply_llm_rankings(cands, rankings, weight=0.6)
    # id 1 got the higher LLM score -> should sort first.
    assert out[0] is cands[1]
    # 0.4*5 + 0.6*9 = 7.4 ; 0.4*5 + 0.6*2 = 3.2
    assert abs(cands[1].score - 7.4) < 1e-6
    assert abs(cands[0].score - 3.2) < 1e-6
    assert cands[1].hook_text == "You won't believe this"
    assert cands[1].title == "Wild moment"


def test_apply_rankings_ignores_bad_entries():
    cands = [_cand(5.0)]
    rankings = [
        "not a dict",
        {"id": 99, "score": 10.0},   # out of range
        {"id": 0, "score": "bad"},    # unparseable score -> score unchanged
    ]
    out = _apply_llm_rankings(cands, rankings)
    assert out[0].score == 5.0


def test_apply_rankings_noop_on_non_list():
    cands = [_cand(5.0)]
    assert _apply_llm_rankings(cands, {"not": "a list"}) is cands


# --- _coerce_rankings: normalize the shapes local models actually return under
#     Ollama format="json" (bare array, wrapped array, or a single object). The
#     single-object case was a real bug: models scored only candidate 0 and the
#     old parser dropped it, so LLM judgement was silently discarded.
def test_coerce_rankings_passthrough_list():
    data = [{"id": 0, "score": 8}, {"id": 1, "score": 3}]
    assert _coerce_rankings(data) == data


def test_coerce_rankings_unwraps_object_with_array():
    data = {"rankings": [{"id": 0, "score": 8}]}
    assert _coerce_rankings(data) == [{"id": 0, "score": 8}]
    # A differently-named wrapper key still works (some models use "clips").
    assert _coerce_rankings({"clips": [{"id": 1}]}) == [{"id": 1}]


def test_coerce_rankings_wraps_single_object():
    # The bug: a lone ranking object must be treated as a one-element list.
    obj = {"id": 0, "score": 7, "hook": "h", "title": "t", "reason": "r"}
    assert _coerce_rankings(obj) == [obj]


def test_coerce_rankings_empty_on_garbage():
    assert _coerce_rankings("nope") == []
    assert _coerce_rankings({"meta": "no rankings here"}) == []


def test_coerce_rankings_feeds_apply_rankings():
    # End-to-end: a single-object reply now actually moves scores.
    cands = [_cand(5.0), _cand(5.0)]
    single = {"id": 1, "score": 10.0, "hook": "wow"}
    out = _apply_llm_rankings(cands, _coerce_rankings(single), weight=0.6)
    assert out[0] is cands[1]
    assert cands[1].hook_text == "wow"


def test_resolve_default_model_returns_valid_name():
    # Smoke test: resolver returns a non-empty model name string on any machine.
    from server.core.system_check import resolve_default_ollama_model
    name = resolve_default_ollama_model()
    assert isinstance(name, str) and ":" in name or name
