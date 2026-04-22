"""Prompt-chain (Phase 6) multi-call dispatch tests.

Covers the plan+sketch+finalize orchestration in
`synthesis.scripts.generate_ablation_inputs.run_ablation` for
``strategy="prompt_chain"``.

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
HB_PREP_ROOT = REPO_ROOT / "dataset" / "fixtures" / "_ablation_hb_dataset"


def _good_plan_payload(
    *, target_gap: str = "src/hb-ot-layout.cc:100",
    plan: str = "Craft a 16-byte sfnt stub with TrueType magic to reach the cmap parser.",
) -> str:
    return json.dumps({"plan": plan, "target_gap": target_gap})


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
                    "reasoning": "sfnt header with TrueType magic triggers the cmap parser path.",
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

    Each `LLMClient.complete` call pops the next canned response. The
    fixture also records the `messages` and `kwargs` of every call so
    tests can assert prompt shape and response_format.
    """
    call_log: list[dict] = []
    canned: list[lc.Response] = []

    def fake_complete(self, messages, **kwargs):  # noqa: ANN001
        call_log.append({"messages": messages, "kwargs": kwargs})
        if not canned:
            raise AssertionError("no canned responses left")
        return canned.pop(0)

    monkeypatch.setattr(lc.LLMClient, "complete", fake_complete)
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


def _run_hb_prompt_chain(tmp_path: Path, prep_root: Path) -> list:
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
        run_id=99999,
        strategy="prompt_chain",
    )


# --- tests -------------------------------------------------------------------


def test_prompt_chain_issues_three_calls(hb_prep_root, patched_llm, tmp_path):
    """Plan + sketch + finalize all parse → 3 LLM calls, final used.

    Round-2 prompt must carry the committed plan under the COMMITTED
    header; round-3 prompt must carry the sketch under the SKETCH UNDER
    REVIEW header. Cache salts must carry distinct round=<X> segments.
    """
    sketch_b64 = base64.b64encode(bytes([0x41] * 16)).decode()
    finalize_b64 = base64.b64encode(bytes([0x42] * 16)).decode()
    patched_llm["canned"].append(_make_response(_good_plan_payload()))
    patched_llm["canned"].append(_make_response(_good_hb_payload(0x41)))
    patched_llm["canned"].append(_make_response(_good_hb_payload(0x42)))

    records = _run_hb_prompt_chain(tmp_path, hb_prep_root)

    assert len(patched_llm["log"]) == 3

    plan_user = patched_llm["log"][0]["messages"][1]["content"]
    sketch_user = patched_llm["log"][1]["messages"][1]["content"]
    finalize_user = patched_llm["log"][2]["messages"][1]["content"]

    # Plan prompt does NOT have the committed/sketch headers.
    assert "ATTACK PLAN (COMMITTED)" not in plan_user
    assert "SKETCH UNDER REVIEW" not in plan_user
    # Sketch prompt echoes the plan under the COMMITTED header.
    assert "=== ATTACK PLAN (COMMITTED) ===" in sketch_user
    assert "Craft a 16-byte sfnt stub" in sketch_user
    # Finalize prompt carries both the plan AND the sketch bytes.
    assert "=== SKETCH UNDER REVIEW ===" in finalize_user
    assert sketch_b64 in finalize_user

    # Cache salts differ by round=<X>.
    plan_salt = patched_llm["log"][0]["kwargs"]["cache_salt"]
    sketch_salt = patched_llm["log"][1]["kwargs"]["cache_salt"]
    finalize_salt = patched_llm["log"][2]["kwargs"]["cache_salt"]
    assert plan_salt.endswith(",strategy=prompt_chain,round=plan")
    assert sketch_salt.endswith(",strategy=prompt_chain,round=sketch")
    assert finalize_salt.endswith(",strategy=prompt_chain,round=finalize")

    # Final record carries the FINALIZE input (0x42 blob), not the sketch.
    assert len(records) == 1
    assert records[0].parse_status == "ok"
    assert records[0].inputs[0].content_b64 == finalize_b64


def test_prompt_chain_response_format_on_all_rounds(
    hb_prep_root, patched_llm, tmp_path,
):
    """Every one of the 3 calls carries response_format={"type":"json_object"}
    for a model whose ``ModelDefaults.supports_json_object`` is True
    (the llama-3.1-8b-instruct UF LiteLLM model qualifies).
    """
    patched_llm["canned"].append(_make_response(_good_plan_payload()))
    patched_llm["canned"].append(_make_response(_good_hb_payload(0x41)))
    patched_llm["canned"].append(_make_response(_good_hb_payload(0x42)))

    _run_hb_prompt_chain(tmp_path, hb_prep_root)

    assert len(patched_llm["log"]) == 3
    for call in patched_llm["log"]:
        assert call["kwargs"].get("response_format") == {"type": "json_object"}


def test_prompt_chain_falls_back_on_finalize_parse_failure(
    hb_prep_root, patched_llm, tmp_path, caplog,
):
    """Good plan + good sketch + garbage finalize → driver returns the
    SKETCH parse result and emits a log line with
    prompt_chain_fallback=True, fallback_stage="finalize".
    """
    sketch_b64 = base64.b64encode(bytes([0x41] * 16)).decode()
    patched_llm["canned"].append(_make_response(_good_plan_payload()))
    patched_llm["canned"].append(_make_response(_good_hb_payload(0x41)))
    patched_llm["canned"].append(_make_response(_garbage_payload()))

    with caplog.at_level(logging.INFO, logger="utcf.ablation.synthesis"):
        records = _run_hb_prompt_chain(tmp_path, hb_prep_root)

    assert len(patched_llm["log"]) == 3
    assert len(records) == 1
    assert records[0].parse_status == "ok"
    # Fell back to the sketch's 0x41 blob, not the garbage finalize.
    assert records[0].inputs[0].content_b64 == sketch_b64

    fallback_records = [
        r for r in caplog.records
        if getattr(r, "prompt_chain_fallback", False) is True
    ]
    assert len(fallback_records) == 1, (
        f"expected exactly one prompt_chain_fallback=True log entry; got "
        f"{len(fallback_records)}"
    )
    assert getattr(fallback_records[0], "fallback_stage", None) == "finalize"


def test_prompt_chain_short_circuits_on_sketch_failure(
    hb_prep_root, patched_llm, tmp_path, caplog,
):
    """Good plan + garbage sketch → driver stops after 2 calls, no
    finalize attempt, parse_failure propagates to the orchestrator.
    """
    patched_llm["canned"].append(_make_response(_good_plan_payload()))
    patched_llm["canned"].append(_make_response(_garbage_payload()))
    # Deliberately no finalize response queued — driver must not call it.

    with caplog.at_level(logging.INFO, logger="utcf.ablation.synthesis"):
        records = _run_hb_prompt_chain(tmp_path, hb_prep_root)

    assert len(patched_llm["log"]) == 2, (
        f"expected exactly two LLM calls (plan+sketch) when the sketch "
        f"fails to parse; saw {len(patched_llm['log'])}"
    )
    assert len(records) == 1
    assert records[0].parse_status == "parse_failure"
    assert records[0].inputs == []

    # No fallback log entry — fallback only fires when finalize drops
    # a good sketch; sketch failure is a short-circuit, not a fallback.
    fallback_records = [
        r for r in caplog.records
        if getattr(r, "prompt_chain_fallback", False) is True
    ]
    assert fallback_records == []
