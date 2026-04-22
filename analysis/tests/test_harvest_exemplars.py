"""Tests for analysis/scripts/harvest_exemplars.py (Phase 4)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_synthesis_record(
    root: Path, target: str, variant: str, model: str, sample_index: int,
    inputs: list[dict],
) -> Path:
    cell_dir = root / "synthesis" / target / "ablation" / variant / model
    cell_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "target": target,
        "model": model,
        "experiment": "exp1",
        "sample_index": sample_index,
        "inputs": inputs,
        "parse_status": "ok",
        "raw_response": "",
        "log": None,
    }
    p = cell_dir / f"sample_{sample_index}.json"
    p.write_text(json.dumps(record))
    return p


def _mk_input(
    *, input_id: str, content_b64: str, reasoning: str,
    target: str = "harfbuzz", model: str = "llama-3.1-8b-instruct",
    sample_index: int = 0,
) -> dict:
    return {
        "input_id": input_id,
        "content_b64": content_b64,
        "target_gaps": [f"src/foo.cc:{sample_index + 10}"],
        "reasoning": reasoning,
        "source": "llm",
        "model": model,
        "temperature": 0.7,
        "sample_index": sample_index,
        "target": target,
        "experiment": "exp1",
    }


def test_harvest_reads_from_synthetic_cell(tmp_path: Path):
    from analysis.scripts.harvest_exemplars import harvest_target

    seeds_root = tmp_path / "synth"
    results_root = tmp_path / "res"  # empty → M2/M1 scorers return None
    _write_synthesis_record(
        seeds_root, "harfbuzz", "v2_src_tests", "llama-3.1-8b-instruct", 0,
        [
            _mk_input(
                input_id="aaaa0001",
                content_b64="AAEAAAAKAIAAAwAg",
                reasoning="Targets sfnt magic bytes.",
            ),
            _mk_input(
                input_id="aaaa0002",
                content_b64="T1RUTwAKAIAAAw==",
                reasoning="Exercises CFF OTTO magic.",
            ),
            _mk_input(
                input_id="aaaa0003",
                content_b64="dHRjZgABAAAAAgAA",
                reasoning="Targets TrueType collection header.",
            ),
        ],
    )

    exemplars, method = harvest_target(
        "harfbuzz",
        synthesis_root=seeds_root, results_root=results_root,
        n_exemplars=3,
    )

    assert method == "v2_src_tests_fallback"
    assert len(exemplars) == 3
    for ex in exemplars:
        origin = ex["origin"]
        assert origin["variant"] == "v2_src_tests"
        assert origin["model"] == "llama-3.1-8b-instruct"
        assert isinstance(origin["sample_index"], int)
        assert origin["seed_path"].endswith(".json")
        # Harfbuzz → base64 content; no regex content.
        assert ex["content"] is None
        assert isinstance(ex["content_b64"], str) and ex["content_b64"]
        assert ex["reasoning"]
        assert isinstance(ex["target_gaps"], list)


def test_harvest_skips_v0_none_cells(tmp_path: Path):
    from analysis.scripts.harvest_exemplars import harvest_target

    seeds_root = tmp_path / "synth"
    results_root = tmp_path / "res"
    # v0_none seed — should be skipped.
    _write_synthesis_record(
        seeds_root, "harfbuzz", "v0_none", "llama-3.1-8b-instruct", 0,
        [_mk_input(
            input_id="v0__0001",
            content_b64="V0NOVE5FQUFBQQ==",
            reasoning="V0-NONE-REASONING should not be picked.",
        )],
    )
    # v2_src_tests seed — should be picked.
    _write_synthesis_record(
        seeds_root, "harfbuzz", "v2_src_tests", "llama-3.1-8b-instruct", 0,
        [_mk_input(
            input_id="v2__0001",
            content_b64="AAEAAAAKAIAAAwAg",
            reasoning="V2-REASONING picked by fallback.",
        )],
    )

    exemplars, method = harvest_target(
        "harfbuzz",
        synthesis_root=seeds_root, results_root=results_root,
        n_exemplars=3,
    )

    assert method == "v2_src_tests_fallback"
    assert len(exemplars) == 1
    ex = exemplars[0]
    assert ex["origin"]["variant"] == "v2_src_tests"
    assert "V2-REASONING" in ex["reasoning"]
    assert "V0-NONE-REASONING" not in ex["reasoning"]


def test_harvest_truncates_long_reasoning(tmp_path: Path):
    from analysis.scripts.harvest_exemplars import (
        MAX_REASONING_CHARS,
        harvest_target,
    )

    seeds_root = tmp_path / "synth"
    results_root = tmp_path / "res"
    long_reasoning = "x" * 2000
    _write_synthesis_record(
        seeds_root, "harfbuzz", "v2_src_tests", "llama-3.1-8b-instruct", 0,
        [_mk_input(
            input_id="longr_01",
            content_b64="AAEAAAAKAIAAAwAg",
            reasoning=long_reasoning,
        )],
    )

    exemplars, _method = harvest_target(
        "harfbuzz",
        synthesis_root=seeds_root, results_root=results_root,
        n_exemplars=3,
    )
    assert len(exemplars) == 1
    r = exemplars[0]["reasoning"]
    assert len(r) <= MAX_REASONING_CHARS
    assert r.endswith("…") or r.endswith("...")


def test_harvest_re2_decodes_content_b64_to_regex_body(tmp_path: Path):
    """RE2 content_b64 is base64 of [2 flag bytes][regex body]. The
    harvester must strip the flag bytes and expose the regex string."""
    import base64

    from analysis.scripts.harvest_exemplars import harvest_target

    seeds_root = tmp_path / "synth"
    results_root = tmp_path / "res"
    flag_bytes = b"\x00\x01"
    regex_body = r"\p{Greek}+"
    content_b64 = base64.b64encode(flag_bytes + regex_body.encode()).decode()

    _write_synthesis_record(
        seeds_root, "re2", "v2_src_tests", "llama-3.1-8b-instruct", 0,
        [_mk_input(
            input_id="re2_0001",
            content_b64=content_b64,
            reasoning="Targets Unicode class parser.",
            target="re2",
        )],
    )

    exemplars, method = harvest_target(
        "re2",
        synthesis_root=seeds_root, results_root=results_root,
        n_exemplars=3,
    )
    assert method == "v2_src_tests_fallback"
    assert len(exemplars) == 1
    assert exemplars[0]["content"] == regex_body
    assert exemplars[0]["content_b64"] is None


def test_harvest_empty_targets_return_empty_method(tmp_path: Path):
    from analysis.scripts.harvest_exemplars import harvest_target

    exemplars, method = harvest_target(
        "harfbuzz",
        synthesis_root=tmp_path / "synth",
        results_root=tmp_path / "res",
    )
    assert exemplars == []
    assert method == "empty_no_seeds_found"


def test_harvest_skips_seeds_with_empty_reasoning(tmp_path: Path):
    from analysis.scripts.harvest_exemplars import harvest_target

    seeds_root = tmp_path / "synth"
    results_root = tmp_path / "res"
    _write_synthesis_record(
        seeds_root, "harfbuzz", "v2_src_tests", "llama-3.1-8b-instruct", 0,
        [
            _mk_input(
                input_id="empty_01",
                content_b64="AAEAAAAKAIAAAwAg",
                reasoning="",  # empty — must skip
            ),
            _mk_input(
                input_id="good_01",
                content_b64="T1RUTwAKAIAAAw==",
                reasoning="Exercises OTTO magic.",
            ),
        ],
    )
    exemplars, _method = harvest_target(
        "harfbuzz",
        synthesis_root=seeds_root, results_root=results_root,
        n_exemplars=3,
    )
    assert len(exemplars) == 1
    assert "OTTO" in exemplars[0]["reasoning"]


def test_harvest_fixture_output_format(tmp_path: Path):
    """End-to-end: writing the fixture file matches the documented schema."""
    from analysis.scripts.harvest_exemplars import (
        harvest_target,
        write_fixture,
    )

    seeds_root = tmp_path / "synth"
    results_root = tmp_path / "res"
    out_path = tmp_path / "fixture.json"
    _write_synthesis_record(
        seeds_root, "harfbuzz", "v2_src_tests", "llama-3.1-8b-instruct", 0,
        [_mk_input(
            input_id="aaaa0001",
            content_b64="AAEAAAAKAIAAAwAg",
            reasoning="test reasoning",
        )],
    )
    exemplars, method = harvest_target(
        "harfbuzz",
        synthesis_root=seeds_root, results_root=results_root,
    )
    write_fixture(
        target="harfbuzz", exemplars=exemplars, selection_method=method,
        out_path=out_path,
        source_commit="abc123", harvested_at="2026-04-21T00:00:00+00:00",
    )
    doc = json.loads(out_path.read_text())
    assert doc["target"] == "harfbuzz"
    assert doc["schema_version"] == 1
    assert doc["selection_method"] == "v2_src_tests_fallback"
    assert doc["source_commit"] == "abc123"
    assert doc["harvested_at"] == "2026-04-21T00:00:00+00:00"
    assert len(doc["exemplars"]) == 1


def test_harvest_is_idempotent(tmp_path: Path):
    """Same inputs + frozen timestamp/commit → byte-identical output."""
    from analysis.scripts.harvest_exemplars import main as harvest_main

    seeds_root = tmp_path / "synth"
    results_root = tmp_path / "res"
    _write_synthesis_record(
        seeds_root, "harfbuzz", "v2_src_tests", "llama-3.1-8b-instruct", 0,
        [_mk_input(
            input_id="aaaa0001",
            content_b64="AAEAAAAKAIAAAwAg",
            reasoning="idempotent test.",
        )],
    )
    out1 = tmp_path / "a.json"
    out2 = tmp_path / "b.json"
    common = [
        "--target", "harfbuzz",
        "--seeds-root", str(seeds_root),
        "--results-root", str(results_root),
        "--frozen-now", "2026-04-21T00:00:00+00:00",
        "--frozen-commit", "deadbeef",
    ]
    assert harvest_main(common + ["--out", str(out1)]) == 0
    assert harvest_main(common + ["--out", str(out2)]) == 0
    assert out1.read_bytes() == out2.read_bytes()


def test_harvest_m2_best_wins_over_fallback(tmp_path: Path):
    """If an M2 summary exists, the cell it ranks best should be picked
    over the v2_src_tests fallback."""
    from analysis.scripts.harvest_exemplars import harvest_target

    seeds_root = tmp_path / "synth"
    results_root = tmp_path / "res"
    # Two candidate cells with seeds.
    _write_synthesis_record(
        seeds_root, "harfbuzz", "v2_src_tests", "llama-3.1-8b-instruct", 0,
        [_mk_input(
            input_id="low__01",
            content_b64="AAEAAAAKAIAAAwAg",
            reasoning="LOW-SCORE-CELL",
        )],
    )
    _write_synthesis_record(
        seeds_root, "harfbuzz", "v1_src", "codestral-22b", 0,
        [_mk_input(
            input_id="high_01",
            content_b64="T1RUTwAKAIAAAw==",
            reasoning="HIGH-SCORE-CELL",
            model="codestral-22b",
        )],
    )
    # M2 summaries: v1_src/codestral-22b scores higher.
    def _write_m2(variant: str, model: str, score: float) -> None:
        d = results_root / "m2" / variant / model
        d.mkdir(parents=True, exist_ok=True)
        (d / "summary.json").write_text(json.dumps({
            "slices": {"shown": {"union_frac_targets_hit": score}},
        }))

    _write_m2("v2_src_tests", "llama-3.1-8b-instruct", 0.05)
    _write_m2("v1_src", "codestral-22b", 0.42)

    exemplars, method = harvest_target(
        "harfbuzz",
        synthesis_root=seeds_root, results_root=results_root,
        n_exemplars=3,
    )
    assert method == "m2_best"
    # The highest-M2 cell should come first; lower-ranked cell fills remainder.
    assert len(exemplars) >= 1
    first = exemplars[0]
    assert first["origin"]["variant"] == "v1_src"
    assert first["origin"]["model"] == "codestral-22b"
    assert "HIGH-SCORE-CELL" in first["reasoning"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
