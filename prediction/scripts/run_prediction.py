"""Run Phase 2 coverage prediction for a (target, model, few_shot, context, variant) cell.

Deterministic knobs (plan §2):
  - temperature=0.0, top_p=1.0 for prediction
  - held-out seed=42, few-shot seed=42
  - responses cached at the LLM client level (model, prompt_hash, temperature)

Outputs:
  prediction/results/raw/<target>/<model>/shot<N>/<variant>/<ctx>/<test>.json
  prediction/results/log.jsonl
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import defaults
from core.dataset_schema import PredictionRecord, PromptLogEntry
from core.llm_client import LLMClient
from core.logging_config import get_logger
from prediction.scripts.build_prompt import build_prompts
from prediction.scripts.parse_response import (
    parse_free_text_response,
    parse_json_response,
)

logger = get_logger("utcf.phase2.run")


def _parse_for_variant(content: str, variant: str):
    if variant == "rephrase_a":
        return parse_free_text_response(content)
    return parse_json_response(content)


def run_prediction(
    target: str,
    *,
    model: str,
    few_shot: int,
    context_size: str = "file",
    prompt_variant: str = "primary",
    dataset_root: Path,
    results_root: Path,
    llm_client: LLMClient | None = None,
) -> list[PredictionRecord]:
    prompts = build_prompts(
        target,
        dataset_root=dataset_root,
        few_shot=few_shot,
        context_size=context_size,
        prompt_variant=prompt_variant,
        model=model,
    )
    if not prompts:
        logger.warning("no prompts built", extra={"target": target})
        return []

    client = llm_client or LLMClient()
    cfg = defaults(model)

    out_dir = (
        results_root
        / "raw"
        / target
        / model.replace("/", "_")
        / f"shot{few_shot}"
        / prompt_variant
        / context_size
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = results_root / "log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    records: list[PredictionRecord] = []
    for p in prompts:
        messages = [
            {"role": "system", "content": ""},  # system prompt is already embedded
            {"role": "user", "content": p.rendered},
        ]
        resp = client.complete(
            messages=messages,
            model=model,
            temperature=cfg.temperature,
            top_p=cfg.top_p,
            max_tokens=cfg.max_tokens,
        )

        parsed, status = _parse_for_variant(resp.content, prompt_variant)

        log = PromptLogEntry(
            model=resp.model,
            temperature=resp.temperature,
            top_p=resp.top_p,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            cost_usd=resp.cost_usd,
            latency_ms=resp.latency_ms,
            prompt_hash=resp.prompt_hash,
            timestamp=resp.timestamp,
            generation_wall_clock_s=resp.generation_wall_clock_s,
            target=target,
            phase="phase2",
            experiment_tag=f"shot={few_shot},ctx={context_size},variant={prompt_variant}",
            cached=resp.cached,
        )
        with open(log_path, "a") as fh:
            fh.write(log.model_dump_json() + "\n")

        record = PredictionRecord(
            target=target,
            model=model,
            few_shot_count=few_shot,
            context_size=context_size,
            prompt_variant=prompt_variant,
            test_name=p.target_test_name,
            prediction=parsed,
            parse_status=status,
            raw_response=resp.content,
            log=log,
        )
        records.append(record)

        safe_name = p.target_test_name.replace("/", "_").replace(" ", "_")
        (out_dir / f"{safe_name}.json").write_text(record.model_dump_json(indent=2))

    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--few-shot", type=int, required=True)
    parser.add_argument("--context-size", default="file", choices=["function_only", "file", "multi_file"])
    parser.add_argument("--prompt-variant", default="primary", choices=["primary", "rephrase_a", "rephrase_b"])
    parser.add_argument("--dataset-root", type=Path, default=REPO_ROOT / "dataset" / "dataset")
    parser.add_argument("--results-root", type=Path, default=REPO_ROOT / "prediction" / "results")
    args = parser.parse_args()

    records = run_prediction(
        args.target,
        model=args.model,
        few_shot=args.few_shot,
        context_size=args.context_size,
        prompt_variant=args.prompt_variant,
        dataset_root=args.dataset_root,
        results_root=args.results_root,
    )
    ok = sum(1 for r in records if r.parse_status == "ok")
    print(f"predictions={len(records)} parsed={ok} failures={len(records) - ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
