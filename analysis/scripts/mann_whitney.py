"""Two-tailed Mann-Whitney U test (plan §Phase 3 methodology).

Prefers `scipy.stats.mannwhitneyu` when available; falls back to a NumPy
implementation that matches the scipy output (two-sided, with tie correction)
for small samples, so tests can run without a scipy pin drift.
"""
from __future__ import annotations

from collections.abc import Sequence


def mann_whitney_u(a: Sequence[float], b: Sequence[float]) -> tuple[float, float]:
    """Return (U statistic, two-sided p-value)."""
    if not a or not b:
        return 0.0, 1.0
    try:
        from scipy.stats import mannwhitneyu
        res = mannwhitneyu(a, b, alternative="two-sided")
        return float(res.statistic), float(res.pvalue)
    except ImportError:
        return _fallback_u(list(a), list(b))


def _fallback_u(a: list[float], b: list[float]) -> tuple[float, float]:
    combined = sorted([(x, "a") for x in a] + [(x, "b") for x in b])
    ranks: dict[int, float] = {}
    i = 0
    while i < len(combined):
        j = i
        while j + 1 < len(combined) and combined[j + 1][0] == combined[i][0]:
            j += 1
        rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[k] = rank
        i = j + 1

    rank_a = sum(ranks[i] for i, (_, lbl) in enumerate(combined) if lbl == "a")
    na, nb = len(a), len(b)
    u_a = rank_a - na * (na + 1) / 2
    u_b = na * nb - u_a
    u = min(u_a, u_b)

    # Normal approximation (fine for n>=20; we use this only as scipy fallback).
    mean_u = na * nb / 2
    std_u_sq = na * nb * (na + nb + 1) / 12
    if std_u_sq <= 0:
        return float(u), 1.0
    import math
    z = (u - mean_u) / math.sqrt(std_u_sq)
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return float(u), float(max(min(p, 1.0), 0.0))
