"""Common interface for ablation metrics.

Each metric measures one coverage-related quantity for a cell (a seeds
directory under a (target, variant, model) triple) and writes a
`summary.json` under its own `results/ablation_*/m{N}/...` subtree.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from core.targets import TargetSpec


@runtime_checkable
class Metric(Protocol):
    """Uniform interface consumed by `scripts._ablation_base.AblationRunner`."""

    # Short name used for the CLI phase flag (e.g. "m1", "m2").
    name: str
    # Subdirectory of `target.results_root` where per-cell summaries land.
    results_subdir: str

    def compute_cell(
        self, seeds_dir: Path, target: TargetSpec, out_dir: Path,
    ) -> dict:
        """Replay `seeds_dir` against `target` and write `out_dir/summary.json`."""
        ...
