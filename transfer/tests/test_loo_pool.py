"""LOO pool hygiene tests (plan §Phase Transfer T.1 VALIDATION).

The held-out target must never appear in its own LOO few-shot pool, and
Tier 3 targets must never appear in any pool.
"""
from __future__ import annotations

from core.config import TIER3_TARGETS, TIER12_TARGETS
from transfer.scripts.build_loo_prompt import _eligible_sources


def test_held_out_target_never_in_own_pool():
    for t in TIER12_TARGETS:
        pool = _eligible_sources(t)
        assert t not in pool, f"{t} appeared in its own LOO pool"


def test_tier3_targets_never_in_pool():
    for t in TIER12_TARGETS:
        pool = _eligible_sources(t)
        for t3 in TIER3_TARGETS:
            assert t3 not in pool, f"Tier 3 target {t3} leaked into {t}'s pool"


def test_tier3_also_excludes_self_and_all_tier3():
    for t3 in TIER3_TARGETS:
        pool = _eligible_sources(t3)
        assert t3 not in pool
        for other in TIER3_TARGETS:
            assert other not in pool


def test_pool_contains_enough_distinct_targets():
    for t in TIER12_TARGETS:
        pool = _eligible_sources(t)
        # Must have at least 3 distinct sources to stratify properly.
        assert len(pool) >= 3, f"{t}: pool too small ({pool})"
