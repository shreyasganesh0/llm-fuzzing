"""Source-only branch difficulty prediction (plan §E2.3).

The LLM reads source + harness and predicts which branches are hardest
to reach. We compare that prediction against Phase 1's coverage_gaps.json
for precision/recall/F1, but the LLM never sees the ground truth.
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
    PREDICTION_MAX_TOKENS,
    PREDICTION_TEMPERATURE,
    PREDICTION_TOP_P,
    SOURCE_CONTEXT_MAX_FILES,
)
from core.dataset_schema import CoverageGapsReport
from core.logging_config import get_logger
from core.loop_detector import is_degenerate_loop
from synthesis.scripts.build_source_prompt import build_analysis_prompt
from synthesis.scripts.extract_source_context import extract_source_context

logger = get_logger("utcf.exp2.predict")


def _parse_hard_branches(text: str) -> tuple[list[dict], str]:
    import re
    if is_degenerate_loop(text):
        return [], "parse_failure"
    m = re.search(r"\{[\s\S]*\"hard_branches\"[\s\S]*\}", text)
    if not m:
        return [], "parse_failure"
    try:
        data = json.loads(m.group(0), strict=False)
    except json.JSONDecodeError:
        return [], "parse_failure"
    branches = data.get("hard_branches", [])
    if not isinstance(branches, list):
        return [], "parse_failure"
    return branches, "ok"


def _gap_branch_keys(report: CoverageGapsReport) -> set[str]:
    return {f"{b.file}:{b.line}" for b in report.gap_branches}


def _pred_keys(branches: list[dict]) -> set[str]:
    return {f"{b.get('file')}:{b.get('line')}" for b in branches if b.get("file") and b.get("line") is not None}


def _prf(pred: set[str], actual: set[str]) -> tuple[float, float, float]:
    tp = len(pred & actual)
    p = tp / len(pred) if pred else 0.0
    r = tp / len(actual) if actual else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f1


def run_source_prediction(
    *,
    target: str,
    model: str,
    dataset_root: Path,
    results_root: Path,
    dry_run: bool = False,
    source_max_files: int = SOURCE_CONTEXT_MAX_FILES,
    source_token_budget: int | None = None,
    max_tokens: int = PREDICTION_MAX_TOKENS,
) -> dict:
    ctx = extract_source_context(
        target,
        model=model,
        max_files=source_max_files,
        token_budget=source_token_budget,
    )
    prompt = build_analysis_prompt(ctx, num_branches=20)

    out_dir = results_root / "source_prediction_results" / target / model.replace("/", "_")
    out_dir.mkdir(parents=True, exist_ok=True)

    hard_branches: list[dict] = []
    status = "ok"
    raw = ""
    if dry_run:
        status = "dry_run"
    else:
        from core.llm_client import LLMClient
        client = LLMClient()
        resp = client.complete(
            messages=[
                {"role": "system", "content": ""},
                {"role": "user", "content": prompt.rendered},
            ],
            model=model,
            temperature=PREDICTION_TEMPERATURE,
            top_p=PREDICTION_TOP_P,
            max_tokens=max_tokens,
        )
        raw = resp.content
        hard_branches, status = _parse_hard_branches(resp.content)

    # Compare against Phase 1 ground truth (the LLM never saw it).
    gaps_path = dataset_root / target / "coverage_gaps.json"
    metrics = {"precision": 0.0, "recall": 0.0, "f1": 0.0, "n_pred": 0, "n_actual": 0}
    if gaps_path.is_file():
        gaps = CoverageGapsReport.model_validate_json(gaps_path.read_text())
        pred_k = _pred_keys(hard_branches)
        actual_k = _gap_branch_keys(gaps)
        p, r, f1 = _prf(pred_k, actual_k)
        metrics = {"precision": p, "recall": r, "f1": f1, "n_pred": len(pred_k), "n_actual": len(actual_k)}

    out = {
        "target": target,
        "model": model,
        "status": status,
        "hard_branches": hard_branches,
        "metrics": metrics,
        "total_tokens": prompt.total_tokens,
        "source_files": prompt.num_source_files,
        "raw_response": raw,
    }
    (out_dir / "prediction.json").write_text(json.dumps(out, indent=2))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--dataset-root", type=Path, default=REPO_ROOT / "dataset" / "dataset")
    parser.add_argument("--results-root", type=Path, default=REPO_ROOT / "synthesis" / "results")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    out = run_source_prediction(
        target=args.target,
        model=args.model,
        dataset_root=args.dataset_root,
        results_root=args.results_root,
        dry_run=args.dry_run,
    )
    print(json.dumps({"target": out["target"], "metrics": out["metrics"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
