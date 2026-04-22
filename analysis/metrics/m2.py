"""M2 — hard-branch hit rate (struct_hits >= 1 AND rand_hits == 0)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from core.targets import TargetSpec


class M2HardBranchMetric:
    name = "m2"
    results_subdir = "m2"

    def compute_cell(
        self, seeds_dir: Path, target: TargetSpec, out_dir: Path,
    ) -> dict:
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable, "-m", "analysis.scripts.measure_gap_coverage",
            "--seeds-dir", str(seeds_dir),
            "--out-dir", str(out_dir),
            "--binary", str(target.coverage_binary),
            "--targets-path", str(target.m2_targets_path),
            "--baseline-profile", str(target.upstream_union_profile_path),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(
                f"M2 failed for {seeds_dir}: {r.stderr[-2000:]}"
            )
        return json.loads((out_dir / "summary.json").read_text())
