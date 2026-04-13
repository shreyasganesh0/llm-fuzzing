"""Build leave-one-out (LOO) coverage-prediction and synthesis prompts.

For each held-out target we draw few-shot examples from the OTHER Tier 1+2
targets (stratified across at least LOO_MIN_DISTINCT_SOURCE_TARGETS source
targets so the prompt isn't dominated by a single target). Tier 3 targets
(libpng/FreeType/zlib) never appear in any few-shot pool — they are the
purest held-out evaluation, always drawing few-shots from Tier 1+2.

See plan §Phase Transfer T.1.
"""
from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from core.config import (
    LOO_FEW_SHOT,
    LOO_MIN_DISTINCT_SOURCE_TARGETS,
    TIER3_TARGETS,
    TIER12_TARGETS,
)
from core.dataset_schema import CoverageProfile, Test
from core.logging_config import get_logger
from prediction.scripts.build_prompt import (
    FewShotExample,
    TargetTest,
    _coverage_pct,
    _load_coverage,
    _load_tests,
    _source_excerpt,
    _to_example,
    _token_estimate,
    split_heldout,
)

logger = get_logger("utcf.transfer.prompt")

PHASE2_PROMPTS = REPO_ROOT / "prediction" / "prompts"

_PRED_ENV = Environment(
    loader=FileSystemLoader(str(PHASE2_PROMPTS)),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)


@dataclass
class LOOPrompt:
    held_out_target: str
    mode: str  # "prediction" | "synthesis"
    target_test_name: str | None
    rendered: str
    few_shot_sources: list[str]
    token_estimate: int


def _eligible_sources(held_out: str) -> list[str]:
    """Few-shot pool excludes held-out target and ALL Tier 3 targets."""
    pool = [t for t in TIER12_TARGETS if t != held_out]
    # Explicit assertion: Tier 3 must never leak into any pool.
    assert all(t not in TIER3_TARGETS for t in pool), "Tier 3 leaked into LOO pool"
    return pool


def _load_target_tests(dataset_root: Path, target: str) -> tuple[list[Test], dict[str, CoverageProfile]]:
    tests = _load_tests(dataset_root, target)
    coverage = _load_coverage(dataset_root, target)
    return tests, coverage


def _stratified_cross_target(
    sources: list[str],
    dataset_root: Path,
    n: int,
    *,
    seed: int = 42,
) -> list[tuple[str, Test, CoverageProfile | None]]:
    """Draw few-shot examples from multiple source targets.

    Round-robin across sources, then across coverage quintiles within each
    source. Returns up to n tuples of (source_target, test, coverage).
    """
    rng = random.Random(seed)
    per_target: dict[str, tuple[list[Test], dict[str, CoverageProfile]]] = {}
    for src in sources:
        try:
            per_target[src] = _load_target_tests(dataset_root, src)
        except FileNotFoundError:
            continue

    ordered = list(per_target.keys())
    rng.shuffle(ordered)

    buckets: dict[str, list[list[Test]]] = {}
    for src, (tests, cov) in per_target.items():
        qbuckets: list[list[Test]] = [[] for _ in range(5)]
        for t in tests:
            profile = cov.get(t.test_name)
            pct = _coverage_pct(profile) if profile else 0.0
            qbuckets[min(int(pct // 20), 4)].append(t)
        buckets[src] = qbuckets

    picked: list[tuple[str, Test, CoverageProfile | None]] = []
    used = set()
    safety = 1000
    bucket_cursor = 0
    while len(picked) < n and safety > 0:
        progress = False
        for src in ordered:
            qb = buckets.get(src, [])
            if not qb:
                continue
            qi = bucket_cursor % 5
            candidates = qb[qi]
            if not candidates:
                continue
            choice = rng.choice(candidates)
            key = (src, choice.test_name)
            if key in used:
                continue
            used.add(key)
            picked.append((src, choice, per_target[src][1].get(choice.test_name)))
            progress = True
            if len(picked) >= n:
                break
        bucket_cursor += 1
        safety -= 1
        if not progress:
            break

    if len(picked) >= LOO_MIN_DISTINCT_SOURCE_TARGETS:
        distinct = {s for s, _, _ in picked}
        assert len(distinct) >= min(
            LOO_MIN_DISTINCT_SOURCE_TARGETS, len(per_target)
        ), f"LOO pool drew from too few source targets: {distinct}"
    return picked


def build_loo_prediction_prompts(
    *,
    held_out_target: str,
    dataset_root: Path,
    upstream_root: Path | None = None,
    few_shot: int = LOO_FEW_SHOT,
    context_size: str = "file",
    model: str = "gpt-4o-2024-08-06",
) -> list[LOOPrompt]:
    sources = _eligible_sources(held_out_target)
    assert held_out_target not in sources, "held-out target must not be in its own LOO pool"

    tests, coverage = _load_target_tests(dataset_root, held_out_target)
    heldout, _ = split_heldout(tests) if held_out_target in TIER12_TARGETS else (tests, [])

    system_prompt = (PHASE2_PROMPTS / "system_prompt.txt").read_text()
    cross = _stratified_cross_target(sources, dataset_root, few_shot)

    fewshot_examples: list[FewShotExample] = []
    for src, t, profile in cross:
        src_upstream = (upstream_root or REPO_ROOT / "dataset" / "targets" / "src") / src / "upstream"
        excerpt = _source_excerpt(t, src_upstream, context_size)
        fewshot_examples.append(_to_example(t, profile, excerpt))
    fewshot_sources = sorted({s for s, _, _ in cross})

    prompts: list[LOOPrompt] = []
    held_upstream = (upstream_root or REPO_ROOT / "dataset" / "targets" / "src") / held_out_target / "upstream"
    template = _PRED_ENV.get_template("coverage_prediction.j2")
    for held in heldout:
        excerpt = _source_excerpt(held, held_upstream, context_size)
        rendered = template.render(
            system_prompt=system_prompt,
            target_name=held_out_target,
            upstream_commit="loo",
            few_shot_examples=fewshot_examples,
            target_test=TargetTest(
                upstream_file=held.upstream_file,
                upstream_line=held.upstream_line,
                test_code=held.test_code,
                source_excerpt=excerpt,
            ),
        )
        prompts.append(
            LOOPrompt(
                held_out_target=held_out_target,
                mode="prediction",
                target_test_name=held.test_name,
                rendered=rendered,
                few_shot_sources=fewshot_sources,
                token_estimate=_token_estimate(rendered, model),
            )
        )
    return prompts


def build_loo_synthesis_prompt(
    *,
    held_out_target: str,
    dataset_root: Path,
    few_shot: int = LOO_FEW_SHOT,
    context_size: str = "file",
    model: str = "gpt-4o-2024-08-06",
    gaps_limit: int = 20,
    num_inputs: int = 10,
) -> LOOPrompt:
    from core.dataset_schema import CoverageGapsReport
    from synthesis.scripts.build_synthesis_prompt import (
        SynthesisExample,
        _load_metadata,
        _read_harness,
    )
    from synthesis.scripts.build_synthesis_prompt import (
        _load_tests as _load_phase3_tests,
    )

    sources = _eligible_sources(held_out_target)
    cross = _stratified_cross_target(sources, dataset_root, few_shot)
    examples: list[SynthesisExample] = [
        SynthesisExample(
            upstream_file=t.upstream_file,
            upstream_line=t.upstream_line,
            test_code=t.test_code,
            input_data=str(t.input_data) if t.input_data else None,
            functions_covered=(
                sorted({fc for f in profile.files.values() for fc in f.functions_covered})
                if profile else []
            ),
        )
        for _src, t, profile in cross
    ]

    gaps_path = dataset_root / held_out_target / "coverage_gaps.json"
    gaps: CoverageGapsReport | None = None
    if gaps_path.is_file():
        gaps = CoverageGapsReport.model_validate_json(gaps_path.read_text())

    metadata = _load_metadata(dataset_root, held_out_target)
    harness_rel = metadata.get("harness_file", "")
    upstream_root = REPO_ROOT / "dataset" / "targets" / "src" / held_out_target / "upstream"
    harness_code = _read_harness(upstream_root, harness_rel) if harness_rel else ""

    system_prompt = (PHASE2_PROMPTS / "system_prompt.txt").read_text()
    template = _PRED_ENV.get_template("input_synthesis.j2")
    rendered = template.render(
        system_prompt=system_prompt,
        target_name=held_out_target,
        harness_code=harness_code or "<harness unavailable in this environment>",
        few_shot_examples=examples,
        total_upstream_tests=(gaps.total_upstream_tests if gaps else len(_load_phase3_tests(dataset_root, held_out_target))),
        union_coverage_pct=round(gaps.union_coverage_pct, 2) if gaps else 0.0,
        coverage_gaps=(gaps.gap_branches if gaps else [])[:gaps_limit],
        max_gaps=gaps_limit,
        num_inputs=num_inputs,
        source_language="cpp",
        input_format="base64-encoded bytes",
    )
    return LOOPrompt(
        held_out_target=held_out_target,
        mode="synthesis",
        target_test_name=None,
        rendered=rendered,
        few_shot_sources=sorted({s for s, _, _ in cross}),
        token_estimate=_token_estimate(rendered, model),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--held-out-target", required=True)
    parser.add_argument("--mode", choices=["prediction", "synthesis"], default="prediction")
    parser.add_argument("--dataset-root", type=Path, default=REPO_ROOT / "dataset" / "dataset")
    parser.add_argument("--few-shot", type=int, default=LOO_FEW_SHOT)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    if args.mode == "prediction":
        prompts = build_loo_prediction_prompts(
            held_out_target=args.held_out_target,
            dataset_root=args.dataset_root,
            few_shot=args.few_shot,
        )
        out_dir = args.out or (REPO_ROOT / "transfer" / "results" / "loo_prompts" / args.held_out_target)
        out_dir.mkdir(parents=True, exist_ok=True)
        for p in prompts:
            safe = (p.target_test_name or "unnamed").replace("/", "_")
            (out_dir / f"{safe}.txt").write_text(p.rendered)
        print(f"wrote {len(prompts)} LOO prediction prompts to {out_dir}")
    else:
        prompt = build_loo_synthesis_prompt(
            held_out_target=args.held_out_target,
            dataset_root=args.dataset_root,
            few_shot=args.few_shot,
        )
        out_path = args.out or (REPO_ROOT / "transfer" / "results" / "loo_prompts" / f"{args.held_out_target}_synth.txt")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(prompt.rendered)
        print(f"wrote synthesis LOO prompt to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
