"""Prompt sensitivity ablation (plan §2.5).

For a single (target, model, few_shot) cell, run all three prompt variants
(primary, rephrase_a, rephrase_b) and compare F1 / MAE deltas against the
primary. Parse failures in variant A are reported separately per spec.
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

from core.logging_config import get_logger
from prediction.scripts.evaluate_prediction import (
    _load_truth,
    evaluate_record,
)
from prediction.scripts.run_prediction import run_prediction

logger = get_logger("utcf.phase2.sensitivity")

SENSITIVITY_FLAG_DELTA = 0.10  # absolute; plan §2.5 says >10% flags sensitivity


def run_sensitivity(
    target: str,
    *,
    model: str,
    few_shot: int,
    dataset_root: Path,
    results_root: Path,
) -> dict:
    variants = ("primary", "rephrase_a", "rephrase_b")
    all_records = []
    for v in variants:
        recs = run_prediction(
            target,
            model=model,
            few_shot=few_shot,
            prompt_variant=v,
            dataset_root=dataset_root,
            results_root=results_root,
        )
        all_records.extend(recs)

    truth = _load_truth(dataset_root, target)

    by_variant: dict[str, list] = {v: [] for v in variants}
    parse_failures: dict[str, int] = {v: 0 for v in variants}
    for r in all_records:
        metrics = evaluate_record(r, truth.get(r.test_name))
        by_variant[r.prompt_variant].append(metrics)
        if r.parse_status != "ok":
            parse_failures[r.prompt_variant] += 1

    per_variant = {
        v: {
            "function_f1_mean": fmean([m.function_f1 for m in by_variant[v]]) if by_variant[v] else 0.0,
            "branch_f1_mean": fmean([m.branch_f1 for m in by_variant[v]]) if by_variant[v] else 0.0,
            "coverage_mae_mean": fmean([m.coverage_mae for m in by_variant[v]]) if by_variant[v] else 0.0,
            "n": len(by_variant[v]),
            "parse_failures": parse_failures[v],
        }
        for v in variants
    }
    baseline = per_variant["primary"]
    max_delta = 0.0
    for v in ("rephrase_a", "rephrase_b"):
        for key in ("function_f1_mean", "branch_f1_mean"):
            max_delta = max(max_delta, abs(per_variant[v][key] - baseline[key]))

    report = {
        "target": target,
        "model": model,
        "few_shot": few_shot,
        "per_variant": per_variant,
        "max_abs_delta": max_delta,
        "sensitivity_flag": max_delta > SENSITIVITY_FLAG_DELTA,
    }
    out = results_root / f"prompt_sensitivity.{target}.{model.replace('/', '_')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--few-shot", type=int, default=5)
    parser.add_argument("--dataset-root", type=Path, default=REPO_ROOT / "dataset" / "dataset")
    parser.add_argument("--results-root", type=Path, default=REPO_ROOT / "prediction" / "results")
    args = parser.parse_args()

    report = run_sensitivity(
        args.target,
        model=args.model,
        few_shot=args.few_shot,
        dataset_root=args.dataset_root,
        results_root=args.results_root,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
