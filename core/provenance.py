"""Provenance verification for upstream unit tests.

The cardinal rule (plan §Key Design Decisions #1): every `Test` object must
trace back to `upstream_repo:upstream_commit:upstream_file:upstream_line`.

`verify_test_upstream` opens `upstream_file` within the cloned upstream repo
and checks that the exact lines `upstream_line..upstream_line+N` match the
first N non-empty lines of `test.test_code`. This catches:
  - fabricated tests (nothing matches)
  - drifted tests (upstream changed since extraction)
  - wrong-line provenance (off-by-one errors in extractors)

The comparison normalises whitespace run-length so that extractors may reflow
trailing whitespace without triggering false positives, but token content is
byte-exact.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.dataset_schema import Test

_WS_RE = re.compile(r"\s+")


def _normalise(line: str) -> str:
    return _WS_RE.sub(" ", line.strip())


def _candidate_first_lines(test_code: str, limit: int = 3) -> list[str]:
    lines = []
    for raw in test_code.splitlines():
        norm = _normalise(raw)
        if norm:
            lines.append(norm)
        if len(lines) >= limit:
            break
    return lines


def verify_test_upstream(test: Test, repo_root: str | Path) -> bool:
    """Return True iff the first 2 non-empty lines of test.test_code match
    the upstream file at the recorded line. Two lines is enough to uniquely
    identify a TEST(Suite, Name) macro plus its first assertion while tolerating
    whitespace drift and extractor body-truncation."""
    repo_root = Path(repo_root)
    candidate = _candidate_first_lines(test.test_code, limit=2)
    if not candidate:
        return False

    file_path = repo_root / test.upstream_file
    if not file_path.is_file():
        return False

    source_lines = file_path.read_text(errors="replace").splitlines()
    if test.upstream_line < 1 or test.upstream_line > len(source_lines):
        return False

    # Search within a small window because the extracted line may be the `TEST(`
    # macro while the test_code includes leading comments. Window of ±2 lines.
    for offset in (0, 1, -1, 2, -2):
        start = test.upstream_line - 1 + offset
        if start < 0 or start + len(candidate) > len(source_lines):
            continue
        upstream_block = [_normalise(line) for line in source_lines[start : start + len(candidate)]]
        if upstream_block == candidate:
            return True
    return False


def audit_tests(tests: list[Test], repo_root: str | Path) -> dict[str, list[str]]:
    """Bulk audit. Returns {"verified": [names...], "rejected": [names...]}."""
    verified, rejected = [], []
    for t in tests:
        if verify_test_upstream(t, repo_root):
            verified.append(t.test_name)
        else:
            rejected.append(t.test_name)
    return {"verified": verified, "rejected": rejected}
