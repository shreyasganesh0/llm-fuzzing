"""Run LOO coverage prediction for a held-out target.

Mirrors `prediction.scripts.run_prediction` but feeds cross-target
LOO prompts. Output is a `TransferRecord` with one `PredictionRecord` per
held-out test. Dry-run mode skips the LLM call and returns stub records —
useful for tests.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import defaults
from core.dataset_schema import PredictionRecord, PromptLogEntry, TransferRecord
from core.logging_config import get_logger
from prediction.scripts.parse_response import parse_json_response
from transfer.scripts.build_loo_prompt import build_loo_prediction_prompts

logger = get_logger("utcf.transfer.predict")


def _stub_log(model: str, target: str) -> PromptLogEntry:
    return PromptLogEntry(
        model=model,
        temperature=0.0,
        top_p=1.0,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        latency_ms=0,
        prompt_hash="dry-run",
        timestamp="1970-01-01T00:00:00Z",
        generation_wall_clock_s=0.0,
        target=target,
        phase="transfer",
        experiment_tag="loo",
        cached=True,
    )


def run_transfer_prediction(
    *,
    held_out_target: str,
    model: str,
    dataset_root: Path,
    results_root: Path,
    dry_run: bool = False,
    few_shot: int = 5,
) -> TransferRecord:
    prompts = build_loo_prediction_prompts(
        held_out_target=held_out_target,
        dataset_root=dataset_root,
        few_shot=few_shot,
        model=model,
    )
    records: list[PredictionRecord] = []
    source_targets: list[str] = []
    if prompts:
        source_targets = prompts[0].few_shot_sources

    out_dir = results_root / "transfer_prediction_results" / held_out_target / model.replace("/", "_")
    out_dir.mkdir(parents=True, exist_ok=True)

    if dry_run or not prompts:
        for p in prompts:
            records.append(
                PredictionRecord(
                    target=held_out_target,
                    model=model,
                    few_shot_count=few_shot,
                    context_size="file",
                    prompt_variant="primary",
                    test_name=p.target_test_name or "unknown",
                    prediction=None,
                    parse_status="dry_run",
                    raw_response="",
                    log=_stub_log(model, held_out_target),
                )
            )
    else:
        from core.llm_client import LLMClient
        client = LLMClient()
        cfg = defaults(model)
        for p in prompts:
            resp = client.complete(
                messages=[
                    {"role": "system", "content": ""},
                    {"role": "user", "content": p.rendered},
                ],
                model=model,
                temperature=cfg.temperature,
                top_p=cfg.top_p,
                max_tokens=cfg.max_tokens,
            )
            parsed, status = parse_json_response(resp.content)
            records.append(
                PredictionRecord(
                    target=held_out_target,
                    model=model,
                    few_shot_count=few_shot,
                    context_size="file",
                    prompt_variant="primary",
                    test_name=p.target_test_name or "unknown",
                    prediction=parsed,
                    parse_status=status,
                    raw_response=resp.content,
                    log=PromptLogEntry(
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
                        target=held_out_target,
                        phase="transfer",
                        experiment_tag="loo",
                        cached=resp.cached,
                    ),
                )
            )

    transfer = TransferRecord(
        held_out_target=held_out_target,
        source_targets=source_targets,
        model=model,
        mode="prediction",
        records=records,
    )
    (out_dir / "transfer_record.json").write_text(transfer.model_dump_json(indent=2))
    return transfer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--held-out-target", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--dataset-root", type=Path, default=REPO_ROOT / "dataset" / "dataset")
    parser.add_argument("--results-root", type=Path, default=REPO_ROOT / "transfer" / "results")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    rec = run_transfer_prediction(
        held_out_target=args.held_out_target,
        model=args.model,
        dataset_root=args.dataset_root,
        results_root=args.results_root,
        dry_run=args.dry_run,
    )
    print(f"transfer predictions={len(rec.records)} sources={rec.source_targets}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
