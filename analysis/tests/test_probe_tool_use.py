"""Unit tests for analysis.scripts.probe_tool_use.

Never make real API calls — monkeypatch ``run_one`` to return canned records
and assert the aggregator builds the documented shape.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis.scripts import probe_tool_use as mod


def _canned(
    accepted: bool,
    emitted: bool,
    name: str | None = None,
    args: dict | None = None,
    error: str | None = None,
) -> dict:
    return {
        "accepted_by_server": accepted,
        "tool_call_emitted": emitted,
        "tool_name": name,
        "tool_args": args or {},
        "error": error,
    }


def test_openai_tool_def_mirrors_input_schema():
    oa = mod._openai_tool_def()
    assert oa["type"] == "function"
    assert oa["function"]["name"] == "ping"
    assert oa["function"]["parameters"] == mod.TOOL_DEF["input_schema"]


def test_run_probe_builds_expected_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    canned: dict[tuple[str, str], dict] = {
        ("claude-sonnet-4-6", "anthropic_style"): _canned(True, True, "ping", {"msg": "hello"}),
        ("claude-sonnet-4-6", "openai_style"):    _canned(False, False, error="400: bad"),
        ("llama-3.1-70b-instruct", "anthropic_style"): _canned(False, False, error="400: bad"),
        ("llama-3.1-70b-instruct", "openai_style"):    _canned(True, True, "ping", {"msg": "hello"}),
    }

    def fake_run_one(model: str, dialect: str, *, use_cache: bool = True) -> dict:
        return canned[(model, dialect)]

    monkeypatch.setattr(mod, "run_one", fake_run_one)

    payload = mod.run_probe(["claude-sonnet-4-6", "llama-3.1-70b-instruct"])
    assert "timestamp" in payload
    assert payload["prompt"] == mod.PROBE_PROMPT
    assert payload["tool"] == mod.TOOL_DEF
    assert set(payload["results"]) == {"claude-sonnet-4-6", "llama-3.1-70b-instruct"}
    for model, dialects in payload["results"].items():
        assert set(dialects) == set(mod.DIALECTS), f"missing dialect for {model}"
        for dialect, rec in dialects.items():
            assert set(rec) >= {
                "accepted_by_server",
                "tool_call_emitted",
                "tool_name",
                "tool_args",
                "error",
            }

    claude = payload["results"]["claude-sonnet-4-6"]
    assert claude["anthropic_style"]["tool_name"] == "ping"
    assert claude["anthropic_style"]["tool_args"] == {"msg": "hello"}
    assert claude["openai_style"]["accepted_by_server"] is False

    llama = payload["results"]["llama-3.1-70b-instruct"]
    assert llama["openai_style"]["tool_call_emitted"] is True


def test_format_summary_tags_each_dialect(monkeypatch: pytest.MonkeyPatch) -> None:
    canned: dict[tuple[str, str], dict] = {
        ("m", "anthropic_style"): _canned(True, True, "ping", {"msg": "hello"}),
        ("m", "openai_style"):    _canned(True, False),    # accepted but no tool call
    }
    monkeypatch.setattr(mod, "run_one", lambda model, dialect, **_: canned[(model, dialect)])

    payload = mod.run_probe(["m"])
    [line] = mod.format_summary(payload)
    assert line.startswith("m |")
    assert "anthropic_style: ok" in line
    assert "openai_style: accepted_no_call" in line


def test_run_one_uses_probe_cache_and_skips_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(mod, "PROBE_CACHE_DIR", tmp_path)
    cached = _canned(True, True, "ping", {"msg": "hello"})
    tmp_path.mkdir(parents=True, exist_ok=True)
    mod._cache_key("claude-sonnet-4-6", "anthropic_style").write_text(json.dumps(cached))

    def forbid_credentials(model: str) -> tuple[str, str | None]:
        raise AssertionError("run_one should not reach network when cache hits")

    monkeypatch.setattr(mod, "_credentials_for", forbid_credentials)
    got = mod.run_one("claude-sonnet-4-6", "anthropic_style")
    assert got == cached


def test_main_writes_output_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(
        mod,
        "run_one",
        lambda model, dialect, **_: _canned(True, True, "ping", {"msg": "hello"}),
    )
    out = tmp_path / "probe.json"
    rc = mod.main(["--models", "claude-sonnet-4-6", "--out", str(out)])
    assert rc == 0
    assert out.is_file()
    payload = json.loads(out.read_text())
    assert "claude-sonnet-4-6" in payload["results"]
    captured = capsys.readouterr().out
    assert "claude-sonnet-4-6" in captured
