"""Compute per-target coverage-gap report from per-test coverage profiles.

Reads `dataset/<target>/tests/test_*/coverage.json` for every valid profile,
computes union coverage, gap branches/functions, overlap matrix, and per-test
unique coverage. Writes `dataset/<target>/coverage_gaps.json` matching the
`CoverageGapsReport` schema (plan §1.6 audit naming: `total_upstream_tests`,
`union_coverage_pct`).

For each gap branch, extracts a ±10-line source context window and asks an LLM
(temperature=0, cached) for a one-sentence `condition_description`. Falls back
to a regex-derived description if the LLM call is unavailable or fails.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.coverage_utils import jaccard, union_coverage
from core.dataset_schema import (
    CoverageGapsReport,
    CoverageProfile,
    GapBranch,
    GapFunction,
)
from core.logging_config import get_logger

logger = get_logger("utcf.phase1.gaps")

CONTEXT_LINES = 10


def load_profiles(dataset_root: Path, target: str) -> list[CoverageProfile]:
    profiles: list[CoverageProfile] = []
    tests_dir = dataset_root / target / "tests"
    if not tests_dir.is_dir():
        return profiles
    for test_dir in sorted(tests_dir.glob("test_*")):
        cov_path = test_dir / "coverage.json"
        if not cov_path.is_file():
            continue
        data = json.loads(cov_path.read_text())
        try:
            profiles.append(CoverageProfile.model_validate(data))
        except Exception as exc:
            logger.warning("invalid coverage profile", extra={"path": str(cov_path), "error": str(exc)})
    return profiles


def _extract_context(upstream_root: Path, rel_file: str, line: int) -> str:
    path = upstream_root / rel_file
    if not path.is_file():
        return ""
    lines = path.read_text(errors="replace").splitlines()
    start = max(0, line - 1 - CONTEXT_LINES)
    end = min(len(lines), line + CONTEXT_LINES)
    window = lines[start:end]
    return "\n".join(window)


def _heuristic_condition(code_context: str, line: int) -> str:
    for ctx_line in code_context.splitlines():
        if "if" in ctx_line or "switch" in ctx_line or "while" in ctx_line:
            stripped = ctx_line.strip()
            return f"Requires the following to evaluate toward the uncovered branch: {stripped}"
    return f"Branch at line {line} is never exercised by any upstream test."


def _llm_condition(
    code_context: str,
    *,
    line: int,
    llm_client,
    model: str,
    cache_hits: dict[str, str],
) -> str:
    h = hashlib.sha256(code_context.encode()).hexdigest()
    if h in cache_hits:
        return cache_hits[h]
    if llm_client is None:
        desc = _heuristic_condition(code_context, line)
        cache_hits[h] = desc
        return desc
    try:
        resp = llm_client.complete(
            messages=[
                {"role": "system", "content": "You are a concise code-review assistant."},
                {
                    "role": "user",
                    "content": (
                        "Given this C/C++ snippet, describe in ONE SENTENCE what input "
                        "condition would cause execution to take the uncovered branch "
                        f"on or near line {line}. Respond with the sentence only, no prose.\n\n"
                        f"```\n{code_context}\n```"
                    ),
                },
            ],
            model=model,
            temperature=0.0,
            top_p=1.0,
            max_tokens=120,
        )
        desc = resp.content.strip().splitlines()[0] if resp.content else _heuristic_condition(code_context, line)
    except Exception as exc:
        logger.warning("condition_description LLM call failed", extra={"error": str(exc)})
        desc = _heuristic_condition(code_context, line)
    cache_hits[h] = desc
    return desc


def compute_report(
    target: str,
    *,
    dataset_root: Path,
    upstream_root: Path,
    llm_client=None,
    condition_model: str = "gpt-4o-2024-08-06",
) -> CoverageGapsReport:
    profiles = load_profiles(dataset_root, target)
    if not profiles:
        logger.warning("no profiles found", extra={"target": target})

    union = union_coverage(profiles)
    total_branches = sum(2 * len(f.branches) for f in union.files.values())
    covered_branches = sum(
        int(b.true_taken) + int(b.false_taken)
        for f in union.files.values()
        for b in f.branches.values()
    )
    union_pct = 100.0 * covered_branches / total_branches if total_branches else 0.0

    # Gap branches
    gap_branches: list[GapBranch] = []
    llm_cache: dict[str, str] = {}
    for filename, fc in union.files.items():
        for key, br in fc.branches.items():
            if br.true_taken and br.false_taken:
                continue
            _, _, line_str = key.rpartition(":")
            try:
                line = int(line_str)
            except ValueError:
                continue
            rel = _relativise(filename, upstream_root)
            ctx = _extract_context(upstream_root, rel, line)
            cond = _llm_condition(ctx, line=line, llm_client=llm_client, model=condition_model, cache_hits=llm_cache)
            gap_branches.append(GapBranch(file=rel, line=line, code_context=ctx, condition_description=cond))

    # Gap functions
    gap_functions: list[GapFunction] = []
    for filename, fc in union.files.items():
        rel = _relativise(filename, upstream_root)
        for func in fc.functions_not_covered:
            if func not in fc.functions_covered:
                gap_functions.append(GapFunction(file=rel, function=func))

    # Per-test unique coverage
    per_test_unique: dict[str, int] = {}
    for primary in profiles:
        primary_lines = {(f, line) for f, fc in primary.files.items() for line in fc.lines_covered}
        other_lines: set = set()
        for other in profiles:
            if other is primary:
                continue
            other_lines.update((f, line) for f, fc in other.files.items() for line in fc.lines_covered)
        per_test_unique[primary.test_name] = len(primary_lines - other_lines)

    # Pairwise Jaccard overlap (only store names)
    overlap: dict[str, dict[str, float]] = {}
    for i, a in enumerate(profiles):
        overlap.setdefault(a.test_name, {})
        for j, b in enumerate(profiles):
            if i >= j:
                continue
            score = jaccard(a, b)
            overlap[a.test_name][b.test_name] = score
            overlap.setdefault(b.test_name, {})[a.test_name] = score

    return CoverageGapsReport(
        total_upstream_tests=len([p for p in profiles if p.status == "ok"]),
        union_coverage_pct=union_pct,
        gap_branches=gap_branches,
        gap_functions=gap_functions,
        per_test_unique_coverage=per_test_unique,
        coverage_overlap_matrix=overlap,
    )


def _relativise(filename: str, upstream_root: Path) -> str:
    """Strip the upstream_root prefix so stored paths are project-relative."""
    try:
        return str(Path(filename).resolve().relative_to(upstream_root.resolve()))
    except ValueError:
        return filename


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--dataset-root", type=Path, default=REPO_ROOT / "dataset" / "dataset")
    parser.add_argument("--upstream-root", type=Path, default=None)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--condition-model", default="gpt-4o-2024-08-06")
    args = parser.parse_args()

    upstream_root = args.upstream_root or REPO_ROOT / "dataset" / "targets" / "src" / args.target / "upstream"
    llm_client = None
    if not args.no_llm:
        try:
            from core.llm_client import LLMClient
            llm_client = LLMClient()
        except Exception as exc:
            logger.warning("LLM client unavailable, using heuristic", extra={"error": str(exc)})

    report = compute_report(
        args.target,
        dataset_root=args.dataset_root,
        upstream_root=upstream_root,
        llm_client=llm_client,
        condition_model=args.condition_model,
    )
    out_path = args.dataset_root / args.target / "coverage_gaps.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report.model_dump_json(indent=2))
    print(f"wrote {out_path} (union_coverage_pct={report.union_coverage_pct:.2f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
