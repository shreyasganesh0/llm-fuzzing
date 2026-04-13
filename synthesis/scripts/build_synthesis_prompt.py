"""Build Phase 3 synthesis prompts from a target's dataset + coverage_gaps.

Renders templates from `synthesis/prompts/` (input_synthesis.j2,
input_synthesis_regex.j2). `system_prompt.txt` still lives under
`prediction/prompts/` so the few-shot prediction and synthesis paths
share a system prompt.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from core.config import (
    DEFAULT_GAPS_PER_PROMPT,
    DEFAULT_INPUTS_PER_PROMPT,
)
from core.dataset_schema import CoverageGapsReport, Test

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = REPO_ROOT / "synthesis" / "prompts"
SYSTEM_PROMPT_PATH = REPO_ROOT / "prediction" / "prompts" / "system_prompt.txt"

_JINJA_ENV = Environment(
    loader=FileSystemLoader(str(PROMPTS_DIR)),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)


@dataclass
class SynthesisExample:
    upstream_file: str
    upstream_line: int
    test_code: str
    input_data: str | None
    functions_covered: list[str]


@dataclass
class SynthesisPrompt:
    rendered: str
    target: str
    total_upstream_tests: int
    union_coverage_pct: float
    n_gaps: int
    n_requested: int


def _load_tests(dataset_root: Path, target: str) -> list[Test]:
    path = dataset_root / target / "tests.json"
    if not path.is_file():
        return []
    return [Test.model_validate(t) for t in json.loads(path.read_text())]


def _load_gaps(dataset_root: Path, target: str) -> CoverageGapsReport | None:
    path = dataset_root / target / "coverage_gaps.json"
    if not path.is_file():
        return None
    return CoverageGapsReport.model_validate_json(path.read_text())


def _load_metadata(dataset_root: Path, target: str) -> dict:
    path = dataset_root / target / "metadata.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text())


def _read_harness(upstream_root: Path, harness_path: str) -> str:
    candidates = [
        upstream_root / harness_path,
        upstream_root.parent / "harness" / Path(harness_path).name,
    ]
    for c in candidates:
        if c.is_file():
            return c.read_text(errors="replace")[:8000]
    return ""


def _pick_examples(tests: list[Test], max_examples: int = 5) -> list[SynthesisExample]:
    out: list[SynthesisExample] = []
    for t in tests[:max_examples]:
        out.append(
            SynthesisExample(
                upstream_file=t.upstream_file,
                upstream_line=t.upstream_line,
                test_code=t.test_code,
                input_data=str(t.input_data) if t.input_data else None,
                functions_covered=[],
            )
        )
    return out


def build_synthesis_prompt(
    target: str,
    *,
    dataset_root: Path,
    upstream_root: Path | None = None,
    max_gaps: int = DEFAULT_GAPS_PER_PROMPT,
    num_inputs: int = DEFAULT_INPUTS_PER_PROMPT,
    input_format: str = "base64-encoded bytes",
    source_language: str = "cpp",
    template_name: str = "input_synthesis.j2",
) -> SynthesisPrompt | None:
    tests = _load_tests(dataset_root, target)
    gaps = _load_gaps(dataset_root, target)
    if gaps is None or not tests:
        return None

    metadata = _load_metadata(dataset_root, target)
    harness_path = metadata.get("harness_file", "")
    upstream_root = upstream_root or REPO_ROOT / "dataset" / "targets" / "src" / target / "upstream"
    harness_code = _read_harness(upstream_root, harness_path) if harness_path else ""

    examples = _pick_examples(tests, max_examples=5)
    system_prompt = SYSTEM_PROMPT_PATH.read_text()

    template = _JINJA_ENV.get_template(template_name)
    rendered = template.render(
        system_prompt=system_prompt,
        target_name=target,
        harness_code=harness_code or "<harness unavailable in this environment>",
        few_shot_examples=examples,
        total_upstream_tests=gaps.total_upstream_tests,
        union_coverage_pct=round(gaps.union_coverage_pct, 2),
        coverage_gaps=gaps.gap_branches,
        max_gaps=max_gaps,
        num_inputs=num_inputs,
        source_language=source_language,
        input_format=input_format,
    )
    return SynthesisPrompt(
        rendered=rendered,
        target=target,
        total_upstream_tests=gaps.total_upstream_tests,
        union_coverage_pct=gaps.union_coverage_pct,
        n_gaps=min(len(gaps.gap_branches), max_gaps),
        n_requested=num_inputs,
    )
