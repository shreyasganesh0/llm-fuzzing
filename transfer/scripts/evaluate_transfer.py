"""Compute the LOO transfer matrix + format-similarity stratification.

For each held-out Tier 1+2 target compute:
  - function F1 / branch F1 / coverage MAE / gap closure rate
  - delta vs within-target Phase 2 baseline

Stratify pair outcomes by (source_target_format, held_out_target_format)
into text_text / text_binary / binary_binary buckets. Tier 3 metrics are
emitted in a separate table since they use a different protocol.

See plan §Phase Transfer T.5.
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

from core.config import (
    TIER3_TARGETS,
    TIER12_TARGETS,
    format_pair,
)
from core.dataset_schema import PredictionRecord, TransferMatrix, TransferRecord
from core.logging_config import get_logger

logger = get_logger("utcf.transfer.eval")

METRICS = ("function_f1", "branch_f1", "coverage_mae", "gap_closure_rate")


def _load_transfer_records(results_root: Path) -> list[TransferRecord]:
    out: list[TransferRecord] = []
    root = results_root / "transfer_prediction_results"
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("transfer_record.json")):
        try:
            out.append(TransferRecord.model_validate_json(path.read_text()))
        except Exception as exc:
            logger.warning("skip transfer record", extra={"path": str(path), "error": str(exc)})
    return out


def _aggregate_record_metrics(records: list[PredictionRecord]) -> dict[str, float]:
    """Pull pre-computed per-record metrics if present; otherwise zero-fill.

    Phase 2's `evaluate_prediction.py` attaches metrics to each
    PredictionRecord. If a transfer record predates that step we still
    return a valid dict (empty cells) so the matrix stays rectangular.
    """
    fn: list[float] = []
    br: list[float] = []
    mae: list[float] = []
    gcr: list[float] = []
    for r in records:
        m = getattr(r, "metrics", None)
        if m is None:
            continue
        if m.function_f1 is not None:
            fn.append(m.function_f1)
        if m.branch_f1 is not None:
            br.append(m.branch_f1)
        if m.coverage_mae is not None:
            mae.append(m.coverage_mae)
        if m.gap_closure_rate is not None:
            gcr.append(m.gap_closure_rate)
    return {
        "function_f1": fmean(fn) if fn else 0.0,
        "branch_f1": fmean(br) if br else 0.0,
        "coverage_mae": fmean(mae) if mae else 0.0,
        "gap_closure_rate": fmean(gcr) if gcr else 0.0,
    }


def _within_target_baseline(phase2_root: Path) -> dict[str, dict[str, float]]:
    """Best-effort read of Phase 2 eval output keyed by target."""
    summary = phase2_root / "summary.json"
    if not summary.is_file():
        return {}
    try:
        raw = json.loads(summary.read_text())
    except Exception:
        return {}
    by_target: dict[str, dict[str, float]] = {}
    for row in raw.get("per_target", []):
        t = row.get("target")
        if not t:
            continue
        by_target[t] = {m: float(row.get(m, 0.0)) for m in METRICS}
    return by_target


def build_matrix(transfer_records: list[TransferRecord]) -> TransferMatrix:
    per_target: dict[str, dict[str, float]] = {}
    for rec in transfer_records:
        if rec.mode != "prediction":
            continue
        per_target[rec.held_out_target] = _aggregate_record_metrics(rec.records)

    rows = sorted(per_target.keys())
    cols = list(METRICS)
    values = [[per_target[r].get(c, 0.0) for c in cols] for r in rows]
    return TransferMatrix(
        metric="composite",
        rows=rows,
        cols=cols,
        values=values,
        per_target_detail=per_target,
    )


def format_stratification(
    transfer_records: list[TransferRecord],
) -> dict[str, float]:
    buckets: dict[str, list[float]] = {"text_text": [], "text_binary": [], "binary_binary": []}
    for rec in transfer_records:
        if rec.mode != "prediction":
            continue
        metrics = _aggregate_record_metrics(rec.records)
        score = metrics.get("branch_f1", 0.0)
        for src in rec.source_targets:
            bucket = format_pair(src, rec.held_out_target)
            buckets[bucket].append(score)
    means = {k: (fmean(v) if v else 0.0) for k, v in buckets.items()}
    means["delta_same_vs_cross"] = means["text_text"] + means["binary_binary"] - 2 * means["text_binary"]
    return means


def evaluate(
    *,
    results_root: Path,
    phase2_results_root: Path,
    out_path: Path,
) -> dict:
    records = _load_transfer_records(results_root)
    tier12 = [r for r in records if r.held_out_target in TIER12_TARGETS]
    tier3 = [r for r in records if r.held_out_target in TIER3_TARGETS]

    matrix = build_matrix(tier12)
    within = _within_target_baseline(phase2_results_root)
    stratification = format_stratification(tier12)

    tier3_rows: dict[str, dict[str, float]] = {}
    for rec in tier3:
        if rec.mode != "prediction":
            continue
        tier3_rows[rec.held_out_target] = _aggregate_record_metrics(rec.records)

    summary = {
        "loo_matrix": {"rows": matrix.rows, "cols": matrix.cols, "values": matrix.values},
        "per_target_detail": matrix.per_target_detail,
        "within_target_baseline": within,
        "format_stratification": stratification,
        "tier3_results": tier3_rows,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, default=REPO_ROOT / "transfer" / "results")
    parser.add_argument("--phase2-results-root", type=Path, default=REPO_ROOT / "prediction" / "results")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "transfer" / "results" / "transfer_evaluation.json")
    args = parser.parse_args()
    summary = evaluate(
        results_root=args.results_root,
        phase2_results_root=args.phase2_results_root,
        out_path=args.out,
    )
    print(f"wrote transfer evaluation to {args.out} — {len(summary['loo_matrix']['rows'])} targets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
