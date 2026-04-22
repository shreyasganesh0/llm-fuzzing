"""M1 — total union edges covered by all seeds in a cell."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from core.targets import TargetSpec


class M1EdgesMetric:
    name = "m1"
    results_subdir = "m1"

    def compute_cell(
        self, seeds_dir: Path, target: TargetSpec, out_dir: Path,
    ) -> dict:
        out_path = out_dir / "summary.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable, "-m", "synthesis.scripts.measure_coverage",
            "--binary", str(target.coverage_binary),
            "--seeds-dir", str(seeds_dir),
            "--source-roots", str(target.source_roots),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(
                f"M1 failed for {seeds_dir}: {r.stderr[-2000:]}"
            )
        metrics = json.loads(r.stdout)
        metrics["seeds_dir"] = str(seeds_dir)
        out_path.write_text(json.dumps(metrics, indent=2))
        return metrics
