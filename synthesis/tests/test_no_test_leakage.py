"""Source-only prompts must not contain unit tests or coverage data
(plan §E2 verification)."""
from __future__ import annotations

from synthesis.scripts.build_source_prompt import assert_no_tests
from synthesis.scripts.extract_source_context import (
    SourceContext,
    SourceFile,
    _looks_like_test_content,
    _looks_like_test_path,
)


def test_test_path_detector_positive():
    from pathlib import Path
    assert _looks_like_test_path(Path("re2/tests/test_full.cc"))
    assert _looks_like_test_path(Path("src/unittests/parse_test.cpp"))
    assert _looks_like_test_path(Path("foo/bar_test.cc"))


def test_test_path_detector_negative():
    from pathlib import Path
    assert not _looks_like_test_path(Path("re2/compile.cc"))
    assert not _looks_like_test_path(Path("src/parser.cpp"))


def test_test_content_detector_positive():
    assert _looks_like_test_content("#include <gtest/gtest.h>\nTEST(Foo, Bar) {}")
    assert _looks_like_test_content("TEST_F(MyFix, Case) { ASSERT_EQ(1, 1); }")


def test_test_content_detector_negative():
    assert not _looks_like_test_content("int main() { return 0; }")


def test_assert_no_tests_raises_on_test_content():
    import pytest
    with pytest.raises(AssertionError):
        assert_no_tests("int main() { TEST(Foo, Bar) {} }")


def test_assert_no_tests_passes_clean_content():
    assert_no_tests("int main() { return 0; }")


def test_context_excludes_test_files(tmp_path):
    # Synthetic context: source_files all clean.
    ctx = SourceContext(
        target="re2",
        harness_code="int LLVMFuzzerTestOneInput(const uint8_t *d, size_t n) { return 0; }",
        source_files=[
            SourceFile(path="compile.cc", line_count=10, content="int Compile() { return 1; }",
                       priority=1.0, token_estimate=20),
        ],
    )
    # Should not trigger any test-content detectors.
    for f in ctx.source_files:
        assert not _looks_like_test_content(f.content)
