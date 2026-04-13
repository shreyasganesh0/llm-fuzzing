"""Random-baseline generator tests (plan §Phase 3 test_random_gen)."""
from __future__ import annotations

import base64

from synthesis.scripts.generate_random_inputs import generate_random


def test_random_gen_produces_requested_count():
    out = generate_random("re2", count=7, max_len=128, seed=1)
    assert len(out) == 7


def test_random_gen_seed_deterministic():
    a = generate_random("re2", count=5, max_len=128, seed=42)
    b = generate_random("re2", count=5, max_len=128, seed=42)
    assert [x.content_b64 for x in a] == [x.content_b64 for x in b]


def test_random_gen_sqlite_uses_keywords():
    out = generate_random("sqlite3", count=3, max_len=64, seed=1)
    payloads = [base64.b64decode(x.content_b64).decode(errors="replace") for x in out]
    # Every payload should contain at least one SQL keyword from the bag.
    assert all(any(kw in p.upper() for kw in ("SELECT", "FROM", "WHERE", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "JOIN", "ON", "VALUES", "SET", "AND", "OR", "COUNT", "DISTINCT", "LIMIT", "ORDER")) for p in payloads)


def test_random_gen_defaults_for_unknown_target_produces_bytes():
    out = generate_random("unknown_target", count=4, max_len=16, seed=1)
    assert all(x.source == "random" for x in out)
    for x in out:
        assert len(base64.b64decode(x.content_b64)) <= 16
