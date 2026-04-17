"""M2 measurer: per-seed gap-coverage replay against the frozen target set.

For each seed in a corpus directory:
  1. Run the RE2 coverage-build seed_replay binary with isolated LLVM_PROFILE_FILE.
  2. Merge the single profraw to profdata, export to JSON, parse to a
     CoverageProfile (paths normalized to upstream-relative).
  3. For each of the 50 frozen target branches, decide "hit" iff the seed takes
     a side that the upstream union baseline did NOT take.

Outputs (under <out-dir>):
  - gap_hits.jsonl   one line per (seed_id, target_idx) pair with hit booleans
  - summary.json     aggregate metrics + bootstrap 95% CIs over seeds
  - seed_profiles/   per-seed CoverageProfile (kept for audit; can be deleted)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import (
    BOOTSTRAP_ITERS,
    HB_M2_TARGETS_PATH,
    HB_UPSTREAM_UNION_PROFILE_PATH,
    M2_RNG_SEED,
    M2_TARGETS_PATH,
    UPSTREAM_UNION_PROFILE_PATH,
)
from core.coverage_utils import parse_llvm_cov_json
from core.dataset_schema import CoverageProfile
from core.logging_config import get_logger

# Reuse the path-normalizer from the freeze script.
from analysis.scripts.freeze_target_branches import (
    RE2_SEED_REPLAY,
    _normalize_profile,
    hits_uncovered_side,
)

logger = get_logger("utcf.ablation.measure_gap")

LLVM_PROFDATA = os.environ.get("LLVM_PROFDATA", "llvm-profdata-15")
LLVM_COV = os.environ.get("LLVM_COV", "llvm-cov-15")


@dataclass
class TargetEntry:
    idx: int
    file: str
    line: int
    slice: str           # "shown" or "held_back"
    uncovered_side: str  # "true" or "false" — the specific branch side to hit


def _check_hit(seed_profile: CoverageProfile, target: "TargetEntry") -> bool:
    """Did the seed take the specific uncovered side of target?"""
    if target.file not in seed_profile.files:
        return False
    br = seed_profile.files[target.file].branches.get(f"{target.file}:{target.line}")
    if br is None:
        return False
    if target.uncovered_side == "true":
        return bool(br.true_taken)
    return bool(br.false_taken)


def load_targets(targets_path: Path | None = None) -> list[TargetEntry]:
    path = targets_path or M2_TARGETS_PATH
    raw = json.loads(path.read_text())
    out: list[TargetEntry] = []
    for i, t in enumerate(raw["shown"]):
        out.append(TargetEntry(idx=i, file=t["file"], line=t["line"],
                               slice="shown", uncovered_side=t["uncovered_side"]))
    for j, t in enumerate(raw["held_back"]):
        out.append(TargetEntry(idx=len(raw["shown"]) + j, file=t["file"], line=t["line"],
                               slice="held_back", uncovered_side=t["uncovered_side"]))
    return out


def load_baseline(baseline_path: Path | None = None) -> CoverageProfile:
    path = baseline_path or UPSTREAM_UNION_PROFILE_PATH
    return CoverageProfile.model_validate_json(path.read_text())


def replay_one_seed(seed_path: Path, work_dir: Path, idx: int,
                    binary: Path | None = None) -> CoverageProfile | None:
    binary = binary or RE2_SEED_REPLAY
    profraw = work_dir / f"seed_{idx}.profraw"
    profdata = work_dir / f"seed_{idx}.profdata"
    cov_json = work_dir / f"seed_{idx}.json"

    env = os.environ.copy()
    env["LLVM_PROFILE_FILE"] = str(profraw)
    try:
        subprocess.run(
            [str(binary), str(seed_path)],
            capture_output=True, timeout=15, check=False, env=env,
        )
    except subprocess.TimeoutExpired:
        return None
    if not profraw.exists():
        return None

    subprocess.run(
        [LLVM_PROFDATA, "merge", "-sparse", str(profraw), "-o", str(profdata)],
        check=True, capture_output=True,
    )
    with open(cov_json, "w") as fh:
        subprocess.run(
            [LLVM_COV, "export", str(binary), f"-instr-profile={profdata}",
             "--skip-expansions"],
            check=True, stdout=fh, stderr=subprocess.PIPE,
        )

    profile = parse_llvm_cov_json(
        cov_json,
        test_name=f"seed_{idx}",
        upstream_file="",
        upstream_line=1,
        framework="seed",
    )
    return _normalize_profile(profile)


def compute_hits(seed_profile: CoverageProfile, baseline: CoverageProfile,
                 targets: list[TargetEntry]) -> list[bool]:
    # Use uncovered_side-aware check; baseline arg is kept for API compat but unused.
    return [_check_hit(seed_profile, t) for t in targets]


def bootstrap_ci(values: list[float], iters: int, rng: random.Random) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    n = len(values)
    means = []
    for _ in range(iters):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int(0.025 * iters)]
    hi = means[int(0.975 * iters)]
    return (lo, hi)


def aggregate(hits_matrix: list[list[bool]], targets: list[TargetEntry],
              rng: random.Random, bootstrap_iters: int) -> dict:
    """hits_matrix[seed_idx][target_idx] -> bool. Aggregate over the three slices."""
    if not hits_matrix:
        return {"n_seeds": 0, "slices": {}}

    slices = {
        "all": list(range(len(targets))),
        "shown": [t.idx for t in targets if t.slice == "shown"],
        "held_back": [t.idx for t in targets if t.slice == "held_back"],
    }

    out = {"n_seeds": len(hits_matrix), "n_targets": len(targets), "slices": {}}
    for slice_name, target_idxs in slices.items():
        if not target_idxs:
            continue
        # frac of targets in this slice hit by union of all seeds in cell
        union_hit = sum(
            1 for ti in target_idxs
            if any(hits_matrix[s][ti] for s in range(len(hits_matrix)))
        )
        union_frac = union_hit / len(target_idxs)
        # per-seed: targets-in-slice hit by this seed
        per_seed_hits = [
            sum(1 for ti in target_idxs if hits_matrix[s][ti])
            for s in range(len(hits_matrix))
        ]
        per_seed_frac = [h / len(target_idxs) for h in per_seed_hits]
        any_hit = [1.0 if h > 0 else 0.0 for h in per_seed_hits]

        mean_per_seed = sum(per_seed_frac) / len(per_seed_frac)
        ci_per_seed = bootstrap_ci(per_seed_frac, bootstrap_iters, rng)
        frac_seeds_with_any_hit = sum(any_hit) / len(any_hit)
        ci_any = bootstrap_ci(any_hit, bootstrap_iters, rng)

        out["slices"][slice_name] = {
            "n_targets_in_slice": len(target_idxs),
            "union_targets_hit": union_hit,
            "union_frac_targets_hit": union_frac,
            "mean_targets_hit_per_seed": sum(per_seed_hits) / len(per_seed_hits),
            "mean_frac_per_seed": mean_per_seed,
            "mean_frac_per_seed_ci95": list(ci_per_seed),
            "frac_seeds_with_any_hit": frac_seeds_with_any_hit,
            "frac_seeds_with_any_hit_ci95": list(ci_any),
        }
    return out


def measure_corpus(seeds_dir: Path, out_dir: Path, *,
                   bootstrap_iters: int = BOOTSTRAP_ITERS,
                   keep_profiles: bool = False,
                   binary: Path | None = None,
                   targets_path: Path | None = None,
                   baseline_path: Path | None = None) -> dict:
    targets = load_targets(targets_path)
    baseline = load_baseline(baseline_path)
    seed_paths = sorted(p for p in seeds_dir.iterdir() if p.is_file() and p.suffix == ".bin")
    if not seed_paths:
        raise FileNotFoundError(f"no .bin seeds in {seeds_dir}")

    out_dir.mkdir(parents=True, exist_ok=True)
    hits_jsonl = out_dir / "gap_hits.jsonl"
    profiles_dir = out_dir / "seed_profiles"
    if keep_profiles:
        profiles_dir.mkdir(parents=True, exist_ok=True)

    hits_matrix: list[list[bool]] = []
    seed_ids: list[str] = []
    n_replay_fail = 0
    with tempfile.TemporaryDirectory() as td, open(hits_jsonl, "w") as outfh:
        td_path = Path(td)
        for i, sp in enumerate(seed_paths):
            profile = replay_one_seed(sp, td_path, i, binary=binary)
            if profile is None:
                n_replay_fail += 1
                # zero-row so seed indexing stays stable
                row = [False] * len(targets)
                hits_matrix.append(row)
                seed_ids.append(sp.stem)
                continue
            row = compute_hits(profile, baseline, targets)
            hits_matrix.append(row)
            seed_ids.append(sp.stem)
            if keep_profiles:
                (profiles_dir / f"{sp.stem}.json").write_text(profile.model_dump_json())
            for t, hit in zip(targets, row):
                outfh.write(json.dumps({
                    "seed_id": sp.stem,
                    "target_idx": t.idx,
                    "target_file": t.file,
                    "target_line": t.line,
                    "uncovered_side": t.uncovered_side,
                    "slice": t.slice,
                    "hit": bool(hit),
                }) + "\n")
            # Cleanup the per-seed temp files between iterations to keep disk in check.
            for ext in (".profraw", ".profdata", ".json"):
                p = td_path / f"seed_{i}{ext}"
                if p.exists():
                    p.unlink()

    rng = random.Random(M2_RNG_SEED)
    summary = aggregate(hits_matrix, targets, rng, bootstrap_iters)
    summary["seeds_dir"] = str(seeds_dir)
    summary["out_dir"] = str(out_dir)
    summary["n_replay_failures"] = n_replay_fail
    summary["seed_ids"] = seed_ids
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    logger.info("M2 done", extra={
        "seeds_dir": str(seeds_dir),
        "n_seeds": len(seed_ids),
        "n_replay_failures": n_replay_fail,
    })
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-iters", type=int, default=BOOTSTRAP_ITERS)
    parser.add_argument("--keep-profiles", action="store_true",
                        help="persist per-seed CoverageProfile JSON for audit")
    parser.add_argument("--binary", type=Path, default=None,
                        help="coverage seed_replay binary (default: RE2)")
    parser.add_argument("--targets-path", type=Path, default=None,
                        help="m2_target_branches.json path (default: RE2)")
    parser.add_argument("--baseline-profile", type=Path, default=None,
                        help="upstream_union_profile.json path (default: RE2)")
    args = parser.parse_args()
    summary = measure_corpus(
        args.seeds_dir, args.out_dir,
        bootstrap_iters=args.bootstrap_iters,
        keep_profiles=args.keep_profiles,
        binary=args.binary,
        targets_path=args.targets_path,
        baseline_path=args.baseline_profile,
    )
    print(json.dumps({"slices": summary["slices"], "n_seeds": summary["n_seeds"],
                      "n_replay_failures": summary["n_replay_failures"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
