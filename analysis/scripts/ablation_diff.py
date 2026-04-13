"""N-cell coverage differential for the prompt-ablation experiment.

Reads multiple CoverageProfile JSONs (one per cell), emits:
  - a single headline table (edges / lines per cell)
  - pairwise deltas against a chosen reference cell (default: exp1_full)
  - top-file edge exclusives for each cell vs the reference

Used by Experiment B; replaces the 2-cell ab_coverage_diff.py for the
ablation writeup but does not deprecate it.
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

from core.dataset_schema import CoverageProfile


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


def _count_by_file(items: set) -> list[tuple[str, int]]:
    counts: dict[str, int] = defaultdict(int)
    for t in items:
        counts[t[0]] += 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def _shorten(path: str, upstream_root: str) -> str:
    if upstream_root and path.startswith(upstream_root):
        return path[len(upstream_root):].lstrip("/")
    return path


def render(
    cells: dict[str, tuple[CoverageProfile, int]],
    reference: str,
    upstream_root: str,
) -> str:
    if reference not in cells:
        raise KeyError(f"reference cell not found: {reference}")
    ref_edges = _edge_set(cells[reference][0])

    rows = []
    for name, (profile, seed_count) in cells.items():
        edges = _edge_set(profile)
        lines = _line_set(profile)
        rows.append(
            {
                "cell": name,
                "seeds": seed_count,
                "edges": len(edges),
                "lines": len(lines),
                "edges_vs_ref": len(edges) - len(ref_edges),
                "only_in_cell": len(edges - ref_edges),
                "only_in_ref": len(ref_edges - edges),
            }
        )

    rows.sort(key=lambda r: -r["edges"])

    out = ["# N-cell prompt-ablation coverage differential", ""]
    out.append(f"**Reference cell:** `{reference}` (edges={len(ref_edges)})")
    out.append("")
    out.append("## Headline — one row per cell")
    out.append("| cell | seeds | edges | lines | Δedges vs ref | edges only-in-cell | edges only-in-ref |")
    out.append("|---|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        out.append(
            f"| `{r['cell']}` | {r['seeds']} | {r['edges']} | {r['lines']} | "
            f"{r['edges_vs_ref']:+d} | {r['only_in_cell']} | {r['only_in_ref']} |"
        )
    out.append("")

    out.append(f"## Top files each cell reaches that `{reference}` does not")
    for name, (profile, _) in cells.items():
        if name == reference:
            continue
        edges = _edge_set(profile)
        diff = edges - ref_edges
        by_file = _count_by_file(diff)[:10]
        if not by_file:
            out.append(f"### `{name}`  —  (no exclusive files)")
            out.append("")
            continue
        out.append(f"### `{name}`")
        out.append("| file | unique edges |")
        out.append("|---|---:|")
        for f, n in by_file:
            out.append(f"| `{_shorten(f, upstream_root)}` | {n} |")
        out.append("")

    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cell",
        action="append",
        required=True,
        metavar="name=profile.json[:seed_count]",
        help="repeatable; cell name, path to CoverageProfile JSON, optional :seed_count suffix",
    )
    parser.add_argument("--reference", default="exp1_full", help="reference cell for deltas")
    parser.add_argument("--out", type=Path, required=True, help="output markdown path")
    parser.add_argument("--out-json", type=Path, default=None, help="optional JSON dump of the table rows")
    parser.add_argument("--upstream-root", default="")
    args = parser.parse_args()

    cells: dict[str, tuple[CoverageProfile, int]] = {}
    for spec in args.cell:
        name, _, rest = spec.partition("=")
        if not name or not rest:
            raise SystemExit(f"bad --cell spec: {spec!r}")
        path_str, _, seed_str = rest.rpartition(":")
        if path_str and seed_str.isdigit():
            path = Path(path_str)
            seed_count = int(seed_str)
        else:
            path = Path(rest)
            seed_count = 0
        profile = CoverageProfile.model_validate_json(path.read_text())
        cells[name] = (profile, seed_count)

    md = render(cells, args.reference, args.upstream_root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(md)

    if args.out_json is not None:
        rows = [
            {
                "cell": name,
                "seeds": sc,
                "edges": len(_edge_set(p)),
                "lines": len(_line_set(p)),
            }
            for name, (p, sc) in cells.items()
        ]
        args.out_json.write_text(json.dumps(rows, indent=2))

    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
