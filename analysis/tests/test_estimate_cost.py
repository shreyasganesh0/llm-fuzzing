"""Unit tests for analysis.scripts.estimate_cost."""
from __future__ import annotations

import json
from pathlib import Path

from analysis.scripts.estimate_cost import estimate, load_historical_means


def test_sonnet_estimate_matches_pricing_table():
    result = estimate(
        "claude-sonnet-4-6", n_calls=100,
        mean_in=10000, mean_out=1000,
    )
    # Sonnet: $3/MTok input + $15/MTok output
    # Per-call: (10000 * 3 + 1000 * 15) / 1e6 = 0.03 + 0.015 = 0.045
    assert abs(result["per_call_usd"] - 0.045) < 1e-9
    assert abs(result["total_usd"] - 4.5) < 1e-9
    assert result["pricing_known"] is True
    assert result["input_usd_per_mtok"] == 3.0
    assert result["output_usd_per_mtok"] == 15.0


def test_unknown_model_returns_zero_with_flag():
    result = estimate("totally-made-up-model", 100, 1000, 100)
    assert result["per_call_usd"] == 0.0
    assert result["total_usd"] == 0.0
    assert result["pricing_known"] is False


def test_load_historical_means_roundtrips(tmp_path: Path) -> None:
    audit_path = tmp_path / "summary.json"
    audit_path.write_text(json.dumps({
        "by_model": {
            "claude-sonnet-4-6": {
                "mean_input_tokens": 12345.6,
                "mean_output_tokens": 789.0,
                "calls": 10,
            },
        },
    }))
    means = load_historical_means(audit_path)
    assert means["claude-sonnet-4-6"] == (12345.6, 789.0)


def test_load_historical_means_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_historical_means(tmp_path / "nonexistent.json") == {}
