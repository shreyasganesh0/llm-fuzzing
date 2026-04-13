"""Measure coverage of a seed corpus using the coverage build.

Runs the coverage-instrumented binary on each seed, merges profraw files,
exports JSON, and reports the number of unique edges hit. Used to produce
an apples-to-apples "coverage-of-seeds" number independent of 23h campaigns.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.coverage_utils import parse_llvm_cov_json
from core.logging_config import get_logger

logger = get_logger("utcf.phase3.coverage")


def measure_seeds(
    binary: Path,
    seeds_dir: Path,
    *,
    source_roots: list[str] | None = None,
    llvm_profdata: str = "llvm-profdata",
    llvm_cov: str = "llvm-cov",
    timeout_s: int = 10,
    profile_out: Path | None = None,
) -> dict:
    if not binary.is_file():
        raise FileNotFoundError(binary)
    if not seeds_dir.is_dir():
        raise FileNotFoundError(seeds_dir)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        profraw_glob = str(tmp / "seed_%m.profraw")
        for seed in sorted(seeds_dir.iterdir()):
            if not seed.is_file():
                continue
            env = os.environ.copy()
            env["LLVM_PROFILE_FILE"] = profraw_glob
            try:
                subprocess.run(
                    [str(binary), str(seed)],
                    capture_output=True,
                    timeout=timeout_s,
                    check=False,
                    env=env,
                )
            except subprocess.TimeoutExpired:
                continue

        raws = list(tmp.glob("seed_*.profraw"))
        if not raws:
            return {"edges": 0, "files": 0}

        merged = tmp / "merged.profdata"
        subprocess.run(
            [llvm_profdata, "merge", "-sparse", *[str(r) for r in raws], "-o", str(merged)],
            check=True,
        )
        export_json = tmp / "cov.json"
        with open(export_json, "w") as out:
            subprocess.run(
                [llvm_cov, "export", str(binary), f"-instr-profile={merged}",
                 "--skip-expansions"],
                check=True, stdout=out,
            )

        profile = parse_llvm_cov_json(
            export_json,
            test_name="__seed_corpus__",
            upstream_file="",
            upstream_line=1,
            framework="seed",
            source_roots=source_roots,
        )
        total_edges = sum(2 * len(fc.branches) for fc in profile.files.values())
        covered = sum(
            int(b.true_taken) + int(b.false_taken)
            for fc in profile.files.values()
            for b in fc.branches.values()
        )
        if profile_out is not None:
            profile_out.parent.mkdir(parents=True, exist_ok=True)
            profile_out.write_text(profile.model_dump_json(indent=2))
        return {
            "edges_covered": covered,
            "edges_total": total_edges,
            "files": len(profile.files),
            "lines_covered": profile.total_lines_covered,
            "lines_total": profile.total_lines_in_source,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--seeds-dir", type=Path, required=True)
    parser.add_argument("--source-roots", nargs="*", default=None)
    parser.add_argument("--timeout-s", type=int, default=10)
    parser.add_argument("--profile-out", type=Path, default=None)
    parser.add_argument("--profdata-bin", default=os.environ.get("LLVM_PROFDATA", "llvm-profdata-15"))
    parser.add_argument("--cov-bin", default=os.environ.get("LLVM_COV", "llvm-cov-15"))
    args = parser.parse_args()

    metrics = measure_seeds(
        args.binary, args.seeds_dir,
        source_roots=args.source_roots,
        timeout_s=args.timeout_s,
        profile_out=args.profile_out,
        llvm_profdata=args.profdata_bin,
        llvm_cov=args.cov_bin,
    )
    import json
    print(json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
