"""Coverage JSON parser tests."""
from __future__ import annotations

from pathlib import Path

from core.coverage_utils import compute_gaps, jaccard, parse_llvm_cov_json, union_coverage

FIXTURE = Path(__file__).parent / "fixtures" / "llvm_cov_sample.json"


def test_parse_llvm_cov_keeps_only_source_roots():
    profile = parse_llvm_cov_json(
        FIXTURE,
        test_name="RE2.FullMatch",
        upstream_file="re2/testing/re2_test.cc",
        upstream_line=42,
        framework="googletest",
        source_roots=["/repo/"],
    )
    assert "/repo/re2/re2.cc" in profile.files
    assert "/usr/include/system.h" not in profile.files


def test_parse_llvm_cov_extracts_lines_and_branches():
    profile = parse_llvm_cov_json(
        FIXTURE,
        test_name="RE2.FullMatch",
        upstream_file="re2/testing/re2_test.cc",
        upstream_line=42,
        framework="googletest",
        source_roots=["/repo/"],
    )
    fc = profile.files["/repo/re2/re2.cc"]
    assert 10 in fc.lines_covered
    assert 12 in fc.lines_not_covered
    assert "/repo/re2/re2.cc:20" in fc.branches
    # branch at line 25 is (1, 0): true taken, false not taken -> gap candidate
    b25 = fc.branches["/repo/re2/re2.cc:25"]
    assert b25.true_taken is True and b25.false_taken is False


def test_union_coverage_merges_and_gaps():
    p = parse_llvm_cov_json(
        FIXTURE, test_name="a", upstream_file="x", upstream_line=1,
        framework="googletest", source_roots=["/repo/"],
    )
    q = parse_llvm_cov_json(
        FIXTURE, test_name="b", upstream_file="x", upstream_line=1,
        framework="googletest", source_roots=["/repo/"],
    )
    merged = union_coverage([p, q])
    assert "/repo/re2/re2.cc" in merged.files
    gaps = compute_gaps(merged)
    assert ("/repo/re2/re2.cc", 25) in gaps


def test_jaccard_identity_and_disjoint():
    p = parse_llvm_cov_json(
        FIXTURE, test_name="a", upstream_file="x", upstream_line=1,
        framework="googletest", source_roots=["/repo/"],
    )
    assert jaccard(p, p) == 1.0
