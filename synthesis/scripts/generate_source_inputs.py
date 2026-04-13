"""Source-only input synthesis (plan §E2.4).

Same shape as Phase 3 generate_inputs but with source-only prompts and
no test conditioning. Writes SynthesisRecord + seeds under
results/seeds/<target>/source_only/<model>/.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import (
    BUDGET_MATCH_TOLERANCE,
    SOURCE_CONTEXT_MAX_FILES,
    SYNTHESIS_MAX_TOKENS,
    SYNTHESIS_SAMPLES,
    SYNTHESIS_TEMPERATURE,
    SYNTHESIS_TOP_P,
)
from core.dataset_schema import PromptLogEntry, SynthesisRecord
from core.logging_config import get_logger
from synthesis.scripts.build_source_prompt import build_synthesis_prompt
from synthesis.scripts.extract_source_context import extract_source_context
from synthesis.scripts.parse_synthesis import (
    parse_regex_response,
    parse_synthesis_response,
)

logger = get_logger("utcf.exp2.synth")


def _stub_log(target: str, model: str) -> PromptLogEntry:
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
        phase="experiment2",
        experiment_tag="source_only_synth",
        cached=True,
    )


def _exp1_token_budget(target: str, results_root: Path) -> int | None:
    log_path = results_root.parents[1] / "synthesis" / "results" / "log.jsonl"
    if not log_path.is_file():
        return None
    total = 0
    for line in log_path.read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("target") == target and row.get("phase") == "phase3":
            total += int(row.get("input_tokens", 0))
    return total or None


def run_source_synthesis(
    *,
    target: str,
    model: str,
    results_root: Path,
    dry_run: bool = False,
    num_inputs: int = 10,
    samples: int = SYNTHESIS_SAMPLES,
    source_max_files: int = SOURCE_CONTEXT_MAX_FILES,
    source_token_budget: int | None = None,
    max_tokens: int = SYNTHESIS_MAX_TOKENS,
    input_format: str = "bytes",
) -> list[SynthesisRecord]:
    ctx = extract_source_context(
        target,
        model=model,
        max_files=source_max_files,
        token_budget=source_token_budget,
    )
    template_name = (
        "source_only_synthesis_regex.j2"
        if input_format == "regex"
        else "source_only_synthesis.j2"
    )
    prompt = build_synthesis_prompt(
        ctx, num_inputs=num_inputs, template_name=template_name
    )

    out_root = results_root / "seeds" / target / "source_only" / model.replace("/", "_")
    out_root.mkdir(parents=True, exist_ok=True)

    # Budget-match check vs Experiment 1 (advisory, not fatal).
    exp1_tokens = _exp1_token_budget(target, results_root)
    budget_delta_pct = 0.0
    if exp1_tokens:
        budget_delta_pct = (prompt.total_tokens - exp1_tokens) / exp1_tokens * 100.0
        if abs(budget_delta_pct / 100.0) > BUDGET_MATCH_TOLERANCE:
            logger.warning(
                "budget mismatch",
                extra={"target": target, "exp1_tokens": exp1_tokens, "exp2_tokens": prompt.total_tokens},
            )

    records: list[SynthesisRecord] = []
    if dry_run:
        records.append(
            SynthesisRecord(
                target=target,
                model=model,
                experiment="exp2",
                sample_index=0,
                parse_status="dry_run",
                inputs=[],
                raw_response="",
                log=_stub_log(target, model),
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
                max_tokens=max_tokens,
                cache_salt=f"sample={i},experiment=exp2_source_only",
            )
            parser_fn = parse_regex_response if input_format == "regex" else parse_synthesis_response
            inputs, status = parser_fn(
                resp.content,
                target=target,
                model=model,
                temperature=SYNTHESIS_TEMPERATURE,
                sample_index=i,
                experiment="exp2",
            )
            for inp in inputs:
                (out_root / f"seed_{inp.input_id}.bin").write_bytes(base64.b64decode(inp.content_b64))
            records.append(
                SynthesisRecord(
                    target=target,
                    model=model,
                    experiment="exp2",
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
                        target=target,
                        phase="experiment2",
                        experiment_tag="source_only_synth",
                        cached=resp.cached,
                    ),
                )
            )

    stats = {
        "target": target,
        "model": model,
        "experiment": "source_only",
        "num_samples": len(records),
        "num_inputs_generated": sum(len(r.inputs) for r in records),
        "total_input_tokens": prompt.total_tokens,
        "exp1_token_budget": exp1_tokens,
        "budget_delta_pct": round(budget_delta_pct, 2),
        "source_files": prompt.num_source_files,
    }
    (out_root / "source_generation_stats.json").write_text(json.dumps(stats, indent=2))
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--results-root", type=Path, default=REPO_ROOT / "synthesis" / "results")
    parser.add_argument("--num-inputs", type=int, default=10)
    parser.add_argument("--samples", type=int, default=SYNTHESIS_SAMPLES)
    parser.add_argument("--source-max-files", type=int, default=SOURCE_CONTEXT_MAX_FILES)
    parser.add_argument("--source-token-budget", type=int, default=None)
    parser.add_argument("--max-tokens", type=int, default=SYNTHESIS_MAX_TOKENS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--input-format",
        default="bytes",
        choices=["bytes", "regex"],
        help="bytes: base64-encoded-bytes template (default). regex: plain regex text (RE2-style).",
    )
    args = parser.parse_args()
    recs = run_source_synthesis(
        target=args.target,
        model=args.model,
        results_root=args.results_root,
        dry_run=args.dry_run,
        num_inputs=args.num_inputs,
        samples=args.samples,
        source_max_files=args.source_max_files,
        source_token_budget=args.source_token_budget,
        max_tokens=args.max_tokens,
        input_format=args.input_format,
    )
    print(f"source-only synthesis: {len(recs)} records, {sum(len(r.inputs) for r in recs)} inputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
