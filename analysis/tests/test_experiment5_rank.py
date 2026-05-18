"""Unit tests for analysis/scripts/experiment5_rank.py.

These tests exercise ONLY the pure offline plumbing on SYNTHETIC
``summary.json`` + ``gap_hits.jsonl`` fixtures. They never invoke the real
M1/M2 metrics, the RE2 ``seed_replay`` binary, LLVM, or any network — so the
suite stays green without the coverage toolchain (matching the
experiment4_score test discipline).

Coverage:
  * path resolution      — default strategy has NO <strategy> segment;
                            non-default inserts it.
  * m2_of / m1_of        — read the exact documented keys; tolerate
                            missing slices.
  * cell_status          — MISSING / CENSORED (n_seeds<150) / OK.
  * union_m2             — hand-checked tiny matrix.
  * bootstrap_cell_ci    — determinism (same seed -> identical CI twice)
                            and CI brackets the point estimate.
  * holm_contrasts       — strict domination of default on every branch
                            yields a Holm-significant contrast; a null
                            (identical) case does not.
  * rank end-to-end      — ~6 synthetic cells (incl. MISSING + CENSORED)
                            produce a stable ranked table + valid Friedman.

Tests needing scipy are guarded with skipif.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from analysis.scripts.experiment5_rank import (
    DEFAULT_STRATEGY,
    STATUS_CENSORED,
    STATUS_MISSING,
    STATUS_OK,
    bootstrap_cell_ci,
    cell_gap_hits_path,
    cell_status,
    cell_summary_path,
    holm_contrasts,
    load_cell,
    m1_of,
    m2_of,
    rank,
    union_m2,
)

_HAS_SCIPY = importlib.util.find_spec("scipy") is not None
_scipy_required = pytest.mark.skipif(not _HAS_SCIPY, reason="scipy not installed")


# ---------------------------------------------------------------------------
# Synthetic fixture helpers (no binaries, no LLVM).
# ---------------------------------------------------------------------------
def _write_m2_cell(
    root: Path,
    strategy: str,
    variant: str,
    model: str,
    *,
    n_seeds: int,
    union_frac: float,
    hit_rows: list[tuple[str, int, bool]] | None,
) -> Path:
    """Write a synthetic m2 summary.json (+ gap_hits.jsonl if rows given)."""
    out = cell_summary_path(root, strategy, variant, model, "m2").parent
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "n_seeds": n_seeds,
        "n_targets": 3,
        "slices": {"all": {"union_frac_targets_hit": union_frac}},
        "seed_ids": [f"seed_{i}" for i in range(n_seeds)],
    }
    (out / "summary.json").write_text(json.dumps(summary))
    if hit_rows is not None:
        with open(out / "gap_hits.jsonl", "w") as fh:
            for sid, tidx, hit in hit_rows:
                fh.write(json.dumps({
                    "seed_id": sid, "target_idx": tidx, "hit": hit,
                }) + "\n")
    return out


def _write_m1_cell(
    root: Path, strategy: str, variant: str, model: str, *, edges: int,
) -> Path:
    out = cell_summary_path(root, strategy, variant, model, "m1").parent
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps({
        "edges_covered": edges, "edges_total": 3380, "files": 30,
    }))
    return out


# ---------------------------------------------------------------------------
# 1. Path resolution.
# ---------------------------------------------------------------------------
def test_path_resolution_default_has_no_strategy_segment(tmp_path: Path) -> None:
    root = tmp_path / "ablation_re2_v2"
    p = cell_summary_path(root, DEFAULT_STRATEGY, "v1_src", "codestral-22b", "m2")
    assert p == root / "m2" / "v1_src" / "codestral-22b" / "summary.json"
    # default => the <strategy> segment must be absent.
    assert "default" not in p.parts


def test_path_resolution_non_default_inserts_strategy_segment(tmp_path: Path) -> None:
    root = tmp_path / "ablation_re2_v2"
    p = cell_summary_path(root, "cot_strict", "v3_all", "codestral-22b", "m1")
    assert p == root / "cot_strict" / "m1" / "v3_all" / "codestral-22b" / "summary.json"
    gh = cell_gap_hits_path(root, "cot_strict", "v3_all", "codestral-22b")
    assert gh == root / "cot_strict" / "m2" / "v3_all" / "codestral-22b" / "gap_hits.jsonl"


# ---------------------------------------------------------------------------
# 2. Key readers + status.
# ---------------------------------------------------------------------------
def test_m2_of_reads_documented_key() -> None:
    summary = {"slices": {"all": {"union_frac_targets_hit": 0.6}}}
    assert m2_of(summary) == 0.6


def test_m2_of_tolerates_missing_slices() -> None:
    assert m2_of(None) is None
    assert m2_of({}) is None
    assert m2_of({"slices": {}}) is None
    assert m2_of({"slices": {"all": {}}}) is None


def test_m1_of_reads_documented_key() -> None:
    assert m1_of({"edges_covered": 1382}) == 1382
    assert m1_of(None) is None
    assert m1_of({"edges_total": 3380}) is None


def test_cell_status_missing_censored_ok() -> None:
    assert cell_status(None) == STATUS_MISSING
    assert cell_status({"n_seeds": 140}) == STATUS_CENSORED
    assert cell_status({"n_seeds": 150}) == STATUS_OK
    assert cell_status({}) == STATUS_CENSORED  # no n_seeds -> censored


def test_load_cell_missing_returns_none(tmp_path: Path) -> None:
    assert load_cell(tmp_path, "few_shot", "v0_none", "codestral-22b", "m2") is None


def test_load_cell_present_round_trips(tmp_path: Path) -> None:
    root = tmp_path / "r"
    _write_m2_cell(root, DEFAULT_STRATEGY, "v1_src", "codestral-22b",
                   n_seeds=150, union_frac=0.4, hit_rows=None)
    s = load_cell(root, DEFAULT_STRATEGY, "v1_src", "codestral-22b", "m2")
    assert s is not None and m2_of(s) == 0.4 and cell_status(s) == STATUS_OK


# ---------------------------------------------------------------------------
# 3. union_m2 on a hand-checked tiny matrix.
# ---------------------------------------------------------------------------
def test_union_m2_hand_checked() -> None:
    # targets 0,1,2; s1 hits {0}, s2 hits {2}. Union over {s1,s2} = {0,2}/3.
    hit = {
        ("s1", 0): True, ("s1", 1): False, ("s1", 2): False,
        ("s2", 0): False, ("s2", 1): False, ("s2", 2): True,
    }
    assert union_m2(["s1", "s2"], [0, 1, 2], hit) == pytest.approx(2 / 3)
    assert union_m2(["s1"], [0, 1, 2], hit) == pytest.approx(1 / 3)
    assert union_m2([], [0, 1, 2], hit) == 0.0
    assert union_m2(["s1"], [], hit) == 0.0


# ---------------------------------------------------------------------------
# 4. bootstrap_cell_ci — determinism + brackets the point.
# ---------------------------------------------------------------------------
def test_bootstrap_cell_ci_deterministic_and_brackets_point(tmp_path: Path) -> None:
    gh = tmp_path / "gap_hits.jsonl"
    # 5 seeds, 3 targets; seeds variably hit -> a non-degenerate CI.
    rows: list[tuple[str, int, bool]] = []
    pattern = {
        "a": {0: True, 1: False, 2: False},
        "b": {0: False, 1: True, 2: False},
        "c": {0: False, 1: False, 2: True},
        "d": {0: True, 1: True, 2: False},
        "e": {0: False, 1: False, 2: False},
    }
    with open(gh, "w") as fh:
        for sid, tmap in pattern.items():
            for ti, hv in tmap.items():
                rows.append((sid, ti, hv))
                fh.write(json.dumps({"seed_id": sid, "target_idx": ti, "hit": hv}) + "\n")

    p1, lo1, hi1 = bootstrap_cell_ci(gh, n=500, seed=42)
    p2, lo2, hi2 = bootstrap_cell_ci(gh, n=500, seed=42)
    # Same seed -> byte-identical CI on every call.
    assert (p1, lo1, hi1) == (p2, lo2, hi2)
    # Observed union = all 3 targets hit by someone -> 1.0.
    assert p1 == pytest.approx(1.0)
    # CI must bracket the point estimate.
    assert lo1 <= p1 <= hi1


def test_bootstrap_cell_ci_all_hit_is_degenerate(tmp_path: Path) -> None:
    gh = tmp_path / "gap_hits.jsonl"
    with open(gh, "w") as fh:
        for sid in ("a", "b"):
            for ti in (0, 1):
                fh.write(json.dumps({"seed_id": sid, "target_idx": ti, "hit": True}) + "\n")
    point, lo, hi = bootstrap_cell_ci(gh, n=200, seed=42)
    assert point == 1.0 and lo == 1.0 and hi == 1.0


# ---------------------------------------------------------------------------
# 5. Holm contrasts: strict domination vs null.
# ---------------------------------------------------------------------------
def _gap_rows_all_targets(hits_by_target: dict[int, bool], seeds: list[str]):
    """Every seed has the same per-target hit (so the cell's per-target
    union indicator == hits_by_target)."""
    rows: list[tuple[str, int, bool]] = []
    for sid in seeds:
        for ti, hv in hits_by_target.items():
            rows.append((sid, ti, hv))
    return rows


@_scipy_required
def test_holm_contrast_strict_domination_is_significant(tmp_path: Path) -> None:
    root = tmp_path / "r"
    variant = "v1_src"
    model = "codestral-22b"
    targets = list(range(12))
    # default hits NOTHING; strategy A hits EVERY branch -> strict
    # domination on all 12 -> Wilcoxon strongly significant, Holm survives.
    _write_m2_cell(
        root, DEFAULT_STRATEGY, variant, model, n_seeds=150, union_frac=0.0,
        hit_rows=_gap_rows_all_targets({t: False for t in targets}, ["s0", "s1"]),
    )
    _write_m2_cell(
        root, "cot_strict", variant, model, n_seeds=150, union_frac=1.0,
        hit_rows=_gap_rows_all_targets({t: True for t in targets}, ["s0", "s1"]),
    )
    out = holm_contrasts(root, model, [variant], [DEFAULT_STRATEGY, "cot_strict"])
    c = next(c for c in out["contrasts"] if c["strategy"] == "cot_strict")
    assert c["status"] == "tested"
    assert c["delta_union_m2"] == pytest.approx(1.0)
    assert c["significant"] is True
    assert c["p_holm"] < 0.05


@_scipy_required
def test_holm_contrast_null_case_not_significant(tmp_path: Path) -> None:
    root = tmp_path / "r"
    variant = "v2_src_tests"
    model = "codestral-22b"
    targets = list(range(12))
    same = {t: (t % 2 == 0) for t in targets}
    # default and strategy have IDENTICAL per-target indicators -> all
    # paired diffs are 0 -> not significant (Wilcoxon undefined -> None).
    _write_m2_cell(root, DEFAULT_STRATEGY, variant, model, n_seeds=150,
                   union_frac=0.5, hit_rows=_gap_rows_all_targets(same, ["s0"]))
    _write_m2_cell(root, "few_shot", variant, model, n_seeds=150,
                   union_frac=0.5, hit_rows=_gap_rows_all_targets(same, ["s0"]))
    out = holm_contrasts(root, model, [variant], [DEFAULT_STRATEGY, "few_shot"])
    c = next(c for c in out["contrasts"] if c["strategy"] == "few_shot")
    assert c["significant"] is False


def test_holm_contrast_skips_missing_cell(tmp_path: Path) -> None:
    root = tmp_path / "r"
    variant = "v0_none"
    model = "codestral-22b"
    # default exists, strategy cell absent -> skipped, never crashes.
    _write_m2_cell(root, DEFAULT_STRATEGY, variant, model, n_seeds=150,
                   union_frac=0.3, hit_rows=_gap_rows_all_targets({0: True}, ["s0"]))
    out = holm_contrasts(root, model, [variant], [DEFAULT_STRATEGY, "prompt_chain"])
    c = next(c for c in out["contrasts"] if c["strategy"] == "prompt_chain")
    assert c["status"] == "skipped_missing_cell"
    assert c["significant"] is False


# ---------------------------------------------------------------------------
# 6. rank() end-to-end on ~6 synthetic cells (incl. MISSING + CENSORED).
# ---------------------------------------------------------------------------
@_scipy_required
def test_rank_end_to_end_stable_table_and_friedman(tmp_path: Path) -> None:
    root = tmp_path / "ablation_re2_v2"
    out_dir = tmp_path / "out"
    model = "codestral-22b"
    variants = ["v0_none", "v1_src"]
    # 4 strategies so v1_src can have >=3 OK cells (scipy Friedman needs k>=3).
    strategies = [DEFAULT_STRATEGY, "cot_strict", "few_shot", "self_critique"]
    targets = list(range(10))

    def rows(frac_true: int):
        # first `frac_true` targets hit by the single seed.
        m = {t: (t < frac_true) for t in targets}
        return _gap_rows_all_targets(m, ["s0", "s1", "s2"])

    # v0_none: only default present (cot/few/self_critique MISSING) ->
    # <3 OK cells -> Friedman not computable here.
    _write_m2_cell(root, DEFAULT_STRATEGY, "v0_none", model, n_seeds=150,
                   union_frac=0.2, hit_rows=rows(2))
    _write_m1_cell(root, DEFAULT_STRATEGY, "v0_none", model, edges=1000)

    # v1_src: default=0.4, cot=0.6, self_critique=0.5 (3 OK -> Friedman),
    #         few_shot=CENSORED (n_seeds<150 -> excluded from rank+Friedman).
    _write_m2_cell(root, DEFAULT_STRATEGY, "v1_src", model, n_seeds=150,
                   union_frac=0.4, hit_rows=rows(4))
    _write_m1_cell(root, DEFAULT_STRATEGY, "v1_src", model, edges=1200)
    _write_m2_cell(root, "cot_strict", "v1_src", model, n_seeds=150,
                   union_frac=0.6, hit_rows=rows(6))
    _write_m1_cell(root, "cot_strict", "v1_src", model, edges=1500)
    _write_m2_cell(root, "self_critique", "v1_src", model, n_seeds=150,
                   union_frac=0.5, hit_rows=rows(5))
    _write_m1_cell(root, "self_critique", "v1_src", model, edges=1300)
    _write_m2_cell(root, "few_shot", "v1_src", model, n_seeds=90,
                   union_frac=0.9, hit_rows=rows(9))  # CENSORED

    result = rank("re2", model, variants, strategies, root, out_dir)

    # Status classification is explicit, no crash on MISSING/CENSORED.
    assert result["cells"]["v0_none"]["few_shot"]["status"] == STATUS_MISSING
    assert result["cells"]["v1_src"]["few_shot"]["status"] == STATUS_CENSORED
    assert result["cells"]["v0_none"]["default"]["status"] == STATUS_OK

    # Documented metric keys are recorded in the JSON.
    assert result["metric_keys"]["m2"] == 'summary["slices"]["all"]["union_frac_targets_hit"]'
    assert result["metric_keys"]["m1"] == 'summary["edges_covered"]'

    # Ranking is stable + ordered by M2 desc; censored/missing excluded.
    ranking = result["ranking"]
    m2_seq = [r["m2"] for r in ranking]
    assert m2_seq == sorted(m2_seq, reverse=True)
    top = ranking[0]
    assert (top["variant"], top["strategy"], top["m2"]) == ("v1_src", "cot_strict", 0.6)
    ranked_keys = {(r["variant"], r["strategy"]) for r in ranking}
    assert ("v0_none", "few_shot") not in ranked_keys      # MISSING excluded
    assert ("v1_src", "few_shot") not in ranked_keys        # CENSORED excluded
    # 4 OK cells: default@v0, default@v1, cot@v1, self_critique@v1.
    assert len(ranking) == 4

    # Friedman needs >=3 OK strategy cells; v0_none has only 1 -> not ok.
    fr_v0 = result["friedman_by_variant"]["v0_none"]
    assert fr_v0["ok"] is False
    fr_v1 = result["friedman_by_variant"]["v1_src"]
    assert fr_v1["ok"] is True
    assert fr_v1["block_unit"] == "frozen_hard_branch_target"
    assert 0.0 <= fr_v1["p_value"] <= 1.0
    assert fr_v1["critical_difference"] > 0.0
    assert set(fr_v1["mean_ranks"]) == {DEFAULT_STRATEGY, "cot_strict", "self_critique"}

    # CD-diagram-ready data is emitted for OK Friedman variants only.
    assert "v1_src" in result["cd_diagram"]
    assert "v0_none" not in result["cd_diagram"]
    assert result["cd_diagram"]["v1_src"]["critical_difference"] > 0.0

    # Both output artefacts written.
    assert (out_dir / "experiment5_re2_rank.json").is_file()
    md = (out_dir / "experiment5_re2_rank.md").read_text()
    assert "experiment5 — re2" in md
    assert "MISSING" in md and "CENSORED" in md
    assert "Critical" in md or "CD=" in md  # CD summary present

    # Deterministic re-run -> identical JSON.
    result2 = rank("re2", model, variants, strategies, root, out_dir)
    assert json.dumps(result2, sort_keys=True) == json.dumps(result, sort_keys=True)
