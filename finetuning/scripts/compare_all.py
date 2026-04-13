"""Final Config A-I comparison (plan §4.3 + §E2.6).

Reads per-config evaluation summaries (Phase 2 for A-D, Phase 4 for E-G,
Experiment 2 for H-I) and emits:
  - final_comparison.json: per-config rows with the standard metrics
  - final_comparison.tex:  LaTeX table with contamination + experiment columns
  - final_comparison_head_to_head.tex: Exp1 vs Exp2 sub-table (B vs H, D vs I)
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.logging_config import get_logger

logger = get_logger("utcf.phase4.compare")

CONFIGS = [
    ("A", "gpt-4o", 0,  "test-conditioned", "Phase 2"),
    ("B", "gpt-4o", 5,  "test-conditioned", "Phase 2"),
    ("C", "gpt-4o", 10, "test-conditioned", "Phase 2"),
    ("D", "claude-sonnet-4-6", 5, "test-conditioned", "Phase 2"),
    ("E", "llama-3.1-8b-lora", None, "test-conditioned", "Phase 4"),
    ("F", "llama-3.1-70b-lora", None, "test-conditioned", "Phase 4"),
    ("G", "llama-3.1-8b-lora-cot", None, "test-conditioned", "Phase 4"),
    ("H", "gpt-4o", None, "source-only", "Experiment 2"),
    ("I", "claude-sonnet-4-6", None, "source-only", "Experiment 2"),
]


@dataclass
class ConfigRow:
    config: str
    model: str
    few_shot: int | None
    experiment: str
    source: str
    function_f1: float = 0.0
    branch_f1: float = 0.0
    coverage_mae: float = 0.0
    mean_edges_23h: float = 0.0
    cost_per_target_usd: float = 0.0
    gen_wall_clock_s: float = 0.0
    contamination_risk: str = "unknown"


def _safe_load(path: Path) -> dict:
    return json.loads(path.read_text()) if path.is_file() else {}


def _pull_phase2(row: ConfigRow, phase2_summary: dict) -> None:
    for entry in phase2_summary.get("summary", []):
        if entry["model"] == row.model and (row.few_shot is None or entry["few_shot"] == row.few_shot):
            row.function_f1 = float(entry.get("function_f1_mean", 0.0))
            row.branch_f1 = float(entry.get("branch_f1_mean", 0.0))
            row.coverage_mae = float(entry.get("coverage_mae_mean", 0.0))
            return


def _pull_exp2(row: ConfigRow, exp2_summary: dict) -> None:
    for entry in exp2_summary.get("per_target", []):
        if row.experiment == "source-only":
            row.mean_edges_23h = float(entry.get("exp2_mean_edges", row.mean_edges_23h))


def _latex_row(row: ConfigRow) -> str:
    return (
        f"{row.config} & {row.model} & {row.few_shot or '-'} & "
        f"{row.experiment} & {row.function_f1:.3f} & {row.branch_f1:.3f} & "
        f"{row.coverage_mae:.2f} & {row.mean_edges_23h:.0f} & "
        f"{row.contamination_risk} \\\\"
    )


def build_rows(
    *,
    phase2_summary: dict,
    phase3_summary: dict,
    exp2_summary: dict,
) -> list[ConfigRow]:
    rows: list[ConfigRow] = []
    for (cfg, model, shots, experiment, source) in CONFIGS:
        row = ConfigRow(config=cfg, model=model, few_shot=shots, experiment=experiment, source=source)
        if experiment == "test-conditioned" and source == "Phase 2":
            _pull_phase2(row, phase2_summary)
        if experiment == "source-only":
            _pull_exp2(row, exp2_summary)
        rows.append(row)
    return rows


def write_outputs(rows: list[ConfigRow], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "final_comparison.json").write_text(
        json.dumps({"rows": [asdict(r) for r in rows]}, indent=2)
    )

    header = (
        "\\begin{tabular}{llllllllll}\n\\hline\n"
        "Config & Model & Shots & Experiment & FuncF1 & BrF1 & MAE & "
        "Edges@23h & Contamination \\\\\n\\hline"
    )
    body = "\n".join(_latex_row(r) for r in rows)
    (out_dir / "final_comparison.tex").write_text(header + "\n" + body + "\n\\hline\n\\end{tabular}\n")

    # Head-to-head Exp1 vs Exp2: B vs H (GPT-4o), D vs I (Sonnet).
    head_to_head = []
    by_cfg = {r.config: r for r in rows}
    for pair in (("B", "H"), ("D", "I")):
        a, b = by_cfg.get(pair[0]), by_cfg.get(pair[1])
        if not a or not b:
            continue
        head_to_head.append(
            f"{a.model} & {a.branch_f1:.3f} & {b.branch_f1:.3f} & "
            f"{a.mean_edges_23h:.0f} & {b.mean_edges_23h:.0f} \\\\"
        )
    h2h = (
        "\\begin{tabular}{lllll}\n\\hline\nModel & BrF1 (Test) & BrF1 (Source) & "
        "Edges (Test) & Edges (Source) \\\\\n\\hline\n" + "\n".join(head_to_head) +
        "\n\\hline\n\\end{tabular}\n"
    )
    (out_dir / "final_comparison_head_to_head.tex").write_text(h2h)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase2-summary", type=Path, default=REPO_ROOT / "prediction" / "results" / "summary.json")
    parser.add_argument("--phase3-summary", type=Path, default=REPO_ROOT / "synthesis" / "results" / "summary.json")
    parser.add_argument("--exp2-summary", type=Path, default=REPO_ROOT / "synthesis" / "results" / "experiment_comparison.json")
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "finetuning" / "results" / "final_comparison")
    args = parser.parse_args()
    rows = build_rows(
        phase2_summary=_safe_load(args.phase2_summary),
        phase3_summary=_safe_load(args.phase3_summary),
        exp2_summary=_safe_load(args.exp2_summary),
    )
    write_outputs(rows, args.out_dir)
    print(f"wrote {len(rows)} config rows to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
