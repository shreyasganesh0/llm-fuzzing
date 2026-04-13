"""Render source-only analysis + synthesis prompts (plan §E2.2).

The rendered prompts must not contain any test code or coverage data.
`assert_no_tests` is called unconditionally before returning a prompt.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from synthesis.scripts.extract_source_context import (
    TEST_CONTENT_MARKERS,
    SourceContext,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = REPO_ROOT / "synthesis" / "prompts"

_ENV = Environment(
    loader=FileSystemLoader(str(PROMPTS_DIR)),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)


@dataclass
class SourcePrompt:
    rendered: str
    mode: str
    target: str
    total_tokens: int
    num_source_files: int


def _system_prompt() -> str:
    return (PROMPTS_DIR / "system_prompt_source_only.txt").read_text()


def _file_dicts(ctx: SourceContext) -> list[dict]:
    return [
        {"path": f.path, "line_count": f.line_count, "content": f.content}
        for f in ctx.source_files
    ]


def assert_no_tests(rendered: str) -> None:
    """Guard against test leakage before we dispatch the prompt."""
    for marker in TEST_CONTENT_MARKERS:
        assert marker not in rendered, f"test content leaked into prompt: {marker}"
    for bad in ("coverage_gaps", "gap_branches", "upstream tests"):
        assert bad not in rendered, f"coverage/test metadata leaked: {bad}"


def build_analysis_prompt(ctx: SourceContext, *, num_branches: int = 20) -> SourcePrompt:
    rendered = _ENV.get_template("source_only_analysis.j2").render(
        system_prompt_source_only=_system_prompt(),
        target_name=ctx.target,
        harness_code=ctx.harness_code,
        source_files=_file_dicts(ctx),
        num_branches=num_branches,
    )
    assert_no_tests(rendered)
    return SourcePrompt(
        rendered=rendered,
        mode="analysis",
        target=ctx.target,
        total_tokens=ctx.total_tokens,
        num_source_files=len(ctx.source_files),
    )


def build_synthesis_prompt(
    ctx: SourceContext,
    *,
    num_inputs: int = 10,
    input_format: str = "base64-encoded bytes",
    template_name: str = "source_only_synthesis.j2",
) -> SourcePrompt:
    rendered = _ENV.get_template(template_name).render(
        system_prompt_source_only=_system_prompt(),
        target_name=ctx.target,
        harness_code=ctx.harness_code,
        source_files=_file_dicts(ctx),
        num_inputs=num_inputs,
        input_format=input_format,
    )
    assert_no_tests(rendered)
    return SourcePrompt(
        rendered=rendered,
        mode="synthesis",
        target=ctx.target,
        total_tokens=ctx.total_tokens,
        num_source_files=len(ctx.source_files),
    )
