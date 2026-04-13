"""Detect degenerate repetition in LLM outputs.

Small instruct-tuned models (llama-3.1-8b especially) sometimes fall into a
loop: they emit the same ~30-char substring hundreds of times until the
max_tokens budget is exhausted. Observed examples from this repo:
  - `{"location": "re2/re2.cc:LINE", ...}` repeated 322 times
  - `"difficulty_reason": "Requires specific magic bytes..."` × 181
  - A base64 fragment looped hundreds of times inside one content_b64 field
  - 200 near-duplicate branch objects (same file, same difficulty_reason,
    only the line number varies) — total 47K chars, 17% unique windows

Two signals; firing either flags the text:
  (a) a single sliding-window substring dominates by count+coverage
      (catches the very-tight single-token loops)
  (b) the unique-window ratio collapses below a floor (catches the
      near-duplicate-but-not-identical loops that pass (a))
"""
from __future__ import annotations

from collections import Counter

_DEFAULT_WINDOW = 40
_DEFAULT_MIN_REPEATS = 6
_DEFAULT_SINGLE_WINDOW_COVERAGE = 0.40
_DEFAULT_MIN_LEN_FOR_RATIO = 1000
_DEFAULT_MIN_UNIQUE_RATIO = 0.30


def is_degenerate_loop(
    text: str,
    *,
    window: int = _DEFAULT_WINDOW,
    min_repeats: int = _DEFAULT_MIN_REPEATS,
    min_coverage: float = _DEFAULT_SINGLE_WINDOW_COVERAGE,
    min_len_for_ratio: int = _DEFAULT_MIN_LEN_FOR_RATIO,
    min_unique_ratio: float = _DEFAULT_MIN_UNIQUE_RATIO,
) -> bool:
    """Return True when the text is degenerate by either heuristic.

    Signal (a) — single-window dominance: a single substring of length
    `window` appears ≥ min_repeats times AND covers ≥ min_coverage of the
    text. Tuned for tight loops of one token/line repeated verbatim.

    Signal (b) — unique-window ratio: for texts ≥ min_len_for_ratio chars,
    (unique windows / total windows) must be ≥ min_unique_ratio. Healthy
    LLM output sits around 0.60-0.80; degenerate output drops below 0.25.
    Catches looser loops (200 near-duplicate JSON objects) that signal (a)
    misses because each entry differs by one integer.
    """
    if len(text) < window * min_repeats:
        return False
    windows = Counter(text[i : i + window] for i in range(len(text) - window + 1))
    total = sum(windows.values())
    top_substring, top_count = windows.most_common(1)[0]
    coverage = (top_count * window) / max(len(text), 1)
    if top_count >= min_repeats and coverage >= min_coverage:
        return True
    if len(text) >= min_len_for_ratio:
        unique_ratio = len(windows) / max(total, 1)
        if unique_ratio < min_unique_ratio:
            return True
    return False
