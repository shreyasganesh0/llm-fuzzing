"""Tool-use multi-call dispatch tests (Phase 7).

Covers the iterative tool loop in
``synthesis.scripts.generate_ablation_inputs.run_ablation`` for
``strategy="tool_use"``.

All tests stay offline:
  - ``LLMClient.complete`` is monkeypatched to return canned responses;
    no network, no real cache writes.
  - ``run_ablation`` is invoked with a ``tmp_path`` results_root so no
    fixture seed directories are touched.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from core import llm_client as lc
from synthesis.scripts import generate_ablation_inputs as gai

REPO_ROOT = Path(__file__).resolve().parents[2]
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
                    "reasoning": "tool-use emitted final seed",
                }
            ]
        }
    )


def _make_response(
    content: str,
    *,
    tool_calls: list[dict] | None = None,
) -> lc.Response:
    return lc.Response(
        content=content,
        model="gpt-oss-20b",
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
        tool_calls=tool_calls,
    )


def _tool_call(
    *, call_id: str = "tc_0", name: str = "check_seed",
    arguments: dict | None = None,
) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments or {}),
        },
    }


@pytest.fixture
def hb_prep_root() -> Path:
    if not HB_PREP_ROOT.is_dir():
        pytest.skip(f"harfbuzz prep dataset missing at {HB_PREP_ROOT}")
    return HB_PREP_ROOT


@pytest.fixture
def patched_llm(monkeypatch):
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


def _run_tool_use(tmp_path: Path, prep_root: Path, *, model: str = "gpt-oss-20b") -> list:
    return gai.run_ablation(
        target="harfbuzz",
        model=model,
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
        strategy="tool_use",
    )


# --- tests -------------------------------------------------------------------


def test_tool_use_no_tool_call_returns_seed_immediately(
    hb_prep_root, patched_llm, tmp_path,
):
    """Model emits a final seed on turn 0 → exactly one call, seed parsed."""
    patched_llm["canned"].append(_make_response(_good_hb_payload(0x41)))

    records = _run_tool_use(tmp_path, hb_prep_root)

    assert len(patched_llm["log"]) == 1
    call = patched_llm["log"][0]
    # tools / tool_choice forwarded into complete().
    assert "tools" in call["kwargs"]
    assert call["kwargs"]["tool_choice"] == "auto"
    # Cache salt has the turn_0 round segment.
    assert call["kwargs"]["cache_salt"].endswith(
        ",strategy=tool_use,round=turn_0",
    )
    # Seed parsed successfully.
    assert len(records) == 1
    assert records[0].parse_status == "ok"
    expected_b64 = base64.b64encode(bytes([0x41] * 16)).decode()
    assert records[0].inputs[0].content_b64 == expected_b64


def test_tool_use_executes_oracle_and_loops(
    hb_prep_root, patched_llm, tmp_path,
):
    """Turn 0 asks to call check_seed; turn 1 emits a final seed.

    Asserts two calls, and that turn-1's messages include the tool
    response dict with the oracle's verdict JSON.
    """
    tool_call = _tool_call(
        call_id="tc_0",
        name="check_seed",
        arguments={"content_b64": base64.b64encode(
            b"\x00\x01\x00\x00" + b"\x00" * 12
        ).decode()},
    )
    patched_llm["canned"].append(
        _make_response(content="let me check", tool_calls=[tool_call]),
    )
    patched_llm["canned"].append(_make_response(_good_hb_payload(0x42)))

    records = _run_tool_use(tmp_path, hb_prep_root)

    assert len(patched_llm["log"]) == 2
    # Cache salts per turn are distinct.
    salt0 = patched_llm["log"][0]["kwargs"]["cache_salt"]
    salt1 = patched_llm["log"][1]["kwargs"]["cache_salt"]
    assert salt0.endswith(",strategy=tool_use,round=turn_0")
    assert salt1.endswith(",strategy=tool_use,round=turn_1")

    # Turn 1's messages must contain an assistant msg with tool_calls
    # and a tool-role message carrying the oracle verdict.
    turn1_messages = patched_llm["log"][1]["messages"]
    roles = [m.get("role") for m in turn1_messages]
    assert "assistant" in roles
    assert "tool" in roles
    tool_msgs = [m for m in turn1_messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    verdict = json.loads(tool_msgs[0]["content"])
    assert verdict["ok"] is True
    assert verdict["issues"] == []
    assert tool_msgs[0]["tool_call_id"] == "tc_0"

    # Final seed is the turn-1 output (0x42), not the turn-0 draft.
    expected_b64 = base64.b64encode(bytes([0x42] * 16)).decode()
    assert records[0].parse_status == "ok"
    assert records[0].inputs[0].content_b64 == expected_b64


def test_tool_use_raises_on_unsupported_model(
    hb_prep_root, patched_llm, tmp_path,
):
    """llama-3.1-8b-instruct has supports_tool_use=False → ValueError."""
    with pytest.raises(ValueError, match="tool_use"):
        _run_tool_use(tmp_path, hb_prep_root, model="llama-3.1-8b-instruct")
    # No LLM calls attempted.
    assert len(patched_llm["log"]) == 0


def test_tool_use_respects_max_turns(
    hb_prep_root, patched_llm, tmp_path,
):
    """Model asks to call check_seed on every turn → driver stops after
    max_tool_turns + 1 calls and gracefully returns parse_failure (or the
    last attempted parse) with tool_turns_used == max_tool_turns.
    """
    tool_call = _tool_call(
        call_id="tc_x",
        name="check_seed",
        arguments={"content_b64": base64.b64encode(b"short").decode()},
    )
    # 4 total canned responses: turn_0, turn_1, turn_2, turn_3.
    for i in range(4):
        patched_llm["canned"].append(
            _make_response(content=f"turn {i}", tool_calls=[
                {**tool_call, "id": f"tc_{i}"},
            ]),
        )

    records = _run_tool_use(tmp_path, hb_prep_root)

    # max_tool_turns=3 → 1 initial + 3 refinements = 4 calls.
    assert len(patched_llm["log"]) == 4
    # Never parsed a valid seed.
    assert len(records) == 1
    assert records[0].parse_status == "parse_failure"
    assert records[0].inputs == []
