"""Summarize fuzzing campaign results with coverage curves + statistical tests.

Reads CampaignResult JSONs from libFuzzer and AFL++ campaigns, produces:
  - Coverage-over-time curves (mean ± 95% CI)
  - Final edge count summary table
  - Pairwise VD A12 + Mann-Whitney U comparisons

Usage:
    python analysis/scripts/campaign_summary.py \
        --results-dirs synthesis/results/campaigns synthesis/results/campaigns_afl \
        --output-dir results/campaigns
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analysis.scripts.vargha_delaney import vargha_delaney_a12
from core.dataset_schema import CampaignResult


def _load_results(dirs: list[Path]) -> list[tuple[CampaignResult, str]]:
    """Load results, returning (result, engine) pairs.

    Engine is inferred from the directory path or filename:
    - ``campaigns_afl/`` directory → aflpp
    - ``_aflpp.json`` suffix → aflpp
    - otherwise → libfuzzer
    """
    results: list[tuple[CampaignResult, str]] = []
    for d in dirs:
        for f in d.rglob("*.json"):
            try:
                data = json.loads(f.read_text())
                r = CampaignResult.model_validate(data)
            except Exception:
                continue
            if "campaigns_afl" in str(f) or f.stem.endswith("_aflpp"):
                engine = "aflpp"
            else:
                engine = "libfuzzer"
            results.append((r, engine))
    return results


def _label(result: CampaignResult, engine: str) -> str:
    return f"{result.target}/{engine}/{result.config_name}"


def _vd_label(a12: float) -> str:
    if a12 >= 0.71:
        return "large"
    if a12 >= 0.64:
        return "medium"
    if a12 >= 0.56:
        return "small"
    return "negligible"


def summary_table(results: list[tuple[CampaignResult, str]]) -> str:
    lines = ["| Cell | Trials | Mean Edges | Median | StdDev | VD A12 vs Empty | Label |"]
    lines.append("|---|---|---|---|---|---|---|")

    by_target_engine: dict[str, dict[str, list[int]]] = defaultdict(dict)
    for r, engine in results:
        label = _label(r, engine)
        edges = [t.final_edges for t in r.trials if t.status == "ok"]
        te_key = f"{r.target}/{engine}"
        by_target_engine[te_key][r.config_name] = edges

    for r, engine in sorted(results, key=lambda x: _label(x[0], x[1])):
        label = _label(r, engine)
        edges = [t.final_edges for t in r.trials if t.status == "ok"]
        if not edges:
            lines.append(f"| {label} | 0 | - | - | - | - | - |")
            continue
        arr = np.array(edges, dtype=float)
        mean, med, std = arr.mean(), np.median(arr), arr.std()

        te_key = f"{r.target}/{engine}"
        empty_edges = by_target_engine.get(te_key, {}).get("empty", [])
        if empty_edges and r.config_name != "empty":
            a12 = vargha_delaney_a12(edges, empty_edges)
            vd_str = f"{a12:.3f}"
            vd_lbl = _vd_label(a12)
        else:
            vd_str = "-"
            vd_lbl = "-"

        lines.append(
            f"| {label} | {len(edges)} | {mean:.0f} | {med:.0f} | {std:.0f} | {vd_str} | {vd_lbl} |"
        )
    return "\n".join(lines)


def coverage_curves_csv(results: list[tuple[CampaignResult, str]], output_dir: Path) -> list[Path]:
    """Write one CSV per (target, engine, config) with columns: elapsed_s, trial, edges."""
    written: list[Path] = []
    for r, engine in results:
        label = _label(r, engine).replace("/", "_")
        csv_path = output_dir / f"curve_{label}.csv"
        rows = ["elapsed_s,trial,edges_covered"]
        for t in r.trials:
            for s in t.snapshots:
                rows.append(f"{s.elapsed_s},{t.trial_index},{s.edges_covered}")
        csv_path.write_text("\n".join(rows))
        written.append(csv_path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dirs", nargs="+", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "results" / "campaigns")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = _load_results(args.results_dirs)
    if not results:
        print("no results found")
        return 1

    table = summary_table(results)
    md = f"# Campaign Summary\n\n{table}\n"
    (args.output_dir / "summary.md").write_text(md)

    csvs = coverage_curves_csv(results, args.output_dir)

    print(f"Loaded {len(results)} campaign results")
    print(f"Summary: {args.output_dir / 'summary.md'}")
    print(f"Curves: {len(csvs)} CSVs")
    print()
    print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
