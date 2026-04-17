"""Harfbuzz 5-variant × 3-model ablation experiment driver.

Phases (each independent, selectable with --phase):
  prep      — build prepped dataset dir with shown-30 gaps from m2_target_branches.json
  synthesis — 15 cells (5 variants × 3 models), 150 seeds each with retry loop
  m1        — replay all cells + random anchor for total-edge metric
  m2        — replay all cells + random anchor for hard-branch hit metric
  random    — generate 150 random binary blobs (harfbuzz anchor)
  all       — run prep, random, synthesis, m1, m2 in order

Seed normalization: after synthesis, each cell is guaranteed to have exactly
TARGET_SEEDS seeds. If synthesis yields fewer, additional samples are run.
If it yields more, seeds are subsampled deterministically.

Outputs land under results/ablation_harfbuzz/.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import (
    HB_FIXTURES_DIR,
    HB_M2_TARGETS_PATH,
    HB_UPSTREAM_UNION_PROFILE_PATH,
    SOURCE_TOKEN_BUDGET_ALL_MODELS,
)
from core.logging_config import get_logger

logger = get_logger("utcf.ablation.harfbuzz")

PY = sys.executable

TARGET = "harfbuzz"
TARGET_SEEDS = 150  # exact seed count per cell

VARIANTS = {
    "v0_none":      {"include_source": False, "include_tests": False, "include_gaps": False},
    "v1_src":       {"include_source": True,  "include_tests": False, "include_gaps": False},
    "v2_src_tests": {"include_source": True,  "include_tests": True,  "include_gaps": False},
    "v3_all":       {"include_source": True,  "include_tests": True,  "include_gaps": True},
    "v4_src_gaps":  {"include_source": True,  "include_tests": False, "include_gaps": True},
}

MODELS = [
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
    "llama-3.1-8b-instruct",
    "llama-3.1-70b-instruct",
    "codestral-22b",
]

# Existing harfbuzz data (tests + metadata)
ORIG_DATASET_ROOT = REPO_ROOT / "dataset/data"

RESULTS_ROOT = REPO_ROOT / "results/ablation_harfbuzz"
SYNTHESIS_RESULTS_ROOT = REPO_ROOT / "synthesis/results/ablation_harfbuzz"
PREPPED_DATASET_ROOT = REPO_ROOT / "dataset/fixtures/_ablation_hb_dataset"

HB_COVERAGE_BINARY = REPO_ROOT / "dataset/targets/src/harfbuzz/build/coverage/seed_replay"
HB_SOURCE_ROOTS = REPO_ROOT / "dataset/targets/src/harfbuzz/upstream/src"
RANDOM_SEEDS_DIR = SYNTHESIS_RESULTS_ROOT / "seeds" / TARGET / "random"

CLAUDE_KEY_PATH = REPO_ROOT / "secrets/claude_key"
LITELLM_URL = "https://api.ai.it.ufl.edu"

# How many seeds to request per synthesis call.
# Most models: 3 inputs per call.
# llama-3.1-70b-instruct: UF endpoint hard-caps responses at 2048 chars; requesting 3 blobs
# causes the JSON to be truncated. Use 1 input per call for that model.
INPUTS_PER_CALL = 3
INPUTS_PER_CALL_SMALL = 1   # for models with tight output limits
SAMPLES_PER_CALL = 1

SMALL_OUTPUT_MODELS = {"llama-3.1-70b-instruct"}


def _env_for_model(model: str) -> dict[str, str]:
    env = os.environ.copy()
    if model.startswith("claude-"):
        env["UTCF_ANTHROPIC_KEY_PATH"] = str(CLAUDE_KEY_PATH)
        env.pop("UTCF_LITELLM_URL", None)
    else:
        env["UTCF_LITELLM_URL"] = LITELLM_URL
        env.pop("UTCF_ANTHROPIC_KEY_PATH", None)
    return env


def _safe_model(model: str) -> str:
    return model.replace("/", "_")


def cell_seeds_dir(variant: str, model: str) -> Path:
    return SYNTHESIS_RESULTS_ROOT / "seeds" / TARGET / "ablation" / variant / _safe_model(model)


def cell_m1_dir(variant: str, model: str) -> Path:
    return RESULTS_ROOT / "m1" / variant / _safe_model(model)


def cell_m2_dir(variant: str, model: str) -> Path:
    return RESULTS_ROOT / "m2" / variant / _safe_model(model)


def _count_seeds(seeds_dir: Path) -> int:
    if not seeds_dir.is_dir():
        return 0
    return sum(1 for p in seeds_dir.iterdir() if p.is_file() and p.suffix == ".bin")


def _subsample_seeds(seeds_dir: Path, target_count: int, rng_seed: int = 42) -> None:
    """If seeds_dir has more than target_count seeds, remove the excess (deterministic)."""
    seed_files = sorted(p for p in seeds_dir.iterdir() if p.is_file() and p.suffix == ".bin")
    if len(seed_files) <= target_count:
        return
    rng = random.Random(rng_seed)
    keep = set(p.name for p in rng.sample(seed_files, k=target_count))
    for p in seed_files:
        if p.name not in keep:
            p.unlink()
    logger.info("subsampled seeds", extra={
        "seeds_dir": str(seeds_dir), "kept": target_count, "removed": len(seed_files) - target_count
    })


# ─── Phase 1: prep ────────────────────────────────────────────────────────────

def phase_prep() -> None:
    """Build prepped dataset with the 30 shown harfbuzz gap branches."""
    if not HB_M2_TARGETS_PATH.exists():
        raise FileNotFoundError(
            f"Harfbuzz M2 targets not found at {HB_M2_TARGETS_PATH}. "
            "Run freeze first: python -m analysis.scripts.freeze_target_branches --target harfbuzz"
        )

    targets = json.loads(HB_M2_TARGETS_PATH.read_text())
    shown = targets["shown"]

    target_hb = PREPPED_DATASET_ROOT / TARGET
    target_hb.mkdir(parents=True, exist_ok=True)

    # Copy tests.json and metadata.json from original data
    for fn in ("tests.json", "metadata.json"):
        src = ORIG_DATASET_ROOT / TARGET / fn
        dst = target_hb / fn
        if src.is_file() and not dst.is_file():
            shutil.copy2(src, dst)

    # Build coverage_gaps.json from frozen shown targets (includes uncovered_side)
    new_gaps = {
        "total_upstream_tests": targets.get("n_all_candidates", 0),
        "union_coverage_pct": 0.0,  # harfbuzz baseline covers very little
        "gap_branches": [
            {
                "file": s["file"],
                "line": s["line"],
                "code_context": s["code_context"],
                "condition_description": s["condition_description"],
                "uncovered_side": s.get("uncovered_side", "unknown"),
                "reachability_score": None,
            }
            for s in shown
        ],
        "gap_functions": [],
        "per_test_unique_coverage": {},
        "coverage_overlap_matrix": {},
    }
    (target_hb / "coverage_gaps.json").write_text(json.dumps(new_gaps, indent=2))
    logger.info("prep done", extra={
        "dataset_root": str(PREPPED_DATASET_ROOT),
        "n_gaps_in_prompt": len(shown),
    })


# ─── Phase 2: synthesis with 150-seed normalization ──────────────────────────

def _run_synthesis_batch(
    variant: str, model: str, flags: dict,
    sample_offset: int,
) -> int:
    """Run one synthesis call for (variant, model). Return number of seeds produced."""
    cmd = [
        PY, "-m", "synthesis.scripts.generate_ablation_inputs",
        "--target", TARGET,
        "--model", model,
        "--cell", variant,
        "--dataset-root", str(PREPPED_DATASET_ROOT),
        "--results-root", str(SYNTHESIS_RESULTS_ROOT),
        "--samples", str(SAMPLES_PER_CALL),
        "--num-inputs", str(INPUTS_PER_CALL_SMALL if model in SMALL_OUTPUT_MODELS else INPUTS_PER_CALL),
        "--max-gaps", "30",
        "--source-token-budget", str(SOURCE_TOKEN_BUDGET_ALL_MODELS),
        "--input-format", "binary",
        "--max-tokens", "8192",  # model ignores 512-byte blob limit; 8192 tokens fits 3 full OTF fonts
        "--run-id", str(sample_offset),  # unique per attempt → breaks cache for retries
    ]
    if flags["include_tests"]:
        cmd.append("--include-tests")
    if flags["include_gaps"]:
        cmd.append("--include-gaps")
    if flags["include_source"]:
        cmd.append("--include-source")

    r = subprocess.run(cmd, capture_output=True, text=True, env=_env_for_model(model))
    if r.returncode != 0:
        raise RuntimeError(
            f"synthesis failed for {variant}/{model} (offset={sample_offset}):\n"
            f"{r.stderr[-2000:]}"
        )
    seeds_dir = cell_seeds_dir(variant, model)
    return _count_seeds(seeds_dir)


def phase_synthesis(*, skip_existing: bool = False, attempt_offset: int = 0) -> None:
    """Run 15 ablation cells, retrying until each has exactly TARGET_SEEDS seeds."""
    for variant, flags in VARIANTS.items():
        for model in MODELS:
            seeds_dir = cell_seeds_dir(variant, model)

            if skip_existing and _count_seeds(seeds_dir) >= TARGET_SEEDS:
                logger.info("skip synthesis (already has enough seeds)",
                            extra={"variant": variant, "model": model,
                                   "n_seeds": _count_seeds(seeds_dir)})
                _subsample_seeds(seeds_dir, TARGET_SEEDS)
                continue

            seeds_dir.mkdir(parents=True, exist_ok=True)
            attempt = 0
            MAX_ATTEMPTS = 300  # hard cap; cells that can't reach TARGET_SEEDS are skipped
            while _count_seeds(seeds_dir) < TARGET_SEEDS and attempt < MAX_ATTEMPTS:
                attempt += 1
                current = _count_seeds(seeds_dir)
                logger.info("synthesis batch", extra={
                    "variant": variant, "model": model,
                    "attempt": attempt, "current_seeds": current,
                    "target": TARGET_SEEDS,
                })
                try:
                    _run_synthesis_batch(variant, model, flags, sample_offset=attempt + attempt_offset)
                except RuntimeError as e:
                    logger.error("synthesis batch failed", extra={
                        "variant": variant, "model": model, "error": str(e)[:500]
                    })
                    if attempt > 100:
                        raise RuntimeError(
                            f"Synthesis for {variant}/{model} failed after 100 attempts "
                            f"(only {_count_seeds(seeds_dir)} seeds)"
                        ) from e

            final_count = _count_seeds(seeds_dir)
            if final_count < TARGET_SEEDS:
                logger.warning("synthesis capped: cell skipped (too many parse failures)",
                               extra={"variant": variant, "model": model,
                                      "n_seeds": final_count, "n_attempts": attempt,
                                      "max_attempts": MAX_ATTEMPTS})
                continue  # don't subsample or assert; leave partial seeds for inspection

            # Subsample to exactly TARGET_SEEDS
            _subsample_seeds(seeds_dir, TARGET_SEEDS)
            final_count = _count_seeds(seeds_dir)
            logger.info("synthesis done", extra={
                "variant": variant, "model": model,
                "n_seeds": final_count, "n_attempts": attempt,
            })
            assert final_count == TARGET_SEEDS, f"Expected {TARGET_SEEDS} seeds, got {final_count}"


# ─── Phase 3: random anchor ───────────────────────────────────────────────────

def phase_random(*, skip_existing: bool = False) -> None:
    if skip_existing and _count_seeds(RANDOM_SEEDS_DIR) >= TARGET_SEEDS:
        logger.info("skip random (exists)")
        _subsample_seeds(RANDOM_SEEDS_DIR, TARGET_SEEDS)
        return

    RANDOM_SEEDS_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        PY, "-m", "synthesis.scripts.generate_random_inputs",
        "--target", TARGET,
        "--count", str(TARGET_SEEDS),
        "--seed", "42",
        "--results-root", str(SYNTHESIS_RESULTS_ROOT),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"random anchor failed: {r.stderr[-2000:]}")
    _subsample_seeds(RANDOM_SEEDS_DIR, TARGET_SEEDS)
    logger.info("random anchor done", extra={
        "dir": str(RANDOM_SEEDS_DIR), "n_seeds": _count_seeds(RANDOM_SEEDS_DIR)
    })


# ─── Phase 4: M1 ─────────────────────────────────────────────────────────────

def _run_m1_one(seeds_dir: Path, label: str, out_path: Path) -> dict:
    cmd = [
        PY, "-m", "synthesis.scripts.measure_coverage",
        "--binary", str(HB_COVERAGE_BINARY),
        "--seeds-dir", str(seeds_dir),
        "--source-roots", str(HB_SOURCE_ROOTS),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"M1 failed for {label}: {r.stderr[-2000:]}")
    metrics = json.loads(r.stdout)
    metrics["seeds_dir"] = str(seeds_dir)
    metrics["label"] = label
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(metrics, indent=2))
    return metrics


def phase_m1(*, skip_existing: bool = False) -> None:
    for variant in VARIANTS:
        for model in MODELS:
            seeds_dir = cell_seeds_dir(variant, model)
            out_path = cell_m1_dir(variant, model) / "summary.json"
            if not seeds_dir.is_dir() or _count_seeds(seeds_dir) == 0:
                logger.warning("M1 skip: no seeds", extra={"variant": variant, "model": model})
                continue
            if skip_existing and out_path.is_file():
                continue
            logger.info("M1 start", extra={"variant": variant, "model": model})
            _run_m1_one(seeds_dir, f"{variant}/{model}", out_path)

    # Random anchor
    if _count_seeds(RANDOM_SEEDS_DIR) > 0:
        out_path = RESULTS_ROOT / "m1" / "random" / "summary.json"
        if not (skip_existing and out_path.is_file()):
            _run_m1_one(RANDOM_SEEDS_DIR, "random", out_path)


# ─── Phase 5: M2 ─────────────────────────────────────────────────────────────

def _run_m2_one(seeds_dir: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        PY, "-m", "analysis.scripts.measure_gap_coverage",
        "--seeds-dir", str(seeds_dir),
        "--out-dir", str(out_dir),
        "--binary", str(HB_COVERAGE_BINARY),
        "--targets-path", str(HB_M2_TARGETS_PATH),
        "--baseline-profile", str(HB_UPSTREAM_UNION_PROFILE_PATH),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"M2 failed for {seeds_dir}: {r.stderr[-2000:]}")
    return json.loads((out_dir / "summary.json").read_text())


def phase_m2(*, skip_existing: bool = False) -> None:
    for variant in VARIANTS:
        for model in MODELS:
            seeds_dir = cell_seeds_dir(variant, model)
            out_dir = cell_m2_dir(variant, model)
            if not seeds_dir.is_dir() or _count_seeds(seeds_dir) == 0:
                logger.warning("M2 skip: no seeds", extra={"variant": variant, "model": model})
                continue
            if skip_existing and (out_dir / "summary.json").is_file():
                continue
            logger.info("M2 start", extra={"variant": variant, "model": model})
            _run_m2_one(seeds_dir, out_dir)

    # Random anchor
    if _count_seeds(RANDOM_SEEDS_DIR) > 0:
        out_dir = RESULTS_ROOT / "m2" / "random"
        if not (skip_existing and (out_dir / "summary.json").is_file()):
            _run_m2_one(RANDOM_SEEDS_DIR, out_dir)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        choices=["prep", "synthesis", "m1", "m2", "random", "all"],
        default="all",
    )
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument(
        "--only-models", nargs="+", default=None,
        metavar="MODEL",
        help="restrict synthesis/m1/m2 to these models (e.g. --only-models llama-3.1-8b-instruct)",
    )
    parser.add_argument(
        "--attempt-offset", type=int, default=0,
        metavar="N",
        help="add N to every attempt's run_id to avoid cache collisions when restarting "
             "(e.g. --attempt-offset 1000 starts run_ids from 1001)",
    )
    args = parser.parse_args()

    # Apply model filter globally if requested
    if args.only_models:
        invalid = set(args.only_models) - set(MODELS)
        if invalid:
            print(f"ERROR: unknown models: {invalid}. Valid: {MODELS}", file=sys.stderr)
            return 1
        MODELS[:] = args.only_models
        logger.info("model filter applied", extra={"models": MODELS})

    if args.phase in ("prep", "all"):
        phase_prep()
    if args.phase in ("random", "all"):
        phase_random(skip_existing=args.skip_existing)
    if args.phase in ("synthesis", "all"):
        phase_synthesis(skip_existing=args.skip_existing, attempt_offset=args.attempt_offset)
    if args.phase in ("m1", "all"):
        phase_m1(skip_existing=args.skip_existing)
    if args.phase in ("m2", "all"):
        phase_m2(skip_existing=args.skip_existing)

    print(f"phase={args.phase} done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
