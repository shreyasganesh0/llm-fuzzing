"""Phase 7 structural oracles + retrieval tools.

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

This module also hosts the retrieval tools used by the
``tool_use_retrieval`` variant: ``list_uncovered_branches`` (reads
``coverage_gaps.json`` verbatim) and ``get_source`` (reads a bounded
slice of the pinned upstream source). Both are deterministic by
construction — no LLM in the retrieval path — so the feedback the
model receives is ground truth.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
from pathlib import Path
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


# ---- Retrieval tools ---------------------------------------------------------
# Deterministic backing: ``list_uncovered_branches`` reads the pinned
# ``coverage_gaps.json`` verbatim; ``get_source`` reads a bounded slice of
# the upstream source. Neither touches the LLM, so whatever the tool returns
# is ground truth. Output sizes are hard-capped so an adversarial model
# cannot inflate cost via huge tool arguments.

_MAX_GAPS_K = 20
_MAX_SOURCE_LINES = 200


def list_uncovered_branches(
    *, target: str, k: int, dataset_root: Path | str,
) -> dict[str, Any]:
    """Return the first ``k`` entries from ``coverage_gaps.json`` verbatim.

    ``k`` is clamped to ``[1, 20]``. Missing fixture → raises FileNotFoundError
    so the tool response surfaces a real error rather than fabricating gaps.
    """
    k = max(1, min(int(k), _MAX_GAPS_K))
    path = Path(dataset_root) / target / "coverage_gaps.json"
    if not path.is_file():
        raise FileNotFoundError(f"coverage_gaps fixture not found: {path}")
    data = json.loads(path.read_text())
    gaps = data.get("gap_branches") or []
    trimmed = []
    for g in gaps[:k]:
        trimmed.append({
            "file": g.get("file"),
            "line": g.get("line"),
            "uncovered_side": g.get("uncovered_side"),
            "condition_description": g.get("condition_description"),
            "code_context": g.get("code_context"),
        })
    return {"gaps": trimmed, "k_returned": len(trimmed), "k_requested": k}


def get_source(
    *, target: str, file: str, line_start: int, line_end: int,
    source_root: Path | str,
) -> dict[str, Any]:
    """Return a bounded slice of upstream source.

    Rejects path traversal via realpath check: the resolved file must live
    under ``source_root``. Range is clamped to ``_MAX_SOURCE_LINES``.
    Missing file → raises FileNotFoundError; out-of-range line numbers
    return the empty portion (no exception) so the model can recover.
    """
    if line_start < 1:
        line_start = 1
    if line_end < line_start:
        line_end = line_start
    if line_end - line_start + 1 > _MAX_SOURCE_LINES:
        line_end = line_start + _MAX_SOURCE_LINES - 1

    root = Path(source_root).resolve()
    candidate = (root / file).resolve()
    # Path traversal guard — candidate must be under root.
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"source path {file!r} resolves outside source_root {root}"
        ) from exc
    if not candidate.is_file():
        raise FileNotFoundError(f"source file not found: {candidate}")

    lines = candidate.read_text(errors="replace").splitlines()
    n = len(lines)
    lo = min(line_start, n + 1)
    hi = min(line_end, n)
    slice_lines = lines[lo - 1:hi] if lo <= hi else []
    content = "\n".join(slice_lines)
    return {
        "file": str(candidate.relative_to(root)),
        "line_start": lo,
        "line_end": hi,
        "total_lines": n,
        "content": content,
    }


# OpenAI-dialect tool schemas for the retrieval tools.
LIST_UNCOVERED_BRANCHES_TOOL_OPENAI: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "list_uncovered_branches",
        "description": (
            "List the top-K uncovered branches for the current target. "
            "Each entry has file, line, uncovered_side, condition_description, "
            "and a code_context window. Call this FIRST if you don't know "
            "what to target. k is clamped to [1, 20]."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "k": {
                    "type": "integer",
                    "description": "How many top uncovered branches to return (1..20).",
                },
            },
            "required": ["k"],
        },
    },
}

GET_SOURCE_TOOL_OPENAI: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_source",
        "description": (
            "Fetch a bounded slice of upstream source code (max 200 lines). "
            "Use this AFTER list_uncovered_branches to inspect the code "
            "around a target branch. Returns verbatim file contents."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Path relative to the upstream source root (e.g. 're2/parse.cc').",
                },
                "line_start": {"type": "integer"},
                "line_end": {"type": "integer"},
            },
            "required": ["file", "line_start", "line_end"],
        },
    },
}
