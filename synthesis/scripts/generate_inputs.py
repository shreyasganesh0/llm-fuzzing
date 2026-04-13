"""Run gap-filling input synthesis for one (target, model) cell.

Plan §Phase 3: temperature=0.7, top_p=0.95, 3 samples per prompt. Each sample
produces up to N generated inputs; the union of all samples is written to disk
as the seed corpus for the `llm_seeds` campaign config.

Outputs:
  synthesis/results/seeds/<target>/exp1/<model>/seed_<id>.bin
  synthesis/results/synthesis/<target>/exp1/<model>/sample_<k>.json
  synthesis/results/log.jsonl (appended)
"""
from __future__ import annotations

import argparse
import base64
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
from core.dataset_schema import PromptLogEntry, SynthesisRecord
from core.llm_client import LLMClient
from core.logging_config import get_logger
from synthesis.scripts.build_synthesis_prompt import build_synthesis_prompt
from synthesis.scripts.parse_synthesis import (
    parse_regex_response,
    parse_synthesis_response,
)

logger = get_logger("utcf.phase3.synthesis")


def _write_seed(seeds_dir: Path, content_b64: str, input_id: str) -> Path:
    seeds_dir.mkdir(parents=True, exist_ok=True)
    out = seeds_dir / f"seed_{input_id}.bin"
    out.write_bytes(base64.b64decode(content_b64))
    return out


def synthesize(
    target: str,
    *,
    model: str,
    dataset_root: Path,
    results_root: Path,
    samples: int = SYNTHESIS_SAMPLES,
    temperature: float = SYNTHESIS_TEMPERATURE,
    top_p: float = SYNTHESIS_TOP_P,
    experiment: str = "exp1",
    llm_client: LLMClient | None = None,
    max_tokens: int = SYNTHESIS_MAX_TOKENS,
    input_format: str = "bytes",
) -> list[SynthesisRecord]:
    template_name = (
        "input_synthesis_regex.j2" if input_format == "regex" else "input_synthesis.j2"
    )
    prompt = build_synthesis_prompt(
        target, dataset_root=dataset_root, template_name=template_name
    )
    if prompt is None:
        logger.warning("no prompt built", extra={"target": target})
        return []

    client = llm_client or LLMClient()
    safe_model = model.replace("/", "_")
    seeds_dir = results_root / "seeds" / target / experiment / safe_model
    synthesis_dir = results_root / "synthesis" / target / experiment / safe_model
    synthesis_dir.mkdir(parents=True, exist_ok=True)
    log_path = results_root / "log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    records: list[SynthesisRecord] = []
    for k in range(samples):
        messages = [
            {"role": "system", "content": ""},
            {"role": "user", "content": prompt.rendered},
        ]
        resp = client.complete(
            messages=messages,
            model=model,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            cache_salt=f"sample={k},experiment={experiment}",
        )

        parser = parse_regex_response if input_format == "regex" else parse_synthesis_response
        inputs, status = parser(
            resp.content,
            target=target,
            model=model,
            temperature=temperature,
            sample_index=k,
            experiment=experiment,
        )
        for inp in inputs:
            _write_seed(seeds_dir, inp.content_b64, inp.input_id)

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
            phase="phase3",
            experiment_tag=f"sample={k},experiment={experiment}",
            cached=resp.cached,
        )
        with open(log_path, "a") as fh:
            fh.write(log.model_dump_json() + "\n")

        record = SynthesisRecord(
            target=target,
            model=model,
            experiment=experiment,
            sample_index=k,
            inputs=inputs,
            parse_status=status,
            raw_response=resp.content,
            log=log,
        )
        (synthesis_dir / f"sample_{k}.json").write_text(record.model_dump_json(indent=2))
        records.append(record)

    total = sum(len(r.inputs) for r in records)
    logger.info(
        "synthesis done",
        extra={"target": target, "model": model, "samples": samples, "seeds_written": total},
    )
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--dataset-root", type=Path, default=REPO_ROOT / "dataset" / "dataset")
    parser.add_argument("--results-root", type=Path, default=REPO_ROOT / "synthesis" / "results")
    parser.add_argument("--samples", type=int, default=SYNTHESIS_SAMPLES)
    parser.add_argument("--experiment", default="exp1", choices=["exp1", "exp2"])
    parser.add_argument(
        "--input-format",
        default="bytes",
        choices=["bytes", "regex"],
        help="bytes: base64-encoded-bytes template (default). regex: plain regex text (RE2-style).",
    )
    args = parser.parse_args()

    records = synthesize(
        args.target,
        model=args.model,
        dataset_root=args.dataset_root,
        results_root=args.results_root,
        samples=args.samples,
        experiment=args.experiment,
        input_format=args.input_format,
    )
    ok = sum(1 for r in records if r.parse_status == "ok")
    total_seeds = sum(len(r.inputs) for r in records)
    print(f"samples={len(records)} ok={ok} total_seeds={total_seeds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
