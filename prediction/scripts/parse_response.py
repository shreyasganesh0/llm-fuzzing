"""Parse LLM coverage-prediction responses into `PredictionResult`.

Primary prompt returns JSON; variant A returns free-text. This module handles:
  - JSON wrapped in triple-backticks
  - JSON with trailing prose
  - Free-text responses (variant A): deterministic regex extraction
    per plan §2.5 (calls/covers/exercises -> function names; line N -> branches;
    N% -> coverage pct).

Every parser returns `(PredictionResult | None, parse_status)`.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from core.dataset_schema import BranchPrediction, PredictionResult
from core.loop_detector import is_degenerate_loop

JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
JSON_BRACE_RE = re.compile(r"\{.*\}", re.DOTALL)

FUNC_CALL_RE = re.compile(
    r"(?:calls?|covers?|exercises?|invokes?|enters?|hits?|reaches?)\s+[`\"]?"
    r"([A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)*)[`\"]?",
    re.IGNORECASE,
)
BRANCH_RE = re.compile(r"([A-Za-z0-9_./-]+\.(?:c|cc|cpp|h|hpp)):(\d+)")
PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def parse_json_response(text: str) -> tuple[PredictionResult | None, Literal["ok", "parse_failure"]]:
    """Pull a PredictionResult out of a JSON-ish LLM response."""
    if is_degenerate_loop(text):
        return None, "parse_failure"
    candidate = _isolate_json_object(text)
    if candidate is None:
        return None, "parse_failure"
    try:
        data = json.loads(candidate, strict=False)
    except json.JSONDecodeError:
        return None, "parse_failure"

    data.setdefault("functions_covered", [])
    data.setdefault("functions_not_covered", [])
    data.setdefault("branches", [])
    data.setdefault("estimated_line_coverage_pct", 0.0)
    data.setdefault("reasoning", "")

    normalised_branches = []
    for b in data["branches"]:
        if not isinstance(b, dict):
            continue
        loc = b.get("location", "")
        normalised_branches.append(
            BranchPrediction(
                location=str(loc),
                true_taken=bool(b.get("true_taken", False)),
                false_taken=bool(b.get("false_taken", False)),
            )
        )
    data["branches"] = normalised_branches

    try:
        result = PredictionResult.model_validate(data)
        return result, "ok"
    except Exception:
        return None, "parse_failure"


def parse_free_text_response(
    text: str,
    *,
    known_functions: set[str] | None = None,
) -> tuple[PredictionResult | None, Literal["ok", "parse_failure"]]:
    """Deterministic regex extraction for the variant A (free-text) prompt."""
    if is_degenerate_loop(text):
        return None, "parse_failure"
    func_candidates = {m.group(1) for m in FUNC_CALL_RE.finditer(text)}
    if known_functions is not None:
        func_candidates = func_candidates & known_functions
    functions_covered = sorted(func_candidates)

    branches = []
    seen_loc: set[tuple[str, int]] = set()
    for m in BRANCH_RE.finditer(text):
        file, line = m.group(1), int(m.group(2))
        if (file, line) in seen_loc:
            continue
        seen_loc.add((file, line))
        branches.append(BranchPrediction(location=f"{file}:{line}", true_taken=True, false_taken=False))

    pct_match = PCT_RE.search(text)
    pct = float(pct_match.group(1)) if pct_match else 0.0

    # Plan §2.5: if <3 function names extracted, mark parse_failure.
    if len(functions_covered) < 3:
        return None, "parse_failure"

    return (
        PredictionResult(
            functions_covered=functions_covered,
            functions_not_covered=[],
            branches=branches,
            estimated_line_coverage_pct=pct,
            reasoning="",
        ),
        "ok",
    )


def _isolate_json_object(text: str) -> str | None:
    if not text:
        return None
    m = JSON_BLOCK_RE.search(text)
    if m:
        return m.group(1)
    m = JSON_BRACE_RE.search(text)
    if m:
        return m.group(0)
    return None
