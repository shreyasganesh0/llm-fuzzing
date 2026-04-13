"""Experiment 1 vs Experiment 2 comparison (plan §E2.6).

Three comparisons:
  1. Prediction quality — Exp 1 (test-conditioned per-test) vs Exp 2
     (source-only hard-branch prediction), both measured against Phase 1
     coverage_gaps.json.
  2. Seed quality pre-fuzzing — immediate edges for each config.
  3. Fuzzing campaign results — Mann-Whitney + Vargha-Delaney per target,
     classified into outcome A/B/C (plan §E2.6 COMPARISON 3).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analysis.scripts.mann_whitney import mann_whitney_u
from analysis.scripts.vargha_delaney import effect_label, vargha_delaney_a12
from core.config import BUDGET_MATCH_TOLERANCE
from core.dataset_schema import ExperimentComparison
from core.logging_config import get_logger

logger = get_logger("utcf.exp2.compare")


def _load_final_edges(results_root: Path, config_name: str, target: str) -> list[float]:
    """Collect final edge counts across trials for a given config/target."""
    stats_dir = results_root / "campaigns" / target / config_name
    if not stats_dir.is_dir():
        return []
    values: list[float] = []
    for f in sorted(stats_dir.glob("trial_*/final_stats.json")):
        try:
            data = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        if "edges_covered" in data:
            values.append(float(data["edges_covered"]))
        elif "final_edges" in data:
            values.append(float(data["final_edges"]))
    return values


def _classify(p: float, a12: float) -> str:
    """Outcome labels per plan §E2.6."""
    if p >= 0.05:
        return "B_no_difference"
    return "A_test_conditioned_wins" if a12 >= 0.5 else "C_source_only_wins"


def compare_per_target(
    *,
    target: str,
    exp1_results_root: Path,
    exp2_results_root: Path,
    exp1_config: str = "llm_seeds",
    exp2_config: str = "source_only_llm_seeds",
) -> ExperimentComparison:
    exp1 = _load_final_edges(exp1_results_root, exp1_config, target)
    exp2 = _load_final_edges(exp2_results_root, exp2_config, target)

    _, p = mann_whitney_u(exp1, exp2)
    a12 = vargha_delaney_a12(exp1, exp2)

    from statistics import fmean
    mean1 = fmean(exp1) if exp1 else 0.0
    mean2 = fmean(exp2) if exp2 else 0.0

    outcome = _classify(p, a12)
    return ExperimentComparison(
        target=target,
        exp1_mean_edges=mean1,
        exp2_mean_edges=mean2,
        mann_whitney_p=p,
        vargha_delaney_a12=a12,
        outcome=outcome,  # type: ignore[arg-type]
    )


def compare_all(
    *,
    targets: list[str],
    exp1_results_root: Path,
    exp2_results_root: Path,
    out_path: Path,
) -> list[ExperimentComparison]:
    comparisons = [
        compare_per_target(
            target=t,
            exp1_results_root=exp1_results_root,
            exp2_results_root=exp2_results_root,
        )
        for t in targets
    ]
    summary = {
        "per_target": [c.model_dump() for c in comparisons],
        "outcome_counts": {
            "A_test_conditioned_wins": sum(1 for c in comparisons if c.outcome == "A_test_conditioned_wins"),
            "B_no_difference": sum(1 for c in comparisons if c.outcome == "B_no_difference"),
            "C_source_only_wins": sum(1 for c in comparisons if c.outcome == "C_source_only_wins"),
        },
        "effect_size_labels": {c.target: effect_label(c.vargha_delaney_a12) for c in comparisons},
        "budget_match_tolerance": BUDGET_MATCH_TOLERANCE,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2))
    return comparisons


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", nargs="+", required=True)
    parser.add_argument("--exp1-results-root", type=Path, default=REPO_ROOT / "synthesis" / "results")
    parser.add_argument("--exp2-results-root", type=Path, default=REPO_ROOT / "synthesis" / "results")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "synthesis" / "results" / "experiment_comparison.json")
    args = parser.parse_args()
    compare_all(
        targets=args.targets,
        exp1_results_root=args.exp1_results_root,
        exp2_results_root=args.exp2_results_root,
        out_path=args.out,
    )
    print(f"wrote experiment comparison to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
