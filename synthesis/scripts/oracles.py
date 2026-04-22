"""Phase 7 structural oracles.

Lightweight per-target *structural* validators the ToolUseStrategy invokes
in-process during synthesis. Each verdict is deterministic: the same
``content`` / ``content_b64`` always produces the same ``{ok, issues, details}``
dict. No subprocess, no random state, no external dependencies.

Non-goals:
- These are NOT coverage oracles. A full ``seed_replay``-backed oracle
  would cost seconds × thousands of tool calls per run. That's Phase 8+.
- The RE2 oracle uses Python's ``re.compile`` as a cheap *proxy* for RE2
  acceptance. They are not equivalent (PCRE vs. RE2 syntax differ on
  backreferences, named groups, Unicode classes). Treat the verdict as
  advisory — a structural smoke test that catches obviously-broken
  regexes but won't flag every RE2-rejected pattern.
"""
from __future__ import annotations

import base64
import binascii
import re
from typing import Any

# ---- harfbuzz header tags --------------------------------------------------
# sfnt version tags recognised by freetype / harfbuzz loaders. See
# https://docs.microsoft.com/en-us/typography/opentype/spec/otff#organization-of-an-opentype-font
_SFNT_VERSIONS: tuple[bytes, ...] = (
    b"\x00\x01\x00\x00",  # TrueType
    b"OTTO",              # OpenType/CFF
    b"true",              # legacy TrueType (Apple)
    b"typ1",              # legacy PostScript Type 1
)

# Harfbuzz binary blobs are capped at 64 bytes by the Phase prompt
# constraint (keeps base64 under the UF LiteLLM 2048-char response cap).
# Matches scripts/run_ablation_harfbuzz.py's --blob-max argument.
_HARFBUZZ_MAX_BYTES = 64
_HARFBUZZ_MIN_BYTES = 5  # strictly > 4 so the header tag check is meaningful


def check_seed(
    target: str,
    *,
    content: str | None = None,
    content_b64: str | None = None,
) -> dict[str, Any]:
    """Return ``{"ok": bool, "issues": list[str], "details": dict}``.

    ``target == "re2"``: accept ``content`` only. ``ok=True`` when the
    string compiles under Python's ``re`` module. ``ok=False`` on an
    empty string or on ``re.error``. Passing ``content_b64`` is ignored
    for re2.

    ``target == "harfbuzz"``: accept ``content_b64`` only. Verify:
      * base64 decodes without error,
      * length is in ``(4, 64]`` bytes (Phase cap + meaningful header),
      * first 4 bytes match a known sfnt version tag.
    Passing ``content`` is ignored for harfbuzz.

    Other targets raise ``NotImplementedError`` so new targets have to
    opt in explicitly — no silent pass-through.
    """
    if target == "re2":
        return _check_re2(content)
    if target == "harfbuzz":
        return _check_harfbuzz(content_b64)
    raise NotImplementedError(
        f"check_seed: no structural oracle for target {target!r}. "
        "Add a branch here when a new target opts into Phase 7 tool use."
    )


def _check_re2(content: str | None) -> dict[str, Any]:
    issues: list[str] = []
    details: dict[str, Any] = {"length": len(content) if content is not None else 0}
    if content is None or content == "":
        issues.append("empty regex")
        return {"ok": False, "issues": issues, "details": details}
    try:
        re.compile(content)
    except re.error as exc:
        issues.append(f"re.error: {exc}")
    return {"ok": not issues, "issues": issues, "details": details}


def _check_harfbuzz(content_b64: str | None) -> dict[str, Any]:
    issues: list[str] = []
    details: dict[str, Any] = {}
    if not content_b64:
        issues.append("empty content_b64")
        return {"ok": False, "issues": issues, "details": details}
    try:
        blob = base64.b64decode(content_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        issues.append(f"base64 decode error: {exc}")
        return {"ok": False, "issues": issues, "details": details}
    n = len(blob)
    details["length"] = n
    details["first4_hex"] = blob[:4].hex()
    if n <= 4:
        issues.append("blob too small")
    if n > _HARFBUZZ_MAX_BYTES:
        issues.append(f"blob exceeds {_HARFBUZZ_MAX_BYTES}-byte cap")
    if n >= 4 and blob[:4] not in _SFNT_VERSIONS:
        issues.append("first 4 bytes not a known sfnt version")
    return {"ok": not issues, "issues": issues, "details": details}


# ---- OpenAI-style tool schema ----------------------------------------------
# Phase 7 runs on ``gpt-oss-20b`` and ``nemotron-3-super-120b-a12b`` via the
# UF LiteLLM proxy — both accept OpenAI-style tool dicts per
# ``results/probes/probe_tool_use.json``. An Anthropic-native schema is NOT
# exported yet: the probe could not verify Anthropic tool use (zero API
# credits) so ``supports_tool_use`` is False on every Claude model.

CHECK_SEED_TOOL_OPENAI: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "check_seed",
        "description": (
            "Validate a seed's structural correctness before emitting it. "
            "Returns {ok, issues, details}. Call at most K times per seed; "
            "after the tool says ok=true, emit the final seed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Regex string (for re2 target).",
                },
                "content_b64": {
                    "type": "string",
                    "description": "Base64-encoded bytes (for harfbuzz target).",
                },
            },
            "required": [],
        },
    },
}
