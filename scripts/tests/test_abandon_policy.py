"""Guards the opt-in abandon policy added for experiment5 cost protection.

The load-bearing requirement: with the env UNSET the policy is
byte-identical to the legacy behaviour (no-gain window ==
CONSEC_FAIL_WINDOW, yield-ceiling OFF), so every prior experiment
(experiment2_1 baselines) keeps the same abort semantics. Only an
explicit, valid UTCF_ABANDON_NOGAIN switches on the aggressive policy.
"""
from __future__ import annotations

import json

import pytest

from scripts._ablation_base import (
    CONSEC_FAIL_WINDOW,
    DEFAULT_ABANDON_WARMUP,
    abandon_policy,
)

_ENV = ("UTCF_ABANDON_NOGAIN", "UTCF_ABANDON_WARMUP")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    yield


def test_unset_is_legacy_byte_identical():
    # The whole comparability argument rests on this exact tuple.
    assert abandon_policy() == (CONSEC_FAIL_WINDOW, False, None)
    assert CONSEC_FAIL_WINDOW == 20


@pytest.mark.parametrize("bad", ["", "   ", "garbage", "0", "-3", "1.5"])
def test_invalid_or_blank_falls_back_to_legacy(monkeypatch, bad):
    monkeypatch.setenv("UTCF_ABANDON_NOGAIN", bad)
    assert abandon_policy() == (CONSEC_FAIL_WINDOW, False, None)


def test_valid_value_enables_aggressive_policy(monkeypatch):
    monkeypatch.setenv("UTCF_ABANDON_NOGAIN", "5")
    assert abandon_policy() == (5, True, DEFAULT_ABANDON_WARMUP)


def test_warmup_override(monkeypatch):
    monkeypatch.setenv("UTCF_ABANDON_NOGAIN", "5")
    monkeypatch.setenv("UTCF_ABANDON_WARMUP", "40")
    assert abandon_policy() == (5, True, 40)


def test_warmup_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("UTCF_ABANDON_NOGAIN", "8")
    monkeypatch.setenv("UTCF_ABANDON_WARMUP", "nope")
    assert abandon_policy() == (8, True, DEFAULT_ABANDON_WARMUP)


def test_synthesis_stats_artifact_is_bin_invisible(tmp_path):
    """A `_synthesis_stats.json` in a seeds dir must not be counted as a
    seed (the artifact is behaviour-neutral by construction)."""
    from core.targets import TARGETS
    from scripts._ablation_base import AblationRunner

    (tmp_path / "seed_a.bin").write_bytes(b"x")
    (tmp_path / "seed_b.bin").write_bytes(b"y")
    (tmp_path / "_synthesis_stats.json").write_text(json.dumps({"k": 1}))

    runner = AblationRunner(
        target=TARGETS["re2"], variants=[], models=["codestral-22b"],
    )
    # _count_seeds filters to *.bin -> the json must be ignored.
    assert runner._count_seeds(tmp_path) == 2
