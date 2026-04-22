"""Metric registry for ablation runners.

Each metric wraps an existing measurement script and exposes a uniform
interface (`Metric`). The ablation runner consumes `METRICS` to
register CLI phases automatically — adding `M3` is one new file plus one
entry in the list below.
"""
from __future__ import annotations

from analysis.metrics.base import Metric
from analysis.metrics.m1 import M1EdgesMetric
from analysis.metrics.m2 import M2HardBranchMetric

METRICS: list[Metric] = [M1EdgesMetric(), M2HardBranchMetric()]

__all__ = ["Metric", "M1EdgesMetric", "M2HardBranchMetric", "METRICS"]
