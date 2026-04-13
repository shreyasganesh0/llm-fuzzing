"""Ablation synthesis driver: run one (include_gaps, include_tests, include_source) cell.

Renders `synthesis/prompts/ablation_synthesis_regex.j2` with any subset of
{tests, gaps, source_files} enabled, runs `samples` LLM calls, parses
regex responses and writes seeds to
  `<results-root>/seeds/<target>/ablation/<cell>/<model>/seed_*.bin`.

Used by Experiment B (the 7-cell prompt ablation) to decompose exp1's
in-distribution advantage over exp2 into contributions from each of
{tests, gaps, source}.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import (
    DEFAULT_GAPS_PER_PROMPT,
    DEFAULT_INPUTS_PER_PROMPT,
    SOURCE_CONTEXT_MAX_FILES,
    SYNTHESIS_MAX_TOKENS,
    SYNTHESIS_SAMPLES,
    SYNTHESIS_TEMPERATURE,
    SYNTHESIS_TOP_P,
)
from core.dataset_schema import PromptLogEntry, SynthesisRecord
from core.llm_client import LLMClient
from core.logging_config import get_logger
from synthesis.scripts.build_synthesis_prompt import (
    _load_gaps,
    _load_metadata,
    _load_tests,
    _pick_examples,
    _read_harness,
)
from synthesis.scripts.extract_source_context import extract_source_context
from synthesis.scripts.parse_synthesis import parse_regex_response

logger = get_logger("utcf.ablation.synthesis")

PROMPTS_DIR = REPO_ROOT / "synthesis" / "prompts"
SYSTEM_PROMPT_PATH = REPO_ROOT / "prediction" / "prompts" / "system_prompt.txt"

_JINJA_ENV = Environment(
    loader=FileSystemLoader(str(PROMPTS_DIR)),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)


def _file_dicts(ctx) -> list[dict]:
    return [
        {"path": f.path, "line_count": f.line_count, "content": f.content}
        for f in ctx.source_files
    ]


def build_ablation_prompt(
    target: str,
    *,
    dataset_root: Path,
    include_tests: bool,
    include_gaps: bool,
    include_source: bool,
    model: str,
    source_max_files: int,
    source_token_budget: int | None,
    num_inputs: int,
    max_gaps: int,
) -> str:
    system_prompt = SYSTEM_PROMPT_PATH.read_text()
    metadata = _load_metadata(dataset_root, target)
    harness_path = metadata.get("harness_file", "")
    upstream_root = REPO_ROOT / "dataset" / "targets" / "src" / target / "upstream"
    harness_code = _read_harness(upstream_root, harness_path) if harness_path else ""

    few_shot_examples: list = []
    if include_tests:
        tests = _load_tests(dataset_root, target)
        few_shot_examples = _pick_examples(tests, max_examples=5)

    gaps_report = None
    coverage_gaps: list = []
    total_upstream_tests = 0
    union_coverage_pct = 0.0
    if include_gaps:
        gaps_report = _load_gaps(dataset_root, target)
        if gaps_report is not None:
            coverage_gaps = gaps_report.gap_branches
            total_upstream_tests = gaps_report.total_upstream_tests
            union_coverage_pct = round(gaps_report.union_coverage_pct, 2)

    source_files: list[dict] = []
    if include_source:
        ctx = extract_source_context(
            target,
            model=model,
            max_files=source_max_files,
            token_budget=source_token_budget,
        )
        source_files = _file_dicts(ctx)
        if not harness_code:
            harness_code = ctx.harness_code

    template = _JINJA_ENV.get_template("ablation_synthesis_regex.j2")
    return template.render(
        system_prompt=system_prompt,
        target_name=target,
        harness_code=harness_code or "<harness unavailable>",
        source_language="cpp",
        include_tests=include_tests,
        few_shot_examples=few_shot_examples,
        include_gaps=include_gaps,
        coverage_gaps=coverage_gaps,
        total_upstream_tests=total_upstream_tests,
        union_coverage_pct=union_coverage_pct,
        max_gaps=max_gaps,
        include_source=include_source,
        source_files=source_files,
        num_inputs=num_inputs,
    )


def run_ablation(
    *,
    target: str,
    model: str,
    cell: str,
    include_tests: bool,
    include_gaps: bool,
    include_source: bool,
    dataset_root: Path,
    results_root: Path,
    samples: int,
    num_inputs: int,
    source_max_files: int,
    source_token_budget: int | None,
    max_tokens: int,
    max_gaps: int,
) -> list[SynthesisRecord]:
    rendered = build_ablation_prompt(
        target,
        dataset_root=dataset_root,
        include_tests=include_tests,
        include_gaps=include_gaps,
        include_source=include_source,
        model=model,
        source_max_files=source_max_files,
        source_token_budget=source_token_budget,
        num_inputs=num_inputs,
        max_gaps=max_gaps,
    )

    safe_model = model.replace("/", "_")
    seeds_dir = results_root / "seeds" / target / "ablation" / cell / safe_model
    synthesis_dir = results_root / "synthesis" / target / "ablation" / cell / safe_model
    seeds_dir.mkdir(parents=True, exist_ok=True)
    synthesis_dir.mkdir(parents=True, exist_ok=True)
    log_path = results_root / "log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    client = LLMClient()
    records: list[SynthesisRecord] = []
    for k in range(samples):
        resp = client.complete(
            messages=[
                {"role": "system", "content": ""},
                {"role": "user", "content": rendered},
            ],
            model=model,
            temperature=SYNTHESIS_TEMPERATURE,
            top_p=SYNTHESIS_TOP_P,
            max_tokens=max_tokens,
            cache_salt=f"sample={k},ablation={cell}",
        )
        inputs, status = parse_regex_response(
            resp.content,
            target=target,
            model=model,
            temperature=SYNTHESIS_TEMPERATURE,
            sample_index=k,
            experiment="exp1" if include_gaps or include_tests else "exp2",
        )
        for inp in inputs:
            (seeds_dir / f"seed_{inp.input_id}.bin").write_bytes(base64.b64decode(inp.content_b64))

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
            phase="ablation",
            experiment_tag=f"cell={cell},sample={k}",
            cached=resp.cached,
        )
        with open(log_path, "a") as fh:
            fh.write(log.model_dump_json() + "\n")

        record = SynthesisRecord(
            target=target,
            model=model,
            experiment="exp1" if include_gaps or include_tests else "exp2",
            sample_index=k,
            inputs=inputs,
            parse_status=status,
            raw_response=resp.content,
            log=log,
        )
        (synthesis_dir / f"sample_{k}.json").write_text(record.model_dump_json(indent=2))
        records.append(record)

    stats = {
        "target": target,
        "model": model,
        "cell": cell,
        "include_tests": include_tests,
        "include_gaps": include_gaps,
        "include_source": include_source,
        "samples": len(records),
        "seeds_written": sum(len(r.inputs) for r in records),
        "parse_status_counts": {
            s: sum(1 for r in records if r.parse_status == s)
            for s in {r.parse_status for r in records}
        },
    }
    (seeds_dir.parent / "cell_stats.json").write_text(json.dumps(stats, indent=2))
    logger.info("ablation cell done", extra=stats)
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--cell", required=True, help="cell name, e.g. exp1_gaps_only")
    parser.add_argument("--include-tests", action="store_true")
    parser.add_argument("--include-gaps", action="store_true")
    parser.add_argument("--include-source", action="store_true")
    parser.add_argument("--dataset-root", type=Path, default=REPO_ROOT / "dataset" / "dataset")
    parser.add_argument("--results-root", type=Path, default=REPO_ROOT / "synthesis" / "results")
    parser.add_argument("--samples", type=int, default=SYNTHESIS_SAMPLES)
    parser.add_argument("--num-inputs", type=int, default=DEFAULT_INPUTS_PER_PROMPT)
    parser.add_argument("--max-gaps", type=int, default=DEFAULT_GAPS_PER_PROMPT)
    parser.add_argument("--source-max-files", type=int, default=SOURCE_CONTEXT_MAX_FILES)
    parser.add_argument("--source-token-budget", type=int, default=None)
    parser.add_argument("--max-tokens", type=int, default=SYNTHESIS_MAX_TOKENS)
    args = parser.parse_args()

    recs = run_ablation(
        target=args.target,
        model=args.model,
        cell=args.cell,
        include_tests=args.include_tests,
        include_gaps=args.include_gaps,
        include_source=args.include_source,
        dataset_root=args.dataset_root,
        results_root=args.results_root,
        samples=args.samples,
        num_inputs=args.num_inputs,
        source_max_files=args.source_max_files,
        source_token_budget=args.source_token_budget,
        max_tokens=args.max_tokens,
        max_gaps=args.max_gaps,
    )
    ok = sum(1 for r in recs if r.parse_status == "ok")
    total = sum(len(r.inputs) for r in recs)
    print(f"cell={args.cell} samples={len(recs)} ok={ok} total_seeds={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
