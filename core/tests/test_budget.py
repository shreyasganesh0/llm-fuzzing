"""Budget tracker unit tests.

Fast, local, no network. Verify per-call, per-cell, and global caps trip
independently and that the ledger is durable across tracker instances.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.budget import (
    BudgetCaps,
    BudgetExceededError,
    BudgetTracker,
)


def _mk_tracker(tmp_path: Path, **caps_kwargs) -> BudgetTracker:
    return BudgetTracker(
        run_id="test",
        ledger_dir=tmp_path,
        caps=BudgetCaps(**caps_kwargs),
    )


def test_no_caps_is_permissive(tmp_path: Path) -> None:
    t = _mk_tracker(tmp_path, global_cap=None, per_cell_cap=None, per_call_cap=None)
    t.assert_can_spend(9999.99)  # no raise
    t.record(9999.99, model="x", input_tokens=1, output_tokens=1, cell_key="c")
    assert t.totals()["global_total_usd"] == pytest.approx(9999.99)


def test_per_call_cap_trips(tmp_path: Path) -> None:
    t = _mk_tracker(tmp_path, global_cap=None, per_cell_cap=None, per_call_cap=0.01)
    t.assert_can_spend(0.005)  # ok
    with pytest.raises(BudgetExceededError, match="per-call cap"):
        t.assert_can_spend(0.02)


def test_global_cap_trips_on_accumulation(tmp_path: Path) -> None:
    t = _mk_tracker(tmp_path, global_cap=0.10, per_cell_cap=None, per_call_cap=None)
    t.record(0.04, model="m", input_tokens=1, output_tokens=1)
    t.record(0.04, model="m", input_tokens=1, output_tokens=1)
    t.assert_can_spend(0.01)  # 0.08 + 0.01 = 0.09, ok
    with pytest.raises(BudgetExceededError, match="global spend cap"):
        t.assert_can_spend(0.05)  # 0.08 + 0.05 = 0.13 > 0.10


def test_per_cell_cap_isolated(tmp_path: Path) -> None:
    t = _mk_tracker(tmp_path, global_cap=None, per_cell_cap=0.05, per_call_cap=None)
    t.record(0.04, model="m", input_tokens=1, output_tokens=1, cell_key="A")
    t.record(0.04, model="m", input_tokens=1, output_tokens=1, cell_key="B")
    # Cell A has 0.04; adding 0.02 trips its cell cap but a fresh cell C is fine.
    with pytest.raises(BudgetExceededError, match="per-cell cap"):
        t.assert_can_spend(0.02, cell_key="A")
    t.assert_can_spend(0.04, cell_key="C")  # new cell, ok


def test_ledger_is_durable_across_instances(tmp_path: Path) -> None:
    t1 = _mk_tracker(tmp_path, global_cap=1.00, per_cell_cap=None, per_call_cap=None)
    t1.record(0.50, model="m", input_tokens=1, output_tokens=1, cell_key="X")
    t2 = _mk_tracker(tmp_path, global_cap=1.00, per_cell_cap=None, per_call_cap=None)
    assert t2.totals()["global_total_usd"] == pytest.approx(0.50)
    with pytest.raises(BudgetExceededError):
        t2.assert_can_spend(0.60)  # would exceed 1.00


def test_negative_estimate_rejected(tmp_path: Path) -> None:
    t = _mk_tracker(tmp_path)
    with pytest.raises(ValueError):
        t.assert_can_spend(-0.01)


def test_ledger_survives_malformed_line(tmp_path: Path) -> None:
    ledger = tmp_path / "test.jsonl"
    ledger.write_text('{"cost_usd": 0.10}\nnot-json\n{"cost_usd": 0.20}\n')
    t = _mk_tracker(tmp_path)
    # Malformed middle line raises — we want that, not a silent accept.
    with pytest.raises(json.JSONDecodeError):
        t.totals()
