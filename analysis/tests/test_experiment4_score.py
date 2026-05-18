"""Unit tests for analysis/scripts/experiment4_score.py.

These tests exercise ONLY the pure bootstrap + matrix logic on SYNTHETIC
`gap_hits.jsonl` content. They never invoke the real M2 metric, the
harfbuzz seed_replay binary, or LLVM — so the suite stays green without
the coverage toolchain. The one place that touches the real metric
(`score_dir_with_existing_metric`) is smoke-tested behind a skipif on the
binary's existence.

Coverage:
  * load_hit_matrix     — file-order seed/target ordering + hit bools.
  * union_m2            — hand-checked tiny matrix.
  * consistency_check   — passes when subsample rows ⊆ pool rows;
                          fails on a deliberately corrupted mismatch.
  * bootstrap_diffs     — determinism, CI brackets the point estimate,
                          strict-domination yields positive Δ_hr / CI
                          excluding 0, null case yields point ≈ 0 / CI
                          containing 0.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis.scripts.experiment4_score import (
    bootstrap_diffs,
    consistency_check,
    load_hit_matrix,
    score_dir_with_existing_metric,
    union_m2,
)
from core.targets import TARGETS


# ---------------------------------------------------------------------------
# Synthetic gap_hits.jsonl helpers.
# ---------------------------------------------------------------------------
def _write_gap_hits(path: Path, rows: list[tuple[str, int, bool]]) -> None:
    """Write a synthetic gap_hits.jsonl with the same line schema the
    unmodified measure_gap_coverage.py emits (extra fields included so the
    parser is exercised the way it would be in production)."""
    with open(path, "w") as fh:
        for seed_id, target_idx, hit in rows:
            fh.write(json.dumps({
                "seed_id": seed_id,
                "target_idx": target_idx,
                "target_file": "synthetic.cc",
                "target_line": 100 + target_idx,
                "uncovered_side": "true",
                "slice": "all",
                "hit": hit,
            }) + "\n")


def _matrix_from_grid(
    seed_ids: list[str],
    target_idxs: list[int],
    grid: dict[str, set[int]],
) -> tuple[list[str], list[int], dict[tuple[str, int], bool]]:
    """Build a (seed_ids, target_idxs, hit) triple directly (no metric
    call). `grid[seed_id]` = set of target indices that seed hits."""
    hit: dict[tuple[str, int], bool] = {}
    for sid in seed_ids:
        hits = grid.get(sid, set())
        for ti in target_idxs:
            hit[(sid, ti)] = ti in hits
    return list(seed_ids), list(target_idxs), hit


# ---------------------------------------------------------------------------
# load_hit_matrix
# ---------------------------------------------------------------------------
def test_load_hit_matrix_orders_and_bools(tmp_path: Path) -> None:
    gh = tmp_path / "gap_hits.jsonl"
    # Deliberately interleaved + non-sorted to pin first-seen file order.
    rows = [
        ("seed_b", 5, True),
        ("seed_b", 2, False),
        ("seed_a", 5, False),
        ("seed_a", 2, True),
        ("seed_c", 5, True),
        ("seed_c", 2, True),
    ]
    _write_gap_hits(gh, rows)

    seed_ids, target_idxs, hit = load_hit_matrix(gh)

    # First-seen file order, NOT sorted.
    assert seed_ids == ["seed_b", "seed_a", "seed_c"]
    assert target_idxs == [5, 2]

    assert hit[("seed_b", 5)] is True
    assert hit[("seed_b", 2)] is False
    assert hit[("seed_a", 5)] is False
    assert hit[("seed_a", 2)] is True
    assert hit[("seed_c", 5)] is True
    assert hit[("seed_c", 2)] is True


def test_load_hit_matrix_skips_blank_lines(tmp_path: Path) -> None:
    gh = tmp_path / "gap_hits.jsonl"
    gh.write_text(
        json.dumps({"seed_id": "s0", "target_idx": 0, "hit": True}) + "\n"
        "\n"
        + json.dumps({"seed_id": "s1", "target_idx": 0, "hit": False}) + "\n"
    )
    seed_ids, target_idxs, hit = load_hit_matrix(gh)
    assert seed_ids == ["s0", "s1"]
    assert target_idxs == [0]
    assert hit == {("s0", 0): True, ("s1", 0): False}


# ---------------------------------------------------------------------------
# union_m2 — hand-checked tiny matrix.
# ---------------------------------------------------------------------------
def test_union_m2_hand_checked() -> None:
    # 3 seeds × 4 branches (idx 0..3).
    #   s0 hits {0}
    #   s1 hits {1, 2}
    #   s2 hits {}
    # Union over {s0,s1,s2} hits {0,1,2} -> 3/4 = 0.75.
    seed_ids = ["s0", "s1", "s2"]
    targets = [0, 1, 2, 3]
    _, _, hit = _matrix_from_grid(seed_ids, targets, {"s0": {0}, "s1": {1, 2}})

    assert union_m2(["s0", "s1", "s2"], targets, hit) == 0.75
    # Subset {s0} alone -> {0} -> 1/4.
    assert union_m2(["s0"], targets, hit) == 0.25
    # Subset {s2} alone -> {} -> 0.
    assert union_m2(["s2"], targets, hit) == 0.0
    # Empty target space -> 0.0 (no division by zero).
    assert union_m2(seed_ids, [], hit) == 0.0
    # Missing pairs treated as not-hit.
    assert union_m2(["ghost"], targets, hit) == 0.0


# ---------------------------------------------------------------------------
# consistency_check
# ---------------------------------------------------------------------------
def test_consistency_check_passes_when_subsample_subset_of_pool(
    tmp_path: Path,
) -> None:
    pool = tmp_path / "pool_gap_hits.jsonl"
    sub = tmp_path / "sub_gap_hits.jsonl"

    # Pool: 4 seeds × 3 branches.
    pool_rows = [
        ("p0", 0, True), ("p0", 1, False), ("p0", 2, False),
        ("p1", 0, False), ("p1", 1, True), ("p1", 2, False),
        ("p2", 0, False), ("p2", 1, False), ("p2", 2, False),
        ("p3", 0, False), ("p3", 1, False), ("p3", 2, True),
    ]
    _write_gap_hits(pool, pool_rows)

    # Subsample = a LITERAL subset of pool rows (same seed ids + same hits).
    sub_ids = ["p0", "p3"]
    sub_rows = [r for r in pool_rows if r[0] in sub_ids]
    _write_gap_hits(sub, sub_rows)

    assert consistency_check(pool, sub, sub_ids) is True


def test_consistency_check_fails_on_corrupted_subsample(
    tmp_path: Path,
) -> None:
    pool = tmp_path / "pool_gap_hits.jsonl"
    sub = tmp_path / "sub_gap_hits.jsonl"

    pool_rows = [
        ("p0", 0, True), ("p0", 1, False),
        ("p1", 0, False), ("p1", 1, True),
        ("p2", 0, False), ("p2", 1, False),
    ]
    _write_gap_hits(pool, pool_rows)

    # Same seed ids, but the subsample's OWN hits disagree with the pool
    # (p0 no longer hits branch 0) — a seed-identity bug. Standalone union
    # = {1}, but pool-rows union for {p0,p1} = {0,1} -> mismatch.
    sub_ids = ["p0", "p1"]
    sub_rows = [
        ("p0", 0, False), ("p0", 1, False),
        ("p1", 0, False), ("p1", 1, True),
    ]
    _write_gap_hits(sub, sub_rows)

    assert consistency_check(pool, sub, sub_ids) is False


def test_consistency_check_fails_when_seed_absent_from_pool(
    tmp_path: Path,
) -> None:
    pool = tmp_path / "pool_gap_hits.jsonl"
    sub = tmp_path / "sub_gap_hits.jsonl"
    _write_gap_hits(pool, [("p0", 0, True), ("p1", 0, False)])
    _write_gap_hits(sub, [("ghost", 0, True)])
    assert consistency_check(pool, sub, ["ghost"]) is False


# ---------------------------------------------------------------------------
# bootstrap_diffs
# ---------------------------------------------------------------------------
def _disjoint_strict_domination() -> tuple:
    """Three disjoint 6-seed subsamples over 5 branches.

    S_high: every seed hits ALL 5 branches -> union always 5/5 = 1.0.
    S_random: each seed hits exactly branch 0 -> union always 1/5 = 0.2.
    S_low: no seed hits anything -> union always 0/5 = 0.0.

    S_high strictly dominates S_random on every branch, so EVERY bootstrap
    replicate has M2*_high - M2*_random = 1.0 - 0.2 = 0.8 > 0.
    """
    targets = [0, 1, 2, 3, 4]
    high = _matrix_from_grid(
        [f"h{i}" for i in range(6)], targets,
        {f"h{i}": {0, 1, 2, 3, 4} for i in range(6)},
    )
    rnd = _matrix_from_grid(
        [f"r{i}" for i in range(6)], targets,
        {f"r{i}": {0} for i in range(6)},
    )
    low = _matrix_from_grid(
        [f"l{i}" for i in range(6)], targets, {},
    )
    return high, rnd, low


def test_bootstrap_is_deterministic() -> None:
    high, rnd, low = _disjoint_strict_domination()
    a = bootstrap_diffs(high, rnd, low, n_resamples=2000, rng_seed=42)
    b = bootstrap_diffs(high, rnd, low, n_resamples=2000, rng_seed=42)
    assert a["hr"]["point"] == b["hr"]["point"]
    assert a["hl"]["point"] == b["hl"]["point"]
    assert a["hr"]["ci95"] == b["hr"]["ci95"]
    assert a["hl"]["ci95"] == b["hl"]["ci95"]
    assert a["m2_observed"] == b["m2_observed"]


def test_bootstrap_strict_domination_positive_and_ci_excludes_zero() -> None:
    high, rnd, low = _disjoint_strict_domination()
    out = bootstrap_diffs(high, rnd, low, n_resamples=3000, rng_seed=42)

    # Observed point estimates from the data itself.
    assert out["hr"]["point"] == pytest.approx(0.8)
    assert out["hl"]["point"] == pytest.approx(1.0)

    lo_hr, hi_hr = out["hr"]["ci95"]
    lo_hl, hi_hl = out["hl"]["ci95"]
    # Strict domination -> every replicate is exactly 0.8 / 1.0.
    assert lo_hr > 0.0 and hi_hr > 0.0
    assert lo_hl > 0.0 and hi_hl > 0.0
    # CI brackets the point estimate.
    assert lo_hr <= out["hr"]["point"] <= hi_hr
    assert lo_hl <= out["hl"]["point"] <= hi_hl


def test_bootstrap_null_case_point_zero_and_ci_contains_zero() -> None:
    """Identical hit structure across all three subsamples (disjoint seed
    ids, same per-seed hit pattern with internal variation so the
    resampled M2 actually varies). Δ point ≈ 0 and the CI contains 0."""
    targets = [0, 1, 2, 3, 4]

    def _build(prefix: str) -> tuple:
        # Seed k hits branch k (k=0..4); seed 5 hits nothing. Union over
        # all 6 = {0,1,2,3,4} = 1.0, but resamples vary (some draws miss
        # branches), so the bootstrap distribution is non-degenerate.
        ids = [f"{prefix}{i}" for i in range(6)]
        grid = {f"{prefix}{k}": {k} for k in range(5)}
        return _matrix_from_grid(ids, targets, grid)

    high = _build("h")
    rnd = _build("r")
    low = _build("l")

    out = bootstrap_diffs(high, rnd, low, n_resamples=4000, rng_seed=42)

    # Identical observed union -> point estimates exactly 0.
    assert out["hr"]["point"] == pytest.approx(0.0)
    assert out["hl"]["point"] == pytest.approx(0.0)

    lo_hr, hi_hr = out["hr"]["ci95"]
    lo_hl, hi_hl = out["hl"]["ci95"]
    # CI must contain 0 and be non-degenerate (the matrices are identical
    # in structure, independent draws -> symmetric spread around 0).
    assert lo_hr <= 0.0 <= hi_hr
    assert lo_hl <= 0.0 <= hi_hl
    assert lo_hr < hi_hr
    assert lo_hl < hi_hl


def test_bootstrap_ci_brackets_point_general_case() -> None:
    """A non-degenerate, non-null construction: S_high mostly dominates
    but not on every branch. CI should still bracket the observed point."""
    targets = list(range(5))
    high = _matrix_from_grid(
        [f"h{i}" for i in range(8)], targets,
        {f"h{i}": {0, 1, 2, 3} for i in range(8)},          # union {0,1,2,3} = 0.8
    )
    rnd = _matrix_from_grid(
        [f"r{i}" for i in range(8)], targets,
        {f"r{i}": {0, 1} for i in range(8)},                # union {0,1} = 0.4
    )
    low = _matrix_from_grid(
        [f"l{i}" for i in range(8)], targets,
        {f"l{i}": {0} for i in range(8)},                   # union {0} = 0.2
    )
    out = bootstrap_diffs(high, rnd, low, n_resamples=3000, rng_seed=42)
    assert out["hr"]["point"] == pytest.approx(0.4)
    assert out["hl"]["point"] == pytest.approx(0.6)
    lo_hr, hi_hr = out["hr"]["ci95"]
    lo_hl, hi_hl = out["hl"]["ci95"]
    assert lo_hr <= out["hr"]["point"] <= hi_hr
    assert lo_hl <= out["hl"]["point"] <= hi_hl


# ---------------------------------------------------------------------------
# Optional smoke test of the real-metric wrapper — skipped without binary.
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    not TARGETS["harfbuzz"].coverage_binary.exists(),
    reason="harfbuzz seed_replay coverage binary absent (no LLVM env)",
)
def test_score_dir_with_existing_metric_smoke(tmp_path: Path) -> None:
    """If (and only if) the real harfbuzz coverage binary exists, confirm
    the wrapper reaches the unmodified metric and that its summary carries
    the pre-registered slices.all.union_frac_targets_hit key. We do NOT
    assert a numeric value (depends on real seeds); we only confirm the
    plumbing + key contract M2(S) reads from."""
    seeds_dir = tmp_path / "seeds"
    seeds_dir.mkdir()
    # A trivial 1-byte seed; the metric tolerates replay failure (zero row)
    # and still writes summary.json with the slice keys.
    (seeds_dir / "seed_0.bin").write_bytes(b"\x00")
    out_dir = tmp_path / "out"

    summary = score_dir_with_existing_metric(seeds_dir, out_dir)

    assert "slices" in summary
    assert "all" in summary["slices"]
    assert "union_frac_targets_hit" in summary["slices"]["all"]
    assert (out_dir / "gap_hits.jsonl").exists()
