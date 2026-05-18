"""Unit tests for analysis/scripts/experiment4_entropy.py.

All fixtures are hand-built synthetic token streams — no network, no LLM,
no LLVM. The goal is to pin the load-bearing METHODS.md §3 masking rule
and the §4 entropy formula to exact, re-readable expectations.

Token-stream convention used by these fixtures: every token is a literal
substring of the intended completion EXCEPT that a leading space is encoded
SentencePiece-style as U+2581 (``▁``) prefixed onto the token (matching
STAGE0_RESULT.md). The helper ``_record`` builds one logprob record.
"""
from __future__ import annotations

import json
import math

from analysis.scripts.experiment4_entropy import (
    STATUS_DROP_B64_UNLOCATABLE,
    STATUS_DROP_NO_PAYLOAD_TOKENS,
    STATUS_DROP_RECONSTRUCTION_MISMATCH,
    STATUS_OK,
    detokenize,
    mean_payload_entropy,
    payload_token_indices,
    per_seed_entropies,
    reconstruct,
    token_entropy_bits,
)

SP = "▁"  # ▁  SentencePiece leading-space marker


def _record(token: str, logprob: float = -0.1, alts: list[tuple[str, float]] | None = None) -> dict:
    """Build one logprobs.content record.

    By default the chosen token is also placed inside its own top_logprobs
    (mirrors STAGE0_RESULT.md: the chosen token appears in its own list).
    """
    top = [{"token": token, "logprob": logprob}]
    if alts:
        top.extend({"token": t, "logprob": lp} for t, lp in alts)
    return {"token": token, "logprob": logprob, "top_logprobs": top}


# --------------------------------------------------------------------------- #
# detokenize
# --------------------------------------------------------------------------- #


def test_detokenize_leading_marker_becomes_space():
    assert detokenize(SP + '{"') == ' {"'


def test_detokenize_no_marker_is_identity():
    assert detokenize("ABCD") == "ABCD"


def test_detokenize_internal_marker_also_maps_to_space():
    # Rare, but the rule says other ▁ occurrences likewise map to a space.
    assert detokenize("a" + SP + "b") == "a b"


def test_detokenize_empty_token():
    assert detokenize("") == ""


# --------------------------------------------------------------------------- #
# reconstruct: round-trip + contiguous, correct spans
# --------------------------------------------------------------------------- #


def test_reconstruct_roundtrips_and_spans_are_contiguous():
    tokens = [SP + "Hello", ",", SP + "world", "!"]
    content = [_record(t) for t in tokens]
    full_text, spans = reconstruct(content)

    assert full_text == " Hello, world!"
    # Spans must be contiguous, start at 0, end at len(full_text), and each
    # slice must equal the detokenised token.
    assert spans[0][0] == 0
    assert spans[-1][1] == len(full_text)
    for i in range(len(spans) - 1):
        assert spans[i][1] == spans[i + 1][0]
    assert full_text[spans[0][0] : spans[0][1]] == " Hello"
    assert full_text[spans[1][0] : spans[1][1]] == ","
    assert full_text[spans[2][0] : spans[2][1]] == " world"
    assert full_text[spans[3][0] : spans[3][1]] == "!"


def test_reconstruct_zero_width_token_gets_empty_span():
    content = [_record(SP + "A"), _record(""), _record("B")]
    full_text, spans = reconstruct(content)
    assert full_text == " AB"
    assert spans[1][0] == spans[1][1]  # zero-width
    assert spans[1][1] == spans[2][0]  # does not advance the cursor


# --------------------------------------------------------------------------- #
# payload_token_indices: clean 1-input response
# --------------------------------------------------------------------------- #


def _clean_single_input_stream() -> tuple[list[dict], str, str]:
    """A clean 1-input JSON response tokenised so the base64 value is its
    own set of tokens, with the surrounding key / quotes / punctuation /
    reasoning as separate structural tokens.

    Completion text (no leading space):
        {"inputs":[{"content_b64":"QUJDRA==","reasoning":"hi"}]}
    """
    b64 = "QUJDRA=="
    tokens = [
        '{"inputs":[{"content_b64":',  # structural prefix incl. key + colon
        '"',  # opening quote of the b64 value  (structural)
        "QUJD",  # payload token 1
        "RA==",  # payload token 2
        '"',  # closing quote of the b64 value  (structural)
        ',"reasoning":"hi"}]}',  # structural suffix incl. reasoning
    ]
    content = [_record(t) for t in tokens]
    full_text = "".join(tokens)
    return content, full_text, b64


def test_payload_tokens_clean_single_input():
    content, full_text, b64 = _clean_single_input_stream()
    _ft, spans = reconstruct(content)
    assert _ft == full_text

    idx = payload_token_indices(full_text, spans, full_text, b64, 0)
    # Exactly the two base64-value tokens (indices 2 and 3). The opening
    # quote (1), closing quote (4), key+colon prefix (0) and the
    # reasoning-bearing suffix (5) are all excluded.
    assert idx == [2, 3]


# --------------------------------------------------------------------------- #
# boundary token straddling the closing quote is EXCLUDED
# --------------------------------------------------------------------------- #


def test_boundary_token_straddling_closing_quote_excluded():
    # The model tokenises the tail of the b64 value together with the
    # closing quote: ...RA==" is one token. Per METHODS §3 that token is
    # excluded (only tokens fully inside (open_quote, close_quote) count).
    b64 = "QUJDRA=="
    tokens = [
        '{"inputs":[{"content_b64":"',  # ends WITH the opening quote
        "QUJD",  # pure payload token  -> included
        'RA=="',  # base64 tail + closing quote (straddles) -> excluded
        ',"reasoning":"x"}]}',
    ]
    content = [_record(t) for t in tokens]
    full_text = "".join(tokens)
    _ft, spans = reconstruct(content)
    assert _ft == full_text

    idx = payload_token_indices(full_text, spans, full_text, b64, 0)
    assert idx == [1]  # only the pure-base64 token, NOT the straddling tail


def test_boundary_token_straddling_opening_quote_excluded():
    # Symmetric case: opening quote glued onto the first base64 chars.
    b64 = "QUJDRA=="
    tokens = [
        '{"inputs":[{"content_b64":',
        '"QUJD',  # opening quote + base64 head (straddles open quote) -> excluded
        "RA==",  # pure payload token -> included
        '"',  # closing quote (structural)
        ',"reasoning":"x"}]}',
    ]
    content = [_record(t) for t in tokens]
    full_text = "".join(tokens)
    _ft, spans = reconstruct(content)
    assert _ft == full_text

    idx = payload_token_indices(full_text, spans, full_text, b64, 0)
    assert idx == [2]


# --------------------------------------------------------------------------- #
# multi-input: positional (not first-match) occurrence selection
# --------------------------------------------------------------------------- #


def test_multi_input_positional_region_selection():
    # Structural rule (METHODS §3, 2026-05-18): input_index_in_response is
    # the seed's POSITIONAL index among parsed inputs and maps to the i-th
    # `content_b64` JSON value REGION — NOT a string match on content_b64
    # (which is a parser artifact). Three regions in one response; the
    # content_b64 argument is irrelevant to selection (pass a dummy).
    DUMMY = "ignored-by-structural-location"
    tokens = [
        '{"inputs":[{"content_b64":"',
        "QUJD",  # 1: region 0 payload
        "RA==",  # 2: region 0 payload
        '","reasoning":"r0"},{"content_b64":"',  # 3
        "WFla",  # 4: region 1 payload
        "Wg==",  # 5: region 1 payload
        '","reasoning":"r1"},{"content_b64":"',  # 6
        "QUJD",  # 7: region 2 payload
        "RA==",  # 8: region 2 payload
        '","reasoning":"r2"}]}',  # 9
    ]
    content = [_record(t) for t in tokens]
    full_text = "".join(tokens)
    _ft, spans = reconstruct(content)
    assert _ft == full_text

    # Seed 0 -> 0th content_b64 region.
    assert payload_token_indices(full_text, spans, full_text, DUMMY, 0) == [1, 2]
    # Seed 1 -> 1st region (positional, independent of payload identity).
    assert payload_token_indices(full_text, spans, full_text, DUMMY, 1) == [4, 5]
    # Seed 2 -> 2nd region.
    assert payload_token_indices(full_text, spans, full_text, DUMMY, 2) == [7, 8]
    # No 4th region -> unlocatable disclosure.
    assert payload_token_indices(full_text, spans, full_text, DUMMY, 3) is None


# --------------------------------------------------------------------------- #
# token_entropy_bits: near-deterministic ~0, uniform K-way ~log2(K)
# --------------------------------------------------------------------------- #


def test_token_entropy_near_deterministic_is_near_zero():
    # One alternative with prob ~1, others vanishingly small.
    top = [
        {"token": "A", "logprob": math.log(0.9999)},
        {"token": "B", "logprob": math.log(1e-9)},
        {"token": "C", "logprob": math.log(1e-9)},
    ]
    h = token_entropy_bits(top, "A", math.log(0.9999))
    assert h is not None
    assert math.isclose(h, 0.0, abs_tol=1e-4)


def test_token_entropy_uniform_k_way_is_log2_k():
    for k in (2, 4, 8, 20):
        # k entries all with the same logprob -> uniform after renormalise.
        lp = math.log(1.0 / k)
        top = [{"token": f"t{i}", "logprob": lp} for i in range(k)]
        h = token_entropy_bits(top, "t0", lp)
        assert h is not None
        assert math.isclose(h, math.log2(k), rel_tol=1e-9, abs_tol=1e-9)


def test_token_entropy_prepends_observed_when_absent():
    # Observed token NOT in the list -> it must be prepended (changes H).
    top = [
        {"token": "B", "logprob": math.log(0.5)},
        {"token": "C", "logprob": math.log(0.5)},
    ]
    # Without prepend: uniform 2-way -> 1 bit. With observed "A" prob 0.5
    # prepended: three entries 0.5/0.5/0.5 -> uniform 3-way -> log2(3).
    h = token_entropy_bits(top, "A", math.log(0.5))
    assert h is not None
    assert math.isclose(h, math.log2(3), rel_tol=1e-9)


def test_token_entropy_does_not_double_count_present_observed():
    # Observed token IS already present -> must NOT be prepended again.
    top = [
        {"token": "A", "logprob": math.log(0.5)},
        {"token": "B", "logprob": math.log(0.5)},
    ]
    h = token_entropy_bits(top, "A", math.log(0.5))
    assert h is not None
    assert math.isclose(h, 1.0, rel_tol=1e-9)  # clean 2-way, not 3-way


def test_token_entropy_empty_top_logprobs_prepends_observed():
    # METHODS §4 / input contract: the observed (token, logprob) is
    # prepended when absent BEFORE the "empty head" check. So an empty
    # top_logprobs list still yields a 1-entry head (the observed token) ->
    # a degenerate single-outcome distribution -> H == 0 bits, NOT None.
    # ("Return None if the head is empty" applies to the head *after* the
    # prepend step; with a valid observed token the head is never empty.)
    h = token_entropy_bits([], "A", math.log(0.7))
    assert h is not None
    assert math.isclose(h, 0.0, abs_tol=1e-12)


def test_token_entropy_all_neg_inf_head_returns_none():
    # Degenerate head where every probability underflows to 0 (logprob
    # -inf). Sum of probs is 0 -> no information to estimate entropy from
    # -> None (we surface this rather than fabricating H == 0).
    top = [
        {"token": "A", "logprob": float("-inf")},
        {"token": "B", "logprob": float("-inf")},
    ]
    assert token_entropy_bits(top, "A", float("-inf")) is None


# --------------------------------------------------------------------------- #
# mean_payload_entropy: ok + every drop status
# --------------------------------------------------------------------------- #


def _sidecar(content: list[dict], raw_response: str, b64: str, occ: int) -> dict:
    return {
        "input_id": "0123456789abcdef",
        "variant": "v1_src",
        "content_b64": b64,
        "raw_response": raw_response,
        "input_index_in_response": occ,
        "logprobs": {"content": content},
    }


def test_mean_payload_entropy_ok():
    # Two payload tokens, each a clean uniform 4-way head -> H == 2 bits each
    # -> mean == 2.0 bits.
    lp4 = math.log(0.25)

    def rec4(tok: str) -> dict:
        top = [{"token": tok, "logprob": lp4}] + [
            {"token": f"alt{i}", "logprob": lp4} for i in range(3)
        ]
        return {"token": tok, "logprob": lp4, "top_logprobs": top}

    tokens = ['{"inputs":[{"content_b64":"', "QUJD", "RA==", '","reasoning":"r"}]}']
    recs = [rec4(t) if t in ("QUJD", "RA==") else _record(t) for t in tokens]
    full_text = "".join(tokens)
    sc = _sidecar(recs, full_text, "QUJDRA==", 0)

    value, status = mean_payload_entropy(sc)
    assert status == STATUS_OK
    assert value is not None
    assert math.isclose(value, 2.0, rel_tol=1e-9)


def test_mean_payload_entropy_reconstruction_mismatch():
    # raw_response deliberately disagrees with the reconstructed text.
    content = [_record("AB"), _record("CD")]
    sc = _sidecar(content, "TOTALLY DIFFERENT", "QUJD", 0)
    value, status = mean_payload_entropy(sc)
    assert value is None
    assert status == STATUS_DROP_RECONSTRUCTION_MISMATCH


def test_mean_payload_entropy_b64_unlocatable():
    # Structural rule: "unlocatable" now means there are fewer than
    # input_index_in_response + 1 content_b64 regions in the response.
    # Here there is exactly ONE region but the seed claims positional
    # index 1 (no 2nd region) -> disclosed drop. (The 2 v3_all
    # seeds<regions responses in the real pool hit exactly this path.)
    tokens = ['{"inputs":[{"content_b64":"', "QUJD", '"}]}']
    content = [_record(t) for t in tokens]
    full_text = "".join(tokens)
    sc = _sidecar(content, full_text, "QUJD", 1)  # asks for region 1; none
    value, status = mean_payload_entropy(sc)
    assert value is None
    assert status == STATUS_DROP_B64_UNLOCATABLE


def test_mean_payload_entropy_no_payload_tokens():
    # The whole b64 value is glued to BOTH quotes in a single token, so no
    # token lies strictly inside (open_quote, close_quote).
    tokens = ['{"inputs":[{"content_b64":', '"QUJD"', ',"reasoning":"r"}]}']
    content = [_record(t) for t in tokens]
    full_text = "".join(tokens)
    sc = _sidecar(content, full_text, "QUJD", 0)
    value, status = mean_payload_entropy(sc)
    assert value is None
    assert status == STATUS_DROP_NO_PAYLOAD_TOKENS


# --------------------------------------------------------------------------- #
# per_seed_entropies: aggregation + drop counting + determinism
# --------------------------------------------------------------------------- #


def test_per_seed_entropies_aggregates_and_counts(tmp_path):
    lp4 = math.log(0.25)

    def rec4(tok: str) -> dict:
        top = [{"token": tok, "logprob": lp4}] + [
            {"token": f"alt{i}", "logprob": lp4} for i in range(3)
        ]
        return {"token": tok, "logprob": lp4, "top_logprobs": top}

    # Sidecar 1: OK, mean entropy == 2.0 bits.
    tokens_ok = ['{"inputs":[{"content_b64":"', "QUJD", "RA==", '","reasoning":"r"}]}']
    recs_ok = [rec4(t) if t in ("QUJD", "RA==") else _record(t) for t in tokens_ok]
    sc_ok = _sidecar(recs_ok, "".join(tokens_ok), "QUJDRA==", 0)
    sc_ok["input_id"] = "aaaaaaaaaaaaaaaa"

    # Sidecar 2: reconstruction mismatch -> dropped.
    sc_bad = _sidecar([_record("AB")], "MISMATCH", "QUJD", 0)
    sc_bad["input_id"] = "bbbbbbbbbbbbbbbb"

    # Sidecar 3: unlocatable -> dropped. One region, but the seed claims
    # positional index 1 (no 2nd content_b64 region) -> structural drop.
    tokens_u = ['{"inputs":[{"content_b64":"', "QUJD", '"}]}']
    sc_unloc = _sidecar([_record(t) for t in tokens_u], "".join(tokens_u), "QUJD", 1)
    sc_unloc["input_id"] = "cccccccccccccccc"

    for name, sc in (
        ("01_ok.json", sc_ok),
        ("02_bad.json", sc_bad),
        ("03_unloc.json", sc_unloc),
    ):
        (tmp_path / name).write_text(json.dumps(sc), encoding="utf-8")
    # A non-JSON file must be ignored by the *.json glob.
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")

    result = per_seed_entropies(tmp_path)

    assert result["n_total"] == 3
    assert result["n_ok"] == 1
    assert set(result["entropies"]) == {"aaaaaaaaaaaaaaaa"}
    assert math.isclose(result["entropies"]["aaaaaaaaaaaaaaaa"], 2.0, rel_tol=1e-9)
    assert result["dropped"]["bbbbbbbbbbbbbbbb"] == STATUS_DROP_RECONSTRUCTION_MISMATCH
    assert result["dropped"]["cccccccccccccccc"] == STATUS_DROP_B64_UNLOCATABLE


def test_per_seed_entropies_is_filename_sorted_deterministic(tmp_path):
    # Two valid sidecars written out of order; result must not depend on
    # filesystem iteration order (we just assert both land + counts).
    lp = math.log(0.5)

    def rec2(tok: str) -> dict:
        top = [{"token": tok, "logprob": lp}, {"token": "x", "logprob": lp}]
        return {"token": tok, "logprob": lp, "top_logprobs": top}

    def make(b64_token: str, iid: str) -> dict:
        tokens = ['{"inputs":[{"content_b64":"', b64_token, '"}]}']
        recs = [rec2(t) if t == b64_token else _record(t) for t in tokens]
        sc = _sidecar(recs, "".join(tokens), b64_token, 0)
        sc["input_id"] = iid
        return sc

    (tmp_path / "z_last.json").write_text(
        json.dumps(make("QUJD", "1111111111111111")), encoding="utf-8"
    )
    (tmp_path / "a_first.json").write_text(
        json.dumps(make("WFla", "2222222222222222")), encoding="utf-8"
    )

    result = per_seed_entropies(tmp_path)
    assert result["n_total"] == 2
    assert result["n_ok"] == 2
    assert set(result["entropies"]) == {"1111111111111111", "2222222222222222"}
    for v in result["entropies"].values():
        assert math.isclose(v, 1.0, rel_tol=1e-9)  # clean 2-way head -> 1 bit


# ---------------------------------------------------------------------------
# detokenize: byte-fallback + special tokens (instrument refinement
# 2026-05-18 — see EXECUTION_LOG.md). Verified to reproduce real
# codestral-22b raw_response exactly.
# ---------------------------------------------------------------------------


def test_detokenize_byte_fallback_newline():
    assert detokenize("<0x0A>") == "\n"
    assert detokenize("<0x09>") == "\t"
    assert detokenize("<0x7E>") == "~"


def test_detokenize_byte_fallback_multibyte_is_replacement_char():
    # >=0x80 fragment -> U+FFFD so reconstruction != raw_response and the
    # response is dropped+counted (never silently misaligned).
    assert detokenize("<0x80>") == "�"
    assert detokenize("<0xC3>") == "�"


def test_detokenize_special_tokens_are_empty():
    for t in ("</s>", "<s>", "<unk>", "<pad>", "<|im_end|>", "<|endoftext|>"):
        assert detokenize(t) == ""


def test_detokenize_byte_fallback_is_not_confused_with_payload():
    # A literal-looking token that is NOT the <0xHH> shape passes through.
    assert detokenize("0x0A") == "0x0A"
    assert detokenize("<0xZZ>") == "<0xZZ>"


def test_reconstruct_mixed_sentencepiece_bytefallback_special():
    # Mirrors the real shape: ' {\n  "x"' then EOS (zero text).
    content = [
        {"token": "▁{"},      # ' {'
        {"token": "<0x0A>"},       # '\n'
        {"token": "▁▁"},  # '  '
        {"token": '"x"'},          # '"x"'
        {"token": "</s>"},          # ''
    ]
    full_text, spans = reconstruct(content)
    assert full_text == ' {\n  "x"'
    assert spans[0] == (0, 2)
    assert spans[1] == (2, 3)
    assert spans[4] == (len(full_text), len(full_text))  # zero-width EOS
