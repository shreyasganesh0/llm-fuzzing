"""Vargha-Delaney Â₁₂ effect size (plan §Phase 3).

Â₁₂(a, b) = P(a > b) + 0.5 · P(a == b). Ranges:
  < 0.44  → b > a large, 0.44-0.56 negligible, 0.56-0.64 small,
  0.64-0.71 medium, > 0.71 large. We return the raw score and a label per
  Vargha-Delaney's thresholds.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from core.config import VARGHA_DELANEY_THRESHOLDS


def vargha_delaney_a12(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.5
    greater = 0
    equal = 0
    for x in a:
        for y in b:
            if x > y:
                greater += 1
            elif x == y:
                equal += 1
    n = len(a) * len(b)
    return (greater + 0.5 * equal) / n


def effect_label(a12: float) -> Literal["negligible", "small", "medium", "large"]:
    distance = abs(a12 - 0.5)
    # thresholds are lower bounds for each label, expressed symmetrically around 0.5
    label: str = "negligible"
    for cutoff, next_label in VARGHA_DELANEY_THRESHOLDS:
        if distance >= cutoff - 0.5:
            label = next_label
        else:
            break
    return label  # type: ignore[return-value]
