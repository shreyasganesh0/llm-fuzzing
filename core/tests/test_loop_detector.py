"""Tests for the loop detector and its integration with parsers + streaming.

Covers:
  - Basic detector semantics (positive, negative, short-input edge cases).
  - Real-world patterns observed from llama-3.1-8b failures in this repo.
  - Integration: each parser short-circuits to parse_failure on loops.
  - LLMClient streaming path aborts mid-generation when a loop forms.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.loop_detector import is_degenerate_loop


def test_detector_flags_simple_repeated_substring():
    text = ("0123456789" * 4 + "ABCDEF") * 20  # 46-char unit × 20 copies
    assert is_degenerate_loop(text) is True


def test_detector_ignores_diverse_text():
    text = "".join(f"func_{i}: calls helper_{i}; reads field_{i}\n" for i in range(200))
    assert is_degenerate_loop(text) is False


def test_detector_ignores_short_input():
    assert is_degenerate_loop("aaaaa" * 10) is False  # 50 chars << 40*6


def test_detector_catches_observed_branch_entry_loop():
    # Reproduces the RE2.HexTests failure: 322 copies of the same branch entry.
    entry = '\n    {"location": "re2/re2.cc:LINE", "true_taken": false, "false_taken": false},'
    text = '{"branches": [' + entry * 300 + ']}'
    assert is_degenerate_loop(text) is True


def test_detector_catches_observed_difficulty_reason_loop():
    # Reproduces the exp2_b.prediction failure: 181 copies of difficulty_reason.
    entry = ',\n      "difficulty_reason": "Requires a specific flag to be set, which is unlikely"'
    text = '{"hard_branches": [{"file": "x.cc", "line": 1' + entry * 200 + '}]}'
    assert is_degenerate_loop(text) is True


def test_detector_catches_near_duplicate_entries():
    # Reproduces the observed 200-near-duplicate-JSON-objects failure where
    # each entry differs only by an integer line number — no single 40-char
    # window dominates by coverage, but unique/total ratio collapses.
    entries = [
        f'{{"file": "re2/testing/exhaustive_tester.cc", "line": {800 + i}, '
        f'"condition": "randomstrings_", '
        f'"difficulty_reason": "requires specific magic bytes to be set", '
        f'"confidence": "HIGH"}}'
        for i in range(200)
    ]
    text = '{"hard_branches": [' + ",".join(entries) + "]}"
    assert is_degenerate_loop(text) is True


def test_detector_catches_base64_internal_loop():
    # Reproduces the exp1_b.synthesis failure: 1 input entry whose content_b64
    # is a 40-char fragment repeated hundreds of times.
    frag = "blJL1JL1WUlFqblJL1JL1WUlFqblJL1JL1WUlFqb"  # 40 chars
    text = '{"inputs": [{"content_b64": "' + frag * 100 + '"}]}'
    assert is_degenerate_loop(text) is True


def test_detector_threshold_tuning():
    # Build a text with exactly 5 non-overlapping copies of a distinctive fragment
    # embedded in genuinely diverse noise (each line unique).
    frag = "UNIQUE_FRAGMENT_40_CHARS_ABCDEFGHIJKLMN" + "\n"  # 40 chars incl. newline
    noise = "".join(f"line_{i} does distinct_work_{i*7} here\n" for i in range(400))
    text = noise + frag * 5 + noise
    # Default min_repeats=6 → 5 copies below threshold.
    assert is_degenerate_loop(text) is False
    # Lowering threshold to 3 catches it.
    assert is_degenerate_loop(text, min_repeats=3, min_coverage=0.001) is True


def test_parse_json_response_rejects_loop():
    from prediction.scripts.parse_response import parse_json_response
    entry = '\n    {"location": "x.c:LINE", "true_taken": false, "false_taken": false},'
    text = '{"branches": [' + entry * 300 + ']}'
    result, status = parse_json_response(text)
    assert result is None
    assert status == "parse_failure"


def test_parse_free_text_response_rejects_loop():
    from prediction.scripts.parse_response import parse_free_text_response
    text = "calls foo(), calls foo(), " * 300
    result, status = parse_free_text_response(text)
    assert result is None
    assert status == "parse_failure"


def test_parse_synthesis_rejects_loop():
    from synthesis.scripts.parse_synthesis import parse_synthesis_response
    frag = "blJL1JL1WUlFqblJL1JL1WUlFqblJL1JL1WUlFqb"
    text = '{"inputs": [{"content_b64": "' + frag * 100 + '"}]}'
    result, status = parse_synthesis_response(
        text, target="t", model="m", temperature=0.0, sample_index=0
    )
    assert result == []
    assert status == "parse_failure"


def test_parse_hard_branches_rejects_loop():
    from synthesis.scripts.run_source_prediction import _parse_hard_branches
    entry = ',\n      "difficulty_reason": "Requires a specific flag to be set"'
    text = '{"hard_branches": [{"file": "x.cc"' + entry * 200 + '}]}'
    branches, status = _parse_hard_branches(text)
    assert branches == []
    assert status == "parse_failure"


class _FakeChunk:
    def __init__(self, content=None, usage=None):
        delta = SimpleNamespace(content=content) if content else SimpleNamespace(content=None)
        self.choices = [SimpleNamespace(delta=delta)] if delta else []
        self.usage = usage


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.closed = False

    def __iter__(self):
        for c in self._chunks:
            if self.closed:
                return
            yield c

    def close(self):
        self.closed = True


class _FakeClient:
    def __init__(self, stream):
        self._stream = stream
        self.calls = 0

        class _Completions:
            def __init__(outer):
                outer._parent = self

            def create(outer, **kwargs):
                outer._parent.calls += 1
                assert kwargs.get("stream") is True
                return outer._parent._stream

        self.chat = SimpleNamespace(completions=_Completions())


def test_stream_with_loop_abort_terminates_early():
    from core.llm_client import _stream_with_loop_abort
    # 200 identical 50-char chunks — triggers the detector after ~2048 chars.
    loop_piece = ("ABCDEFGHIJ" * 5)
    chunks = [_FakeChunk(content=loop_piece) for _ in range(200)]
    stream = _FakeStream(chunks)
    client = _FakeClient(stream)

    text, inp, out = _stream_with_loop_abort(client, "m", [], 0.0, 1.0, 4096)
    # Abort must fire well before the 200th chunk (each is 50 chars).
    assert len(text) < 50 * 100, "should abort long before consuming all chunks"
    assert stream.closed is True
    # Fallback output_tokens estimation when usage unavailable.
    assert out >= 1


def test_stream_with_loop_abort_returns_full_text_when_no_loop():
    from core.llm_client import _stream_with_loop_abort
    chunks = [_FakeChunk(content=f"entry_{i} distinct content\n") for i in range(20)]
    stream = _FakeStream(chunks)
    client = _FakeClient(stream)

    text, inp, out = _stream_with_loop_abort(client, "m", [], 0.0, 1.0, 4096)
    assert stream.closed is False
    # Should have accumulated all 20 distinct chunks.
    for i in range(20):
        assert f"entry_{i}" in text
