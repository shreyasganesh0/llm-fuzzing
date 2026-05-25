"""Per-seed coverage helper for in-loop, coverage-grounded synthesis
strategies (experiment5/FOLLOWUP2).

Reuses `analysis.scripts.measure_gap_coverage.replay_one_seed` /
`compute_hits` / `load_targets` — does NOT re-implement the metric.
Returns, for a single seed file, the boolean hit vector against the
frozen RE2 15-branch hard-target set, plus a human-readable summary
suitable for embedding into a refine prompt.

This is the only piece of new infrastructure for the coverage-grounded
strategies. The seed_replay binary, target file, baseline, llvm-cov
toolchain, and uncovered-side check are all the unmodified production
pieces — by design, so the in-loop signal is byte-identical to the
post-hoc M2 score that scores the final corpus.

Design choice: this helper is tolerant — if replay times out, profdata
is corrupt, or any subprocess fails, it returns ``(None, "<no signal
this seed>")`` rather than raising. A strategy that can't get coverage
for one seed should still try the next; failing the whole batch on one
bad replay would lose information the FAIL-SAFE design needs to keep.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Tuple

from analysis.scripts.measure_gap_coverage import (
    compute_hits,
    load_baseline,
    load_targets,
    replay_one_seed,
)
from core.targets import TARGETS


def per_seed_hits(
    seed_bytes: bytes,
    *,
    idx: int = 0,
    targets_path: Path | None = None,
    baseline_path: Path | None = None,
    coverage_binary: Path | None = None,
) -> Tuple[list[bool] | None, list[dict] | None]:
    """Replay a single seed and return (hits, target_records).

    Returns ``(None, None)`` if replay or profdata processing fails for
    this seed — callers must handle the no-signal case (typically by
    falling back to a draft-only or feedback-empty path).

    ``hits[i]`` is True iff this seed hit the i-th frozen target on its
    *uncovered side*. ``target_records[i]`` is a small dict with
    ``file``, ``line``, ``uncovered_side``, ``slice`` for prompt
    formatting.
    """
    # Default to the canonical RE2-v2 15-target frozen set used by the
    # production M2 metric. measure_gap_coverage.load_targets defaults
    # to the OLD 50-target file (re2_ab), so a no-args call here would
    # silently use the wrong denominator. The TargetSpec is the single
    # source of truth (per core/CLAUDE.md).
    if targets_path is None:
        targets_path = TARGETS["re2"].m2_targets_path
    targets = load_targets(targets_path)
    _ = load_baseline(baseline_path)  # validate, kept for API symmetry
    target_records = [
        {
            "file": t.file,
            "line": t.line,
            "uncovered_side": t.uncovered_side,
            "slice": t.slice,
        }
        for t in targets
    ]

    with tempfile.TemporaryDirectory(prefix="utcf_perseed_") as td:
        work_dir = Path(td)
        seed_path = work_dir / f"seed_{idx}.bin"
        seed_path.write_bytes(seed_bytes)
        try:
            profile = replay_one_seed(
                seed_path, work_dir, idx, binary=coverage_binary,
            )
        except Exception:
            return None, target_records
        if profile is None:
            return None, target_records
        hits = compute_hits(profile, _, targets)
    return hits, target_records


def format_coverage_feedback(
    hits: list[bool] | None,
    target_records: list[dict],
    *,
    max_hits_listed: int = 6,
    max_misses_listed: int = 8,
) -> str:
    """Render a per-seed coverage report for embedding in a refine prompt.

    Stays under ~600 chars (well below the 2048-char LiteLLM output cap,
    and the input side has plenty of headroom). The format is stable —
    the dispatch branches assume this exact shape.
    """
    if hits is None or len(hits) != len(target_records):
        return (
            "[no coverage signal for this seed — replay or profdata failed; "
            "treat draft critique generically]"
        )
    hit_pairs = [(rec, h) for rec, h in zip(target_records, hits)]
    hits_taken = [r for r, h in hit_pairs if h][:max_hits_listed]
    misses = [r for r, h in hit_pairs if not h][:max_misses_listed]
    n_hit = sum(1 for h in hits if h)
    n_total = len(hits)
    lines = [
        f"DRAFT'S ACTUAL COVERAGE (replayed through the RE2 coverage build):",
        f"  hit {n_hit}/{n_total} frozen hard-target branches.",
    ]
    if hits_taken:
        lines.append("  HIT branches (already covered by THIS draft):")
        for r in hits_taken:
            side = (r.get("uncovered_side") or "UNCOVERED").upper()
            lines.append(f"    - {r['file']}:{r['line']} ({side} side, {r['slice']} slice)")
    else:
        lines.append("  HIT branches: (none — this draft hits 0/15)")
    if misses:
        lines.append("  MISSED branches you should consider targeting:")
        for r in misses:
            side = (r.get("uncovered_side") or "UNCOVERED").upper()
            lines.append(f"    - {r['file']}:{r['line']} ({side} side, {r['slice']} slice)")
    return "\n".join(lines)


def format_corpus_coverage_feedback(
    union_hits: list[bool] | None,
    target_records: list[dict],
    n_seeds_so_far: int,
    *,
    max_listed: int = 8,
) -> str:
    """Render a *corpus-level* coverage summary for diversity-aware
    grounded prompts.

    `union_hits[i]` is True iff ANY prior seed in the cell hit target i.
    The summary lists branches still uncovered by the cell so far, so
    the model can prefer one of those.
    """
    if union_hits is None or len(union_hits) != len(target_records):
        return (
            f"[no corpus coverage signal yet — {n_seeds_so_far} prior seeds "
            "but coverage replay unavailable for them]"
        )
    n_hit = sum(1 for h in union_hits if h)
    n_total = len(union_hits)
    misses = [r for r, h in zip(target_records, union_hits) if not h][:max_listed]
    lines = [
        f"CELL CORPUS COVERAGE SO FAR ({n_seeds_so_far} prior seeds, replayed):",
        f"  the corpus union hits {n_hit}/{n_total} frozen hard-target branches.",
    ]
    if misses:
        lines.append("  STILL-UNCOVERED branches (prefer one of these to grow the union):")
        for r in misses:
            side = (r.get("uncovered_side") or "UNCOVERED").upper()
            lines.append(f"    - {r['file']}:{r['line']} ({side} side, {r['slice']} slice)")
    else:
        lines.append("  STILL-UNCOVERED branches: (none — the corpus already covers all 15)")
    return "\n".join(lines)
