"""Unit tests for the grounding verifier.

Synthetic fixtures only — no real ablation data touched. Covers:
- Step 1 (Quote) extraction from reasoning strings.
- verbatim / paraphrased / hallucinated classification.
- target_gaps fabrication detection.
- multi-sample aggregation.
- the --walk discovery path.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis.scripts.verify_grounding import (
    _extract_step1_quote,
    _ground_quote,
    _walk_cells,
    verify_cell,
)


def _write_prep_dataset(root: Path, target: str = "re2") -> None:
    d = root / target
    d.mkdir(parents=True)
    d.joinpath("coverage_gaps.json").write_text(json.dumps({
        "gap_branches": [
            {
                "file": "re2/parse.cc", "line": 42,
                "uncovered_side": "true",
                "condition_description": "p == end",
                "code_context": "if (p == end) return false;",
            },
            {
                "file": "re2/prog.cc", "line": 108,
                "uncovered_side": "false",
                "condition_description": "flags & EmptyWidth",
                "code_context": "if (flags & EmptyWidth) { Emit(kInstEmptyWidth); }",
            },
        ],
    }))


def _write_sample(
    synthesis_dir: Path, sample_idx: int,
    inputs: list[dict],
) -> None:
    synthesis_dir.mkdir(parents=True, exist_ok=True)
    synthesis_dir.joinpath(f"sample_{sample_idx}.json").write_text(json.dumps({
        "target": "re2", "model": "m", "experiment": "test",
        "sample_index": sample_idx,
        "inputs": inputs,
        "parse_status": "ok",
        "raw_response": "",
        "log": {
            "model": "m", "temperature": 0.0, "top_p": 1.0,
            "input_tokens": 10, "output_tokens": 10, "cost_usd": 0.0,
            "latency_ms": 0.0, "prompt_hash": "h",
            "timestamp": "2026-04-23T00:00:00+00:00",
            "generation_wall_clock_s": 0.0,
            "target": "re2", "phase": "ablation",
            "experiment_tag": "cell=x,sample=0", "cached": False,
        },
    }))


# --- unit helpers ----------------------------------------------------


def test_extract_step1_quote_simple() -> None:
    r = (
        "Step 1 (Quote): if (p == end) return false;\n"
        "Step 2 (Locate): Parser::Parse"
    )
    assert _extract_step1_quote(r) == "if (p == end) return false;"


def test_extract_step1_quote_trailing_period() -> None:
    r = "Step 1 (Quote): flags & EmptyWidth. Step 2 (Locate): Prog::Emit"
    assert _extract_step1_quote(r) == "flags & EmptyWidth"


def test_extract_step1_quote_none_when_label_absent() -> None:
    assert _extract_step1_quote("free-form reasoning without labels") is None


def test_extract_step1_quote_rejects_empty() -> None:
    assert _extract_step1_quote("Step 1 (Quote): ") is None


def test_ground_quote_grounded() -> None:
    haystack = "if (p == end) return false; // parse.cc:42"
    assert _ground_quote("if (p == end) return false;", haystack) == "grounded"


def test_ground_quote_paraphrased() -> None:
    # Close but not exact (one character difference).
    haystack = "if (p == end) return false;"
    # Introduce a small diff — still ≥0.8 ratio.
    assert _ground_quote(
        "if(p == end)return false", haystack,
    ) == "paraphrased"


def test_ground_quote_hallucinated() -> None:
    haystack = "unrelated code in a different file"
    assert _ground_quote(
        "xyz_definitely_not_present_xyz", haystack,
    ) == "hallucinated"


def test_ground_quote_short_is_hallucinated() -> None:
    # 3-char quotes are unverifiable noise; classify as hallucinated.
    assert _ground_quote("ab", "this contains ab") == "hallucinated"


# --- cell-level aggregation -----------------------------------------


def test_verify_cell_mixed_seeds(tmp_path: Path) -> None:
    dataset_root = tmp_path / "prep"
    _write_prep_dataset(dataset_root)
    synthesis_dir = tmp_path / "synth" / "cot_strict" / "v3_all" / "gpt-oss-20b"

    # Seed 1: grounded quote, real target_gap.
    _write_sample(synthesis_dir, 0, [{
        "input_id": "s1", "content_b64": "AA==",
        "target_gaps": ["re2/parse.cc:42"],
        "reasoning": (
            "Step 1 (Quote): if (p == end) return false;\n"
            "Step 2 (Locate): Parser::Parse\n"
            "Step 3 (Construct): ^$\n"
            "Step 4 (Regex): ^$"
        ),
    }])
    # Seed 2: hallucinated quote, fabricated target_gap.
    _write_sample(synthesis_dir, 1, [{
        "input_id": "s2", "content_b64": "AA==",
        "target_gaps": ["re2/parse.cc:42", "re2/nonexistent.cc:999"],
        "reasoning": (
            "Step 1 (Quote): fabricated_condition_never_in_prompt\n"
            "Step 2 (Locate): DFA::FakePath"
        ),
    }])
    # Seed 3: missing Step 1 altogether (non-CoT strategy).
    _write_sample(synthesis_dir, 2, [{
        "input_id": "s3", "content_b64": "AA==",
        "target_gaps": ["re2/prog.cc:108"],
        "reasoning": "free-form reasoning",
    }])

    r = verify_cell(synthesis_dir, dataset_root=dataset_root, target="re2")
    assert r["seeds_total"] == 3
    assert r["quote_grounded"] == 1
    assert r["quote_hallucinated"] == 1
    assert r["reasoning_missing_step1"] == 1
    assert r["target_gaps_declared"] == 4  # 1 + 2 + 1
    assert r["target_gaps_fabricated"] == 1  # re2/nonexistent.cc:999
    # Rates
    assert r["hallucinated_quote_rate"] == pytest.approx(0.5)  # 1/2 attested
    assert r["grounded_quote_rate"] == pytest.approx(0.5)
    assert r["target_gap_fabrication_rate"] == pytest.approx(0.25)  # 1/4


def test_verify_cell_no_samples_returns_error(tmp_path: Path) -> None:
    dataset_root = tmp_path / "prep"
    _write_prep_dataset(dataset_root)
    empty = tmp_path / "synth" / "empty"
    empty.mkdir(parents=True)
    r = verify_cell(empty, dataset_root=dataset_root, target="re2")
    assert r["error"] == "no sample_*.json files"


def test_walk_cells_finds_leaf_dirs(tmp_path: Path) -> None:
    # Two cells, three sample files total.
    (tmp_path / "cellA").mkdir()
    (tmp_path / "cellA" / "sample_0.json").write_text("{}")
    (tmp_path / "cellA" / "sample_1.json").write_text("{}")
    (tmp_path / "cellB").mkdir()
    (tmp_path / "cellB" / "sample_0.json").write_text("{}")
    # Empty dir must be ignored.
    (tmp_path / "empty").mkdir()

    cells = _walk_cells(tmp_path)
    names = {p.name for p in cells}
    assert names == {"cellA", "cellB"}
