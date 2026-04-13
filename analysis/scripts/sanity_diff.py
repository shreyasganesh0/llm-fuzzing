"""Differential analysis of the scaled sanity run (exp1_b vs exp2_b).

Reads the per-sample synthesis records, per-model prediction outputs, and the
aggregate sanity_summary.json, then emits a markdown + JSON side-by-side
comparison of Experiment 1 (gap-targeted) and Experiment 2 (source-only)
for each model on the same fixture.

Metrics per (model, experiment):
  - prediction_parse_success:   1 if status='ok' else 0
  - synthesis_samples:           total samples requested
  - synthesis_ok_samples:        samples that parsed successfully
  - synthesis_seeds:             total seeds written across all samples
  - unique_seed_ratio:           unique / total, by content hash
  - output_tokens_total:         sum across synthesis samples
  - tokens_per_seed:             output_tokens / max(seeds, 1)
  - cost_per_seed:               synth cost / max(seeds, 1)
  - loop_truncated_samples:      samples whose out_tok ≤ their cap/8 AND parse_failure
                                 — proxy for the loop-abort path firing

Usage:
    .venv/bin/python -m analysis.scripts.sanity_diff \
        --results-root results/sanity \
        --out analysis/sanity_diff
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@dataclass
class Cell:
    model: str
    experiment: str
    prediction_status: str
    prediction_n_pred: int
    prediction_n_actual: int
    synthesis_samples: int
    synthesis_ok: int
    synthesis_seeds: int
    unique_seed_ratio: float
    output_tokens_total: int
    tokens_per_seed: float
    cost_usd: float
    cost_per_seed: float
    loop_truncated_samples: int

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "experiment": self.experiment,
            "prediction_status": self.prediction_status,
            "prediction_n_pred": self.prediction_n_pred,
            "prediction_n_actual": self.prediction_n_actual,
            "synthesis_samples": self.synthesis_samples,
            "synthesis_ok": self.synthesis_ok,
            "synthesis_seeds": self.synthesis_seeds,
            "unique_seed_ratio": round(self.unique_seed_ratio, 3),
            "output_tokens_total": self.output_tokens_total,
            "tokens_per_seed": round(self.tokens_per_seed, 1),
            "cost_usd": round(self.cost_usd, 6),
            "cost_per_seed": round(self.cost_per_seed, 6),
            "loop_truncated_samples": self.loop_truncated_samples,
        }


def _unique_ratio(seeds_dir: Path) -> tuple[int, int]:
    bins = list(seeds_dir.glob("seed_*.bin"))
    if not bins:
        return 0, 0
    hashes = {hashlib.sha256(b.read_bytes()).hexdigest() for b in bins}
    return len(hashes), len(bins)


def _exp1_cell(model: str, results_root: Path, summary: dict) -> Cell:
    safe = model.replace("/", "_")
    per = summary["models"][model]["exp1_b"]
    pred = per["prediction"]
    synth = per["synthesis"]

    # exp1 prediction is per-test (5 tests); treat it as ok if ≥1 test parsed ok.
    pred_status = "ok" if pred["ok"] > 0 else "parse_failure"

    seeds_dir = results_root / "exp1_b" / "synthesis" / "seeds" / "re2" / "exp1" / safe
    uniq, tot = _unique_ratio(seeds_dir)
    unique_ratio = (uniq / tot) if tot else 0.0

    # Count samples where output_tokens < 75% of the cap AND parse_failure — a
    # proxy for loop-aborted truncation. (Clean cap-hits emit exactly max_tokens.)
    truncated = 0
    for sample_file in sorted(
        (results_root / "exp1_b" / "synthesis" / "synthesis" / "re2" / "exp1" / safe).glob(
            "sample_*.json"
        )
    ):
        rec = json.loads(sample_file.read_text())
        out_tok = rec["log"]["output_tokens"]
        if rec["parse_status"] != "ok" and out_tok > 0 and out_tok < 3072:
            truncated += 1

    seeds = synth["seeds_written"]
    cost_per_seed = (synth["cost_usd"] / seeds) if seeds else 0.0
    tps = (synth["output_tokens"] / seeds) if seeds else float("inf")

    return Cell(
        model=model,
        experiment="exp1_gap_targeted",
        prediction_status=pred_status,
        prediction_n_pred=pred["n_records"],
        prediction_n_actual=pred["ok"],
        synthesis_samples=synth["n_records"],
        synthesis_ok=synth["ok"],
        synthesis_seeds=seeds,
        unique_seed_ratio=unique_ratio,
        output_tokens_total=synth["output_tokens"],
        tokens_per_seed=tps if tps != float("inf") else 0.0,
        cost_usd=synth["cost_usd"],
        cost_per_seed=cost_per_seed,
        loop_truncated_samples=truncated,
    )


def _exp2_cell(model: str, results_root: Path, summary: dict) -> Cell:
    safe = model.replace("/", "_")
    per = summary["models"][model]["exp2_b"]
    pred = per["prediction"]
    synth = per["synthesis"]

    seeds_dir = results_root / "exp2_b" / "synthesis" / "seeds" / "re2" / "source_only" / safe
    uniq, tot = _unique_ratio(seeds_dir)
    unique_ratio = (uniq / tot) if tot else 0.0

    seeds = synth["seeds_written"]
    cost_per_seed = (synth["cost_usd"] / seeds) if seeds else 0.0
    tps = (synth["output_tokens"] / seeds) if seeds else float("inf")

    # exp2 synth doesn't emit per-sample record files, so we can't compute the
    # truncated-sample proxy directly. Use (samples - ok) as a coarse upper bound.
    truncated = synth["n_records"] - synth["ok"]

    return Cell(
        model=model,
        experiment="exp2_source_only",
        prediction_status=pred["status"],
        prediction_n_pred=pred["metrics"]["n_pred"],
        prediction_n_actual=pred["metrics"]["n_actual"],
        synthesis_samples=synth["n_records"],
        synthesis_ok=synth["ok"],
        synthesis_seeds=seeds,
        unique_seed_ratio=unique_ratio,
        output_tokens_total=synth["output_tokens"],
        tokens_per_seed=tps if tps != float("inf") else 0.0,
        cost_usd=synth["cost_usd"],
        cost_per_seed=cost_per_seed,
        loop_truncated_samples=truncated,
    )


def _markdown_table(cells: list[Cell]) -> str:
    # Pivot: one row per metric, one column per (model, experiment).
    headers = [f"{c.model} / {c.experiment}" for c in cells]
    rows: list[tuple[str, list[str]]] = [
        ("prediction_status", [c.prediction_status for c in cells]),
        ("prediction_n_pred", [str(c.prediction_n_pred) for c in cells]),
        ("prediction_n_actual", [str(c.prediction_n_actual) for c in cells]),
        ("synthesis_samples", [str(c.synthesis_samples) for c in cells]),
        ("synthesis_ok", [str(c.synthesis_ok) for c in cells]),
        ("synthesis_seeds", [str(c.synthesis_seeds) for c in cells]),
        ("unique_seed_ratio", [f"{c.unique_seed_ratio:.3f}" for c in cells]),
        ("output_tokens_total", [str(c.output_tokens_total) for c in cells]),
        ("tokens_per_seed", [f"{c.tokens_per_seed:.1f}" for c in cells]),
        ("cost_usd", [f"{c.cost_usd:.6f}" for c in cells]),
        ("cost_per_seed", [f"{c.cost_per_seed:.6f}" for c in cells]),
        ("loop_truncated_samples", [str(c.loop_truncated_samples) for c in cells]),
    ]
    md = ["| metric | " + " | ".join(headers) + " |",
          "|--------|" + "|".join(["---"] * len(headers)) + "|"]
    for name, vals in rows:
        md.append(f"| {name} | " + " | ".join(vals) + " |")
    return "\n".join(md)


def _headline_findings(cells: list[Cell]) -> list[str]:
    by_key = {(c.model, c.experiment): c for c in cells}
    findings: list[str] = []
    for model in sorted({c.model for c in cells}):
        e1 = by_key.get((model, "exp1_gap_targeted"))
        e2 = by_key.get((model, "exp2_source_only"))
        if not e1 or not e2:
            continue
        seeds_diff = e2.synthesis_seeds - e1.synthesis_seeds
        cost_diff = e2.cost_usd - e1.cost_usd
        findings.append(
            f"- **{model}**: exp1={e1.synthesis_seeds} seeds @ "
            f"${e1.cost_usd:.4f}; exp2={e2.synthesis_seeds} seeds @ "
            f"${e2.cost_usd:.4f} (Δ seeds = {seeds_diff:+d}, Δ cost = ${cost_diff:+.4f}). "
            f"exp1 synth_ok={e1.synthesis_ok}/{e1.synthesis_samples}, "
            f"exp2 synth_ok={e2.synthesis_ok}/{e2.synthesis_samples}."
        )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, default=REPO_ROOT / "results" / "sanity")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "analysis" / "sanity_diff")
    args = parser.parse_args()

    summary_path = args.results_root / "sanity_summary.json"
    summary = json.loads(summary_path.read_text())

    args.out.mkdir(parents=True, exist_ok=True)
    cells: list[Cell] = []
    for model in summary["models"]:
        cells.append(_exp1_cell(model, args.results_root, summary))
        cells.append(_exp2_cell(model, args.results_root, summary))

    (args.out / "cells.json").write_text(
        json.dumps([c.to_dict() for c in cells], indent=2)
    )

    md = [
        "# Sanity differential: Exp 1 (gap-targeted) vs Exp 2 (source-only)",
        "",
        f"- Fixture: RE2, {summary['models'].__len__()} models × 2 experiments",
        f"- Total cost (scaled sanity): ${summary['total_cost_usd']:.4f}",
        "",
        "## Headline",
        *_headline_findings(cells),
        "",
        "## Per-cell metrics",
        "",
        _markdown_table(cells),
        "",
        "## Glossary",
        "",
        "- **prediction_status**: whether Phase 2 / source-only prediction produced parseable JSON.",
        "- **synthesis_seeds**: total seed files written to disk across all samples.",
        "- **unique_seed_ratio**: unique(sha256(seed_bytes)) / total_seeds. 1.0 = all distinct.",
        "- **tokens_per_seed**: output tokens spent per written seed (lower is more efficient).",
        "- **loop_truncated_samples**: samples ended by the loop-abort streaming path "
        "(detected as parse_failure with output_tokens well below the cap).",
    ]
    (args.out / "sanity_diff.md").write_text("\n".join(md))
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
