"""Friedman + Nemenyi cross-benchmark ranking (plan §Phase 3).

Input: per-(target, config) mean edge counts across trials.
Output: Friedman statistic + p-value; Nemenyi post-hoc pairwise p-values.
Used to feed the critical-difference diagram in `plot_cd.py` / notebook 05.
"""
from __future__ import annotations

from collections.abc import Sequence


def friedman(per_target_ranks: Sequence[Sequence[float]]) -> tuple[float, float]:
    """per_target_ranks: list of per-target lists of metric values, one value per config.

    Each inner list must have the same length (k configs).
    """
    if not per_target_ranks or not per_target_ranks[0]:
        return 0.0, 1.0
    try:
        from scipy.stats import friedmanchisquare
        cols = list(zip(*per_target_ranks, strict=True))
        res = friedmanchisquare(*cols)
        return float(res.statistic), float(res.pvalue)
    except ImportError:
        return 0.0, 1.0


def nemenyi(per_target_ranks: Sequence[Sequence[float]]) -> list[list[float]]:
    """Nemenyi post-hoc pairwise p-values. Returns a symmetric k×k matrix."""
    try:
        import numpy as np
        import scikit_posthocs as sp
        arr = np.array(per_target_ranks)
        p_matrix = sp.posthoc_nemenyi_friedman(arr).values
        return p_matrix.tolist()
    except ImportError:
        k = len(per_target_ranks[0]) if per_target_ranks else 0
        return [[1.0] * k for _ in range(k)]


def mean_ranks(per_target_ranks: Sequence[Sequence[float]]) -> list[float]:
    """Average rank of each config across targets (1 = best edge count)."""
    if not per_target_ranks:
        return []
    k = len(per_target_ranks[0])
    rank_sums = [0.0] * k
    for row in per_target_ranks:
        # Highest value -> rank 1 (best coverage).
        indexed = sorted(enumerate(row), key=lambda p: p[1], reverse=True)
        ranks = [0.0] * k
        i = 0
        while i < k:
            j = i
            while j + 1 < k and indexed[j + 1][1] == indexed[i][1]:
                j += 1
            avg_rank = (i + j) / 2 + 1
            for p in range(i, j + 1):
                ranks[indexed[p][0]] = avg_rank
            i = j + 1
        for idx in range(k):
            rank_sums[idx] += ranks[idx]
    return [s / len(per_target_ranks) for s in rank_sums]
