"""Generate gap-filling inputs for a held-out target using cross-target examples.

Builds a synthesis prompt with LOO few-shots, runs 3 samples at T=0.7,
parses the responses into GeneratedInput + SynthesisRecord. Dry-run mode
emits a stub synthesis record without calling any LLM.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import (
    SYNTHESIS_MAX_TOKENS,
    SYNTHESIS_SAMPLES,
    SYNTHESIS_TEMPERATURE,
    SYNTHESIS_TOP_P,
)
from core.dataset_schema import (
    PromptLogEntry,
    SynthesisRecord,
    TransferRecord,
)
from core.logging_config import get_logger
from synthesis.scripts.parse_synthesis import parse_synthesis_response
from transfer.scripts.build_loo_prompt import build_loo_synthesis_prompt

logger = get_logger("utcf.transfer.synth")


def _stub_log(model: str, target: str) -> PromptLogEntry:
    return PromptLogEntry(
        model=model,
        temperature=SYNTHESIS_TEMPERATURE,
        top_p=SYNTHESIS_TOP_P,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        latency_ms=0,
        prompt_hash="dry-run",
        timestamp="1970-01-01T00:00:00Z",
        generation_wall_clock_s=0.0,
        target=target,
        phase="transfer",
        experiment_tag="loo_synthesis",
        cached=True,
    )


def run_transfer_synthesis(
    *,
    held_out_target: str,
    model: str,
    dataset_root: Path,
    results_root: Path,
    dry_run: bool = False,
    samples: int = SYNTHESIS_SAMPLES,
) -> TransferRecord:
    prompt = build_loo_synthesis_prompt(
        held_out_target=held_out_target,
        dataset_root=dataset_root,
        model=model,
    )

    synth_records: list[SynthesisRecord] = []
    out_dir = results_root / "transfer_seeds" / held_out_target / model.replace("/", "_")
    out_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        synth_records.append(
            SynthesisRecord(
                target=held_out_target,
                model=model,
                sample_index=0,
                parse_status="dry_run",
                inputs=[],
                raw_response="",
                log=_stub_log(model, held_out_target),
            )
        )
    else:
        from core.llm_client import LLMClient
        client = LLMClient()
        for i in range(samples):
            resp = client.complete(
                messages=[
                    {"role": "system", "content": ""},
                    {"role": "user", "content": prompt.rendered},
                ],
                model=model,
                temperature=SYNTHESIS_TEMPERATURE,
                top_p=SYNTHESIS_TOP_P,
                max_tokens=SYNTHESIS_MAX_TOKENS,
                cache_salt=f"sample={i},experiment=transfer_synth",
            )
            inputs, status = parse_synthesis_response(
                resp.content,
                target=held_out_target,
                model=model,
                temperature=SYNTHESIS_TEMPERATURE,
                sample_index=i,
            )
            synth_records.append(
                SynthesisRecord(
                    target=held_out_target,
                    model=model,
                    sample_index=i,
                    parse_status=status,
                    inputs=inputs,
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
                        experiment_tag="loo_synthesis",
                        cached=resp.cached,
                    ),
                )
            )

    transfer = TransferRecord(
        held_out_target=held_out_target,
        source_targets=prompt.few_shot_sources,
        model=model,
        mode="synthesis",
        synthesis_records=synth_records,
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
    rec = run_transfer_synthesis(
        held_out_target=args.held_out_target,
        model=args.model,
        dataset_root=args.dataset_root,
        results_root=args.results_root,
        dry_run=args.dry_run,
    )
    print(f"transfer synthesis samples={len(rec.synthesis_records)} sources={rec.source_targets}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
