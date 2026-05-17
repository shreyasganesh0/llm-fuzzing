"""experiment6 cache-key back-compat guard.

The entropy-stratification experiment adds optional ``logprobs`` /
``top_logprobs`` kwargs to ``LLMClient.complete`` and ``_prompt_hash``.
The hard constraint (PROJECT_CONTEXT §6 invariant 9): when these are NOT
requested the cache key must be **byte-identical** to the pre-change key,
so the ~14k existing ``.cache/llm/`` entries — and every non-experiment6
caller — keep hitting cache.

``FROZEN_PRE_CHANGE_HASH`` was computed by reproducing the exact
pre-change payload dict (no logprobs keys) and sha256-ing it the same way
``_prompt_hash`` does. If a future edit perturbs the default payload this
test fails loudly instead of silently invalidating $100 of cached
responses.
"""
from __future__ import annotations

import hashlib
import json

from core.llm_client import Response, _prompt_hash

_MODEL = "codestral-22b"
_MESSAGES = [
    {"role": "system", "content": ""},
    {"role": "user", "content": "hello"},
]
_TEMP = 0.7
_TOP_P = 0.95
_MAX_TOKENS = 4096
_SALT = "model=codestral-22b,sample=0,ablation=v1_src,run=600001"

# sha256 of the pre-change payload (model, messages, temperature, top_p,
# max_tokens, cache_salt — and nothing else).
FROZEN_PRE_CHANGE_HASH = (
    "9878904ae4d50fe49e162f5aadaf2bc1f649bab90fdd3fc05dc9d38cdfa7f373"
)


def _legacy_payload_hash() -> str:
    payload = {
        "model": _MODEL,
        "messages": _MESSAGES,
        "temperature": _TEMP,
        "top_p": _TOP_P,
        "max_tokens": _MAX_TOKENS,
        "cache_salt": _SALT,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def test_no_logprobs_is_byte_identical_to_pre_change_key():
    """Default (no logprobs) cache key == frozen pre-change digest."""
    h = _prompt_hash(_MODEL, _MESSAGES, _TEMP, _TOP_P, _MAX_TOKENS, _SALT)
    assert h == FROZEN_PRE_CHANGE_HASH
    assert h == _legacy_payload_hash()


def test_explicit_none_logprobs_is_also_byte_identical():
    """Passing logprobs=None/top_logprobs=None must not change the key."""
    h = _prompt_hash(
        _MODEL, _MESSAGES, _TEMP, _TOP_P, _MAX_TOKENS, _SALT,
        logprobs=None, top_logprobs=None,
    )
    assert h == FROZEN_PRE_CHANGE_HASH


def test_requesting_logprobs_changes_the_key():
    """logprob runs must NOT collide with the 14k no-logprob entries."""
    h_plain = _prompt_hash(_MODEL, _MESSAGES, _TEMP, _TOP_P, _MAX_TOKENS, _SALT)
    h_lp = _prompt_hash(
        _MODEL, _MESSAGES, _TEMP, _TOP_P, _MAX_TOKENS, _SALT,
        logprobs=True, top_logprobs=20,
    )
    assert h_lp != h_plain
    # top_logprobs is part of the key too: a different K is a different run.
    h_lp_k5 = _prompt_hash(
        _MODEL, _MESSAGES, _TEMP, _TOP_P, _MAX_TOKENS, _SALT,
        logprobs=True, top_logprobs=5,
    )
    assert h_lp_k5 != h_lp


def test_legacy_cache_json_still_rehydrates():
    """Old cache files (no raw/tool_calls fields) must load unchanged.

    ``Response.raw`` (where experiment6 stows logprobs) and
    ``tool_calls`` default to None, so ``Response(**legacy_dict)`` works
    for the pre-existing entries.
    """
    legacy = {
        "content": "x",
        "model": _MODEL,
        "temperature": _TEMP,
        "top_p": _TOP_P,
        "input_tokens": 1,
        "output_tokens": 1,
        "cost_usd": 0.0,
        "latency_ms": 1.0,
        "prompt_hash": FROZEN_PRE_CHANGE_HASH,
        "timestamp": "2026-04-17T00:00:00+00:00",
        "generation_wall_clock_s": 0.0,
        "cached": True,
    }
    r = Response(**legacy)
    assert r.raw is None
    assert r.tool_calls is None
    assert r.content == "x"
