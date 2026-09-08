"""
Speaker diarization labeling/prefixing — pure-helper tests (no pyannote, no video).

Covers the parts of feature #16 that are fully deterministic: mapping raw
pyannote labels to friendly "Speaker N" names, grouping words into speaker
turns (with timeline offset + gap carry-forward), and the caption-styler
speaker prefix.
"""

from server.core.diarizer import (
    assign_speakers_to_segments,
    build_speaker_name_map,
    group_words_into_speaker_turns,
)
from server.core.caption_styler import generate_karaoke_captions
from server.models import TranscriptSegment, WordTimestamp


def _w(word, start, end):
    return {"word": word, "start": start, "end": end}


def test_assign_speakers_picks_max_overlap():
    segs = [{"start": 0.0, "end": 1.0}, {"start": 5.0, "end": 6.0}]
    diar = [
        {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"},
        {"start": 4.0, "end": 7.0, "speaker": "SPEAKER_01"},
    ]
    assert assign_speakers_to_segments(segs, diar) == ["SPEAKER_00", "SPEAKER_01"]


def test_assign_speakers_none_when_no_overlap():
    segs = [{"start": 10.0, "end": 11.0}]
    diar = [{"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"}]
    assert assign_speakers_to_segments(segs, diar) == [None]


def test_name_map_orders_by_first_appearance():
    # SPEAKER_01 talks first in time, so it must become "Speaker 1".
    diar = [
        {"start": 5.0, "end": 6.0, "speaker": "SPEAKER_00"},
        {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_01"},
        {"start": 6.0, "end": 7.0, "speaker": "SPEAKER_00"},
    ]
    m = build_speaker_name_map(diar)
    assert m == {"SPEAKER_01": "Speaker 1", "SPEAKER_00": "Speaker 2"}


def test_name_map_empty():
    assert build_speaker_name_map([]) == {}


def test_group_words_into_turns_basic():
    words = [_w("hello", 0.0, 0.5), _w("there", 0.5, 1.0),
             _w("hi", 4.0, 4.5), _w("back", 4.5, 5.0)]
    diar = [
        {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"},
        {"start": 3.5, "end": 6.0, "speaker": "SPEAKER_01"},
    ]
    turns = group_words_into_speaker_turns(words, diar)
    assert len(turns) == 2
    assert turns[0]["speaker"] == "Speaker 1"
    assert [w["word"] for w in turns[0]["words"]] == ["hello", "there"]
    assert turns[1]["speaker"] == "Speaker 2"
    assert [w["word"] for w in turns[1]["words"]] == ["hi", "back"]
    # Turns keep the words' original timeline.
    assert turns[1]["start"] == 4.0 and turns[1]["end"] == 5.0


def test_group_words_carry_forward_over_gap():
    # Middle word overlaps no diar segment -> inherits the previous speaker
    # instead of forming an orphan turn.
    words = [_w("a", 0.0, 0.5), _w("gap", 2.2, 2.4), _w("b", 4.0, 4.5)]
    diar = [
        {"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
        {"start": 3.5, "end": 5.0, "speaker": "SPEAKER_00"},
    ]
    turns = group_words_into_speaker_turns(words, diar)
    # All one speaker -> a single merged turn.
    assert len(turns) == 1
    assert [w["word"] for w in turns[0]["words"]] == ["a", "gap", "b"]


def test_group_words_offset_aligns_clip_local_diar():
    # Words are source-time (start at 100s); diar is clip-local (0-based).
    words = [_w("x", 100.0, 100.5), _w("y", 105.0, 105.5)]
    diar_local = [
        {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"},
        {"start": 4.0, "end": 6.0, "speaker": "SPEAKER_01"},
    ]
    # Without offset nothing lines up -> single carried turn.
    no_offset = group_words_into_speaker_turns(words, diar_local)
    assert len(no_offset) == 1
    # With offset = clip start, the two speakers separate correctly.
    turns = group_words_into_speaker_turns(words, diar_local, diar_offset=100.0)
    assert len(turns) == 2
    assert turns[0]["speaker"] == "Speaker 1"
    assert turns[1]["speaker"] == "Speaker 2"


def test_group_words_empty():
    assert group_words_into_speaker_turns([], []) == []


def test_caption_styler_prefixes_speaker_label(tmp_path):
    seg = TranscriptSegment(
        id=0, start=0.0, end=1.0, text="hello there",
        words=[WordTimestamp(word="hello", start=0.0, end=0.5),
               WordTimestamp(word="there", start=0.5, end=1.0)],
        speaker="Speaker 1",
    )
    out = str(tmp_path / "cap.ass")
    generate_karaoke_captions([seg], out, chunk_size=4)
    content = open(out, encoding="utf-8").read()
    # Label is uppercased by the default style and prefixes the caption line.
    assert "SPEAKER 1:" in content


def test_caption_styler_no_prefix_without_speaker(tmp_path):
    seg = TranscriptSegment(
        id=0, start=0.0, end=1.0, text="hello there",
        words=[WordTimestamp(word="hello", start=0.0, end=0.5),
               WordTimestamp(word="there", start=0.5, end=1.0)],
    )
    out = str(tmp_path / "cap.ass")
    generate_karaoke_captions([seg], out, chunk_size=4)
    content = open(out, encoding="utf-8").read()
    assert "SPEAKER" not in content.upper().replace("SCRIPT", "")


def test_caption_styler_label_only_on_first_chunk(tmp_path):
    # A long turn split into multiple chunks should show the label once.
    words = [WordTimestamp(word=f"w{i}", start=float(i), end=float(i) + 0.5)
             for i in range(8)]
    seg = TranscriptSegment(id=0, start=0.0, end=8.0, text="x", words=words, speaker="Speaker 2")
    out = str(tmp_path / "cap.ass")
    generate_karaoke_captions([seg], out, chunk_size=4)
    content = open(out, encoding="utf-8").read()
    # Per-word events mean the label rides each word line of the FIRST chunk
    # (w0-w3) only, never the second chunk (w4-w7). Verify it's confined there.
    labeled = [l for l in content.splitlines()
               if l.startswith("Dialogue") and "SPEAKER 2:" in l]
    assert labeled, "expected the speaker label on the first chunk"
    assert all("w4" not in l and "w7" not in l for l in labeled)


# ---------------------------------------------------------------------------
# #16.2 Speaker-aware clip selection — coherence scoring + ranking
# ---------------------------------------------------------------------------
from types import SimpleNamespace

from server.core.diarizer import speaker_coherence, rank_clips_by_speaker


def test_coherence_single_speaker_is_high():
    diar = [{"start": 0.0, "end": 10.0, "speaker": "SPEAKER_00"}]
    assert speaker_coherence(0.0, 10.0, diar) == 1.0


def test_coherence_clean_two_way_is_good():
    diar = [{"start": 0.0, "end": 5.0, "speaker": "A"},
            {"start": 5.0, "end": 10.0, "speaker": "B"}]
    score = speaker_coherence(0.0, 10.0, diar)
    assert 0.8 <= score <= 0.9


def test_coherence_three_speakers_lower_than_two():
    two = speaker_coherence(0.0, 10.0, [
        {"start": 0.0, "end": 5.0, "speaker": "A"},
        {"start": 5.0, "end": 10.0, "speaker": "B"}])
    three = speaker_coherence(0.0, 10.0, [
        {"start": 0.0, "end": 3.0, "speaker": "A"},
        {"start": 3.0, "end": 6.0, "speaker": "B"},
        {"start": 6.0, "end": 10.0, "speaker": "C"}])
    assert three < two


def test_coherence_frantic_switching_penalised():
    diar = [{"start": float(i), "end": float(i + 1),
             "speaker": ("A" if i % 2 == 0 else "B")} for i in range(5)]
    assert speaker_coherence(0.0, 5.0, diar) < 0.6


def test_coherence_no_speech_is_zero():
    diar = [{"start": 0.0, "end": 10.0, "speaker": "A"}]
    assert speaker_coherence(20.0, 30.0, diar) == 0.0


def test_coherence_low_coverage_dampened():
    # Single clean speaker but only half the window has speech.
    diar = [{"start": 0.0, "end": 5.0, "speaker": "A"}]
    half = speaker_coherence(0.0, 10.0, diar)
    full = speaker_coherence(0.0, 5.0, diar)
    assert half < full
    assert 0.7 <= half <= 0.85


def test_rank_clips_prefers_coherent_window():
    diar = [
        {"start": 0.0, "end": 10.0, "speaker": "A"},        # clip 1: monologue
        {"start": 10.0, "end": 13.0, "speaker": "A"},       # clip 2: 3-way chatter
        {"start": 13.0, "end": 16.0, "speaker": "B"},
        {"start": 16.0, "end": 20.0, "speaker": "C"},
    ]
    good = SimpleNamespace(start_time=0.0, end_time=10.0, score=10.0)
    messy = SimpleNamespace(start_time=10.0, end_time=20.0, score=10.0)
    ranked = rank_clips_by_speaker([messy, good], diar)
    assert ranked[0] is good
    assert good.score > messy.score
