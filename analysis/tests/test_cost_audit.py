"""Unit tests for analysis.scripts.cost_audit."""
from __future__ import annotations

import json
from pathlib import Path

from analysis.scripts.cost_audit import (
    TARGET_SIGNATURES,  # noqa: F401 — imported to ensure public surface
    audit,
    infer_target,
    render_markdown,
)


def _write(tmp: Path, name: str, payload: dict) -> None:
    (tmp / name).write_text(json.dumps(payload))


def test_infer_target_matches_signatures():
    assert infer_target('{"regex": "a+b"}') == "re2"
    assert infer_target('hb_blob_create(...)') == "harfbuzz"
    assert infer_target('opaque response') == "unknown"


def test_audit_sums_cost_and_tokens(tmp_path: Path) -> None:
    _write(tmp_path, "claude-sonnet-4-6_aaa.json", {
        "content": '{"regex": "x"}',
        "model": "claude-sonnet-4-6",
        "input_tokens": 1000, "output_tokens": 200,
        "cost_usd": 0.006, "timestamp": "2026-04-20T10:00:00+00:00",
        "prompt_hash": "aaa",
    })
    _write(tmp_path, "claude-sonnet-4-6_bbb.json", {
        "content": '{"regex": "y"}',
        "model": "claude-sonnet-4-6",
        "input_tokens": 2000, "output_tokens": 400,
        "cost_usd": 0.012, "timestamp": "2026-04-21T10:00:00+00:00",
        "prompt_hash": "bbb",
    })
    _write(tmp_path, "llama-3.1-8b-instruct_ccc.json", {
        "content": "hb_blob_create(...)",
        "model": "llama-3.1-8b-instruct",
        "input_tokens": 500, "output_tokens": 100,
        "cost_usd": 0.000132, "timestamp": "2026-04-21T10:00:00+00:00",
        "prompt_hash": "ccc",
    })

    result = audit(tmp_path)
    by_model = result["by_model"]
    assert set(by_model) == {"claude-sonnet-4-6", "llama-3.1-8b-instruct"}
    sonnet = by_model["claude-sonnet-4-6"]
    assert sonnet["calls"] == 2
    assert sonnet["input_tokens"] == 3000
    assert sonnet["output_tokens"] == 600
    assert abs(sonnet["cost_usd"] - 0.018) < 1e-9
    assert sonnet["by_target"]["re2"]["calls"] == 2
    assert sonnet["by_day"]["2026-04-20"]["calls"] == 1
    assert sonnet["by_day"]["2026-04-21"]["calls"] == 1
    llama = by_model["llama-3.1-8b-instruct"]
    assert llama["by_target"]["harfbuzz"]["calls"] == 1


def test_render_markdown_includes_totals(tmp_path: Path) -> None:
    _write(tmp_path, "claude-sonnet-4-6_aaa.json", {
        "content": '{"regex": "x"}', "model": "claude-sonnet-4-6",
        "input_tokens": 1000, "output_tokens": 200, "cost_usd": 0.006,
        "timestamp": "2026-04-20T10:00:00+00:00", "prompt_hash": "aaa",
    })
    md = render_markdown(audit(tmp_path), tmp_path)
    assert "Grand total" in md
    assert "$0.01" in md or "$0.006" in md
    assert "claude-sonnet-4-6" in md
