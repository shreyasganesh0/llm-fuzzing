"""Self-critique multi-call dispatch tests (Phase 5).

Covers the draft+refine orchestration in
`synthesis.scripts.generate_ablation_inputs.run_ablation` for
``strategy="self_critique"``.

All tests stay offline:
  - `LLMClient.complete` is monkeypatched to return canned responses;
    no network, no real cache writes.
  - run_ablation is invoked with a `tmp_path` results_root so no fixture
    seed directories are touched.
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

import pytest

from core import llm_client as lc
from synthesis.scripts import generate_ablation_inputs as gai

# --- fixtures ----------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parents[2]
# Harfbuzz prep dataset is materialised in the repo fixtures; fall back
# to skip if missing.
HB_PREP_ROOT = REPO_ROOT / "dataset" / "fixtures" / "_ablation_hb_dataset"


def _good_hb_payload(seed_byte: int = 0x41) -> str:
    """A parseable harfbuzz JSON response with one 16-byte blob."""
    blob = bytes([seed_byte] * 16)
    b64 = base64.b64encode(blob).decode()
    return json.dumps(
        {
            "inputs": [
                {
                    "content_b64": b64,
                    "target_gaps": ["src/hb-ot-layout.cc:100"],
                    "reasoning": (
                        "Step 1 (Quote): if (version != 0x00010000). "
                        "Step 2 (Locate): sfnt header. Step 3 (Offset): 0x04. "
                        "Step 4 (Bytes): AAAA..."
                    ),
                }
            ]
        }
    )


def _garbage_payload() -> str:
    return "this is not JSON and it does not contain any seed objects"


def _make_response(content: str) -> lc.Response:
    return lc.Response(
        content=content,
        model="llama-3.1-8b-instruct",
        temperature=0.7,
        top_p=0.95,
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.0,
        latency_ms=10.0,
        prompt_hash="hash",
        timestamp="2026-04-21T00:00:00+00:00",
        generation_wall_clock_s=0.01,
        cached=False,
    )


@pytest.fixture
def hb_prep_root() -> Path:
    if not HB_PREP_ROOT.is_dir():
        pytest.skip(f"harfbuzz prep dataset missing at {HB_PREP_ROOT}")
    return HB_PREP_ROOT


@pytest.fixture
def patched_llm(monkeypatch):
    """Yield a list you can fill with canned responses.

    Each `LLMClient.complete` call pops the next canned response.
    The fixture also records the `messages` arg of every call so tests
    can assert what the refine prompt looked like.
    """
    call_log: list[dict] = []
    canned: list[lc.Response] = []

    def fake_complete(self, messages, **kwargs):  # noqa: ANN001
        call_log.append({"messages": messages, "kwargs": kwargs})
        if not canned:
            raise AssertionError("no canned responses left")
        return canned.pop(0)

    monkeypatch.setattr(lc.LLMClient, "complete", fake_complete)
    # Avoid real key lookups — LLMClient construction reads secrets/llm_key.
    monkeypatch.setattr(
        lc.LLMClient, "__init__",
        lambda self, **kwargs: setattr(self, "provider", "vllm")
        or setattr(self, "cache_dir", Path("/tmp/utcf_test_cache_ignored"))
        or setattr(self, "api_key", "fake")
        or setattr(self, "base_url", None)
        or setattr(self, "_client", None)
        or None,
    )
    return {"canned": canned, "log": call_log}


def _run_hb_self_critique(tmp_path: Path, prep_root: Path) -> list:
    return gai.run_ablation(
        target="harfbuzz",
        model="llama-3.1-8b-instruct",
        cell="v1_src",
        include_tests=False,
        include_gaps=False,
        include_source=False,
        dataset_root=prep_root,
        results_root=tmp_path,
        samples=1,
        num_inputs=1,
        source_max_files=40,
        source_token_budget=2000,
        max_tokens=2048,
        max_gaps=30,
        input_format="binary",
        run_id=99999,  # irrelevant — complete() is mocked
        strategy="self_critique",
    )


# --- tests -------------------------------------------------------------------


def test_self_critique_issues_two_calls(hb_prep_root, patched_llm, tmp_path):
    """Two parseable responses → driver calls LLM twice and uses the refine.

    Also asserts that the 2nd call's prompt contains the DRAFT UNDER REVIEW
    block (proof the refine template is actually rendered with the draft)
    and that response_format={"type":"json_object"} reaches complete()
    on both rounds for the llama-3.1-8b-instruct model (Phase 3 opt-in).
    """
    draft_b64 = base64.b64encode(bytes([0x41] * 16)).decode()
    refine_b64 = base64.b64encode(bytes([0x42] * 16)).decode()
    patched_llm["canned"].append(_make_response(_good_hb_payload(0x41)))
    patched_llm["canned"].append(_make_response(_good_hb_payload(0x42)))

    records = _run_hb_self_critique(tmp_path, hb_prep_root)

    assert len(patched_llm["log"]) == 2
    # Both calls must carry the JSON-object response_format (Phase 3 surface).
    for call in patched_llm["log"]:
        assert call["kwargs"].get("response_format") == {"type": "json_object"}

    # Round-1 prompt is the default binary template; round-2 must contain the
    # refine template's "=== DRAFT UNDER REVIEW ===" marker AND echo the draft.
    first_user = patched_llm["log"][0]["messages"][1]["content"]
    second_user = patched_llm["log"][1]["messages"][1]["content"]
    assert "=== DRAFT UNDER REVIEW ===" not in first_user
    assert "=== DRAFT UNDER REVIEW ===" in second_user
    assert "=== CRITIQUE TASK ===" in second_user
    assert draft_b64 in second_user

    # Cache salts differ via the `round=` segment.
    draft_salt = patched_llm["log"][0]["kwargs"]["cache_salt"]
    refine_salt = patched_llm["log"][1]["kwargs"]["cache_salt"]
    assert draft_salt.endswith(",strategy=self_critique,round=draft")
    assert refine_salt.endswith(",strategy=self_critique,round=refine")

    # Final record carries the REFINE input (0x42 blob), not the draft.
    assert len(records) == 1
    assert records[0].parse_status == "ok"
    assert records[0].inputs[0].content_b64 == refine_b64


def test_self_critique_falls_back_on_refine_parse_failure(
    hb_prep_root, patched_llm, tmp_path, caplog,
):
    """Good draft, garbage refine → driver returns the DRAFT parse result.

    Also emits a log line with structured field self_critique_fallback=True.
    """
    draft_b64 = base64.b64encode(bytes([0x41] * 16)).decode()
    patched_llm["canned"].append(_make_response(_good_hb_payload(0x41)))
    patched_llm["canned"].append(_make_response(_garbage_payload()))

    with caplog.at_level(logging.INFO, logger="utcf.ablation.synthesis"):
        records = _run_hb_self_critique(tmp_path, hb_prep_root)

    assert len(patched_llm["log"]) == 2
    assert len(records) == 1
    assert records[0].parse_status == "ok"
    # We fell back to the draft's 0x41 blob, not the garbage refine.
    assert records[0].inputs[0].content_b64 == draft_b64

    fallback_records = [
        r for r in caplog.records
        if getattr(r, "self_critique_fallback", False) is True
    ]
    assert len(fallback_records) == 1, (
        f"expected exactly one self_critique_fallback=True log entry; got "
        f"{len(fallback_records)}"
    )


def test_self_critique_draft_parse_failure_propagates(
    hb_prep_root, patched_llm, tmp_path, caplog,
):
    """Garbage draft → driver stops, no refine call, returns parse_failure."""
    patched_llm["canned"].append(_make_response(_garbage_payload()))
    # Deliberately do NOT queue a refine response — the driver must not
    # make the second call.

    with caplog.at_level(logging.INFO, logger="utcf.ablation.synthesis"):
        records = _run_hb_self_critique(tmp_path, hb_prep_root)

    assert len(patched_llm["log"]) == 1, (
        f"expected exactly one LLM call (draft only) when the draft fails to "
        f"parse; saw {len(patched_llm['log'])}"
    )
    assert len(records) == 1
    assert records[0].parse_status == "parse_failure"
    assert records[0].inputs == []

    # No fallback log entry — fallback only fires when a good draft is
    # superseded by a bad refine.
    fallback_records = [
        r for r in caplog.records
        if getattr(r, "self_critique_fallback", False) is True
    ]
    assert fallback_records == []
