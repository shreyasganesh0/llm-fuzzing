"""Unit tests for the retrieval tools used by cell E (tool-driven RAG).

Hallucination-safety invariants:
- list_uncovered_branches reads coverage_gaps.json verbatim. No fabrication.
- get_source rejects path traversal and clamps slice size.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from synthesis.scripts.oracles import (
    GET_SOURCE_TOOL_OPENAI,
    LIST_UNCOVERED_BRANCHES_TOOL_OPENAI,
    get_source,
    list_uncovered_branches,
)


def _write_gap_fixture(root: Path, target: str, k: int) -> Path:
    target_dir = root / target
    target_dir.mkdir(parents=True, exist_ok=True)
    gaps = {
        "gap_branches": [
            {
                "file": f"re2/parse.cc",
                "line": 100 + i,
                "uncovered_side": "true",
                "condition_description": f"condition {i}",
                "code_context": f"ctx {i}",
            }
            for i in range(k)
        ],
    }
    path = target_dir / "coverage_gaps.json"
    path.write_text(json.dumps(gaps))
    return path


def test_list_uncovered_branches_clamps_k(tmp_path: Path) -> None:
    _write_gap_fixture(tmp_path, "re2", k=30)
    r = list_uncovered_branches(target="re2", k=50, dataset_root=tmp_path)
    assert r["k_returned"] == 20  # clamped to _MAX_GAPS_K
    assert len(r["gaps"]) == 20


def test_list_uncovered_branches_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        list_uncovered_branches(target="re2", k=5, dataset_root=tmp_path)


def test_list_uncovered_branches_preserves_content(tmp_path: Path) -> None:
    _write_gap_fixture(tmp_path, "re2", k=3)
    r = list_uncovered_branches(target="re2", k=3, dataset_root=tmp_path)
    assert r["gaps"][0]["line"] == 100
    assert r["gaps"][0]["condition_description"] == "condition 0"
    assert r["gaps"][2]["line"] == 102


def test_get_source_returns_slice(tmp_path: Path) -> None:
    src = tmp_path / "re2" / "parse.cc"
    src.parent.mkdir(parents=True)
    src.write_text("\n".join(f"line {i}" for i in range(1, 50)))
    r = get_source(
        target="re2", file="re2/parse.cc",
        line_start=10, line_end=15, source_root=tmp_path,
    )
    assert r["line_start"] == 10
    assert r["line_end"] == 15
    assert r["content"].splitlines() == [f"line {i}" for i in range(10, 16)]


def test_get_source_clamps_200_lines(tmp_path: Path) -> None:
    src = tmp_path / "re2" / "big.cc"
    src.parent.mkdir(parents=True)
    src.write_text("\n".join(f"L{i}" for i in range(1, 1000)))
    r = get_source(
        target="re2", file="re2/big.cc",
        line_start=1, line_end=999, source_root=tmp_path,
    )
    assert r["line_end"] - r["line_start"] + 1 == 200


def test_get_source_rejects_path_traversal(tmp_path: Path) -> None:
    (tmp_path / "re2").mkdir()
    (tmp_path / "re2" / "parse.cc").write_text("x")
    (tmp_path.parent / "secret.txt").write_text("leak")
    with pytest.raises(ValueError, match="outside source_root"):
        get_source(
            target="re2", file="../../etc/passwd",
            line_start=1, line_end=5, source_root=tmp_path,
        )


def test_get_source_missing_file(tmp_path: Path) -> None:
    (tmp_path / "re2").mkdir()
    with pytest.raises(FileNotFoundError):
        get_source(
            target="re2", file="re2/nope.cc",
            line_start=1, line_end=5, source_root=tmp_path,
        )


def test_tool_schemas_are_openai_shape() -> None:
    for tool in (LIST_UNCOVERED_BRANCHES_TOOL_OPENAI, GET_SOURCE_TOOL_OPENAI):
        assert tool["type"] == "function"
        assert "name" in tool["function"]
        assert "parameters" in tool["function"]
        assert tool["function"]["parameters"]["type"] == "object"
