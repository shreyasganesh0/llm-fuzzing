"""experiment4 — M2 scoring + entropy-stratified subsample bootstrap.

This module implements ONLY the scoring / bootstrap plumbing for
experiment4 (METHODS.md §2, §2.1, §5). It does NOT reimplement M2, the
hard-branch filter (`struct_hits >= 1 AND rand_hits == 0`), or the frozen
50-branch set. M2 is obtained by *calling the unmodified*
`analysis.metrics.M2HardBranchMetric.compute_cell`, which shells out to the
unmodified `analysis/scripts/measure_gap_coverage.py`. We only ever read
its two outputs:

  * `summary.json`     — `slices.all.union_frac_targets_hit` IS M2(S).
  * `gap_hits.jsonl`   — one line per (seed_id, target_idx) with a boolean
                         `hit`. This is the per-seed × per-branch matrix the
                         bootstrap resamples over.

What lives here:

  1. `score_dir_with_existing_metric` — thin wrapper over the real metric
     (needs the harfbuzz seed_replay binary + LLVM at runtime; the unit
     tests do NOT exercise this path).
  2. `load_hit_matrix`        — parse the metric's `gap_hits.jsonl`.
  3. `union_m2`               — union (set) M2 over a subset of seed rows.
  4. `consistency_check`      — METHODS §2.1 anchor (subsample-in-pool
                                union must equal the subsample's standalone
                                union).
  5. `bootstrap_diffs`        — METHODS §5 EXACTLY (10000 independent
                                row-resamples per subsample; Δ_hr / Δ_hl
                                point estimates + percentile 95% CIs).
  6. `run`                    — orchestrate score + check + bootstrap for
                                one variant and emit the result JSON.
  7. `main`                   — argparse CLI.

The pure functions (2–5) are deterministic and binary-free, which is what
the unit tests cover; they build synthetic matrices directly.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

# Bootstrap REPO_ROOT onto sys.path so this works as
# `python -m analysis.scripts.experiment4_score` AND as a plain script,
# mirroring the idiom in analysis/scripts/measure_gap_coverage.py (~26-29).
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Imports after the sys.path bootstrap above (E402/I001 intentional —
# same idiom as analysis/scripts/measure_gap_coverage.py).
from analysis.metrics import M2HardBranchMetric  # noqa: E402, I001
from core.targets import TARGETS  # noqa: E402

# Pre-registered bootstrap constants (METHODS §5; MANIFEST.bootstrap).
BOOTSTRAP_N_RESAMPLES = 10000
BOOTSTRAP_RNG_SEED = 42

# The pre-registered headline statistic (METHODS §2; MANIFEST.m2_definition).
M2_SLICE = "all"
M2_KEY = "union_frac_targets_hit"


# ---------------------------------------------------------------------------
# 1. Thin wrapper over the UNMODIFIED existing metric.
# ---------------------------------------------------------------------------
def score_dir_with_existing_metric(seeds_dir: Path, out_dir: Path) -> dict:
    """Score one materialised seed directory with the *unmodified* M2 metric.

    Instantiates `analysis.metrics.M2HardBranchMetric` and calls
    `.compute_cell(seeds_dir, target, out_dir)` with the REAL harfbuzz
    `TargetSpec` from `core.targets.TARGETS["harfbuzz"]`. That carries the
    real frozen 50-branch set (`m2_target_branches.json`), the real
    coverage binary, and the real upstream baseline profile — so M2 here is
    byte-for-byte the same definition as `experiment2_1` (METHODS §2).

    Returns the parsed `out_dir/summary.json`. M2(S) is then
    `summary["slices"]["all"]["union_frac_targets_hit"]`.

    NOTE: this requires the harfbuzz seed_replay binary + LLVM toolchain at
    runtime. It is deliberately a thin pass-through so the pure
    bootstrap/matrix logic below stays testable without either.
    """
    target = TARGETS["harfbuzz"]
    metric = M2HardBranchMetric()
    summary = metric.compute_cell(Path(seeds_dir), target, Path(out_dir))
    return summary


def m2_from_summary(summary: dict) -> float:
    """Read M2(S) = slices.all.union_frac_targets_hit from a summary dict."""
    return float(summary["slices"][M2_SLICE][M2_KEY])


# ---------------------------------------------------------------------------
# 2. Parse the metric's gap_hits.jsonl into a hit matrix.
# ---------------------------------------------------------------------------
def load_hit_matrix(
    gap_hits_jsonl: Path,
) -> tuple[list[str], list[int], dict[tuple[str, int], bool]]:
    """Parse `gap_hits.jsonl` (written by the unmodified metric) into the
    per-seed × per-branch boolean matrix METHODS §2.1/§5 describe.

    Each line is a JSON object with at least `seed_id`, `target_idx`,
    `hit`. We return:

      * `seed_ids`   — unique seed ids in *first-seen* file order.
      * `target_idxs`— unique target indices in *first-seen* file order.
      * `hit`        — {(seed_id, target_idx) -> bool}.

    File order is preserved (not sorted) so the matrix row/column order is
    exactly what the metric emitted; callers that need a canonical order
    sort explicitly.
    """
    seed_ids: list[str] = []
    seen_seeds: set[str] = set()
    target_idxs: list[int] = []
    seen_targets: set[int] = set()
    hit: dict[tuple[str, int], bool] = {}

    with open(gap_hits_jsonl) as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            rec = json.loads(line)
            sid = str(rec["seed_id"])
            tidx = int(rec["target_idx"])
            is_hit = bool(rec["hit"])

            if sid not in seen_seeds:
                seen_seeds.add(sid)
                seed_ids.append(sid)
            if tidx not in seen_targets:
                seen_targets.add(tidx)
                target_idxs.append(tidx)
            hit[(sid, tidx)] = is_hit

    return seed_ids, target_idxs, hit


# ---------------------------------------------------------------------------
# 3. Union (set) M2 over a subset of seed rows.
# ---------------------------------------------------------------------------
def union_m2(
    seed_ids_subset: list[str],
    target_idxs: list[int],
    hit: dict[tuple[str, int], bool],
) -> float:
    """Fraction of `target_idxs` for which ANY seed in `seed_ids_subset`
    has `hit == True`.

    When `target_idxs` is all 50 distinct target indices present, this
    equals the metric's `slices.all.union_frac_targets_hit` for the same
    seed set (METHODS §2.1: M2 is a union/set function of the corpus).
    Missing (seed, target) pairs are treated as not-hit, matching the
    metric's zero-row behaviour for replay failures.
    """
    if not target_idxs:
        return 0.0
    union_count = 0
    for ti in target_idxs:
        if any(hit.get((sid, ti), False) for sid in seed_ids_subset):
            union_count += 1
    return union_count / len(target_idxs)


# ---------------------------------------------------------------------------
# 4. Consistency anchor (METHODS §2.1).
# ---------------------------------------------------------------------------
def consistency_check(
    pool_gap_hits: Path,
    subsample_gap_hits: Path,
    subsample_seed_ids: list[str],
) -> bool:
    """METHODS §2.1 anchor.

    The union over a subsample's rows *as taken from the POOL matrix* must
    equal the subsample's *own standalone* `union_frac_targets_hit`. A
    mismatch indicates a seed-identity bug (a seed id resolving to
    different bytes / different replay between pool and subsample scoring),
    which invalidates the experiment — the caller aborts on a False.

    Both matrices are scored by the same unmodified metric against the same
    frozen 50-branch set, so the target index space is identical; we union
    over the intersection of target indices present in both files (in
    practice all 50 in both).
    """
    pool_seeds, pool_targets, pool_hit = load_hit_matrix(pool_gap_hits)
    sub_seeds, sub_targets, sub_hit = load_hit_matrix(subsample_gap_hits)

    # Every subsample seed must exist in the pool matrix; absence is itself
    # a seed-identity failure.
    pool_seed_set = set(pool_seeds)
    if any(sid not in pool_seed_set for sid in subsample_seed_ids):
        return False

    target_space = sorted(set(pool_targets) & set(sub_targets))
    if not target_space:
        return False

    m2_from_pool_rows = union_m2(subsample_seed_ids, target_space, pool_hit)
    m2_standalone = union_m2(sub_seeds, target_space, sub_hit)
    return m2_from_pool_rows == m2_standalone


# ---------------------------------------------------------------------------
# 5. Bootstrap (METHODS §5 — EXACT).
# ---------------------------------------------------------------------------
Matrix = tuple[list[str], list[int], dict[tuple[str, int], bool]]


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Percentile by the same nearest-rank convention measure_gap_coverage
    uses (`means[int(0.025*iters)]` / `means[int(0.975*iters)]`): index
    into the sorted resample array at `int(pct * n)`."""
    n = len(sorted_vals)
    idx = int(pct * n)
    if idx >= n:
        idx = n - 1
    return sorted_vals[idx]


def _resample_union_m2(
    matrix: Matrix,
    target_space: list[int],
    rng: random.Random,
) -> float:
    """One bootstrap replicate of M2 for a single subsample (METHODS §5
    steps 1–2): draw `len(seed_ids)` row indices uniformly WITH replacement
    from `0..len(seed_ids)-1`, then union over the drawn rows."""
    seed_ids, _all_targets, hit = matrix
    n = len(seed_ids)
    drawn = [seed_ids[rng.randrange(n)] for _ in range(n)]
    return union_m2(drawn, target_space, hit)


def bootstrap_diffs(
    matrix_high: Matrix,
    matrix_random: Matrix,
    matrix_low: Matrix,
    n_resamples: int = BOOTSTRAP_N_RESAMPLES,
    rng_seed: int = BOOTSTRAP_RNG_SEED,
) -> dict:
    """METHODS §5, implemented exactly.

    Each `matrix_*` is the `(seed_ids, target_idxs, hit)` triple for a
    150-seed subsample (S_high / S_random / S_low). The three subsamples
    are DISJOINT, so their bootstrap replicates are drawn INDEPENDENTLY
    (no pairing across subsamples).

    For b in 1..n_resamples:
      * independently draw len(seed_ids) rows with replacement from EACH
        subsample,
      * compute M2*_high(b), M2*_random(b), M2*_low(b) via union over the
        drawn rows,
      * Δ_hr*(b) = M2*_high(b) − M2*_random(b),
        Δ_hl*(b) = M2*_high(b) − M2*_low(b).

    Returns, for each of Δ_hr / Δ_hl:
      * `point`  — the OBSERVED (non-resampled) difference of the two
        subsamples' own standalone union_m2 (point estimate is on the data,
        not the resamples).
      * `ci95`   — [2.5, 97.5] percentile of {Δ*(b)}.

    RNG: a single `random.Random(rng_seed)` instantiated ONCE here, at the
    bootstrap call site. This is an INDEPENDENT stream from the
    subsample-selection RNG (`scripts/_ablation_base.py::_subsample_seeds`
    also uses `random.Random(42)`): same seed value, distinct call site,
    distinct stream — fixed for reproducibility per METHODS §5 /
    research_document_v3.md §6.2.
    """
    rng = random.Random(rng_seed)

    # Target index space: the union of indices present across the three
    # subsamples (all 50 frozen branches in practice — the metric always
    # writes a row per frozen target even for replay failures).
    target_space = sorted(
        set(matrix_high[1]) | set(matrix_random[1]) | set(matrix_low[1])
    )

    # Observed (non-resampled) point estimates: each subsample's own
    # standalone union M2 over its full row set.
    m2_high_obs = union_m2(matrix_high[0], target_space, matrix_high[2])
    m2_random_obs = union_m2(matrix_random[0], target_space, matrix_random[2])
    m2_low_obs = union_m2(matrix_low[0], target_space, matrix_low[2])
    point_hr = m2_high_obs - m2_random_obs
    point_hl = m2_high_obs - m2_low_obs

    diffs_hr: list[float] = []
    diffs_hl: list[float] = []
    for _ in range(n_resamples):
        m2_high_b = _resample_union_m2(matrix_high, target_space, rng)
        m2_random_b = _resample_union_m2(matrix_random, target_space, rng)
        m2_low_b = _resample_union_m2(matrix_low, target_space, rng)
        diffs_hr.append(m2_high_b - m2_random_b)
        diffs_hl.append(m2_high_b - m2_low_b)

    diffs_hr.sort()
    diffs_hl.sort()
    ci_hr = [_percentile(diffs_hr, 0.025), _percentile(diffs_hr, 0.975)]
    ci_hl = [_percentile(diffs_hl, 0.025), _percentile(diffs_hl, 0.975)]

    return {
        "n_resamples": n_resamples,
        "rng_seed": rng_seed,
        "m2_observed": {
            "S_high": m2_high_obs,
            "S_random": m2_random_obs,
            "S_low": m2_low_obs,
        },
        "hr": {"point": point_hr, "ci95": ci_hr},
        "hl": {"point": point_hl, "ci95": ci_hl},
    }


# ---------------------------------------------------------------------------
# 6. Orchestration for one variant.
# ---------------------------------------------------------------------------
def _read_seed_ids_for_dir(seeds_dir: Path) -> list[str]:
    """The seed-id list of a materialised subsample dir = the `.bin` stems,
    in the same sorted order the unmodified metric uses
    (`sorted(... suffix == '.bin')` in measure_gap_coverage.py)."""
    return [
        p.stem
        for p in sorted(seeds_dir.iterdir())
        if p.is_file() and p.suffix == ".bin"
    ]


def run(
    variant: str,
    subsample_dirs: dict[str, Path],
    pool_dir: Path,
    out_root: Path,
) -> dict:
    """Score + check + bootstrap for one variant.

    `subsample_dirs` maps "S_random"/"S_high"/"S_low" -> materialised seed
    directory (each exactly 150 seeds, invariant 4). `pool_dir` is the full
    over-generation pool directory (consistency anchor, METHODS §2.1).

    Steps:
      1. Score the pool and each subsample with the UNMODIFIED metric
         (`score_dir_with_existing_metric`), capturing each `summary.json`
         and `gap_hits.jsonl` path.
      2. `consistency_check` each subsample against the pool; abort on any
         mismatch (returns `consistency_ok=False` and does not bootstrap).
      3. `bootstrap_diffs` over the three subsample matrices.

    Emits the result dict and writes it to
    `out_root/<variant>_score.json`.
    """
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    # --- Step 1: score the pool. -----------------------------------------
    pool_out = out_root / variant / "pool"
    pool_summary = score_dir_with_existing_metric(Path(pool_dir), pool_out)
    pool_gap_hits = pool_out / "gap_hits.jsonl"
    n_pool = int(pool_summary.get("n_seeds", 0))

    # --- Step 1: score each subsample. -----------------------------------
    sub_summaries: dict[str, dict] = {}
    sub_gap_hits: dict[str, Path] = {}
    sub_matrices: dict[str, Matrix] = {}
    sub_seed_ids: dict[str, list[str]] = {}
    for name in ("S_random", "S_high", "S_low"):
        sdir = Path(subsample_dirs[name])
        sout = out_root / variant / name
        sub_summaries[name] = score_dir_with_existing_metric(sdir, sout)
        gh = sout / "gap_hits.jsonl"
        sub_gap_hits[name] = gh
        sub_matrices[name] = load_hit_matrix(gh)
        sub_seed_ids[name] = _read_seed_ids_for_dir(sdir)

    # --- Step 2: consistency anchor (METHODS §2.1). ----------------------
    consistency_ok = True
    consistency_detail: dict[str, bool] = {}
    for name in ("S_random", "S_high", "S_low"):
        ok = consistency_check(
            pool_gap_hits, sub_gap_hits[name], sub_seed_ids[name]
        )
        consistency_detail[name] = ok
        consistency_ok = consistency_ok and ok

    result: dict = {
        "variant": variant,
        "n_pool": n_pool,
        "consistency_ok": consistency_ok,
        "consistency_detail": consistency_detail,
        "m2": {
            name: m2_from_summary(sub_summaries[name])
            for name in ("S_random", "S_high", "S_low")
        },
    }

    # --- Step 3: bootstrap (only if the anchor held). --------------------
    if consistency_ok:
        boot = bootstrap_diffs(
            sub_matrices["S_high"],
            sub_matrices["S_random"],
            sub_matrices["S_low"],
        )
        result["deltas"] = {
            "hr": {"point": boot["hr"]["point"], "ci95": boot["hr"]["ci95"]},
            "hl": {"point": boot["hl"]["point"], "ci95": boot["hl"]["ci95"]},
        }
        result["bootstrap"] = {
            "n_resamples": boot["n_resamples"],
            "rng_seed": boot["rng_seed"],
            "m2_observed": boot["m2_observed"],
        }
    else:
        # Surface the failure rather than silently bootstrapping bad data.
        result["deltas"] = None
        result["bootstrap"] = None

    out_path = out_root / f"{variant}_score.json"
    out_path.write_text(json.dumps(result, indent=2))
    return result


# ---------------------------------------------------------------------------
# 7. CLI.
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", required=True,
                        help="e.g. v1_src or v3_all")
    parser.add_argument("--pool-dir", type=Path, required=True,
                        help="full over-generation pool seed dir (consistency anchor)")
    parser.add_argument("--s-random-dir", type=Path, required=True,
                        help="materialised S_random 150-seed dir")
    parser.add_argument("--s-high-dir", type=Path, required=True,
                        help="materialised S_high 150-seed dir")
    parser.add_argument("--s-low-dir", type=Path, required=True,
                        help="materialised S_low 150-seed dir")
    parser.add_argument("--out-root", type=Path, required=True,
                        help="results/experiment4 (or a test dir)")
    args = parser.parse_args()

    result = run(
        variant=args.variant,
        subsample_dirs={
            "S_random": args.s_random_dir,
            "S_high": args.s_high_dir,
            "S_low": args.s_low_dir,
        },
        pool_dir=args.pool_dir,
        out_root=args.out_root,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
