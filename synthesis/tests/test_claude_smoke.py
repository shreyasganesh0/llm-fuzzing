"""Mocked unit test for the Claude format-smoke driver.

Patches `LLMClient.complete` so no network call happens; verifies the
exit codes for the four documented failure modes plus the OK path.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from synthesis.scripts import claude_smoke_check as smoke


@dataclass
class _FakeResp:
    content: str
    input_tokens: int = 60
    output_tokens: int = 80
    cost_usd: float = 0.001


def _patch(monkeypatch, *, content: str, cost_usd: float = 0.001, provider: str = "anthropic"):
    def fake_init(self, *_a, **_kw):
        self.provider = provider
        self.api_key = "sk-ant-fake"
        self.base_url = None
        self._client = None
        self.cache_dir = None  # not used by the smoke path

    def fake_complete(self, **kwargs):
        return _FakeResp(content=content, cost_usd=cost_usd)

    monkeypatch.setattr(smoke.LLMClient, "__init__", fake_init)
    monkeypatch.setattr(smoke.LLMClient, "complete", fake_complete)


def test_smoke_passes_on_valid_response(monkeypatch):
    _patch(
        monkeypatch,
        content='{"regexes":[{"regex":"a+","target_gaps":["re2/parse.cc:100"],"reasoning":"x"}]}',
    )
    assert smoke.run_smoke("claude-sonnet-4-6") == 0


def test_smoke_fails_on_non_anthropic_provider(monkeypatch):
    _patch(monkeypatch, content="anything", provider="openai")
    assert smoke.run_smoke("claude-sonnet-4-6") == 2


def test_smoke_fails_on_parse_failure(monkeypatch):
    _patch(monkeypatch, content="no json here, just prose")
    assert smoke.run_smoke("claude-sonnet-4-6") == 3


def test_smoke_fails_when_seeds_empty(monkeypatch):
    # Valid JSON, but `regexes` is empty so parser returns parse_failure.
    # The driver treats parse_failure as exit 3 (parse failure takes priority).
    _patch(monkeypatch, content='{"regexes":[]}')
    rc = smoke.run_smoke("claude-sonnet-4-6")
    assert rc in (3, 4)  # either ordering is acceptable


def test_smoke_fails_when_cost_exceeds_cap(monkeypatch):
    _patch(
        monkeypatch,
        content='{"regexes":[{"regex":"a+","target_gaps":[],"reasoning":""}]}',
        cost_usd=0.05,
    )
    assert smoke.run_smoke("claude-sonnet-4-6") == 5


@pytest.mark.parametrize("regex", ["a+", "(?P<x>a+)", "[a-z]{3,}"])
def test_smoke_handles_various_regexes(monkeypatch, regex):
    body = f'{{"regexes":[{{"regex":"{regex}","target_gaps":[],"reasoning":""}}]}}'
    _patch(monkeypatch, content=body)
    assert smoke.run_smoke("claude-sonnet-4-6") == 0
