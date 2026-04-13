"""Source-only prompt assembly (plan §E2.2)."""
from __future__ import annotations

from synthesis.scripts.build_source_prompt import (
    build_analysis_prompt,
    build_synthesis_prompt,
)
from synthesis.scripts.extract_source_context import SourceContext, SourceFile


def _ctx() -> SourceContext:
    return SourceContext(
        target="re2",
        harness_code="extern \"C\" int LLVMFuzzerTestOneInput(const uint8_t* d, size_t n) { return 0; }",
        source_files=[
            SourceFile(
                path="compile.cc",
                line_count=5,
                content="int Compile(const char* s) { if (*s == '?') return 1; return 0; }",
                priority=1.0,
                token_estimate=50,
            ),
        ],
        total_tokens=100,
    )


def test_analysis_prompt_contains_target_and_harness():
    p = build_analysis_prompt(_ctx(), num_branches=5)
    assert p.mode == "analysis"
    assert "re2" in p.rendered
    assert "LLVMFuzzerTestOneInput" in p.rendered
    assert "hard_branches" in p.rendered


def test_synthesis_prompt_contains_input_format():
    p = build_synthesis_prompt(_ctx(), num_inputs=3, input_format="base64-encoded bytes")
    assert p.mode == "synthesis"
    assert "base64-encoded bytes" in p.rendered
    assert "content_b64" in p.rendered


def test_prompt_has_no_test_content():
    p = build_analysis_prompt(_ctx(), num_branches=5)
    for marker in ("TEST(", "TEST_F(", "<gtest/"):
        assert marker not in p.rendered
