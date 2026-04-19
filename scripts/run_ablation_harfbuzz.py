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
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    "llama-3.3-70b-instruct",
    "codestral-22b",
    "nemotron-3-super-120b-a12b",
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
INPUTS_PER_CALL_CLAUDE = 4  # more inputs per Claude call = fewer total calls
INPUTS_PER_CALL_SMALL = 1   # for models with tight output limits (70b UF endpoint)
SAMPLES_PER_CALL = 1

SMALL_OUTPUT_MODELS = {"llama-3.1-70b-instruct", "llama-3.3-70b-instruct", "nemotron-3-super-120b-a12b"}
MAX_ATTEMPTS_SMALL = 100   # binary format: ~12% parse rate for 70b; cap early
MAX_ATTEMPTS_DEFAULT = 300
N_WORKERS = 4              # parallel synthesis calls per cell
N_WORKERS_SMALL = 4        # same parallelism as other models — parse rate doesn't affect throughput

# Subprocess timeout per call (seconds). UF endpoint latency is 10–55s for legitimate
# responses; 45s cuts genuinely hung calls without killing valid ones.
SUBPROCESS_TIMEOUT = 45

# Early-exit: if zero new seeds in the last CONSEC_FAIL_WINDOW completed batches,
# the model has stalled on this variant — skip rather than burn remaining attempts.
CONSEC_FAIL_WINDOW = 20

# Skip all Claude models — run free (UF endpoint) models only
FREE_ONLY = True
CLAUDE_MODELS = {"claude-sonnet-4-6", "claude-haiku-4-5-20251001"}

# Sonnet only on the hardest variant (15x more expensive than Haiku)
SONNET_ONLY_VARIANTS = {"v4_src_gaps"}

# Per-model output token caps — 5 binary inputs with base64 + reasoning needs room
# Each input: ~88 base64 chars + reasoning ~30 tok = ~55 tok; 5 × 55 + overhead = ~375 min
# Set 2x headroom
MAX_TOKENS_PER_MODEL = {
    "claude-sonnet-4-6": 1200,
    "claude-haiku-4-5-20251001": 1200,
}
DEFAULT_MAX_TOKENS = 8192  # for non-Claude models (70b UF endpoint ignores this anyway)


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
        "--num-inputs", str(
            INPUTS_PER_CALL_SMALL if model in SMALL_OUTPUT_MODELS
            else INPUTS_PER_CALL_CLAUDE if model.startswith("claude-")
            else INPUTS_PER_CALL
        ),
        "--max-gaps", "30",
        "--source-token-budget", str(SOURCE_TOKEN_BUDGET_ALL_MODELS),
        "--input-format", "binary",
        "--max-tokens", str(MAX_TOKENS_PER_MODEL.get(model, DEFAULT_MAX_TOKENS)),
        "--run-id", str(sample_offset),  # unique per attempt → breaks cache for retries
    ]
    if flags["include_tests"]:
        cmd.append("--include-tests")
    if flags["include_gaps"]:
        cmd.append("--include-gaps")
    if flags["include_source"]:
        cmd.append("--include-source")

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=_env_for_model(model), timeout=SUBPROCESS_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"synthesis timed out for {variant}/{model} (offset={sample_offset})")
    if r.returncode != 0:
        raise RuntimeError(
            f"synthesis failed for {variant}/{model} (offset={sample_offset}):\n"
            f"{r.stderr[-2000:]}"
        )
    seeds_dir = cell_seeds_dir(variant, model)
    return _count_seeds(seeds_dir)


def phase_synthesis(*, skip_existing: bool = False, attempt_offset: int = 0) -> None:
    """Run ablation cells in parallel (N_WORKERS concurrent synthesis calls per cell)."""
    for variant, flags in VARIANTS.items():
        for model in MODELS:
            seeds_dir = cell_seeds_dir(variant, model)

            # Skip Claude models entirely — running free UF-endpoint models only
            if FREE_ONLY and model in CLAUDE_MODELS:
                logger.info("skip synthesis (claude skipped — free-only mode)",
                            extra={"variant": variant, "model": model})
                continue

            # Sonnet is 15x more expensive — only run it on v4_src_gaps
            if not FREE_ONLY and model == "claude-sonnet-4-6" and variant not in SONNET_ONLY_VARIANTS:
                logger.info("skip synthesis (sonnet reserved for targeted variants)",
                            extra={"variant": variant, "model": model})
                continue

            if skip_existing and _count_seeds(seeds_dir) >= TARGET_SEEDS:
                logger.info("skip synthesis (already has enough seeds)",
                            extra={"variant": variant, "model": model,
                                   "n_seeds": _count_seeds(seeds_dir)})
                _subsample_seeds(seeds_dir, TARGET_SEEDS)
                continue

            seeds_dir.mkdir(parents=True, exist_ok=True)
            MAX_ATTEMPTS = MAX_ATTEMPTS_SMALL if model in SMALL_OUTPUT_MODELS else MAX_ATTEMPTS_DEFAULT
            n_workers = N_WORKERS_SMALL if model in SMALL_OUTPUT_MODELS else N_WORKERS

            # Sliding-window parallel synthesis: keep n_workers calls in flight,
            # submit more until TARGET_SEEDS reached or MAX_ATTEMPTS exhausted.
            # Each subprocess writes to unique content-hashed filenames so there
            # are no write races. attempt_counter is only touched by main thread.
            attempt_counter = 0
            lock = threading.Lock()
            recent_gains: list[bool] = []  # True if that completion produced ≥1 new seed
            last_recorded_seeds = _count_seeds(seeds_dir)

            def _submit_next(executor, futures):
                nonlocal attempt_counter
                attempt_counter += 1
                a = attempt_counter
                logger.info("synthesis batch", extra={
                    "variant": variant, "model": model,
                    "attempt": a, "current_seeds": _count_seeds(seeds_dir),
                    "target": TARGET_SEEDS,
                })
                fut = executor.submit(
                    _run_synthesis_batch, variant, model, flags,
                    sample_offset=a + attempt_offset,
                )
                futures[fut] = a

            with ThreadPoolExecutor(max_workers=n_workers) as executor:
                futures: dict = {}
                # Seed the pool
                for _ in range(min(n_workers, MAX_ATTEMPTS)):
                    _submit_next(executor, futures)

                while futures:
                    done_fut = next(as_completed(futures))
                    done_attempt = futures.pop(done_fut)
                    try:
                        done_fut.result()
                    except RuntimeError as e:
                        logger.error("synthesis batch failed", extra={
                            "variant": variant, "model": model,
                            "attempt": done_attempt, "error": str(e)[:500],
                        })

                    current = _count_seeds(seeds_dir)

                    # Rolling early-exit: track whether each completion produced seeds
                    gained = current > last_recorded_seeds
                    last_recorded_seeds = current
                    recent_gains.append(gained)
                    if len(recent_gains) > CONSEC_FAIL_WINDOW:
                        recent_gains.pop(0)
                    if (len(recent_gains) == CONSEC_FAIL_WINDOW
                            and not any(recent_gains)):
                        logger.warning(
                            "early exit: no seeds in last %d attempts",
                            CONSEC_FAIL_WINDOW,
                            extra={"variant": variant, "model": model,
                                   "n_seeds": current, "n_attempts": attempt_counter},
                        )
                        executor.shutdown(wait=False, cancel_futures=True)
                        break

                    if current >= TARGET_SEEDS:
                        # Cancel remaining futures (they may still run to completion
                        # but we won't wait on them — cancel_futures on shutdown handles it)
                        executor.shutdown(wait=False, cancel_futures=True)
                        break

                    if attempt_counter < MAX_ATTEMPTS:
                        _submit_next(executor, futures)

            final_count = _count_seeds(seeds_dir)
            if final_count < TARGET_SEEDS:
                logger.warning("synthesis capped: cell skipped (too many parse failures)",
                               extra={"variant": variant, "model": model,
                                      "n_seeds": final_count, "n_attempts": attempt_counter,
                                      "max_attempts": MAX_ATTEMPTS})
                continue  # leave partial seeds for inspection

            # Subsample to exactly TARGET_SEEDS
            _subsample_seeds(seeds_dir, TARGET_SEEDS)
            final_count = _count_seeds(seeds_dir)
            logger.info("synthesis done", extra={
                "variant": variant, "model": model,
                "n_seeds": final_count, "n_attempts": attempt_counter,
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
