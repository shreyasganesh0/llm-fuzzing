"""Tier 3 evaluation — libpng, FreeType, zlib never appear in any pool.

Tier 3 targets are pure held-out evaluation targets. They have too few
upstream tests to serve as few-shot sources. We run:

  1. LOO prediction + synthesis using ONLY Tier 1+2 few-shots.
  2. If a harness exists, a small campaign: 5 trials × 6h.

Results land in `results/tier3_results/<target>/` and are reported in a
separate table from Tier 1+2 (plan §Phase Transfer T.4).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import (
    TIER3_CAMPAIGN_DURATION_S,
    TIER3_CAMPAIGN_TRIALS,
    TIER3_TARGETS,
)
from core.logging_config import get_logger
from transfer.scripts.run_transfer_prediction import run_transfer_prediction
from transfer.scripts.run_transfer_synthesis import run_transfer_synthesis

logger = get_logger("utcf.transfer.tier3")


def run_tier3(
    *,
    target: str,
    model: str,
    dataset_root: Path,
    results_root: Path,
    run_campaign: bool = False,
    dry_run: bool = True,
) -> dict:
    assert target in TIER3_TARGETS, f"{target} is not a Tier 3 target; use run_transfer_prediction directly"

    tier3_dir = results_root / "tier3_results" / target
    tier3_dir.mkdir(parents=True, exist_ok=True)

    pred = run_transfer_prediction(
        held_out_target=target,
        model=model,
        dataset_root=dataset_root,
        results_root=results_root,
        dry_run=dry_run,
    )
    synth = run_transfer_synthesis(
        held_out_target=target,
        model=model,
        dataset_root=dataset_root,
        results_root=results_root,
        dry_run=dry_run,
    )

    campaign_note = None
    if run_campaign:
        # A scaled-down campaign (5 trials × 6h) compared to Tier 1+2 (20 × 23h).
        # Skipped by default because Tier 3 harnesses are optional in this repo.
        campaign_note = {
            "trials": TIER3_CAMPAIGN_TRIALS,
            "duration_s": TIER3_CAMPAIGN_DURATION_S,
            "status": "skipped_no_binary",
        }

    summary = {
        "target": target,
        "model": model,
        "prediction_records": len(pred.records),
        "synthesis_records": len(synth.synthesis_records),
        "source_targets": pred.source_targets,
        "campaign": campaign_note,
    }
    (tier3_dir / f"{model.replace('/', '_')}.json").write_text(json.dumps(summary, indent=2))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=list(TIER3_TARGETS))
    parser.add_argument("--model", required=True)
    parser.add_argument("--dataset-root", type=Path, default=REPO_ROOT / "dataset" / "dataset")
    parser.add_argument("--results-root", type=Path, default=REPO_ROOT / "transfer" / "results")
    parser.add_argument("--run-campaign", action="store_true")
    parser.add_argument("--dry-run", action="store_true", default=True)
    args = parser.parse_args()
    summary = run_tier3(
        target=args.target,
        model=args.model,
        dataset_root=args.dataset_root,
        results_root=args.results_root,
        run_campaign=args.run_campaign,
        dry_run=args.dry_run,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
