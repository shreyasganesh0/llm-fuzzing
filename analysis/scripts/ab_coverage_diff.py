"""Diff exp1 vs exp2 seed-corpus coverage on a single target binary.

Reads two CoverageProfile JSONs produced by measure_coverage.py (with
`--profile-out`), computes set-wise line and branch differentials, and
writes a concise markdown report.

The two profiles must come from the same binary. Lines/edges are identified
as (file, line) and (file, line, true|false) tuples respectively, so
incidental path differences don't confuse the diff.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.dataset_schema import CoverageProfile  # noqa: E402


def _line_set(profile: CoverageProfile) -> set[tuple[str, int]]:
    return {
        (file, line)
        for file, fc in profile.files.items()
        for line in fc.lines_covered
    }


def _edge_set(profile: CoverageProfile) -> set[tuple[str, int, str]]:
    edges: set[tuple[str, int, str]] = set()
    for file, fc in profile.files.items():
        for key, br in fc.branches.items():
            _, _, line_str = key.rpartition(":")
            try:
                line = int(line_str)
            except ValueError:
                continue
            if br.true_taken:
                edges.add((file, line, "true"))
            if br.false_taken:
                edges.add((file, line, "false"))
    return edges


def _count_by_file(items: set, idx: int = 0) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for t in items:
        counts[t[idx]] += 1
    return dict(counts)


def diff(
    exp1: CoverageProfile,
    exp2: CoverageProfile,
) -> dict:
    exp1_lines, exp2_lines = _line_set(exp1), _line_set(exp2)
    exp1_edges, exp2_edges = _edge_set(exp1), _edge_set(exp2)

    line_union = exp1_lines | exp2_lines
    line_inter = exp1_lines & exp2_lines
    edges_union = exp1_edges | exp2_edges
    edges_inter = exp1_edges & exp2_edges

    exp1_only_edges = exp1_edges - exp2_edges
    exp2_only_edges = exp2_edges - exp1_edges

    return {
        "exp1": {
            "lines": len(exp1_lines),
            "edges": len(exp1_edges),
        },
        "exp2": {
            "lines": len(exp2_lines),
            "edges": len(exp2_edges),
        },
        "union": {
            "lines": len(line_union),
            "edges": len(edges_union),
        },
        "intersection": {
            "lines": len(line_inter),
            "edges": len(edges_inter),
        },
        "exp1_only_edges": len(exp1_only_edges),
        "exp2_only_edges": len(exp2_only_edges),
        "exp1_only_by_file": sorted(
            _count_by_file(exp1_only_edges).items(), key=lambda kv: -kv[1]
        )[:15],
        "exp2_only_by_file": sorted(
            _count_by_file(exp2_only_edges).items(), key=lambda kv: -kv[1]
        )[:15],
        "jaccard_lines": round(len(line_inter) / len(line_union), 4) if line_union else 0.0,
        "jaccard_edges": round(len(edges_inter) / len(edges_union), 4) if edges_union else 0.0,
    }


def _shorten(path: str, upstream_root: str) -> str:
    if upstream_root and path.startswith(upstream_root):
        return path[len(upstream_root) :].lstrip("/")
    return path


def render_markdown(result: dict, meta: dict, upstream_root: str = "") -> str:
    lines = ["# A/B coverage differential — exp1 (gap-targeted) vs exp2 (source-only)", ""]
    if meta:
        lines.append("## Setup")
        for k, v in meta.items():
            lines.append(f"- **{k}**: {v}")
        lines.append("")
    lines.append("## Headline numbers")
    lines.append("| metric | exp1 | exp2 | union | intersection |")
    lines.append("|---|---:|---:|---:|---:|")
    lines.append(
        f"| lines | {result['exp1']['lines']} | {result['exp2']['lines']} | "
        f"{result['union']['lines']} | {result['intersection']['lines']} |"
    )
    lines.append(
        f"| edges | {result['exp1']['edges']} | {result['exp2']['edges']} | "
        f"{result['union']['edges']} | {result['intersection']['edges']} |"
    )
    lines.append("")
    lines.append(f"- edges ONLY in exp1: **{result['exp1_only_edges']}**")
    lines.append(f"- edges ONLY in exp2: **{result['exp2_only_edges']}**")
    lines.append(f"- Jaccard (edges): {result['jaccard_edges']}")
    lines.append(f"- Jaccard (lines): {result['jaccard_lines']}")
    lines.append("")
    lines.append("## Top files exp1 reaches that exp2 misses")
    if result["exp1_only_by_file"]:
        lines.append("| file | unique edges |")
        lines.append("|---|---:|")
        for f, n in result["exp1_only_by_file"]:
            lines.append(f"| `{_shorten(f, upstream_root)}` | {n} |")
    else:
        lines.append("_(none)_")
    lines.append("")
    lines.append("## Top files exp2 reaches that exp1 misses")
    if result["exp2_only_by_file"]:
        lines.append("| file | unique edges |")
        lines.append("|---|---:|")
        for f, n in result["exp2_only_by_file"]:
            lines.append(f"| `{_shorten(f, upstream_root)}` | {n} |")
    else:
        lines.append("_(none)_")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exp1-profile", type=Path, required=True)
    parser.add_argument("--exp2-profile", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--upstream-root", default="")
    parser.add_argument("--exp1-seed-count", type=int, default=0)
    parser.add_argument("--exp2-seed-count", type=int, default=0)
    parser.add_argument("--model", default="")
    parser.add_argument("--target", default="")
    args = parser.parse_args()

    exp1 = CoverageProfile.model_validate_json(args.exp1_profile.read_text())
    exp2 = CoverageProfile.model_validate_json(args.exp2_profile.read_text())
    result = diff(exp1, exp2)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "ab_coverage_diff.json").write_text(json.dumps(result, indent=2))

    meta = {
        "target": args.target,
        "model": args.model,
        "exp1_seeds": args.exp1_seed_count,
        "exp2_seeds": args.exp2_seed_count,
    }
    md = render_markdown(result, meta, args.upstream_root)
    (args.out_dir / "ab_coverage_diff.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
