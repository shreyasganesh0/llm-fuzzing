"""Provenance audit tests.

Verifies that `verify_test_upstream` accepts faithful tests and rejects both
fabricated code and off-by-many-line provenance.
"""
from __future__ import annotations

from pathlib import Path

from core.dataset_schema import Test
from core.provenance import audit_tests, verify_test_upstream

FIXTURE = Path(__file__).parent / "fixtures" / "mini_test.cc"


def _write_fixture(tmp_path: Path) -> Path:
    target = tmp_path / "mini_test.cc"
    target.write_text(FIXTURE.read_text())
    return tmp_path


def _make_test(test_code: str, *, line: int) -> Test:
    return Test(
        test_name="RE2.FullMatch",
        test_code=test_code,
        test_file="mini_test.cc",
        upstream_repo="https://example.com/re2.git",
        upstream_commit="deadbeef",
        upstream_file="mini_test.cc",
        upstream_line=line,
        framework="googletest",
    )


def test_verify_accepts_faithful_test(tmp_path):
    repo = _write_fixture(tmp_path)
    body = "TEST(RE2, FullMatch) {\n  ASSERT_TRUE(RE2::FullMatch(\"hello\", \"h.*o\"));\n}"
    # Fixture places TEST(RE2, FullMatch) at line 9 (1-based).
    t = _make_test(body, line=9)
    assert verify_test_upstream(t, repo) is True


def test_verify_rejects_fabricated_code(tmp_path):
    repo = _write_fixture(tmp_path)
    fake = "TEST(RE2, Bogus) {\n  do_something_never_in_upstream();\n}"
    t = _make_test(fake, line=9)
    assert verify_test_upstream(t, repo) is False


def test_verify_rejects_wrong_line(tmp_path):
    repo = _write_fixture(tmp_path)
    body = "TEST(RE2, FullMatch) {\n  ASSERT_TRUE(RE2::FullMatch(\"hello\", \"h.*o\"));\n}"
    # Deliberately far from the real location (and outside the ±2 window).
    t = _make_test(body, line=1)
    assert verify_test_upstream(t, repo) is False


def test_audit_bulk(tmp_path):
    repo = _write_fixture(tmp_path)
    good = _make_test(
        "TEST(RE2, FullMatch) {\n  ASSERT_TRUE(RE2::FullMatch(\"hello\", \"h.*o\"));\n}",
        line=9,
    )
    bad = _make_test("TEST(RE2, Fake) {}", line=9)
    bad.test_name = "RE2.Fake"
    report = audit_tests([good, bad], repo)
    assert "RE2.FullMatch" in report["verified"]
    assert "RE2.Fake" in report["rejected"]
