"""Build Phase 2 coverage-prediction prompts from a dataset.

Given a target's dataset directory and experiment knobs (model, few-shot
count, context size, prompt variant), this module:

  1. Loads all tests + coverage profiles for the target.
  2. Picks a held-out evaluation subset (seed=42, size=HELDOUT_SIZE).
  3. Picks few-shot examples via decile-stratified sampling (seed=42).
  4. Renders the appropriate Jinja template with Pydantic-validated inputs.

The rendered prompt is returned as a single string. Token counting uses
tiktoken for OpenAI (`cl100k_base`) and a chars/3.5 approximation otherwise.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, Template

from core.config import HELDOUT_SIZE, SEED_FEW_SHOT, SEED_HELDOUT
from core.dataset_schema import CoverageProfile, Test
from core.logging_config import get_logger

logger = get_logger("utcf.phase2.prompt")

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = REPO_ROOT / "prediction" / "prompts"

_JINJA_ENV = Environment(
    loader=FileSystemLoader(str(PROMPTS_DIR)),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)

VARIANT_TEMPLATES = {
    "primary": "coverage_prediction.j2",
    "rephrase_a": "coverage_prediction_rephrase_A.j2",
    "rephrase_b": "coverage_prediction_rephrase_B.j2",
}


@dataclass
class FewShotExample:
    upstream_file: str
    upstream_line: int
    test_code: str
    source_excerpt: str
    functions_covered: list[str]
    functions_not_covered: list[str]
    branches: list[dict]
    coverage_pct: float
    input_data: str | None


@dataclass
class TargetTest:
    upstream_file: str
    upstream_line: int
    test_code: str
    source_excerpt: str


@dataclass
class BuiltPrompt:
    rendered: str
    target_test_name: str
    few_shot_count: int
    context_size: str
    prompt_variant: str
    token_estimate: int


# ----------------------------------------------------------------------------
# Dataset loading
# ----------------------------------------------------------------------------


def _load_tests(dataset_root: Path, target: str) -> list[Test]:
    path = dataset_root / target / "tests.json"
    return [Test.model_validate(t) for t in json.loads(path.read_text())]


def _load_coverage(dataset_root: Path, target: str) -> dict[str, CoverageProfile]:
    profiles: dict[str, CoverageProfile] = {}
    tests_dir = dataset_root / target / "tests"
    if not tests_dir.is_dir():
        return profiles
    for test_dir in sorted(tests_dir.glob("test_*")):
        cov = test_dir / "coverage.json"
        if cov.is_file():
            data = json.loads(cov.read_text())
            profile = CoverageProfile.model_validate(data)
            profiles[profile.test_name] = profile
    return profiles


def _coverage_pct(profile: CoverageProfile) -> float:
    if profile.total_lines_in_source == 0:
        return 0.0
    return 100.0 * profile.total_lines_covered / profile.total_lines_in_source


# ----------------------------------------------------------------------------
# Held-out + few-shot selection (deterministic, decile-stratified)
# ----------------------------------------------------------------------------


def split_heldout(tests: list[Test], n: int = HELDOUT_SIZE, seed: int = SEED_HELDOUT) -> tuple[list[Test], list[Test]]:
    rng = random.Random(seed)
    pool = list(tests)
    rng.shuffle(pool)
    heldout = pool[:n]
    train = pool[n:]
    return heldout, train


def stratified_few_shot(
    candidates: list[Test],
    coverage: dict[str, CoverageProfile],
    n: int,
    seed: int = SEED_FEW_SHOT,
) -> list[Test]:
    if n <= 0:
        return []
    rng = random.Random(seed)
    buckets: list[list[Test]] = [[] for _ in range(5)]
    for t in candidates:
        profile = coverage.get(t.test_name)
        pct = _coverage_pct(profile) if profile else 0.0
        idx = min(int(pct // 20), 4)
        buckets[idx].append(t)

    picked: list[Test] = []
    # Round-robin draw from each bucket (stratified by coverage decile/quintile).
    bucket_order = list(range(5))
    rng.shuffle(bucket_order)
    safety = 500
    while len(picked) < n and safety > 0:
        for b in bucket_order:
            if not buckets[b]:
                continue
            choice = rng.choice(buckets[b])
            if choice in picked:
                continue
            picked.append(choice)
            if len(picked) >= n:
                break
        safety -= 1

    # Backfill from any remaining candidates if buckets were sparse.
    if len(picked) < n:
        leftover = [t for t in candidates if t not in picked]
        rng.shuffle(leftover)
        picked.extend(leftover[: n - len(picked)])
    return picked[:n]


# ----------------------------------------------------------------------------
# Source excerpt selection by context size
# ----------------------------------------------------------------------------


def _source_excerpt(test: Test, upstream_root: Path, context_size: str) -> str:
    """Best-effort: read a source excerpt from the upstream repo.

    function_only: return only the test file (contains the test itself).
    file:          return the test file + all files matching called_functions
                   heuristically (primary source files named in the target YAML).
    multi_file:    same as file, plus any files they #include (1 level deep).

    If the upstream repo isn't cloned (local testing), returns the test_code
    itself as the excerpt — the template still renders cleanly.
    """
    if not upstream_root.is_dir():
        return test.test_code

    seed_file = upstream_root / test.upstream_file
    if not seed_file.is_file():
        return test.test_code

    if context_size == "function_only":
        return seed_file.read_text(errors="replace")[:8000]

    pieces = [seed_file.read_text(errors="replace")[:4000]]
    siblings = [p for p in seed_file.parent.glob("*.cc") if p != seed_file][:2]
    for sib in siblings:
        pieces.append(sib.read_text(errors="replace")[:2000])
    excerpt = "\n\n// ---\n".join(pieces)

    if context_size == "multi_file":
        includes = _extract_includes(seed_file.read_text(errors="replace"))
        for inc in includes[:3]:
            inc_path = seed_file.parent / inc
            if inc_path.is_file():
                excerpt += "\n\n// included: " + inc + "\n" + inc_path.read_text(errors="replace")[:1500]
    return excerpt[:16000]


def _extract_includes(source: str) -> list[str]:
    out = []
    for line in source.splitlines():
        line = line.strip()
        if line.startswith("#include") and '"' in line:
            start = line.find('"') + 1
            end = line.find('"', start)
            if end > start:
                out.append(line[start:end])
    return out


# ----------------------------------------------------------------------------
# Assemble + render
# ----------------------------------------------------------------------------


def _to_example(t: Test, profile: CoverageProfile | None, excerpt: str) -> FewShotExample:
    pct = _coverage_pct(profile) if profile else 0.0
    if profile is None:
        functions_covered: list[str] = []
        functions_not_covered: list[str] = []
        branches: list[dict] = []
    else:
        functions_covered = sorted({fc for f in profile.files.values() for fc in f.functions_covered})
        functions_not_covered = sorted({fc for f in profile.files.values() for fc in f.functions_not_covered})
        branches = [
            {
                "location": key,
                "status": "both_taken" if (b.true_taken and b.false_taken) else (
                    "true_only" if b.true_taken else ("false_only" if b.false_taken else "not_taken")
                ),
            }
            for f in profile.files.values()
            for key, b in f.branches.items()
        ][:40]
    return FewShotExample(
        upstream_file=t.upstream_file,
        upstream_line=t.upstream_line,
        test_code=t.test_code,
        source_excerpt=excerpt,
        functions_covered=functions_covered,
        functions_not_covered=functions_not_covered,
        branches=branches,
        coverage_pct=round(pct, 2),
        input_data=str(t.input_data) if t.input_data else None,
    )


def _render(variant: str, **context) -> str:
    template: Template = _JINJA_ENV.get_template(VARIANT_TEMPLATES[variant])
    return template.render(**context)


def _token_estimate(text: str, model: str) -> int:
    try:
        import tiktoken
        enc = tiktoken.encoding_for_model(model)
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def build_prompts(
    target: str,
    *,
    dataset_root: Path,
    upstream_root: Path | None = None,
    few_shot: int = 5,
    context_size: str = "file",
    prompt_variant: str = "primary",
    model: str = "gpt-4o-2024-08-06",
) -> list[BuiltPrompt]:
    if prompt_variant not in VARIANT_TEMPLATES:
        raise ValueError(f"Unknown prompt_variant {prompt_variant!r}")

    system_prompt = (PROMPTS_DIR / "system_prompt.txt").read_text()
    config_path = REPO_ROOT / "dataset" / "targets" / f"{target}.yaml"
    from dataset.scripts.pinned_loader import load_target_yaml
    config = load_target_yaml(config_path, require_resolved=True)
    target_name = config["name"]
    upstream_commit = config["upstream"]["commit"]

    tests = _load_tests(dataset_root, target)
    coverage = _load_coverage(dataset_root, target)

    heldout, train_pool = split_heldout(tests)

    upstream_root = upstream_root or REPO_ROOT / "dataset" / "targets" / "src" / target / "upstream"

    # Validate provenance on every few-shot candidate before we render prompts.
    # (Scaffolding: audit happens in Phase 1; here we only trust the dataset.)

    prompts: list[BuiltPrompt] = []
    for held in heldout:
        fewshot_tests = stratified_few_shot(train_pool, coverage, few_shot)
        fewshot_examples = [
            _to_example(t, coverage.get(t.test_name), _source_excerpt(t, upstream_root, context_size))
            for t in fewshot_tests
        ]
        target_test = TargetTest(
            upstream_file=held.upstream_file,
            upstream_line=held.upstream_line,
            test_code=held.test_code,
            source_excerpt=_source_excerpt(held, upstream_root, context_size),
        )
        rendered = _render(
            prompt_variant,
            system_prompt=system_prompt,
            target_name=target_name,
            upstream_commit=upstream_commit,
            few_shot_examples=fewshot_examples,
            target_test=target_test,
        )
        prompts.append(
            BuiltPrompt(
                rendered=rendered,
                target_test_name=held.test_name,
                few_shot_count=len(fewshot_tests),
                context_size=context_size,
                prompt_variant=prompt_variant,
                token_estimate=_token_estimate(rendered, model),
            )
        )
    return prompts
