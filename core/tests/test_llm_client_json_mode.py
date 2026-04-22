"""Constrained-decoding plumbing tests for LLMClient.complete (Phase 3).

Covers:
  - Cache key is byte-identical when no constraint is supplied (backwards
    compat with the ~14k existing `.cache/llm/` entries).
  - response_format and guided_json each perturb the cache key.
  - response_format is rejected on Anthropic (until Phase 7 wires tool-use).
  - guided_json is rejected on OpenAI and Anthropic — LiteLLM/vllm only.
  - response_format and guided_json are mutually exclusive.
  - When both are set to None, the JSON payload hashed is exactly the same
    object the pre-Phase-3 code hashed (structural equality against a
    hand-rolled reference payload).

All tests stay offline — no network, no `.cache/llm/` writes (each LLMClient
gets a `tmp_path` cache dir), no real secrets.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from core import llm_client as lc


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    monkeypatch.delenv("UTCF_LITELLM_URL", raising=False)
    monkeypatch.delenv("UTCF_VLLM_URL", raising=False)
    monkeypatch.delenv("UTCF_LLM_RPM", raising=False)
    monkeypatch.delenv(lc.ANTHROPIC_KEY_ENV, raising=False)


# ---- cache-key backwards compatibility ------------------------------------

def _legacy_payload_hash(
    model: str,
    messages: list[dict],
    temperature: float,
    top_p: float,
    max_tokens: int,
    cache_salt: str | None,
) -> str:
    """Re-implementation of the pre-Phase-3 _prompt_hash payload shape.

    Any divergence from this string means an existing cache entry would
    miss and trigger a paid re-generation. This is the load-bearing
    invariant for the 14k cache entries.
    """
    payload = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "cache_salt": cache_salt,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_cache_key_unchanged_when_no_constraint():
    msgs = [
        {"role": "system", "content": "you are helpful"},
        {"role": "user", "content": "generate seeds"},
    ]
    legacy = _legacy_payload_hash("llama-3.1-8b-instruct", msgs, 0.7, 0.95, 4096, None)
    current = lc._prompt_hash("llama-3.1-8b-instruct", msgs, 0.7, 0.95, 4096)
    assert legacy == current, (
        "Adding response_format/guided_json kwargs must not change the hash "
        "input when both are None. Cache backwards compatibility is broken."
    )


def test_cache_key_unchanged_with_cache_salt_only():
    msgs = [{"role": "user", "content": "x"}]
    legacy = _legacy_payload_hash("m", msgs, 0.0, 1.0, 2048, "sample=3")
    current = lc._prompt_hash("m", msgs, 0.0, 1.0, 2048, cache_salt="sample=3")
    assert legacy == current


def test_cache_key_differs_with_response_format():
    msgs = [{"role": "user", "content": "x"}]
    base = lc._prompt_hash("m", msgs, 0.0, 1.0, 2048)
    with_rf = lc._prompt_hash(
        "m", msgs, 0.0, 1.0, 2048,
        response_format={"type": "json_object"},
    )
    assert base != with_rf


def test_cache_key_differs_with_guided_json():
    msgs = [{"role": "user", "content": "x"}]
    base = lc._prompt_hash("m", msgs, 0.0, 1.0, 2048)
    with_gj = lc._prompt_hash(
        "m", msgs, 0.0, 1.0, 2048,
        guided_json={"type": "object", "properties": {"k": {"type": "string"}}},
    )
    assert base != with_gj


def test_response_format_and_guided_json_produce_distinct_hashes():
    msgs = [{"role": "user", "content": "x"}]
    with_rf = lc._prompt_hash(
        "m", msgs, 0.0, 1.0, 2048,
        response_format={"type": "json_object"},
    )
    with_gj = lc._prompt_hash(
        "m", msgs, 0.0, 1.0, 2048,
        guided_json={"type": "object"},
    )
    assert with_rf != with_gj, (
        "Different constraint shapes must produce different cache keys so "
        "they do not collide on the same file."
    )


# ---- provider-level validation --------------------------------------------

def _fake_anthropic_client(monkeypatch, tmp_path) -> lc.LLMClient:
    key_path = tmp_path / "llm_key"
    key_path.write_text("sk-ant-api03-fake")
    # Use a throwaway cache dir so we never touch the real .cache/llm.
    client = lc.LLMClient(
        secrets_path=key_path,
        cache_dir=tmp_path / "cache",
    )
    assert client.provider == "anthropic"
    # If any path actually tries to reach the network we want an explicit
    # AssertionError rather than a silent failure.
    monkeypatch.setattr(
        client, "_get_client",
        lambda: (_ for _ in ()).throw(AssertionError("no network in tests")),
    )
    return client


def _fake_openai_client(monkeypatch, tmp_path) -> lc.LLMClient:
    key_path = tmp_path / "llm_key"
    key_path.write_text("sk-proj-openai-fake")
    client = lc.LLMClient(
        secrets_path=key_path,
        cache_dir=tmp_path / "cache",
    )
    assert client.provider == "openai"
    monkeypatch.setattr(
        client, "_get_client",
        lambda: (_ for _ in ()).throw(AssertionError("no network in tests")),
    )
    return client


def _fake_litellm_client(monkeypatch, tmp_path) -> lc.LLMClient:
    key_path = tmp_path / "llm_key"
    key_path.write_text("sk-proj-litellm-fake")
    monkeypatch.setenv("UTCF_LITELLM_URL", "https://api.example.edu")
    client = lc.LLMClient(
        secrets_path=key_path,
        cache_dir=tmp_path / "cache",
    )
    assert client.provider == "vllm"
    return client


def test_response_format_rejected_on_anthropic(monkeypatch, tmp_path):
    client = _fake_anthropic_client(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="not supported on"):
        client.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-sonnet-4-6",
            response_format={"type": "json_object"},
        )


def test_response_format_json_schema_rejected_on_anthropic(monkeypatch, tmp_path):
    client = _fake_anthropic_client(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="not supported on"):
        client.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-haiku-4-5-20251001",
            response_format={
                "type": "json_schema",
                "json_schema": {"schema": {"type": "object"}},
            },
        )


def test_guided_json_rejected_on_anthropic(monkeypatch, tmp_path):
    client = _fake_anthropic_client(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="guided_json"):
        client.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-sonnet-4-6",
            guided_json={"type": "object"},
        )


def test_guided_json_rejected_on_openai(monkeypatch, tmp_path):
    client = _fake_openai_client(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="guided_json"):
        client.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-4o-2024-08-06",
            guided_json={"type": "object"},
        )


def test_response_format_and_guided_json_mutually_exclusive(monkeypatch, tmp_path):
    client = _fake_litellm_client(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="mutually exclusive"):
        client.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="llama-3.1-8b-instruct",
            response_format={"type": "json_object"},
            guided_json={"type": "object"},
        )


# ---- ModelDefaults population ---------------------------------------------

def test_litellm_models_advertise_json_support():
    """The 6 UF LiteLLM models verified by the Phase 0 probe must opt in.

    Source of truth: results/probes/probe_json_mode.json.
    """
    from core.config import defaults as md
    for model in [
        "llama-3.1-8b-instruct",
        "llama-3.1-70b-instruct",
        "llama-3.3-70b-instruct",
        "codestral-22b",
        "nemotron-3-super-120b-a12b",
        "gpt-oss-20b",
    ]:
        d = md(model)
        assert d.supports_json_object is True, model
        assert d.supports_json_schema is True, model


def test_anthropic_models_do_not_advertise_json_support():
    """Zero-credit probe result means both Anthropic flags stay False."""
    from core.config import defaults as md
    for model in ["claude-sonnet-4-6", "claude-haiku-4-5-20251001"]:
        d = md(model)
        assert d.supports_json_object is False, model
        assert d.supports_json_schema is False, model
