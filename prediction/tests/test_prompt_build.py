"""Tests for build_prompt.stratified_few_shot + split_heldout determinism."""
from __future__ import annotations

from core.dataset_schema import CoverageProfile, Test
from prediction.scripts.build_prompt import split_heldout, stratified_few_shot


def _mk_test(name: str) -> Test:
    return Test(
        test_name=name,
        test_code=f"TEST({name.replace('.', ', ')}) {{}}",
        test_file="x.cc",
        upstream_repo="https://example.com/re2.git",
        upstream_commit="deadbeef",
        upstream_file="x.cc",
        upstream_line=1,
        framework="googletest",
    )


def _mk_profile(name: str, pct: float) -> CoverageProfile:
    return CoverageProfile(
        test_name=name,
        upstream_file="x.cc",
        upstream_line=1,
        framework="googletest",
        total_lines_covered=int(pct),
        total_lines_in_source=100,
    )


def test_split_heldout_deterministic():
    tests = [_mk_test(f"S.T{i}") for i in range(20)]
    a1, b1 = split_heldout(tests)
    a2, b2 = split_heldout(tests)
    assert [t.test_name for t in a1] == [t.test_name for t in a2]
    assert [t.test_name for t in b1] == [t.test_name for t in b2]
    assert len(a1) == 5
    assert len(b1) == 15


def test_stratified_few_shot_size():
    tests = [_mk_test(f"S.T{i}") for i in range(20)]
    coverage = {t.test_name: _mk_profile(t.test_name, (i * 5) % 100) for i, t in enumerate(tests)}
    picked = stratified_few_shot(tests, coverage, n=5)
    assert len(picked) == 5
    assert len({t.test_name for t in picked}) == 5


def test_stratified_few_shot_zero():
    tests = [_mk_test("S.T0")]
    assert stratified_few_shot(tests, {}, n=0) == []


def test_stratified_few_shot_backfill_when_sparse():
    tests = [_mk_test(f"S.T{i}") for i in range(3)]
    # All in one bucket; ensure we still return up to n without crashing.
    coverage = {t.test_name: _mk_profile(t.test_name, 50.0) for t in tests}
    picked = stratified_few_shot(tests, coverage, n=5)
    assert len(picked) == 3
