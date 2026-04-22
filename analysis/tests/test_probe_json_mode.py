"""Unit tests for analysis.scripts.probe_json_mode.

Never make real API calls — monkeypatch ``run_one`` to return canned records
and assert the aggregator builds the documented shape.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis.scripts import probe_json_mode as mod


def _canned(accepted: bool, parsed: bool, content: str = "", error: str | None = None) -> dict:
    return {
        "accepted_by_server": accepted,
        "parsed_as_json": parsed,
        "content": content,
        "error": error,
    }


def test_parses_as_json_handles_fenced_output():
    assert mod._parses_as_json('{"status": "ok"}') is True
    assert mod._parses_as_json('```json\n{"status": "ok"}\n```') is True
    assert mod._parses_as_json("not json") is False
    assert mod._parses_as_json("") is False


def test_run_probe_builds_expected_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    # Claude: everything parses. Llama: only json_object produces JSON.
    canned: dict[tuple[str, str], dict] = {
        ("claude-sonnet-4-6", "plain"):       _canned(True, True, '{"status": "ok"}'),
        ("claude-sonnet-4-6", "json_object"): _canned(True, True, '{"status": "ok"}'),
        ("claude-sonnet-4-6", "json_schema"): _canned(True, True, '{"status": "ok"}'),
        ("claude-sonnet-4-6", "guided_json"): _canned(False, False, "", "400: bad"),
        ("llama-3.1-8b-instruct", "plain"):       _canned(True, False, "sure, here's json"),
        ("llama-3.1-8b-instruct", "json_object"): _canned(True, True, '{"status": "ok"}'),
        ("llama-3.1-8b-instruct", "json_schema"): _canned(False, False, "", "400: no"),
        ("llama-3.1-8b-instruct", "guided_json"): _canned(True, True, '{"status": "ok"}'),
    }

    def fake_run_one(model: str, flavor: str, *, use_cache: bool = True) -> dict:
        return canned[(model, flavor)]

    monkeypatch.setattr(mod, "run_one", fake_run_one)

    payload = mod.run_probe(["claude-sonnet-4-6", "llama-3.1-8b-instruct"])
    assert "timestamp" in payload
    assert payload["prompt"] == mod.PROBE_PROMPT
    assert payload["schema"] == mod.PROBE_SCHEMA
    assert set(payload["results"]) == {"claude-sonnet-4-6", "llama-3.1-8b-instruct"}
    for model, flavors in payload["results"].items():
        assert set(flavors) == set(mod.FLAVORS), f"missing flavor for {model}"
        for flavor, rec in flavors.items():
            assert set(rec) >= {"accepted_by_server", "parsed_as_json", "content", "error"}

    # Spot-check a few key values from the canned set.
    assert payload["results"]["claude-sonnet-4-6"]["guided_json"]["accepted_by_server"] is False
    assert payload["results"]["llama-3.1-8b-instruct"]["json_object"]["parsed_as_json"] is True


def test_format_summary_tags_each_flavor(monkeypatch: pytest.MonkeyPatch) -> None:
    canned: dict[tuple[str, str], dict] = {
        ("m", "plain"):       _canned(True, True),
        ("m", "json_object"): _canned(True, False),    # accepted but no JSON
        ("m", "json_schema"): _canned(False, False),   # rejected
        ("m", "guided_json"): _canned(True, True),
    }
    monkeypatch.setattr(mod, "run_one", lambda model, flavor, **_: canned[(model, flavor)])

    payload = mod.run_probe(["m"])
    [line] = mod.format_summary(payload)
    assert line.startswith("m |")
    assert "plain: ok" in line
    assert "json_object: accepted_no_json" in line
    assert "json_schema: rejected" in line
    assert "guided_json: ok" in line


def test_run_one_uses_probe_cache_and_skips_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Point the probe cache at a tmp dir and pre-populate a record so
    # run_one must use it rather than hitting _credentials_for.
    monkeypatch.setattr(mod, "PROBE_CACHE_DIR", tmp_path)
    cached = _canned(True, True, '{"status": "ok"}')
    tmp_path.mkdir(parents=True, exist_ok=True)
    mod._cache_key("claude-sonnet-4-6", "plain").write_text(json.dumps(cached))

    def forbid_credentials(model: str) -> tuple[str, str | None]:
        raise AssertionError("run_one should not reach network when cache hits")

    monkeypatch.setattr(mod, "_credentials_for", forbid_credentials)
    got = mod.run_one("claude-sonnet-4-6", "plain")
    assert got == cached


def test_main_writes_output_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(
        mod,
        "run_one",
        lambda model, flavor, **_: _canned(True, True, '{"status": "ok"}'),
    )
    out = tmp_path / "probe.json"
    rc = mod.main(["--models", "claude-sonnet-4-6", "--out", str(out)])
    assert rc == 0
    assert out.is_file()
    payload = json.loads(out.read_text())
    assert "claude-sonnet-4-6" in payload["results"]
    captured = capsys.readouterr().out
    assert "claude-sonnet-4-6" in captured
