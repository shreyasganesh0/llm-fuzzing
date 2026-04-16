"""Unit tests for the citation-usage aggregate pass.

Does not exercise per-seed (that path shells out to measure_coverage, which
requires the instrumented binary and is covered end-to-end by the launcher).
"""
from __future__ import annotations

import json
from pathlib import Path

from analysis.scripts import citation_usage as cu
from core.dataset_schema import CoverageProfile, FileCoverage


def _profile(covered: dict[str, list[int]]) -> CoverageProfile:
    return CoverageProfile(
        test_name="synthetic",
        upstream_file="x",
        upstream_line=1,
        framework="googletest",
        files={p: FileCoverage(lines_covered=lines) for p, lines in covered.items()},
    )


def test_normalize_gap_strips_line_suffix():
    assert cu.normalize_gap("re2/parse.cc:100") == "re2/parse.cc"
    assert cu.normalize_gap("re2/parse.cc") == "re2/parse.cc"
    assert cu.normalize_gap("parse.cc:0") == "parse.cc"


def test_citation_hit_matches_on_parent_basename():
    covered = {"re2/parse.cc", "re2/dfa.cc"}
    assert cu.citation_hit("re2/parse.cc", covered)
    assert not cu.citation_hit("re2/nfa.cc", covered)


def test_covered_file_suffixes_drops_uncovered_files():
    prof = _profile({
        "/abs/src/re2/parse.cc": [1, 2, 3],
        "/abs/src/re2/nfa.cc": [],
    })
    out = cu.covered_file_suffixes(prof)
    assert out == {"re2/parse.cc"}


def test_aggregate_cell_computes_hit_rate(tmp_path: Path):
    samples_dir = tmp_path / "samples"
    samples_dir.mkdir()
    (samples_dir / "sample_0.json").write_text(json.dumps({
        "target": "re2", "model": "m", "experiment": "exp1", "sample_index": 0,
        "inputs": [
            {
                "input_id": "s0",
                "content_b64": "AAA=",
                "target_gaps": ["re2/parse.cc:100", "re2/nfa.cc:42"],
                "reasoning": "x",
                "source": "llm", "target": "re2", "experiment": "exp1",
            },
            {
                "input_id": "s1",
                "content_b64": "AAA=",
                "target_gaps": ["re2/parse.cc:200"],
                "reasoning": "y",
                "source": "llm", "target": "re2", "experiment": "exp1",
            },
        ],
        "parse_status": "ok", "raw_response": "",
    }))

    prof = _profile({"/abs/re2/parse.cc": [1]})  # parse.cc hit, nfa.cc missed
    row = cu.aggregate_cell(samples_dir, prof)

    assert row["seeds"] == 2
    assert row["unique_cited_files"] == 2
    assert row["citation_count_total"] == 3
    assert row["citation_hit"] == 2  # 2 hits on parse.cc
    assert row["citation_miss"] == 1  # 1 miss on nfa.cc
    assert abs(row["hit_rate"] - 2 / 3) < 1e-9
    assert row["hit_files"] == ["re2/parse.cc"]
    assert row["miss_files"] == ["re2/nfa.cc"]


def test_aggregate_cell_handles_empty_samples(tmp_path: Path):
    samples_dir = tmp_path / "samples"
    samples_dir.mkdir()
    (samples_dir / "sample_0.json").write_text(json.dumps({
        "target": "re2", "model": "m", "experiment": "exp1", "sample_index": 0,
        "inputs": [], "parse_status": "parse_failure", "raw_response": "",
    }))
    row = cu.aggregate_cell(samples_dir, _profile({}))
    assert row["seeds"] == 0
    assert row["citation_count_total"] == 0
    assert row["hit_rate"] == 0.0
