"""Experiment 1 vs 2 outcome classification (plan §E2.6 COMPARISON 3)."""
from __future__ import annotations

from synthesis.scripts.compare_experiments import _classify


def test_non_significant_is_b():
    assert _classify(p=0.3, a12=0.7) == "B_no_difference"
    assert _classify(p=0.5, a12=0.4) == "B_no_difference"


def test_significant_and_exp1_higher_is_a():
    assert _classify(p=0.01, a12=0.8) == "A_test_conditioned_wins"


def test_significant_and_exp2_higher_is_c():
    assert _classify(p=0.01, a12=0.2) == "C_source_only_wins"


def test_p_exactly_005_is_b():
    assert _classify(p=0.05, a12=0.9) == "B_no_difference"
