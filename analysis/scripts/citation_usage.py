"""Audit how often LLM-cited gaps (`target_gaps[]`) actually get hit.

Two passes:
  1. **Aggregate** — union all `target_gaps` across every sample_*.json in a
     cell directory, drop `:line` suffix, and check which cited files appear
     in the cell's coverage profile (a covered file = at least one line hit).
     Reports `hit_rate = cited_hit / (cited_hit + cited_miss)`.
  2. **Per-seed (optional)** — for the `exp1_full` cell, re-measure each seed
     alone via `measure_coverage.py` and check whether its individual
     `target_gaps[]` land in its individual coverage set. Writes a histogram.

The aggregate pass is cheap (milliseconds). The per-seed pass shells out to
the instrumented binary once per seed (~15 s each × ~20-40 seeds ≈ 10 min),
so it is gated behind `--per-seed-cell`.

Usage:
    python -m analysis.scripts.citation_usage \\
        --cell exp1_full=samples_dir=coverage.json \\
        --cell exp2_source=samples_dir=coverage.json \\
        --out-dir dataset/fixtures/re2_ab/claude_sonnet_results/citation_audit
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.dataset_schema import CoverageProfile


def normalize_gap(gap: str) -> str:
    """`re2/parse.cc:100` -> `re2/parse.cc`; leaves non-line-suffixed values alone."""
    base, _, rest = gap.rpartition(":")
    if base and rest.isdigit():
        return base
    return gap


def covered_file_suffixes(profile: CoverageProfile) -> set[str]:
    """Return `{parent/basename}` for every file with ≥1 line covered.

    The profile stores absolute paths, gap citations use `re2/parse.cc`-style
    suffixes, so we compare on the last two path components.
    """
    out: set[str] = set()
    for path, fc in profile.files.items():
        if not fc.lines_covered:
            continue
        parts = path.split("/")
        if len(parts) >= 2:
            out.add("/".join(parts[-2:]))
        else:
            out.add(path)
    return out


def citation_hit(cited: str, covered_suffixes: set[str]) -> bool:
    """A cited file is a hit if its `parent/basename` appears in the covered set."""
    if cited in covered_suffixes:
        return True
    parts = cited.split("/")
    if len(parts) >= 2:
        return "/".join(parts[-2:]) in covered_suffixes
    return False


def aggregate_cell(samples_dir: Path, coverage_profile: CoverageProfile) -> dict:
    """Aggregate pass: union all gap citations, report hit-rate vs cell coverage."""
    cited_counter: Counter[str] = Counter()
    total_seeds = 0
    for sample_path in sorted(samples_dir.glob("sample_*.json")):
        data = json.loads(sample_path.read_text())
        for inp in data.get("inputs", []):
            total_seeds += 1
            for gap in inp.get("target_gaps", []):
                cited_counter[normalize_gap(gap)] += 1

    covered = covered_file_suffixes(coverage_profile)
    hit, miss = 0, 0
    hit_files: list[str] = []
    miss_files: list[str] = []
    for f, count in cited_counter.items():
        if citation_hit(f, covered):
            hit += count
            hit_files.append(f)
        else:
            miss += count
            miss_files.append(f)

    total = hit + miss
    return {
        "seeds": total_seeds,
        "unique_cited_files": len(cited_counter),
        "citation_count_total": total,
        "citation_hit": hit,
        "citation_miss": miss,
        "hit_rate": (hit / total) if total else 0.0,
        "hit_files": sorted(hit_files),
        "miss_files": sorted(miss_files),
    }


def per_seed_audit(
    samples_dir: Path,
    *,
    binary: Path,
    source_roots: list[str],
    work_dir: Path,
) -> list[dict]:
    """Per-seed pass: one-shot `measure_coverage` per seed; did its cited files hit?

    Returns a list of `{seed_id, cited, hit_files, miss_files, hit_count, total}`.
    """
    results: list[dict] = []
    work_dir.mkdir(parents=True, exist_ok=True)

    for sample_path in sorted(samples_dir.glob("sample_*.json")):
        data = json.loads(sample_path.read_text())
        for inp in data.get("inputs", []):
            seed_id = inp["input_id"]
            cited = [normalize_gap(g) for g in inp.get("target_gaps", [])]
            seed_bin = samples_dir / f"{seed_id}.bin"
            if not seed_bin.is_file():
                continue

            seed_work = work_dir / seed_id
            seed_work.mkdir(parents=True, exist_ok=True)
            shutil.copy(seed_bin, seed_work / seed_bin.name)
            profile_path = work_dir / f"{seed_id}.json"

            cmd = [
                sys.executable, "-m", "synthesis.scripts.measure_coverage",
                "--binary", str(binary),
                "--seeds-dir", str(seed_work),
                "--profile-out", str(profile_path),
            ]
            for root in source_roots:
                cmd += ["--source-roots", root]
            subprocess.run(cmd, check=True, capture_output=True, cwd=REPO_ROOT)

            profile = CoverageProfile.model_validate_json(profile_path.read_text())
            covered = covered_file_suffixes(profile)

            hits = [c for c in cited if citation_hit(c, covered)]
            misses = [c for c in cited if not citation_hit(c, covered)]
            results.append({
                "seed_id": seed_id,
                "cited": cited,
                "hit_files": hits,
                "miss_files": misses,
                "hit_count": len(hits),
                "total": len(cited),
                "hit_rate": (len(hits) / len(cited)) if cited else 0.0,
            })

    return results


def _parse_cell_spec(spec: str) -> tuple[str, Path, Path]:
    name, _, rest = spec.partition("=")
    samples_str, _, coverage_str = rest.partition("=")
    if not (name and samples_str and coverage_str):
        raise SystemExit(f"bad --cell spec: {spec!r} (need name=samples_dir=coverage.json)")
    return name, Path(samples_str), Path(coverage_str)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cell",
        action="append",
        required=True,
        metavar="name=samples_dir=coverage.json",
        help="repeatable; bind each cell to its sample directory and coverage profile",
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--per-seed-cell", default=None,
                        help="cell name to run per-seed audit on (e.g. exp1_full)")
    parser.add_argument("--per-seed-binary", type=Path, default=None,
                        help="seed_replay binary for per-seed measure_coverage")
    parser.add_argument("--per-seed-source-root", action="append", default=[],
                        help="repeatable --source-roots for measure_coverage")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    aggregate: dict[str, dict] = {}
    for spec in args.cell:
        name, samples_dir, coverage_json = _parse_cell_spec(spec)
        profile = CoverageProfile.model_validate_json(coverage_json.read_text())
        aggregate[name] = aggregate_cell(samples_dir, profile)

    (args.out_dir / "aggregate.json").write_text(json.dumps(aggregate, indent=2))

    lines = ["# Citation-usage audit — aggregate", ""]
    lines.append("| cell | seeds | unique cited | citations | hit | miss | hit-rate |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for name, row in aggregate.items():
        lines.append(
            f"| `{name}` | {row['seeds']} | {row['unique_cited_files']} | "
            f"{row['citation_count_total']} | {row['citation_hit']} | "
            f"{row['citation_miss']} | {row['hit_rate']:.3f} |"
        )
    lines.append("")

    if args.per_seed_cell:
        cell_spec = next((s for s in args.cell if s.startswith(f"{args.per_seed_cell}=")), None)
        if not cell_spec:
            raise SystemExit(f"--per-seed-cell {args.per_seed_cell!r} not in --cell list")
        if not args.per_seed_binary:
            raise SystemExit("--per-seed-cell requires --per-seed-binary")
        _, samples_dir, _ = _parse_cell_spec(cell_spec)

        with tempfile.TemporaryDirectory(prefix="citation_per_seed_") as tmp:
            per_seed = per_seed_audit(
                samples_dir,
                binary=args.per_seed_binary,
                source_roots=args.per_seed_source_root,
                work_dir=Path(tmp),
            )
        (args.out_dir / f"per_seed_{args.per_seed_cell}.json").write_text(
            json.dumps(per_seed, indent=2)
        )
        if per_seed:
            rates = [row["hit_rate"] for row in per_seed]
            rates.sort()
            median = rates[len(rates) // 2]
            mean = sum(rates) / len(rates)
            lines.append(f"## Per-seed audit — `{args.per_seed_cell}`")
            lines.append(f"- seeds: **{len(per_seed)}**")
            lines.append(f"- mean hit-rate: **{mean:.3f}**")
            lines.append(f"- median hit-rate: **{median:.3f}**")
            lines.append("")

    (args.out_dir / "summary.md").write_text("\n".join(lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
