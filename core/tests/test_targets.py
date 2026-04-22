"""Smoke test for the TargetSpec registry."""
from __future__ import annotations

from pathlib import Path

from core.targets import TARGETS, TargetSpec


def test_re2_spec_valid():
    spec = TARGETS["re2"]
    assert isinstance(spec, TargetSpec)
    assert spec.name == "re2"
    assert spec.input_format == "regex"
    assert spec.m2_targets_path.exists(), (
        f"RE2 M2 targets file missing at {spec.m2_targets_path} — run "
        "`python -m analysis.scripts.freeze_target_branches --target re2_v2`"
    )
    assert spec.m2_targets_path.name == "m2_target_branches.json"


def test_harfbuzz_spec_valid():
    spec = TARGETS["harfbuzz"]
    assert spec.name == "harfbuzz"
    assert spec.input_format == "binary"
    assert spec.m2_targets_path.exists(), (
        f"Harfbuzz M2 targets file missing at {spec.m2_targets_path} — run "
        "`python -m analysis.scripts.freeze_target_branches --target harfbuzz`"
    )
    assert spec.max_gaps_override == 30


def test_input_format_is_constrained():
    for name, spec in TARGETS.items():
        assert spec.input_format in {"regex", "binary", "text"}, (
            f"{name}: unexpected input_format {spec.input_format!r}"
        )


def test_cell_paths_are_under_results_root():
    for spec in TARGETS.values():
        seeds = spec.cell_seeds_dir("v0_none", "some-model")
        assert isinstance(seeds, Path)
        assert spec.results_root in spec.cell_m1_dir("v0_none", "m").parents
        assert spec.results_root in spec.cell_m2_dir("v0_none", "m").parents


def test_default_strategy_preserves_legacy_paths():
    """strategy='default' must produce the pre-registry layout verbatim."""
    for spec in TARGETS.values():
        assert (
            spec.cell_seeds_dir("v0_none", "m", strategy="default")
            == spec.cell_seeds_dir("v0_none", "m")
        )
        assert (
            spec.cell_m1_dir("v0_none", "m", strategy="default")
            == spec.cell_m1_dir("v0_none", "m")
        )
        assert (
            spec.cell_m2_dir("v0_none", "m", strategy="default")
            == spec.cell_m2_dir("v0_none", "m")
        )
        # And no "default" segment leaks into the legacy path.
        assert "default" not in spec.cell_m1_dir("v0_none", "m").parts


def test_non_default_strategy_inserts_segment():
    """Non-default strategies get a leading <strategy> segment under the root."""
    for spec in TARGETS.values():
        seeds = spec.cell_seeds_dir("v0_none", "m", strategy="cot_strict")
        m1 = spec.cell_m1_dir("v0_none", "m", strategy="cot_strict")
        m2 = spec.cell_m2_dir("v0_none", "m", strategy="cot_strict")
        assert "cot_strict" in seeds.parts
        assert "cot_strict" in m1.parts
        assert "cot_strict" in m2.parts
        # Still rooted under the expected bases.
        assert spec.results_root in m1.parents
        assert spec.results_root in m2.parents
