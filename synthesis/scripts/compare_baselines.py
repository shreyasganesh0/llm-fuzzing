"""Pairwise baseline comparison + plots (plan §3.8–§3.9).

Runs Mann-Whitney U + Vargha-Delaney Â₁₂ on final_edges for every pair of
campaign configs per target. Produces a JSON summary + CSV (suitable for
Friedman-Nemenyi in analysis/) and an optional coverage-over-time plot.

We depend on `analysis.scripts.mann_whitney` + `analysis.scripts.vargha_delaney`
so the statistical core is reusable across Phase 3, Phase Transfer, and Exp 2.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from itertools import combinations
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analysis.scripts.mann_whitney import mann_whitney_u
from analysis.scripts.vargha_delaney import effect_label, vargha_delaney_a12
from core.dataset_schema import CampaignResult, PairwiseComparison
from core.logging_config import get_logger

logger = get_logger("utcf.phase3.compare")


def load_campaigns(results_root: Path, target: str) -> dict[str, CampaignResult]:
    out: dict[str, CampaignResult] = {}
    camp_dir = results_root / "campaigns" / target
    if not camp_dir.is_dir():
        return out
    for path in sorted(camp_dir.glob("*.json")):
        try:
            cr = CampaignResult.model_validate_json(path.read_text())
            out[cr.config_name] = cr
        except Exception as exc:
            logger.warning("skip campaign", extra={"path": str(path), "error": str(exc)})
    return out


def compare(
    *,
    target: str,
    campaigns: dict[str, CampaignResult],
    metric: str = "final_edges",
) -> list[PairwiseComparison]:
    configs = sorted(campaigns.keys())
    out: list[PairwiseComparison] = []
    for a, b in combinations(configs, 2):
        a_vals = [getattr(t, metric) for t in campaigns[a].trials if t.status == "ok"]
        b_vals = [getattr(t, metric) for t in campaigns[b].trials if t.status == "ok"]
        if not a_vals or not b_vals:
            continue
        u, p = mann_whitney_u(a_vals, b_vals)
        a12 = vargha_delaney_a12(a_vals, b_vals)
        out.append(
            PairwiseComparison(
                target=target,
                config_a=a,
                config_b=b,
                metric=metric,
                mann_whitney_u=u,
                mann_whitney_p=p,
                vargha_delaney_a12=a12,
                effect_label=effect_label(a12),
                significant_at_0_05=p < 0.05,
                n_a=len(a_vals),
                n_b=len(b_vals),
            )
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--metric", default="final_edges")
    parser.add_argument("--results-root", type=Path, default=REPO_ROOT / "synthesis" / "results")
    args = parser.parse_args()

    campaigns = load_campaigns(args.results_root, args.target)
    if not campaigns:
        print(f"no campaigns for {args.target}", file=sys.stderr)
        return 1

    comparisons = compare(target=args.target, campaigns=campaigns, metric=args.metric)
    out_dir = args.results_root / "comparisons"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_out = out_dir / f"{args.target}.json"
    json_out.write_text(json.dumps([c.model_dump() for c in comparisons], indent=2))

    csv_out = out_dir / f"{args.target}.csv"
    with open(csv_out, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["target", "config_a", "config_b", "metric", "u", "p", "a12", "effect", "sig", "n_a", "n_b"])
        for c in comparisons:
            writer.writerow([c.target, c.config_a, c.config_b, c.metric,
                             c.mann_whitney_u, c.mann_whitney_p, c.vargha_delaney_a12,
                             c.effect_label, c.significant_at_0_05, c.n_a, c.n_b])
    print(f"wrote {json_out} and {csv_out} ({len(comparisons)} pairs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
