"""Claude (Anthropic) provider wiring tests — no network.

Covers:
  - Haiku 4.5 pricing row is present in PRICING_USD_PER_MTOK so cost
    accounting does not silently report $0 during budget-bounded runs.
  - UTCF_ANTHROPIC_KEY_PATH overrides the default secrets path when the
    referenced file holds an `sk-ant-` key. Lets the existing driver
    scripts switch to Claude without taking a new --secrets-path flag.
"""
from __future__ import annotations

import pytest

from core import llm_client as lc


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    monkeypatch.delenv("UTCF_LITELLM_URL", raising=False)
    monkeypatch.delenv("UTCF_VLLM_URL", raising=False)
    monkeypatch.delenv(lc.ANTHROPIC_KEY_ENV, raising=False)


def test_haiku_pricing_present():
    assert "claude-haiku-4-5-20251001" in lc.PRICING_USD_PER_MTOK
    rate = lc.PRICING_USD_PER_MTOK["claude-haiku-4-5-20251001"]
    assert rate == {"input": 1.00, "output": 5.00}


def test_haiku_cost_is_nonzero():
    cost = lc._estimate_cost("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
    assert pytest.approx(cost, abs=1e-9) == 6.00


def test_anthropic_key_env_overrides_default(monkeypatch, tmp_path):
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "llm_key").write_text("sk-proj-not-anthropic")

    anthropic_key = tmp_path / "claude_key"
    anthropic_key.write_text("sk-ant-api03-fake")
    monkeypatch.setenv(lc.ANTHROPIC_KEY_ENV, str(anthropic_key))

    monkeypatch.chdir(tmp_path)
    client = lc.LLMClient()  # default secrets_path triggers env-var path
    assert client.provider == "anthropic"
    assert client.api_key.startswith("sk-ant-")


def test_anthropic_env_ignored_when_explicit_secrets_path(monkeypatch, tmp_path):
    explicit = tmp_path / "explicit_key"
    explicit.write_text("sk-proj-explicit-openai")

    anthropic_key = tmp_path / "claude_key"
    anthropic_key.write_text("sk-ant-api03-fake")
    monkeypatch.setenv(lc.ANTHROPIC_KEY_ENV, str(anthropic_key))

    client = lc.LLMClient(secrets_path=explicit)
    assert client.provider == "openai"
    assert client.api_key == "sk-proj-explicit-openai"


def test_anthropic_env_ignored_when_target_file_missing(monkeypatch, tmp_path):
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "llm_key").write_text("sk-proj-fallback-openai")
    monkeypatch.setenv(lc.ANTHROPIC_KEY_ENV, str(tmp_path / "does_not_exist"))

    monkeypatch.chdir(tmp_path)
    client = lc.LLMClient()
    assert client.provider == "openai"
