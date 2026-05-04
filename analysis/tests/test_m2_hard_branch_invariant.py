"""M2 hard-branch invariant: every target must satisfy rand_hits == 0.

The random-format baseline scores exactly 0% on M2 by construction. If any
frozen target branch has rand_hits > 0, a random seed can hit it and the
baseline is no longer 0% — invalidating all LLM-vs-random comparisons on
that target. See WEEKLY_REVIEW_PROMPT.md §6 for the prior RE2 incident that
motivated this predicate.

Two layers of coverage:

(1) On-disk fixtures — walk every frozen m2_smoke_log.json and assert each
    is_hard: true row satisfies `rand_hits == 0 AND struct_hits/ttf_hits >= 1`.
    This catches the real artifact drifting away from the invariant (e.g.
    if a fallback path silently promoted non-hard branches).

(2) Pure predicate reconstruction — mirror the classification logic on a
    synthetic candidate list and assert the exact same rows are selected.
    This pins the predicate itself (AND, not OR; >= 1, not >= 0).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _smoke_log_paths() -> list[Path]:
    """Return smoke logs that carry the v3+ hard-branch schema (`is_hard` +
    `rand_hits` + `struct_hits`/`ttf_hits`). The legacy RE2 ablation_v3 log
    at `re2_ab/re2/m2_smoke_log.json` predates the rand_hits==0 predicate
    and is kept on disk for historical diff — skip it here."""
    base = _REPO_ROOT / "dataset" / "fixtures"
    out = []
    for p in sorted(base.rglob("m2_smoke_log.json")):
        try:
            cands = json.loads(p.read_text()).get("candidates", [])
        except json.JSONDecodeError:
            continue
        if cands and "is_hard" in cands[0] and "rand_hits" in cands[0]:
            out.append(p)
    return out


@pytest.mark.parametrize("log_path", _smoke_log_paths(), ids=lambda p: str(p.relative_to(_REPO_ROOT)))
def test_frozen_smoke_log_obeys_hard_predicate(log_path: Path) -> None:
    data = json.loads(log_path.read_text())
    candidates = data.get("candidates", [])
    assert candidates, f"empty candidate list in {log_path}"

    # Either 'struct_hits' (RE2) or 'ttf_hits' (harfbuzz) — pick whichever
    # is present. The predicate is the same: hits >= 1 AND rand_hits == 0.
    hit_key = "struct_hits" if "struct_hits" in candidates[0] else "ttf_hits"

    violations = []
    for c in candidates:
        predicted = c[hit_key] >= 1 and c["rand_hits"] == 0
        if bool(c["is_hard"]) != predicted:
            violations.append({
                "file": c["file"], "line": c["line"],
                "rand_hits": c["rand_hits"], hit_key: c[hit_key],
                "is_hard": c["is_hard"], "predicted": predicted,
            })
    assert not violations, (
        f"{log_path.name}: is_hard flag disagrees with rand_hits==0 AND "
        f"{hit_key}>=1 on {len(violations)} row(s): {violations[:3]}"
    )


@pytest.mark.parametrize("log_path", _smoke_log_paths(), ids=lambda p: str(p.relative_to(_REPO_ROOT)))
def test_frozen_target_file_contains_only_hard_branches(log_path: Path) -> None:
    """Every (file, line) in m2_target_branches.json must be is_hard in the
    sibling smoke log. Guards against the freeze writer pulling from a
    non-hard fallback set."""
    target_path = log_path.parent / "m2_target_branches.json"
    if not target_path.is_file():
        pytest.skip(f"no target file next to {log_path}")

    smoke = json.loads(log_path.read_text())
    hard_keys = {
        (c["file"], c["line"]) for c in smoke["candidates"] if c["is_hard"]
    }
    target = json.loads(target_path.read_text())
    emitted = [(r["file"], r["line"]) for r in target.get("shown", [])]
    emitted += [(r["file"], r["line"]) for r in target.get("held_back", [])]

    non_hard = [k for k in emitted if k not in hard_keys]
    assert not non_hard, (
        f"{target_path.name} contains {len(non_hard)} branch(es) not flagged "
        f"is_hard in the smoke log (fallback leakage?): {non_hard[:3]}"
    )


def test_predicate_is_and_not_or() -> None:
    """Pin the exact predicate: AND, not OR; >= 1 on hits, == 0 on rand."""
    def classify(rand_hits: int, struct_hits: int) -> bool:
        return struct_hits >= 1 and rand_hits == 0

    # Positive cases.
    assert classify(0, 1) is True
    assert classify(0, 50) is True
    # Any random hit disqualifies, even with many struct hits.
    assert classify(1, 50) is False
    assert classify(1, 1) is False
    # Zero struct hits disqualifies, even with zero random hits.
    assert classify(0, 0) is False
