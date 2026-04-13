"""LLMClient provider-routing + throttle tests (no network).

Covers:
  - UTCF_LITELLM_URL selects the vllm (OpenAI-compatible) code path.
  - Explicit provider="vllm" works without a secrets file.
  - UTCF_LLM_RPM enforces a minimum interval between outgoing calls.

Uses monkeypatch to replace the OpenAI SDK and the real secrets loader so
no network traffic or filesystem secrets are required.
"""
from __future__ import annotations

import time

import pytest

from core import llm_client as lc


@pytest.fixture(autouse=True)
def _reset_globals(monkeypatch):
    monkeypatch.setattr(lc, "_last_call_monotonic", 0.0, raising=False)
    monkeypatch.delenv("UTCF_LITELLM_URL", raising=False)
    monkeypatch.delenv("UTCF_VLLM_URL", raising=False)
    monkeypatch.delenv("UTCF_LLM_RPM", raising=False)


def test_litellm_url_routes_through_vllm(monkeypatch, tmp_path):
    key_path = tmp_path / "llm_key"
    key_path.write_text("sk-proj-realkey-for-litellm")
    monkeypatch.setenv("UTCF_LITELLM_URL", "https://api.example.edu")

    client = lc.LLMClient(secrets_path=key_path)
    assert client.provider == "vllm"
    assert client.base_url == "https://api.example.edu"
    assert client.api_key == "sk-proj-realkey-for-litellm"


def test_explicit_vllm_allows_missing_secret(monkeypatch, tmp_path):
    # Pass a path that does not exist; provider=vllm should tolerate absence.
    client = lc.LLMClient(
        provider="vllm",
        base_url="http://localhost:8000/v1",
        secrets_path=tmp_path / "missing_key",
    )
    assert client.provider == "vllm"
    assert client.base_url == "http://localhost:8000/v1"
    assert client.api_key == "EMPTY"


def test_openai_key_still_routes_to_openai(monkeypatch, tmp_path):
    key_path = tmp_path / "llm_key"
    key_path.write_text("sk-proj-openai-key")
    client = lc.LLMClient(secrets_path=key_path)
    assert client.provider == "openai"
    assert client.base_url is None


def test_rpm_throttle_enforces_min_interval(monkeypatch):
    monkeypatch.setenv("UTCF_LLM_RPM", "120")  # 500ms min interval
    sleeps: list[float] = []
    monkeypatch.setattr(lc.time, "sleep", lambda s: sleeps.append(s))

    # Start the mocked clock well past 0 so the first call's delta is large
    # and does not trigger a sleep against the 0.0 init sentinel.
    clock = [100.0]
    monkeypatch.setattr(lc.time, "monotonic", lambda: clock[0])

    # First call: delta vs 0.0 sentinel is huge → no sleep.
    lc._throttle_if_needed()
    assert sleeps == []

    # Second call 100ms later: should sleep ~400ms to hit the 500ms budget.
    clock[0] = 100.1
    lc._throttle_if_needed()
    assert len(sleeps) == 1
    assert pytest.approx(sleeps[0], abs=0.01) == 0.4


def test_rpm_throttle_disabled_by_default(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(lc.time, "sleep", lambda s: sleeps.append(s))
    lc._throttle_if_needed()
    lc._throttle_if_needed()
    assert sleeps == []


def test_rpm_throttle_ignores_bad_env(monkeypatch):
    monkeypatch.setenv("UTCF_LLM_RPM", "not-a-number")
    sleeps: list[float] = []
    monkeypatch.setattr(lc.time, "sleep", lambda s: sleeps.append(s))
    lc._throttle_if_needed()
    assert sleeps == []


def test_cost_table_includes_litellm_models():
    for model in [
        "llama-3.1-8b-instruct",
        "gpt-oss-20b",
        "mistral-small-3.1",
        "codestral-22b",
    ]:
        assert model in lc.PRICING_USD_PER_MTOK, model
        assert lc.PRICING_USD_PER_MTOK[model]["input"] > 0


def test_estimate_cost_returns_zero_for_unknown_model():
    assert lc._estimate_cost("no-such-model", 1000, 500) == 0.0


def test_estimate_cost_computes_from_pricing_table():
    # llama-3.1-8b-instruct = 0.22 in / 0.22 out per 1M tokens.
    cost = lc._estimate_cost("llama-3.1-8b-instruct", 1_000_000, 0)
    assert pytest.approx(cost, abs=1e-9) == 0.22


def test_last_call_monotonic_updates(monkeypatch):
    monkeypatch.setenv("UTCF_LLM_RPM", "60")
    monkeypatch.setattr(lc.time, "sleep", lambda _s: None)
    clock = [100.0]
    monkeypatch.setattr(lc.time, "monotonic", lambda: clock[0])
    lc._throttle_if_needed()
    assert lc._last_call_monotonic == 100.0
    # Wait a real millisecond to avoid zero-duration ambiguity if we ever
    # revert to wall-clock time (defensive, not load-bearing).
    time.sleep(0.001)


def test_prompt_hash_includes_max_tokens():
    msgs = [{"role": "user", "content": "hi"}]
    h_short = lc._prompt_hash("m", msgs, 0.0, 1.0, 512)
    h_long = lc._prompt_hash("m", msgs, 0.0, 1.0, 8192)
    assert h_short != h_long, "max_tokens must affect cache key to prevent truncation collisions"


def test_prompt_hash_cache_salt_differentiates_samples():
    # Multi-sample synthesis calls complete() with identical messages but must
    # produce independent generations. cache_salt is the supported way to
    # differentiate their cache entries.
    msgs = [{"role": "user", "content": "synthesize inputs"}]
    h0 = lc._prompt_hash("m", msgs, 0.7, 0.95, 4096, cache_salt="sample=0")
    h1 = lc._prompt_hash("m", msgs, 0.7, 0.95, 4096, cache_salt="sample=1")
    h_none = lc._prompt_hash("m", msgs, 0.7, 0.95, 4096)
    assert h0 != h1, "distinct cache_salts must produce distinct cache keys"
    assert h0 != h_none, "salted hash must not collide with unsalted hash"


def test_cache_read_tolerates_raw_newlines(monkeypatch, tmp_path):
    # Simulate a cached Response whose content field contains raw newlines
    # (e.g. from a model that emitted a multi-line reasoning string).
    key_path = tmp_path / "llm_key"
    key_path.write_text("sk-openai-fake")
    monkeypatch.setattr(lc, "_load_key", lambda *_a, **_k: "sk-openai-fake")
    client = lc.LLMClient(cache_dir=tmp_path / "cache", secrets_path=key_path)

    msgs = [{"role": "user", "content": "x"}]
    h = lc._prompt_hash("m", msgs, 0.0, 1.0, 100)
    cache_path = client.cache_dir / f"m_{h}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    # Write a JSON file containing a raw-newline inside the content string.
    # strict=False must tolerate this; strict=True would raise.
    raw_newline_content = 'line1\nline2\nline3'
    cache_path.write_text(
        '{"content": "' + raw_newline_content + '", "model": "m", '
        '"temperature": 0.0, "top_p": 1.0, "input_tokens": 1, '
        '"output_tokens": 1, "cost_usd": 0.0, "latency_ms": 1.0, "prompt_hash": "'
        + h + '", "timestamp": "t", "generation_wall_clock_s": 0.0}',
    )

    # Monkeypatch _get_client so a cache miss wouldn't try to reach the network.
    monkeypatch.setattr(client, "_get_client", lambda: (_ for _ in ()).throw(AssertionError("cache miss")))
    resp = client.complete(messages=msgs, model="m", max_tokens=100, use_cache=True)
    assert resp.content == raw_newline_content
    assert resp.cached is True


def test_prediction_result_ignores_extra_fields():
    # LLMs sometimes emit extra keys (e.g. "overall_score", "notes") that
    # aren't in PredictionResult. We want those silently dropped, not to
    # raise ValidationError — otherwise a single chatty model breaks parsing.
    from core.dataset_schema import PredictionResult
    result = PredictionResult.model_validate({
        "functions_covered": ["f"],
        "functions_not_covered": [],
        "branches": [],
        "estimated_line_coverage_pct": 10.0,
        "reasoning": "",
        "overall_score": 85,
        "notes": "a model added this",
    })
    assert result.functions_covered == ["f"]
    assert not hasattr(result, "overall_score")


def test_rephrase_B_prompt_has_no_literal_placeholders():
    # coverage_prediction_rephrase_B.j2 used to contain "file.c:LINE" and `bool`
    # literals that small models copied verbatim, producing degenerate loops.
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[2]
    text = (repo_root / "prediction/prompts/coverage_prediction_rephrase_B.j2").read_text()
    assert "file.c:LINE" not in text
    assert ":LINE\"" not in text
    assert "true_taken\": bool" not in text
    assert "EXACTLY ONE JSON" in text, "rephrase_B must declare the emit-one-object rule"
