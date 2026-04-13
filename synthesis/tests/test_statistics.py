"""Mann-Whitney + Vargha-Delaney tests (plan §Phase 3 test_statistics)."""
from __future__ import annotations

from analysis.scripts.mann_whitney import mann_whitney_u
from analysis.scripts.vargha_delaney import effect_label, vargha_delaney_a12


def test_mann_whitney_identical_samples_not_significant():
    a = [10, 20, 30, 40, 50]
    u, p = mann_whitney_u(a, a)
    assert p > 0.5


def test_mann_whitney_clearly_different_samples_significant():
    a = [100, 110, 120, 130, 140, 150, 160, 170, 180, 190]
    b = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    _, p = mann_whitney_u(a, b)
    assert p < 0.05


def test_mann_whitney_empty_returns_one():
    u, p = mann_whitney_u([], [1, 2, 3])
    assert p == 1.0


def test_vargha_delaney_identical_is_half():
    a = [1, 2, 3]
    b = [1, 2, 3]
    a12 = vargha_delaney_a12(a, b)
    assert abs(a12 - 0.5) < 1e-9


def test_vargha_delaney_dominant_is_one():
    a = [100, 110, 120]
    b = [1, 2, 3]
    a12 = vargha_delaney_a12(a, b)
    assert a12 == 1.0


def test_vargha_delaney_dominated_is_zero():
    a = [1, 2, 3]
    b = [10, 20, 30]
    a12 = vargha_delaney_a12(a, b)
    assert a12 == 0.0


def test_effect_labels_map_to_thresholds():
    assert effect_label(0.50) == "negligible"
    assert effect_label(0.58) == "small"
    assert effect_label(0.66) == "medium"
    assert effect_label(0.80) == "large"
