"""Generate threat-to-validity evidence tables (plan §Threats to Validity).

Walks the results trees and produces per-threat evidence rows:
  TV1 (contamination):    dataset/data/<target>/contamination_report.json
  TV2 (params logged):    prediction/results/log.jsonl sanity counts
  TV3 (version gap):      pinned_versions.yaml dates vs model knowledge cutoff
  TV4 (bimodality):       phase3 final_edges distribution sanity
  TV5 (prompt sensitivity): phase2 prompt_sensitivity.json max delta
  TV6 (corpus pollution): phase3 failure_analysis outputs
  TV7 (token budgets):    experiment2 exp1_total_tokens vs exp2_total_tokens
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import fmean, stdev

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.logging_config import get_logger

logger = get_logger("utcf.analysis.tv")


def _read_json(path: Path) -> object | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def tv1_contamination(dataset_root: Path) -> list[dict]:
    rows = []
    for report in sorted(dataset_root.glob("*/contamination_report.json")):
        data = _read_json(report) or {}
        rows.append({
            "target": report.parent.name,
            "risk": data.get("contamination_risk_level", "unknown"),
            "verbatim_em": data.get("verbatim_exact_match_rate", 0.0),
        })
    return rows


def tv4_bimodality(phase3_root: Path) -> list[dict]:
    rows = []
    for result in sorted(phase3_root.glob("campaigns/*/*.json")):
        data = _read_json(result) or {}
        trials = data.get("trials", [])
        edges = [t.get("final_edges", 0) for t in trials]
        if len(edges) < 2:
            continue
        mean = fmean(edges)
        sd = stdev(edges)
        cv = sd / mean if mean else 0.0
        rows.append({
            "target": result.parent.name,
            "config": data.get("config_name", ""),
            "mean_edges": mean,
            "stdev_edges": sd,
            "cv": cv,
            "flag_bimodal": cv > 0.5,
        })
    return rows


def tv5_sensitivity(phase2_root: Path) -> list[dict]:
    rows = []
    for report in sorted(phase2_root.glob("prompt_sensitivity.json")):
        data = _read_json(report) or {}
        rows.append(data if isinstance(data, dict) else {"data": data})
    return rows


def tv6_pollution(phase3_root: Path) -> list[dict]:
    rows = []
    for report in sorted(phase3_root.glob("failure_analysis/*.json")):
        data = _read_json(report) or {}
        rows.append(data)
    return rows


def tv7_tokens(exp2_root: Path) -> list[dict]:
    rows = []
    for report in sorted(exp2_root.glob("experiment_comparison/*.json")):
        data = _read_json(report) or {}
        rows.append(data)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, default=REPO_ROOT / "dataset" / "dataset")
    parser.add_argument("--phase2-root", type=Path, default=REPO_ROOT / "prediction" / "results")
    parser.add_argument("--phase3-root", type=Path, default=REPO_ROOT / "synthesis" / "results")
    parser.add_argument("--exp2-root", type=Path, default=REPO_ROOT / "synthesis" / "results")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "analysis" / "figures" / "threat_tables.json")
    args = parser.parse_args()

    tables = {
        "TV1_contamination": tv1_contamination(args.dataset_root),
        "TV4_bimodality": tv4_bimodality(args.phase3_root),
        "TV5_prompt_sensitivity": tv5_sensitivity(args.phase2_root),
        "TV6_corpus_pollution": tv6_pollution(args.phase3_root),
        "TV7_token_budgets": tv7_tokens(args.exp2_root),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(tables, indent=2))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
