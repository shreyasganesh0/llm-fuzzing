"""Mini sanity orchestrator (exp1_b / exp2_b).

Runs Phase 2 prediction + Phase 3 synthesis for Exp 1, and source-only
prediction + synthesis for Exp 2, against a small RE2 fixture. Uses the
LiteLLM proxy at UTCF_LITELLM_URL — no GPT-4o/Claude, no libFuzzer,
no GPU.

Entry points:
    python -m sanity.run_sanity --experiment exp1_b
    python -m sanity.run_sanity --experiment exp2_b
    python -m sanity.run_sanity --experiment both

Environment (set automatically if missing):
    UTCF_LITELLM_URL   default: https://api.ai.it.ufl.edu
    UTCF_LLM_RPM       default: 12
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.llm_client import LLMClient
from core.logging_config import get_logger
from prediction.scripts.run_prediction import run_prediction
from sanity.build_fixture import DEFAULT_FIXTURE_ROOT, build_fixture
from sanity.config import (
    SANITY_EXP2_MAX_OUTPUT_TOKENS,
    SANITY_FEW_SHOT,
    SANITY_MODEL_PRIMARY,
    SANITY_MODEL_SECONDARY,
    SANITY_RPM,
    SANITY_SAMPLES,
    SANITY_SEEDS_PER_PROMPT,
    SANITY_SOURCE_MAX_FILES,
    SANITY_SOURCE_TOKEN_BUDGET,
    SANITY_TARGET,
)
from synthesis.scripts.generate_inputs import synthesize
from synthesis.scripts.generate_source_inputs import run_source_synthesis
from synthesis.scripts.run_source_prediction import run_source_prediction

logger = get_logger("utcf.sanity.run")

DEFAULT_RESULTS_ROOT = REPO_ROOT / "results" / "sanity"
DEFAULT_LITELLM_URL = "https://api.ai.it.ufl.edu"


def _ensure_env() -> None:
    os.environ.setdefault("UTCF_LITELLM_URL", DEFAULT_LITELLM_URL)
    os.environ.setdefault("UTCF_LLM_RPM", str(SANITY_RPM))


def _summarize(records: list, label: str) -> dict:
    total_cost = sum((r.log.cost_usd if r.log else 0.0) for r in records)
    total_in = sum((r.log.input_tokens if r.log else 0) for r in records)
    total_out = sum((r.log.output_tokens if r.log else 0) for r in records)
    ok = sum(1 for r in records if r.parse_status == "ok")
    return {
        "label": label,
        "n_records": len(records),
        "ok": ok,
        "parse_failures": len(records) - ok,
        "input_tokens": total_in,
        "output_tokens": total_out,
        "cost_usd": round(total_cost, 6),
    }


def _summarize_source_pred(out: dict, label: str) -> dict:
    return {
        "label": label,
        "status": out.get("status"),
        "n_hard_branches": len(out.get("hard_branches", [])),
        "input_tokens_prompt": out.get("total_tokens", 0),
        "metrics": out.get("metrics", {}),
    }


def run_exp1_b(
    *,
    model: str,
    fixture_root: Path,
    results_root: Path,
    llm_client: LLMClient,
) -> dict:
    logger.info("exp1_b.predict start", extra={"model": model})
    pred_records = run_prediction(
        SANITY_TARGET,
        model=model,
        few_shot=SANITY_FEW_SHOT,
        context_size="file",
        prompt_variant="primary",
        dataset_root=fixture_root,
        results_root=results_root / "exp1_b" / "prediction",
        llm_client=llm_client,
    )
    pred_summary = _summarize(pred_records, "exp1_b.prediction")

    logger.info("exp1_b.synthesize start", extra={"model": model})
    synth_records = synthesize(
        SANITY_TARGET,
        model=model,
        dataset_root=fixture_root,
        results_root=results_root / "exp1_b" / "synthesis",
        samples=SANITY_SAMPLES,
        experiment="exp1",
        llm_client=llm_client,
    )
    synth_summary = _summarize(synth_records, "exp1_b.synthesis")
    synth_summary["seeds_written"] = sum(len(r.inputs) for r in synth_records)
    return {"prediction": pred_summary, "synthesis": synth_summary}


def run_exp2_b(
    *,
    model: str,
    fixture_root: Path,
    results_root: Path,
) -> dict:
    logger.info("exp2_b.predict start", extra={"model": model})
    pred_out = run_source_prediction(
        target=SANITY_TARGET,
        model=model,
        dataset_root=fixture_root,
        results_root=results_root / "exp2_b" / "prediction",
        source_max_files=SANITY_SOURCE_MAX_FILES,
        source_token_budget=SANITY_SOURCE_TOKEN_BUDGET,
        max_tokens=SANITY_EXP2_MAX_OUTPUT_TOKENS,
    )
    pred_summary = _summarize_source_pred(pred_out, "exp2_b.prediction")

    logger.info("exp2_b.synthesize start", extra={"model": model})
    synth_records = run_source_synthesis(
        target=SANITY_TARGET,
        model=model,
        results_root=results_root / "exp2_b" / "synthesis",
        num_inputs=SANITY_SEEDS_PER_PROMPT,
        samples=SANITY_SAMPLES,
        source_max_files=SANITY_SOURCE_MAX_FILES,
        source_token_budget=SANITY_SOURCE_TOKEN_BUDGET,
        max_tokens=SANITY_EXP2_MAX_OUTPUT_TOKENS,
    )
    synth_summary = _summarize(synth_records, "exp2_b.synthesis")
    synth_summary["seeds_written"] = sum(len(r.inputs) for r in synth_records)
    return {"prediction": pred_summary, "synthesis": synth_summary}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", default="both", choices=["exp1_b", "exp2_b", "both"])
    parser.add_argument("--model", default=SANITY_MODEL_PRIMARY)
    parser.add_argument("--secondary-model", default=None,
                        help="Optional second model for contrast; default: none. "
                             f"Try {SANITY_MODEL_SECONDARY!r} for a cheaper contrast run.")
    parser.add_argument("--fixture-root", type=Path, default=DEFAULT_FIXTURE_ROOT)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--skip-fixture-build", action="store_true",
                        help="Assume the fixture already exists (useful for repeat runs)")
    args = parser.parse_args()

    _ensure_env()

    if not args.skip_fixture_build:
        build_fixture(target=SANITY_TARGET, fixture_root=args.fixture_root)

    results_root = args.results_root
    results_root.mkdir(parents=True, exist_ok=True)

    client = LLMClient()
    logger.info("LLM client ready", extra={"provider": client.provider, "base_url": client.base_url})

    models = [args.model] + ([args.secondary_model] if args.secondary_model else [])
    full_summary: dict = {"models": {}}
    for m in models:
        per_model: dict = {}
        if args.experiment in ("exp1_b", "both"):
            per_model["exp1_b"] = run_exp1_b(
                model=m,
                fixture_root=args.fixture_root,
                results_root=results_root,
                llm_client=client,
            )
        if args.experiment in ("exp2_b", "both"):
            per_model["exp2_b"] = run_exp2_b(
                model=m,
                fixture_root=args.fixture_root,
                results_root=results_root,
            )
        full_summary["models"][m] = per_model

    total_cost = 0.0
    for per in full_summary["models"].values():
        for exp in per.values():
            for stage in exp.values():
                total_cost += stage.get("cost_usd", 0.0)
    full_summary["total_cost_usd"] = round(total_cost, 6)

    summary_path = results_root / "sanity_summary.json"
    summary_path.write_text(json.dumps(full_summary, indent=2))
    print(json.dumps(full_summary, indent=2))
    print(f"\nwrote summary to {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
