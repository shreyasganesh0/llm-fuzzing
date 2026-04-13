"""Detect corpus pollution + low seed-survival (plan §3.7 / TV6).

Compares a configuration (typically `combined_seeds`) to the unit-test-only
baseline across 20 trials. Reports:
  - mean edge delta at 23h
  - whether the config *hurts* the baseline
  - seed-survival rates at 1h and 23h (approximated from snapshot corpus size)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import fmean

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.dataset_schema import CampaignResult, FailureAnalysisReport
from core.logging_config import get_logger

logger = get_logger("utcf.phase3.failure")


def _load_campaign(results_root: Path, target: str, config_name: str) -> CampaignResult | None:
    path = results_root / "campaigns" / target / f"{config_name}.json"
    if not path.is_file():
        return None
    return CampaignResult.model_validate_json(path.read_text())


def _seed_survival(trial, *, elapsed_s: int, seed_count_start: int) -> float:
    if seed_count_start <= 0:
        return 0.0
    for snap in trial.snapshots:
        if snap.elapsed_s >= elapsed_s:
            # Survival approximation: corpus_size never shrinks, so we use
            # min(corpus_size, seed_count_start) / seed_count_start.
            return min(snap.corpus_size, seed_count_start) / seed_count_start
    return 1.0


def analyse(
    *,
    target: str,
    config_name: str,
    baseline_config: str,
    results_root: Path,
    seed_count_start: int = 0,
) -> FailureAnalysisReport:
    target_result = _load_campaign(results_root, target, config_name)
    baseline_result = _load_campaign(results_root, target, baseline_config)
    if target_result is None or baseline_result is None:
        return FailureAnalysisReport(
            target=target,
            config_name=config_name,
            hurts_vs_baseline=False,
            baseline_config=baseline_config,
            notes="missing campaign result(s); analysis skipped",
        )

    target_edges = [t.final_edges for t in target_result.trials if t.status == "ok"]
    base_edges = [t.final_edges for t in baseline_result.trials if t.status == "ok"]
    mean_diff = fmean(target_edges) - fmean(base_edges) if target_edges and base_edges else 0.0
    hurts = mean_diff < 0

    if target_result.trials and seed_count_start > 0:
        survival_1h = fmean(
            _seed_survival(t, elapsed_s=3600, seed_count_start=seed_count_start)
            for t in target_result.trials
        )
        survival_23h = fmean(
            _seed_survival(t, elapsed_s=82800, seed_count_start=seed_count_start)
            for t in target_result.trials
        )
    else:
        survival_1h = survival_23h = 0.0

    return FailureAnalysisReport(
        target=target,
        config_name=config_name,
        hurts_vs_baseline=hurts,
        baseline_config=baseline_config,
        mean_edges_diff=mean_diff,
        seed_survival_at_1h=survival_1h,
        seed_survival_at_23h=survival_23h,
        notes=("config underperforms baseline" if hurts else "config matches or beats baseline"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--config-name", required=True)
    parser.add_argument("--baseline-config", default="unittest_seeds")
    parser.add_argument("--seed-count-start", type=int, default=0)
    parser.add_argument("--results-root", type=Path, default=REPO_ROOT / "synthesis" / "results")
    args = parser.parse_args()

    report = analyse(
        target=args.target,
        config_name=args.config_name,
        baseline_config=args.baseline_config,
        results_root=args.results_root,
        seed_count_start=args.seed_count_start,
    )
    out = args.results_root / "failure_analysis" / f"{args.target}_{args.config_name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.model_dump_json(indent=2))
    print(json.dumps(report.model_dump(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
