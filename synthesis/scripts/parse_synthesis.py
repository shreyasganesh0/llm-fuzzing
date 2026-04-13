"""Parse LLM synthesis responses into `GeneratedInput` objects.

The input_synthesis.j2 template requests JSON. Responses may contain:
  {"inputs": [{"content_b64": "...", "target_gaps": ["file:line"], "reasoning": "..."}, ...]}
or the input may be given as plain text / hex / escaped bytes, which we
convert to base64 on the fly. Invalid items are dropped and counted.

For regex-string synthesis (input_synthesis_regex.j2), responses contain:
  {"regexes": [{"regex": "<raw pattern>", "target_gaps": [...], "reasoning": "..."}, ...]}
The parser prepends 2 deterministic flag bytes to each regex to match the
RE2 harness layout (`[2 flag bytes][regex string]`, total 3-64 bytes).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from typing import Literal

from core.dataset_schema import GeneratedInput
from core.loop_detector import is_degenerate_loop

JSON_BLOCK_OBJ_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
JSON_BLOCK_ARR_RE = re.compile(r"```(?:json)?\s*(\[.*?\])\s*```", re.DOTALL)
JSON_BRACE_RE = re.compile(r"\{.*\}", re.DOTALL)
JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def _extract_json(text: str) -> dict | None:
    """Return a dict of shape {"inputs": [...]}, normalising top-level arrays.

    Uses json.loads(strict=False) so raw newlines/tabs inside string values
    are tolerated — small instruction-tuned models frequently emit multi-line
    "reasoning" fields without escaping the newlines, which strict JSON rejects.

    Falls back to per-object salvage when the outer JSON is truncated (common
    when the loop detector aborts mid-array): scans for standalone `{...}`
    objects with a `content_b64` key and returns those as the input list.
    """
    if not text:
        return None
    for regex, group_idx in (
        (JSON_BLOCK_OBJ_RE, 1),
        (JSON_BLOCK_ARR_RE, 1),
        (JSON_BRACE_RE, 0),
        (JSON_ARRAY_RE, 0),
    ):
        m = regex.search(text)
        if not m:
            continue
        candidate = m.group(group_idx)
        try:
            data = json.loads(candidate, strict=False)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {"inputs": data}

    salvaged = _salvage_objects(text)
    if salvaged:
        return {"inputs": salvaged}
    return None


def _salvage_objects(text: str) -> list[dict]:
    """Greedy brace-matching scan for `{...}` objects that carry `content_b64`.

    Used when the outer JSON is truncated (loop-detector aborts) but one or
    more individual input objects are fully formed earlier in the stream.
    Emits any balanced sub-object at any depth so long as it has an
    input-content key.
    """
    objects: list[dict] = []
    stack: list[int] = []
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            stack.append(i)
        elif ch == "}":
            if not stack:
                continue
            start = stack.pop()
            candidate = text[start : i + 1]
            try:
                obj = json.loads(candidate, strict=False)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and any(
                k in obj for k in ("content_b64", "input", "content")
            ):
                objects.append(obj)
    return objects


def _coerce_to_b64(raw: object) -> str | None:
    """Accept a base64 string, a hex string, or raw text and normalise to base64."""
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return base64.b64encode(raw).decode("ascii")
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    # Try base64 first (round-trip).
    try:
        decoded = base64.b64decode(s, validate=True)
        return base64.b64encode(decoded).decode("ascii")
    except (binascii.Error, ValueError):
        pass
    # Hex?
    try:
        decoded = bytes.fromhex(s.replace(" ", "").replace("0x", ""))
        return base64.b64encode(decoded).decode("ascii")
    except ValueError:
        pass
    # Fall back to treating as utf-8 text.
    return base64.b64encode(s.encode("utf-8", errors="replace")).decode("ascii")


def _stable_id(target: str, content_b64: str, sample_index: int, idx: int) -> str:
    h = hashlib.sha256(f"{target}|{sample_index}|{idx}|{content_b64}".encode()).hexdigest()
    return h[:16]


def parse_synthesis_response(
    text: str,
    *,
    target: str,
    model: str,
    temperature: float,
    sample_index: int,
    experiment: Literal["exp1", "exp2"] = "exp1",
) -> tuple[list[GeneratedInput], Literal["ok", "parse_failure"]]:
    if is_degenerate_loop(text):
        return [], "parse_failure"
    data = _extract_json(text)
    if data is None:
        return [], "parse_failure"

    items = data.get("inputs") or data.get("seeds") or []
    if not isinstance(items, list):
        return [], "parse_failure"

    results: list[GeneratedInput] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        raw = item.get("content_b64") or item.get("input") or item.get("content")
        b64 = _coerce_to_b64(raw)
        if not b64:
            continue
        gaps_field = (
            item.get("target_gaps")
            or item.get("gaps")
            or item.get("gap_branches")
            or []
        )
        if isinstance(gaps_field, str):
            gaps_field = [gaps_field]
        gaps = [str(g) for g in gaps_field if isinstance(g, (str, int))]
        reasoning = str(item.get("reasoning", ""))[:2000]
        results.append(
            GeneratedInput(
                input_id=_stable_id(target, b64, sample_index, idx),
                content_b64=b64,
                target_gaps=gaps,
                reasoning=reasoning,
                source="llm",
                model=model,
                temperature=temperature,
                sample_index=sample_index,
                target=target,
                experiment=experiment,
            )
        )

    if not results:
        return [], "parse_failure"
    return results, "ok"


def _truncate_utf8(text: str, max_bytes: int) -> bytes:
    """Encode `text` to UTF-8 and truncate to `max_bytes` without splitting a codepoint."""
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        return raw
    # Walk back from max_bytes until we land on a codepoint boundary.
    end = max_bytes
    while end > 0 and (raw[end] & 0xC0) == 0x80:
        end -= 1
    return raw[:end]


def _flag_bytes(target: str, sample_index: int, idx: int, regex: str) -> bytes:
    """Deterministic 2-flag-byte prefix; same (target, sample, idx, regex) → same bytes."""
    seed_material = f"{target}|{sample_index}|{idx}|{regex}".encode()
    digest = hashlib.sha256(seed_material).digest()
    return digest[:2]


def parse_regex_response(
    text: str,
    *,
    target: str,
    model: str,
    temperature: float,
    sample_index: int,
    experiment: Literal["exp1", "exp2"] = "exp1",
    max_total_bytes: int = 64,
    flag_prefix_bytes: int = 2,
) -> tuple[list[GeneratedInput], Literal["ok", "parse_failure"]]:
    """Parse a regex-synthesis response and pack each regex with flag-byte prefix.

    The RE2 harness requires `3 <= size <= 64` and treats the first 2 bytes
    as flags. We prepend deterministic flag bytes (sha256-seeded) and clip
    the regex body so total size stays within the harness range.
    """
    data = _extract_json(text)
    if data is None:
        return [], "parse_failure"

    items = data.get("regexes") or data.get("inputs") or data.get("seeds") or []
    if not isinstance(items, list):
        return [], "parse_failure"

    max_regex_bytes = max_total_bytes - flag_prefix_bytes  # 62 for RE2

    results: list[GeneratedInput] = []
    seen: set[bytes] = set()
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        raw = item.get("regex") or item.get("pattern") or item.get("input") or item.get("content")
        if not isinstance(raw, str) or not raw:
            continue
        body = _truncate_utf8(raw, max_regex_bytes)
        if not body:
            continue
        flags = _flag_bytes(target, sample_index, idx, raw)
        seed_bytes = flags + body
        if len(seed_bytes) < 3 or len(seed_bytes) > max_total_bytes:
            continue
        if seed_bytes in seen:
            continue
        seen.add(seed_bytes)
        b64 = base64.b64encode(seed_bytes).decode("ascii")
        gaps_field = (
            item.get("target_gaps")
            or item.get("gaps")
            or item.get("gap_branches")
            or []
        )
        if isinstance(gaps_field, str):
            gaps_field = [gaps_field]
        gaps = [str(g) for g in gaps_field if isinstance(g, (str, int))]
        reasoning = str(item.get("reasoning", ""))[:2000]
        results.append(
            GeneratedInput(
                input_id=_stable_id(target, b64, sample_index, idx),
                content_b64=b64,
                target_gaps=gaps,
                reasoning=reasoning,
                source="llm",
                model=model,
                temperature=temperature,
                sample_index=sample_index,
                target=target,
                experiment=experiment,
            )
        )

    if not results:
        return [], "parse_failure"
    return results, "ok"
