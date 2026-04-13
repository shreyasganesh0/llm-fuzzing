"""Parse `llvm-cov export --format=json` output and derive per-test coverage.

`llvm-cov export` emits a nested schema with per-file segments and per-function
regions. We consume the "export JSON v2" layout produced by LLVM 15+. Only the
fields we actually rely on are parsed — the rest is ignored tolerantly.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from core.dataset_schema import (
    BranchCoverage,
    CoverageProfile,
    FileCoverage,
)


def parse_llvm_cov_json(
    json_path: str | Path,
    *,
    test_name: str,
    upstream_file: str,
    upstream_line: int,
    framework: str,
    source_roots: Iterable[str] | None = None,
) -> CoverageProfile:
    """Parse an `llvm-cov export` JSON file into a `CoverageProfile`.

    source_roots: optional iterable of path prefixes; files matching any prefix
    are kept, other files are dropped. This is how we restrict coverage to the
    upstream project sources (and not, say, system headers).
    """
    with open(json_path) as f:
        raw = json.load(f)

    files: dict[str, FileCoverage] = {}
    total_lines_covered = 0
    total_lines_in_source = 0
    total_branches_covered = 0
    total_branches_in_source = 0

    roots = tuple(source_roots or ())

    for export in raw.get("data", []):
        for file_entry in export.get("files", []):
            filename = file_entry.get("filename", "")
            if roots and not any(filename.startswith(r) for r in roots):
                continue

            lines_covered: list[int] = []
            lines_not_covered: list[int] = []
            for segment in file_entry.get("segments", []):
                # segment format: [line, col, count, has_count, is_region_entry]
                if len(segment) < 4 or not segment[3]:
                    continue
                line = int(segment[0])
                count = int(segment[2])
                bucket = lines_covered if count > 0 else lines_not_covered
                if line not in bucket:
                    bucket.append(line)

            branches: dict[str, BranchCoverage] = {}
            for branch in file_entry.get("branches", []):
                # branch: [line_start, col_start, line_end, col_end,
                #          execution_count, false_execution_count,
                #          file_id, expanded_file_id, kind]
                if len(branch) < 6:
                    continue
                line = int(branch[0])
                true_count = int(branch[4])
                false_count = int(branch[5])
                key = f"{filename}:{line}"
                if key in branches:
                    prev = branches[key]
                    branches[key] = BranchCoverage.model_construct(
                        true=prev.true_taken or true_count > 0,
                        false=prev.false_taken or false_count > 0,
                    )
                else:
                    branches[key] = BranchCoverage.model_construct(
                        true=true_count > 0,
                        false=false_count > 0,
                    )
                total_branches_in_source += 1
                if true_count > 0:
                    total_branches_covered += 1
                if false_count > 0:
                    total_branches_covered += 1

            functions_covered: list[str] = []
            functions_not_covered: list[str] = []

            summary = file_entry.get("summary", {})
            line_summary = summary.get("lines", {})
            total_lines_covered += int(line_summary.get("covered", len(lines_covered)))
            total_lines_in_source += int(line_summary.get("count", len(lines_covered) + len(lines_not_covered)))

            files[filename] = FileCoverage(
                lines_covered=sorted(lines_covered),
                lines_not_covered=sorted(lines_not_covered),
                branches=branches,
                functions_covered=functions_covered,
                functions_not_covered=functions_not_covered,
            )

        for func_entry in export.get("functions", []):
            filenames = func_entry.get("filenames") or []
            if not filenames:
                continue
            filename = filenames[0]
            if filename not in files:
                continue
            name = func_entry.get("name", "")
            regions = func_entry.get("regions", [])
            covered = any(int(r[4]) > 0 for r in regions if len(r) >= 5)
            bucket = files[filename].functions_covered if covered else files[filename].functions_not_covered
            if name not in bucket:
                bucket.append(name)

    return CoverageProfile(
        test_name=test_name,
        upstream_file=upstream_file,
        upstream_line=upstream_line,
        framework=framework,
        files=files,
        total_lines_covered=total_lines_covered,
        total_lines_in_source=total_lines_in_source,
        total_branches_covered=total_branches_covered,
        total_branches_in_source=total_branches_in_source,
    )


def union_coverage(profiles: Iterable[CoverageProfile]) -> CoverageProfile:
    """Union coverage across tests. Returns a CoverageProfile with aggregated files."""
    merged_files: dict[str, FileCoverage] = {}

    for profile in profiles:
        for filename, fc in profile.files.items():
            if filename not in merged_files:
                merged_files[filename] = FileCoverage(
                    lines_covered=list(fc.lines_covered),
                    lines_not_covered=list(fc.lines_not_covered),
                    branches={k: BranchCoverage.model_construct(true=v.true_taken, false=v.false_taken) for k, v in fc.branches.items()},
                    functions_covered=list(fc.functions_covered),
                    functions_not_covered=list(fc.functions_not_covered),
                )
                continue

            m = merged_files[filename]
            covered = set(m.lines_covered) | set(fc.lines_covered)
            not_covered = (set(m.lines_not_covered) | set(fc.lines_not_covered)) - covered
            m.lines_covered = sorted(covered)
            m.lines_not_covered = sorted(not_covered)

            for key, br in fc.branches.items():
                if key in m.branches:
                    prev = m.branches[key]
                    m.branches[key] = BranchCoverage.model_construct(
                        true=prev.true_taken or br.true_taken,
                        false=prev.false_taken or br.false_taken,
                    )
                else:
                    m.branches[key] = BranchCoverage.model_construct(
                        true=br.true_taken, false=br.false_taken
                    )

            func_covered = set(m.functions_covered) | set(fc.functions_covered)
            func_not_covered = (set(m.functions_not_covered) | set(fc.functions_not_covered)) - func_covered
            m.functions_covered = sorted(func_covered)
            m.functions_not_covered = sorted(func_not_covered)

    total_lines_covered = sum(len(f.lines_covered) for f in merged_files.values())
    total_lines_in_source = total_lines_covered + sum(len(f.lines_not_covered) for f in merged_files.values())
    total_branches_covered = sum(
        int(br.true_taken) + int(br.false_taken)
        for f in merged_files.values()
        for br in f.branches.values()
    )
    total_branches_in_source = sum(2 * len(f.branches) for f in merged_files.values())

    return CoverageProfile(
        test_name="__union__",
        upstream_file="",
        upstream_line=1,
        framework="union",
        files=merged_files,
        total_lines_covered=total_lines_covered,
        total_lines_in_source=total_lines_in_source,
        total_branches_covered=total_branches_covered,
        total_branches_in_source=total_branches_in_source,
    )


def compute_gaps(union_profile: CoverageProfile) -> list[tuple[str, int]]:
    """Return [(file, line), ...] for every uncovered branch in the union."""
    gaps: list[tuple[str, int]] = []
    for filename, fc in union_profile.files.items():
        for key, br in fc.branches.items():
            if not (br.true_taken and br.false_taken):
                _, _, line_str = key.rpartition(":")
                try:
                    gaps.append((filename, int(line_str)))
                except ValueError:
                    continue
    gaps.sort()
    return gaps


def jaccard(a: CoverageProfile, b: CoverageProfile) -> float:
    """Jaccard similarity over covered (file, line) pairs."""
    set_a = {(f, line) for f, fc in a.files.items() for line in fc.lines_covered}
    set_b = {(f, line) for f, fc in b.files.items() for line in fc.lines_covered}
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)
