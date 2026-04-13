"""Unit tests for the googletest extractor."""
from __future__ import annotations

from pathlib import Path

from dataset.scripts.extractors import googletest

FIXTURE = Path(__file__).parent / "fixtures" / "mini_test.cc"


def _make_config() -> dict:
    return {
        "name": "re2",
        "upstream": {
            "repo": "https://example.com/re2.git",
            "commit": "deadbeef",
        },
        "tests": {"framework": "googletest", "locations": ["mini_test.cc"]},
    }


def test_extracts_all_three_tests(tmp_path):
    (tmp_path / "mini_test.cc").write_text(FIXTURE.read_text())
    tests = googletest.extract(_make_config(), tmp_path)

    names = [t.test_name for t in tests]
    assert names == ["RE2.FullMatch", "RE2.PartialMatch", "RegexpTest.Parse"]


def test_extracts_provenance(tmp_path):
    (tmp_path / "mini_test.cc").write_text(FIXTURE.read_text())
    tests = googletest.extract(_make_config(), tmp_path)

    for t in tests:
        assert t.upstream_repo == "https://example.com/re2.git"
        assert t.upstream_commit == "deadbeef"
        assert t.upstream_file == "mini_test.cc"
        assert t.framework == "googletest"
        assert t.upstream_line >= 1


def test_extracts_re2_input_pattern_text(tmp_path):
    (tmp_path / "mini_test.cc").write_text(FIXTURE.read_text())
    tests = googletest.extract(_make_config(), tmp_path)
    by_name = {t.test_name: t for t in tests}

    assert by_name["RE2.FullMatch"].input_data == {"pattern": "hello", "text": "h.*o"}


def test_called_functions_filters_framework_macros(tmp_path):
    (tmp_path / "mini_test.cc").write_text(FIXTURE.read_text())
    tests = googletest.extract(_make_config(), tmp_path)
    by_name = {t.test_name: t for t in tests}

    called_partial = by_name["RE2.PartialMatch"].called_functions
    assert "RE2::PartialMatch" in called_partial
    for macro in ("ASSERT_TRUE", "EXPECT_TRUE", "EXPECT_EQ"):
        assert macro not in called_partial


def test_body_has_balanced_braces(tmp_path):
    (tmp_path / "mini_test.cc").write_text(FIXTURE.read_text())
    tests = googletest.extract(_make_config(), tmp_path)

    for t in tests:
        body = t.test_code
        assert body.count("{") == body.count("}"), f"unbalanced braces in {t.test_name}"
