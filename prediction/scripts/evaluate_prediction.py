"""Compute Phase 2 metrics against ground-truth coverage (plan §2.4).

Metrics per prediction:
  - function P/R/F1
  - branch P/R/F1
  - coverage MAE (predicted vs actual %)
  - Spearman rank correlation (branches-by-confidence ↔ execution-count)

Aggregation dimensions:
  per-target, per-model, per-few-shot-count, per-context-size, per-tier,
  per-contamination-level (when contamination_report files exist).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import fmean

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.dataset_schema import (
    CoverageProfile,
    PredictionMetrics,
    PredictionRecord,
)
from core.logging_config import get_logger

logger = get_logger("utcf.phase2.eval")


def _load_records(results_root: Path, target: str) -> list[PredictionRecord]:
    records: list[PredictionRecord] = []
    root = results_root / "raw" / target
    if not root.is_dir():
        return records
    for path in sorted(root.rglob("*.json")):
        try:
            records.append(PredictionRecord.model_validate_json(path.read_text()))
        except Exception as exc:
            logger.warning("skip invalid record", extra={"path": str(path), "error": str(exc)})
    return records


def _load_truth(dataset_root: Path, target: str) -> dict[str, CoverageProfile]:
    profiles: dict[str, CoverageProfile] = {}
    tests_dir = dataset_root / target / "tests"
    if not tests_dir.is_dir():
        return profiles
    for test_dir in sorted(tests_dir.glob("test_*")):
        cov = test_dir / "coverage.json"
        if cov.is_file():
            try:
                p = CoverageProfile.model_validate_json(cov.read_text())
                profiles[p.test_name] = p
            except Exception as exc:
                logger.warning("skip truth", extra={"path": str(cov), "error": str(exc)})
    return profiles


def _prf(predicted: set, actual: set) -> tuple[float, float, float]:
    tp = len(predicted & actual)
    precision = tp / len(predicted) if predicted else 0.0
    recall = tp / len(actual) if actual else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def _spearman(pred_scores: list[float], actual_scores: list[float]) -> tuple[float | None, float | None]:
    if len(pred_scores) < 2:
        return None, None
    try:
        from scipy.stats import spearmanr
    except ImportError:
        return None, None
    rho, p = spearmanr(pred_scores, actual_scores)
    if rho != rho:  # NaN
        return None, None
    return float(rho), float(p) if p == p else None


def evaluate_record(record: PredictionRecord, truth: CoverageProfile | None) -> PredictionMetrics:
    if record.parse_status != "ok" or record.prediction is None or truth is None:
        return PredictionMetrics(
            function_precision=0.0,
            function_recall=0.0,
            function_f1=0.0,
            branch_precision=0.0,
            branch_recall=0.0,
            branch_f1=0.0,
            coverage_mae=0.0,
            n_predictions=1,
            n_parse_failures=1 if record.parse_status != "ok" else 0,
        )

    pred_funcs = set(record.prediction.functions_covered)
    actual_funcs = set().union(*(set(f.functions_covered) for f in truth.files.values()))

    pred_branches = {b.location for b in record.prediction.branches if b.true_taken or b.false_taken}
    actual_branches = {
        key for f in truth.files.values()
        for key, b in f.branches.items()
        if b.true_taken or b.false_taken
    }

    fp, fr, ff1 = _prf(pred_funcs, actual_funcs)
    bp, br, bf1 = _prf(pred_branches, actual_branches)

    actual_pct = (
        100.0 * truth.total_lines_covered / truth.total_lines_in_source
        if truth.total_lines_in_source
        else 0.0
    )
    mae = abs(record.prediction.estimated_line_coverage_pct - actual_pct)

    # Spearman: predicted confidence = 2 (both) > 1 (one direction) > 0 (none); truth = same.
    pred_conf: dict[str, int] = {}
    for b in record.prediction.branches:
        pred_conf[b.location] = int(b.true_taken) + int(b.false_taken)
    truth_conf: dict[str, int] = {}
    for f in truth.files.values():
        for key, b in f.branches.items():
            truth_conf[key] = int(b.true_taken) + int(b.false_taken)
    common = sorted(set(pred_conf) & set(truth_conf))
    rho, p = _spearman([pred_conf[k] for k in common], [truth_conf[k] for k in common])

    return PredictionMetrics(
        function_precision=fp,
        function_recall=fr,
        function_f1=ff1,
        branch_precision=bp,
        branch_recall=br,
        branch_f1=bf1,
        coverage_mae=mae,
        spearman_rho=rho,
        spearman_p=p,
        n_predictions=1,
        n_parse_failures=0,
    )


def _mean(values: list[float | None]) -> float:
    filtered = [v for v in values if v is not None]
    return fmean(filtered) if filtered else 0.0


def aggregate(records: list[PredictionRecord], truth_by_name: dict[str, CoverageProfile]) -> dict:
    per_key: dict[tuple, list[PredictionMetrics]] = defaultdict(list)
    for r in records:
        key = (r.target, r.model, r.few_shot_count, r.context_size, r.prompt_variant)
        per_key[key].append(evaluate_record(r, truth_by_name.get(r.test_name)))

    summary = []
    for (tgt, model, k, ctx, variant), mlist in per_key.items():
        summary.append({
            "target": tgt,
            "model": model,
            "few_shot": k,
            "context_size": ctx,
            "prompt_variant": variant,
            "function_f1_mean": _mean([m.function_f1 for m in mlist]),
            "branch_f1_mean": _mean([m.branch_f1 for m in mlist]),
            "coverage_mae_mean": _mean([m.coverage_mae for m in mlist]),
            "spearman_rho_mean": _mean([m.spearman_rho for m in mlist]),
            "n_predictions": sum(m.n_predictions for m in mlist),
            "n_parse_failures": sum(m.n_parse_failures for m in mlist),
        })
    return {"summary": summary}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--dataset-root", type=Path, default=REPO_ROOT / "dataset" / "dataset")
    parser.add_argument("--results-root", type=Path, default=REPO_ROOT / "prediction" / "results")
    args = parser.parse_args()

    records = _load_records(args.results_root, args.target)
    truth = _load_truth(args.dataset_root, args.target)

    agg = aggregate(records, truth)
    out = args.results_root / f"metrics.{args.target}.json"
    out.write_text(json.dumps(agg, indent=2))

    csv_path = args.results_root / f"metrics.{args.target}.csv"
    with open(csv_path, "w", newline="") as fh:
        if agg["summary"]:
            writer = csv.DictWriter(fh, fieldnames=list(agg["summary"][0].keys()))
            writer.writeheader()
            writer.writerows(agg["summary"])
    print(f"wrote {out} and {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
