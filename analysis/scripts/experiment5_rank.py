"""experiment5 — Strategy-as-Variant ablation aggregator + ranking analysis.

This module is the *offline scoring/ranking* layer for experiment5
(`docs/experiment5/METHODS.md` §2, §4). It does NOT run synthesis, does NOT
re-score seeds, does NOT touch the M2 hard-branch filter or the frozen
15-branch RE2-v2 set. It ONLY reads the two per-cell artefacts that the
*unmodified* `analysis/scripts/measure_gap_coverage.py` already wrote:

  * ``summary.json``    — M2(cell) = ``slices.all.union_frac_targets_hit``
                          (METHODS §2; verified against a real RE2 m2
                          summary). For m1 cells the same file instead
                          carries the M1EdgesMetric payload and M1(cell) =
                          ``edges_covered`` (see ``M1_KEY`` below).
  * ``gap_hits.jsonl``  — one line per (seed_id, target_idx) with a boolean
                          ``hit``. This is the per-seed × per-branch matrix
                          the per-cell bootstrap and the Friedman/Nemenyi
                          profiles are computed from.

Documented metric keys (quote them — METHODS §2 mandates a blind reader can
reproduce every number):

  * M2 key:  ``summary["slices"]["all"]["union_frac_targets_hit"]``
             — identical to ``experiment4_score.M2_SLICE``/``M2_KEY`` and to
             the real ``results/ablation_re2_v2/m2/.../summary.json`` schema.
  * M1 key:  ``summary["edges_covered"]`` — "total union edges covered by
             all seeds in a cell", emitted by the unmodified
             ``M1EdgesMetric`` (``analysis/metrics/m1.py`` ->
             ``synthesis/scripts/measure_coverage.py`` line ~86: it is the
             sum over every branch of ``int(true_taken)+int(false_taken)``
             across the whole corpus, i.e. the corpus union edge count).
             Verified against a real
             ``results/ablation_re2_v2/m1/.../summary.json``
             (``{"edges_covered": 1382, "edges_total": 3380, ...}``).
             NOTE: the m1 summary has NO ``n_seeds`` field, so the
             CENSORED (n_seeds<150) check only applies to the m2 summary;
             an m1 cell's status is inherited from its sibling m2 cell.

Friedman/Nemenyi path (documented per the task brief): we IMPORT (do not
copy) the repo's ``analysis/scripts/friedman_nemenyi.py``. It already
exposes the three functions we need —

  * ``friedman(per_target_ranks)``   -> (statistic, p) via
    ``scipy.stats.friedmanchisquare`` over the columns;
  * ``nemenyi(per_target_ranks)``    -> k×k post-hoc p-matrix (uses
    ``scikit_posthocs`` if present; degrades to an all-ones matrix
    otherwise — we additionally compute the analytic Nemenyi
    critical-difference value here so the CD diagram is well-defined even
    when ``scikit_posthocs`` is absent);
  * ``mean_ranks(per_target_ranks)`` -> average rank per config (1 = best).

``per_target_ranks`` is the shape ``friedman_nemenyi`` expects: a list of
"blocks", each block a list of one metric value per treatment/config. For
experiment5 the **repeated measure (block)** is one of the (up to) 15
frozen hard-branch *targets*, and the **treatments** are the strategy cells
within a single variant. That is the unit ``friedman_nemenyi`` was written
for (per-(target,config) values feeding a CD diagram) and it is also the
statistically correct block here: the 15 frozen branches are the matched
units shared by every strategy at a fixed variant. (Documented choice — the
alternative, per-seed M2 indicators, is NOT used because seed identity is
not shared across strategy cells, so seeds are not a valid repeated
measure; targets are. Surfaced here rather than silently chosen.)

The pure functions (path resolution, key readers, union M2, the per-cell
seed bootstrap, the Holm contrasts) are deterministic and binary-free —
that is exactly what the unit tests exercise with synthetic
``summary.json`` + ``gap_hits.jsonl`` fixtures (no real binaries, no LLVM,
no network).
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

# Bootstrap REPO_ROOT onto sys.path so this works as
# `python -m analysis.scripts.experiment5_rank` AND as a plain script,
# mirroring the idiom in analysis/scripts/measure_gap_coverage.py (~26-29).
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Imports after the sys.path bootstrap above (E402/I001 intentional —
# same idiom as analysis/scripts/measure_gap_coverage.py).
from analysis.scripts import friedman_nemenyi as fn  # noqa: E402, I001
from analysis.scripts.experiment4_score import (  # noqa: E402
    load_hit_matrix as _exp6_load_hit_matrix,
)
from analysis.scripts.experiment4_score import (  # noqa: E402
    union_m2 as _exp6_union_m2,
)
from core.targets import TARGETS  # noqa: E402

# ---------------------------------------------------------------------------
# Pre-registered constants (METHODS §2, §4; mirrors experiment4_score).
# ---------------------------------------------------------------------------
M2_SLICE = "all"
M2_KEY = "union_frac_targets_hit"
# M1 = total union edges covered by all seeds in the cell. The unmodified
# M1EdgesMetric writes this as the top-level "edges_covered" key.
M1_KEY = "edges_covered"

DEFAULT_STRATEGY = "default"

# Per-cell seed bootstrap (METHODS §4 — reuse the experiment4 pattern:
# resample the cell's seeds WITH replacement, recompute union M2,
# n=10000, random.Random(42), percentile [2.5, 97.5]).
BOOTSTRAP_N_RESAMPLES = 10000
BOOTSTRAP_RNG_SEED = 42

# Cell status tags.
STATUS_OK = "OK"
STATUS_MISSING = "MISSING"      # the cell's summary.json does not exist
STATUS_CENSORED = "CENSORED"    # summary exists but n_seeds < 150
SEED_FLOOR = 150                # invariant 4: every scored cell == 150 seeds


# ---------------------------------------------------------------------------
# 1. Path resolution + cell loading.
# ---------------------------------------------------------------------------
def cell_summary_path(
    results_root: Path,
    strategy: str,
    variant: str,
    model: str,
    metric: str,
) -> Path:
    """Resolve the on-disk ``summary.json`` path for one cell.

    Default strategy => NO ``<strategy>`` path segment (the read-only
    experiment2_1 baseline column lives at
    ``<root>/m{1,2}/<variant>/<model>/summary.json``). Any non-default
    strategy inserts the ``<strategy>`` segment immediately under the
    results root, exactly as the unmodified AblationRunner writes it
    (METHODS §2 / MANIFEST ``orchestration.why_no_wrapper``):
    ``<root>/<strategy>/m{1,2}/<variant>/<model>/summary.json``.
    """
    root = Path(results_root)
    if strategy != DEFAULT_STRATEGY:
        root = root / strategy
    return root / metric / variant / model / "summary.json"


def cell_gap_hits_path(
    results_root: Path,
    strategy: str,
    variant: str,
    model: str,
) -> Path:
    """``gap_hits.jsonl`` sits next to the m2 ``summary.json`` for a cell."""
    return cell_summary_path(results_root, strategy, variant, model, "m2").parent / "gap_hits.jsonl"


def load_cell(
    results_root: Path,
    strategy: str,
    variant: str,
    model: str,
    metric: str,
) -> dict | None:
    """Return the parsed cell ``summary.json`` or ``None`` if absent.

    Absence (a not-yet-generated cell) is a first-class outcome here:
    callers map ``None`` to status ``MISSING`` and DO NOT crash
    (METHODS §4 parse-rate confound: a cell may simply not exist yet).
    """
    path = cell_summary_path(results_root, strategy, variant, model, metric)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        # A corrupt/half-written summary is treated as MISSING rather than
        # crashing the whole ranking run — surfaced via status, not silently
        # coerced to a number.
        return None


# ---------------------------------------------------------------------------
# 2. Metric key readers (tolerate missing slices / keys).
# ---------------------------------------------------------------------------
def m2_of(summary: dict | None) -> float | None:
    """M2(cell) = ``summary["slices"]["all"]["union_frac_targets_hit"]``.

    Returns ``None`` (not 0.0) when the summary is absent or the slice/key
    is missing — a missing measurement is NOT the same as a measured zero,
    and conflating them would bias the ranking.
    """
    if not summary:
        return None
    slices = summary.get("slices")
    if not isinstance(slices, dict):
        return None
    sl = slices.get(M2_SLICE)
    if not isinstance(sl, dict) or M2_KEY not in sl:
        return None
    return float(sl[M2_KEY])


def m1_of(summary: dict | None) -> int | None:
    """M1(cell) = ``summary["edges_covered"]`` (total union edges).

    Returns ``None`` when absent — same rationale as ``m2_of``.
    """
    if not summary or M1_KEY not in summary:
        return None
    return int(summary[M1_KEY])


def cell_status(m2_summary: dict | None) -> str:
    """Classify a cell from its *m2* summary (the one that carries
    ``n_seeds``): MISSING (no summary), CENSORED (n_seeds < 150,
    METHODS §4 parse-rate confound — reported, never padded), else OK."""
    if m2_summary is None:
        return STATUS_MISSING
    n_seeds = m2_summary.get("n_seeds")
    if n_seeds is None or int(n_seeds) < SEED_FLOOR:
        return STATUS_CENSORED
    return STATUS_OK


# ---------------------------------------------------------------------------
# 3. Hit matrix + union M2 (mirror experiment4_score; import the clean
#    pure functions rather than re-implementing — they are binary-free).
# ---------------------------------------------------------------------------
def hit_matrix(
    gap_hits_jsonl: Path,
) -> tuple[list[str], list[int], dict[tuple[str, int], bool]]:
    """Parse ``gap_hits.jsonl`` -> (seed_ids, target_idxs, {(sid,tidx):hit}).

    Delegates to the UNMODIFIED ``experiment4_score.load_hit_matrix``: same
    file format, same first-seen ordering, same (sid,tidx)->bool mapping.
    Re-using it (rather than copying) keeps this aggregator byte-consistent
    with the experiment4 bootstrap convention METHODS §4 points at.
    """
    return _exp6_load_hit_matrix(Path(gap_hits_jsonl))


def union_m2(
    seed_ids_subset: list[str],
    target_idxs: list[int],
    hit: dict[tuple[str, int], bool],
) -> float:
    """Fraction of ``target_idxs`` hit by ANY seed in the subset.

    Thin re-export of ``experiment4_score.union_m2`` (the union/set M2 of a
    corpus). Missing pairs count as not-hit, matching the metric's zero-row
    behaviour for replay failures.
    """
    return _exp6_union_m2(seed_ids_subset, target_idxs, hit)


# ---------------------------------------------------------------------------
# 4. Per-cell seed bootstrap CI (METHODS §4).
# ---------------------------------------------------------------------------
def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Same nearest-rank convention as measure_gap_coverage / experiment4:
    index into the sorted resample array at ``int(pct * n)``."""
    n = len(sorted_vals)
    idx = int(pct * n)
    if idx >= n:
        idx = n - 1
    return sorted_vals[idx]


def bootstrap_cell_ci(
    gap_hits_jsonl: Path,
    n: int = BOOTSTRAP_N_RESAMPLES,
    seed: int = BOOTSTRAP_RNG_SEED,
) -> tuple[float, float, float]:
    """(point, lo, hi) for one cell's union M2 via the seed bootstrap.

    METHODS §4: resample the cell's seeds WITH replacement (the SAME count
    as the cell — len(seed_ids) draws), recompute the union M2 each replicate
    (n=10000), take the [2.5, 97.5] percentiles. ``point`` is the OBSERVED
    (non-resampled) union M2 over the cell's full seed set.

    RNG: a SINGLE ``random.Random(seed)`` instantiated ONCE per call, here at
    the bootstrap call site. This is an INDEPENDENT stream from the RNG-42
    seed-subsampling done in AblationRunner (``random.Random(42)`` there):
    same seed value, distinct call site, distinct stream — fixed for
    reproducibility per METHODS §4/§6. Determinism: the same gap_hits file
    and seed yield byte-identical (point, lo, hi) on every call.
    """
    seed_ids, target_idxs, hit = hit_matrix(Path(gap_hits_jsonl))
    point = union_m2(seed_ids, target_idxs, hit)
    n_seeds = len(seed_ids)
    if n_seeds == 0:
        return (point, point, point)

    rng = random.Random(seed)
    reps: list[float] = []
    for _ in range(n):
        drawn = [seed_ids[rng.randrange(n_seeds)] for _ in range(n_seeds)]
        reps.append(union_m2(drawn, target_idxs, hit))
    reps.sort()
    lo = _percentile(reps, 0.025)
    hi = _percentile(reps, 0.975)
    return (point, lo, hi)


# ---------------------------------------------------------------------------
# 5. Friedman + Nemenyi over the strategy cells within a variant.
# ---------------------------------------------------------------------------
def _per_target_profile(
    gap_hits_jsonl: Path,
) -> dict[int, float]:
    """{target_idx -> 1.0 if ANY seed in the cell hit it else 0.0}.

    This is exactly the per-target component of the cell's union M2: the
    Friedman/Nemenyi "block" is a target, so each cell contributes one
    0/1 value per target (the union indicator the metric already uses to
    compute ``union_frac_targets_hit``).
    """
    seed_ids, target_idxs, hit = hit_matrix(Path(gap_hits_jsonl))
    out: dict[int, float] = {}
    for ti in target_idxs:
        out[ti] = 1.0 if any(hit.get((sid, ti), False) for sid in seed_ids) else 0.0
    return out


def friedman_over_cells(
    per_cell_seed_profiles: dict[str, dict[int, float]],
) -> dict:
    """Friedman over the strategy cells of ONE variant block.

    ``per_cell_seed_profiles`` maps strategy-name -> {target_idx -> 0/1}
    (from ``_per_target_profile``). We pivot to the
    ``friedman_nemenyi``-expected shape — a list of per-target blocks, each
    block a list of one value per strategy (in a stable strategy order) —
    and IMPORT the repo's ``friedman_nemenyi.friedman`` /
    ``.nemenyi`` / ``.mean_ranks`` (we do not re-implement: it already
    wraps ``scipy.stats.friedmanchisquare``).

    We additionally compute the analytic Nemenyi critical difference

        CD = q_alpha * sqrt(k*(k+1) / (6*N))

    (alpha=0.05, q from the studentized-range / Nemenyi table) so the
    CD-diagram data is well-defined even if ``scikit_posthocs`` is absent.
    Only targets present in EVERY strategy cell are used (a complete block
    design — Friedman requires balanced blocks).
    """
    strategies = sorted(per_cell_seed_profiles)
    k = len(strategies)
    # The Friedman test is only defined for >=3 treatments (k>=3) — this is
    # a hard constraint of scipy.stats.friedmanchisquare, which the imported
    # friedman_nemenyi.friedman wraps. METHODS §4 says "Friedman over the
    # per-seed M2 profiles of the cells" but does NOT state a minimum cell
    # count; we surface k<3 as a non-computable block rather than silently
    # substituting a different test (documented deviation-avoidance).
    if k < 3:
        return {
            "ok": False,
            "reason": f"Friedman needs >=3 strategy cells (scipy), got {k}",
            "strategies": strategies,
        }

    # Complete-block design: keep only targets present in every cell.
    common = None
    for prof in per_cell_seed_profiles.values():
        keys = set(prof)
        common = keys if common is None else (common & keys)
    common_targets = sorted(common or set())
    if len(common_targets) < 2:
        return {
            "ok": False,
            "reason": f"need >=2 common targets, got {len(common_targets)}",
            "strategies": strategies,
        }

    per_target_ranks = [
        [per_cell_seed_profiles[s][ti] for s in strategies]
        for ti in common_targets
    ]

    stat, p = fn.friedman(per_target_ranks)
    mean_ranks = fn.mean_ranks(per_target_ranks)
    nemenyi_matrix = fn.nemenyi(per_target_ranks)

    n_blocks = len(common_targets)
    cd = _nemenyi_cd(k, n_blocks)

    return {
        "ok": True,
        "strategies": strategies,
        "n_blocks": n_blocks,
        "block_unit": "frozen_hard_branch_target",
        "statistic": stat,
        "p_value": p,
        "mean_ranks": {strategies[i]: mean_ranks[i] for i in range(k)},
        "critical_difference": cd,
        "nemenyi_p_matrix": nemenyi_matrix,
    }


# Studentized-range upper-quantile q_alpha for the Nemenyi test at
# alpha=0.05, indexed by k = number of treatments (infinite df — the
# standard Nemenyi/Demsar 2006 table). Index 0/1 unused (k>=2).
_NEMENYI_Q05 = [
    0.0, 0.0,
    1.960, 2.343, 2.569, 2.728, 2.850, 2.949, 3.031, 3.102, 3.164,
    3.219, 3.268, 3.313, 3.354, 3.391, 3.426, 3.458, 3.489, 3.517, 3.544,
]


def _nemenyi_cd(k: int, n_blocks: int) -> float:
    """Analytic Nemenyi critical difference (Demsar 2006):

        CD = q_alpha * sqrt( k*(k+1) / (6 * N) )

    with N = number of blocks (here: common frozen targets). q_alpha is the
    alpha=0.05 studentized-range quantile from ``_NEMENYI_Q05``; for k beyond
    the table we conservatively reuse the last tabulated value.
    """
    if k < 2 or n_blocks < 1:
        return 0.0
    q = _NEMENYI_Q05[k] if k < len(_NEMENYI_Q05) else _NEMENYI_Q05[-1]
    return q * math.sqrt(k * (k + 1) / (6.0 * n_blocks))


def nemenyi_posthoc(
    per_cell_seed_profiles: dict[str, dict[int, float]],
) -> dict:
    """Convenience wrapper: just the Nemenyi p-matrix + CD + mean ranks
    portion of ``friedman_over_cells`` (kept as a named entry point per the
    task brief; shares the exact same balanced-block computation)."""
    res = friedman_over_cells(per_cell_seed_profiles)
    if not res.get("ok"):
        return res
    return {
        "ok": True,
        "strategies": res["strategies"],
        "mean_ranks": res["mean_ranks"],
        "critical_difference": res["critical_difference"],
        "nemenyi_p_matrix": res["nemenyi_p_matrix"],
    }


# ---------------------------------------------------------------------------
# 6. Holm-corrected strategy-vs-default contrasts.
# ---------------------------------------------------------------------------
def _wilcoxon_signed_rank_p(diffs: list[float]) -> float | None:
    """Two-sided Wilcoxon signed-rank p over paired per-target differences.

    Uses ``scipy.stats.wilcoxon`` when available. The matched pairs are
    (strategy_target_indicator, default_target_indicator) over the common
    frozen targets — the same balanced-block units the Friedman uses, which
    is the test METHODS §4 / analysis/CLAUDE.md mandate for paired
    strategy-vs-default contrasts. Returns ``None`` if the test cannot be
    formed (all-zero differences or scipy missing) so the caller can mark
    the contrast non-significant without fabricating a p-value.
    """
    if not diffs or all(d == 0.0 for d in diffs):
        return None
    try:
        from scipy.stats import wilcoxon
    except ImportError:
        return None
    try:
        res = wilcoxon(diffs, zero_method="wilcox", alternative="two-sided")
        return float(res.pvalue)
    except ValueError:
        return None


def _holm_adjust(pvals: list[float]) -> list[float]:
    """Holm step-down adjustment over a family of raw p-values.

    Sort ascending, multiply the i-th smallest by (m-i), enforce
    monotonicity, clip to 1.0. Mandated by analysis/CLAUDE.md /
    METHODS §4 for the strategy-vs-default family.
    """
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    running = 0.0
    for rank, idx in enumerate(order):
        val = (m - rank) * pvals[idx]
        running = max(running, val)
        adj[idx] = min(1.0, running)
    return adj


def holm_contrasts(
    results_root: Path,
    model: str,
    variants: list[str],
    strategies: list[str],
    alpha: float = 0.05,
) -> dict:
    """For each variant, each non-default strategy vs ``default``: a paired
    Wilcoxon signed-rank test over the common frozen-target indicators,
    Holm-adjusted across the WHOLE family of size
    ``(n_non_default_strategies) * n_variants`` (METHODS §4).

    A contrast is skipped (status != tested) when either the strategy cell
    or the default cell is MISSING/lacks gap_hits — surfaced, never imputed.
    """
    non_default = [s for s in strategies if s != DEFAULT_STRATEGY]
    contrasts: list[dict] = []

    for variant in variants:
        default_gh = cell_gap_hits_path(results_root, DEFAULT_STRATEGY, variant, model)
        default_prof = (
            _per_target_profile(default_gh) if default_gh.is_file() else None
        )
        for strat in non_default:
            strat_gh = cell_gap_hits_path(results_root, strat, variant, model)
            entry: dict = {
                "variant": variant,
                "strategy": strat,
                "vs": DEFAULT_STRATEGY,
            }
            if default_prof is None or not strat_gh.is_file():
                entry["status"] = "skipped_missing_cell"
                entry["p_raw"] = None
                contrasts.append(entry)
                continue

            strat_prof = _per_target_profile(strat_gh)
            common = sorted(set(strat_prof) & set(default_prof))
            if len(common) < 2:
                entry["status"] = "skipped_no_common_targets"
                entry["p_raw"] = None
                contrasts.append(entry)
                continue

            diffs = [strat_prof[t] - default_prof[t] for t in common]
            strat_union = sum(strat_prof[t] for t in common) / len(common)
            default_union = sum(default_prof[t] for t in common) / len(common)
            p_raw = _wilcoxon_signed_rank_p(diffs)
            entry.update({
                "status": "tested",
                "n_common_targets": len(common),
                "strategy_union_m2": strat_union,
                "default_union_m2": default_union,
                "delta_union_m2": strat_union - default_union,
                "p_raw": p_raw,
            })
            contrasts.append(entry)

    tested = [c for c in contrasts if c["status"] == "tested" and c["p_raw"] is not None]
    raw = [c["p_raw"] for c in tested]
    adj = _holm_adjust(raw) if raw else []
    for c, a in zip(tested, adj, strict=True):
        c["p_holm"] = a
        c["significant"] = bool(a < alpha and c["delta_union_m2"] > 0.0)
    for c in contrasts:
        if c["status"] != "tested" or c.get("p_raw") is None:
            c.setdefault("p_holm", None)
            c.setdefault("significant", False)

    return {
        "alpha": alpha,
        "family_size": len(tested),
        "test": "wilcoxon_signed_rank_two_sided_holm",
        "contrasts": contrasts,
    }


# ---------------------------------------------------------------------------
# 7. End-to-end ranking.
# ---------------------------------------------------------------------------
def _build_metric_table(
    results_root: Path,
    model: str,
    variants: list[str],
    strategies: list[str],
) -> dict:
    """variant×strategy M1/M2 tables with MISSING/CENSORED status + per-cell
    bootstrap CIs (CI only for OK m2 cells that have a gap_hits file)."""
    cells: dict[str, dict[str, dict]] = {}
    for variant in variants:
        cells[variant] = {}
        for strat in strategies:
            m2_sum = load_cell(results_root, strat, variant, model, "m2")
            m1_sum = load_cell(results_root, strat, variant, model, "m1")
            status = cell_status(m2_sum)
            entry: dict = {
                "status": status,
                "m2": m2_of(m2_sum),
                "m1": m1_of(m1_sum),
                "n_seeds": (m2_sum or {}).get("n_seeds"),
                "m2_ci95": None,
            }
            gh = cell_gap_hits_path(results_root, strat, variant, model)
            if status == STATUS_OK and gh.is_file():
                point, lo, hi = bootstrap_cell_ci(gh)
                entry["m2_ci95"] = [point, lo, hi]
            cells[variant][strat] = entry
    return cells


def rank(
    target: str,
    model: str,
    variants: list[str],
    strategies: list[str],
    results_root: Path,
    out_dir: Path,
) -> dict:
    """Full experiment5 ranking for one (target, model).

    Builds the variant×strategy M2/M1 tables (with MISSING/CENSORED), the
    per-cell bootstrap CIs, the per-variant Friedman+Nemenyi over the
    strategy cells, and the Holm strategy-vs-default contrasts. Writes
    ``out_dir/experiment5_<target>_rank.json`` and a human-readable
    ``out_dir/experiment5_<target>_rank.md`` (markdown tables + a text
    critical-difference summary). The JSON also carries CD-diagram-ready
    data (per-variant mean ranks + the CD value).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_root = Path(results_root)

    table = _build_metric_table(results_root, model, variants, strategies)

    # Flat ranking by M2 (primary), M1 (secondary); only OK cells rank.
    flat: list[dict] = []
    for variant in variants:
        for strat in strategies:
            e = table[variant][strat]
            if e["status"] == STATUS_OK and e["m2"] is not None:
                flat.append({
                    "variant": variant,
                    "strategy": strat,
                    "m2": e["m2"],
                    "m1": e["m1"] if e["m1"] is not None else -1,
                    "m2_ci95": e["m2_ci95"],
                })
    flat.sort(key=lambda r: (r["m2"], r["m1"]), reverse=True)
    for i, r in enumerate(flat):
        r["rank"] = i + 1

    # Per-variant Friedman/Nemenyi over the strategy cells.
    friedman_by_variant: dict[str, dict] = {}
    for variant in variants:
        profiles: dict[str, dict[int, float]] = {}
        for strat in strategies:
            gh = cell_gap_hits_path(results_root, strat, variant, model)
            if table[variant][strat]["status"] == STATUS_OK and gh.is_file():
                profiles[strat] = _per_target_profile(gh)
        if len(profiles) >= 2:
            friedman_by_variant[variant] = friedman_over_cells(profiles)
        else:
            friedman_by_variant[variant] = {
                "ok": False,
                "reason": f"only {len(profiles)} OK strategy cell(s) at {variant}",
                "strategies": sorted(profiles),
            }

    holm = holm_contrasts(results_root, model, variants, strategies)

    cd_diagram = {
        variant: {
            "mean_ranks": fr.get("mean_ranks", {}),
            "critical_difference": fr.get("critical_difference"),
            "n_blocks": fr.get("n_blocks"),
        }
        for variant, fr in friedman_by_variant.items()
        if fr.get("ok")
    }

    result = {
        "experiment": "experiment5",
        "target": target,
        "model": model,
        "variants": variants,
        "strategies": strategies,
        "results_root": str(results_root),
        "metric_keys": {
            "m2": f'summary["slices"]["{M2_SLICE}"]["{M2_KEY}"]',
            "m1": f'summary["{M1_KEY}"]',
        },
        "bootstrap": {
            "n_resamples": BOOTSTRAP_N_RESAMPLES,
            "rng_seed": BOOTSTRAP_RNG_SEED,
            "ci": [0.025, 0.975],
        },
        "cells": table,
        "ranking": flat,
        "friedman_by_variant": friedman_by_variant,
        "holm_contrasts": holm,
        "cd_diagram": cd_diagram,
    }

    json_path = out_dir / f"experiment5_{target}_rank.json"
    json_path.write_text(json.dumps(result, indent=2))
    md_path = out_dir / f"experiment5_{target}_rank.md"
    md_path.write_text(_render_markdown(result))
    return result


# ---------------------------------------------------------------------------
# Markdown rendering.
# ---------------------------------------------------------------------------
def _fmt_m2(entry: dict) -> str:
    if entry["status"] == STATUS_MISSING:
        return "MISSING"
    if entry["status"] == STATUS_CENSORED:
        n = entry.get("n_seeds")
        return f"CENSORED (n={n})"
    if entry["m2"] is None:
        return "—"
    ci = entry.get("m2_ci95")
    if ci:
        return f"{entry['m2']:.3f} [{ci[1]:.3f}, {ci[2]:.3f}]"
    return f"{entry['m2']:.3f}"


def _fmt_m1(entry: dict) -> str:
    if entry["status"] == STATUS_MISSING:
        return "MISSING"
    if entry["status"] == STATUS_CENSORED:
        return "CENSORED"
    return "—" if entry["m1"] is None else str(entry["m1"])


def _render_markdown(result: dict) -> str:
    variants = result["variants"]
    strategies = result["strategies"]
    cells = result["cells"]
    lines: list[str] = []
    lines.append(f"# experiment5 — {result['target']} / {result['model']} ranking")
    lines.append("")
    lines.append(
        "M2 = `" + result["metric_keys"]["m2"] + "` (primary); "
        "M1 = `" + result["metric_keys"]["m1"] + "` (secondary, total union edges). "
        "Random anchor M2 = 0.000 by construction (report absolute AND vs-random)."
    )
    lines.append("")

    lines.append("## M2 by variant × strategy (point [boot 95% CI])")
    lines.append("")
    lines.append("| variant | " + " | ".join(strategies) + " |")
    lines.append("|" + "---|" * (len(strategies) + 1))
    for v in variants:
        row = [v] + [_fmt_m2(cells[v][s]) for s in strategies]
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    lines.append("## M1 by variant × strategy (total union edges)")
    lines.append("")
    lines.append("| variant | " + " | ".join(strategies) + " |")
    lines.append("|" + "---|" * (len(strategies) + 1))
    for v in variants:
        row = [v] + [_fmt_m1(cells[v][s]) for s in strategies]
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    lines.append("## Ranking (OK cells only — M2 primary, M1 tiebreak)")
    lines.append("")
    if result["ranking"]:
        lines.append("| rank | variant | strategy | M2 | M1 |")
        lines.append("|---|---|---|---|---|")
        for r in result["ranking"]:
            m1 = "" if r["m1"] < 0 else r["m1"]
            lines.append(
                f"| {r['rank']} | {r['variant']} | {r['strategy']} "
                f"| {r['m2']:.3f} | {m1} |"
            )
    else:
        lines.append("_No OK cells to rank yet._")
    lines.append("")

    lines.append("## Friedman + Nemenyi (per variant; block = frozen hard-branch target)")
    lines.append("")
    for v in variants:
        fr = result["friedman_by_variant"].get(v, {})
        if not fr.get("ok"):
            lines.append(f"- **{v}**: not computed — {fr.get('reason', 'n/a')}")
            continue
        mr = ", ".join(
            f"{s}={fr['mean_ranks'][s]:.3f}" for s in fr["mean_ranks"]
        )
        lines.append(
            f"- **{v}**: Friedman χ²={fr['statistic']:.4f}, p={fr['p_value']:.4g}, "
            f"N(blocks)={fr['n_blocks']}, CD={fr['critical_difference']:.4f}. "
            f"Mean ranks (1=best): {mr}."
        )
    lines.append("")

    lines.append("## Strategy-vs-default contrasts (Holm-adjusted)")
    lines.append("")
    holm = result["holm_contrasts"]
    lines.append(
        f"Test: {holm['test']}; family size = {holm['family_size']}; "
        f"alpha = {holm['alpha']}."
    )
    lines.append("")
    lines.append("| variant | strategy | Δ union M2 | p (raw) | p (Holm) | sig |")
    lines.append("|---|---|---|---|---|---|")
    for c in holm["contrasts"]:
        if c["status"] != "tested":
            lines.append(
                f"| {c['variant']} | {c['strategy']} | — | — | — | "
                f"{c['status']} |"
            )
            continue
        praw = "n/a" if c["p_raw"] is None else f"{c['p_raw']:.4g}"
        pholm = "n/a" if c.get("p_holm") is None else f"{c['p_holm']:.4g}"
        sig = "**yes**" if c.get("significant") else "no"
        lines.append(
            f"| {c['variant']} | {c['strategy']} | {c['delta_union_m2']:+.3f} "
            f"| {praw} | {pholm} | {sig} |"
        )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 8. CLI.
# ---------------------------------------------------------------------------
def main() -> int:
    default_root = TARGETS["re2"].results_root
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="re2")
    parser.add_argument("--model", default="codestral-22b")
    parser.add_argument(
        "--variants",
        default="v0_none,v1_src,v2_src_tests,v3_all,v4_src_gaps",
        help="comma-separated variant list",
    )
    parser.add_argument(
        "--strategies",
        default="default,cot_strict,few_shot,self_critique,prompt_chain",
        help="comma-separated strategy list ('default' => no path segment)",
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=default_root,
        help=f"ablation results root (default: re2 -> {default_root})",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "results" / "experiment5",
    )
    args = parser.parse_args()

    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]

    result = rank(
        target=args.target,
        model=args.model,
        variants=variants,
        strategies=strategies,
        results_root=args.results_root,
        out_dir=args.out_dir,
    )
    print(json.dumps({
        "target": result["target"],
        "model": result["model"],
        "n_cells": sum(len(v) for v in result["cells"].values()),
        "n_ranked": len(result["ranking"]),
        "out_dir": str(args.out_dir),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
