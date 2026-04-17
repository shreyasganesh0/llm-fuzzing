"""4x2 ablation experiment driver: 4 variants x 3 models, evaluated under M1+M2.

Phases (each phase is independent, --phase can be passed):
  1. prep     -- materialize a "minimized" dataset dir whose coverage_gaps.json
                 contains exactly the 30 shown gaps from m2_target_branches.json.
  2. synthesis -- 12 cells: run generate_ablation_inputs.py per (variant, model).
  3. m1       -- run measure_coverage.py on each cell + the random anchor.
  4. m2       -- run measure_gap_coverage.py on each cell + the random anchor.
  5. random   -- generate the 30-seed random anchor (regex-shaped).
  6. all      -- run prep, random, synthesis, m1, m2 in order.

Outputs land under results/ablation_v3/.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import (
    M2_TARGETS_PATH,
    SOURCE_TOKEN_BUDGET_ALL_MODELS,
)
from core.logging_config import get_logger

logger = get_logger("utcf.ablation.orchestrator")

PY = sys.executable

VARIANTS = {
    "v0_none":       {"include_source": False, "include_tests": False, "include_gaps": False},
    "v1_src":        {"include_source": True,  "include_tests": False, "include_gaps": False},
    "v2_src_tests":  {"include_source": True,  "include_tests": True,  "include_gaps": False},
    "v3_all":        {"include_source": True,  "include_tests": True,  "include_gaps": True},
    "v4_src_gaps":   {"include_source": True,  "include_tests": False, "include_gaps": True},
}

MODELS = [
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
    "llama-3.1-8b-instruct",
]

# Existing real dataset (tests + metadata) for re2.
ORIG_DATASET_ROOT = REPO_ROOT / "dataset/fixtures/re2_ab"

RESULTS_ROOT = REPO_ROOT / "results/ablation_v3"
SYNTHESIS_RESULTS_ROOT = REPO_ROOT / "synthesis/results/ablation_v3"
PREPPED_DATASET_ROOT = REPO_ROOT / "dataset/fixtures/_ablation_v3_dataset"

RE2_COVERAGE_BINARY = REPO_ROOT / "dataset/targets/src/re2/build/coverage/seed_replay"
RANDOM_SEEDS_DIR = SYNTHESIS_RESULTS_ROOT / "seeds" / "re2" / "random"

CLAUDE_KEY_PATH = REPO_ROOT / "secrets/claude_key"
LITELLM_URL = "https://api.ai.it.ufl.edu"


def _env_for_model(model: str) -> dict[str, str]:
    """Route Claude models via Anthropic SDK; everything else via UF LiteLLM."""
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
    return SYNTHESIS_RESULTS_ROOT / "seeds" / "re2" / "ablation" / variant / _safe_model(model)


def cell_m2_dir(variant: str, model: str) -> Path:
    return RESULTS_ROOT / "m2" / variant / _safe_model(model)


def cell_m1_dir(variant: str, model: str) -> Path:
    return RESULTS_ROOT / "m1" / variant / _safe_model(model)


# ─── Phase 1: prep ────────────────────────────────────────────────────────
def phase_prep() -> None:
    """Materialize a stripped dataset with only the 30 shown gaps.

    Uses the fixed RE2 public API tests.json from _ablation_v3_dataset if it
    exists (preferred); otherwise falls back to the original re2_ab fixture.
    Carries uncovered_side through to coverage_gaps.json so the prompt template
    can tell the LLM exactly which branch side to target.
    """
    targets = json.loads(M2_TARGETS_PATH.read_text())
    shown = targets["shown"]

    target_re2 = PREPPED_DATASET_ROOT / "re2"
    target_re2.mkdir(parents=True, exist_ok=True)

    # tests.json: prefer the fixed RE2-public-API version in _ablation_v3_dataset.
    fixed_tests = PREPPED_DATASET_ROOT / "re2" / "tests.json"
    for fn in ("tests.json", "metadata.json"):
        # For tests.json, if the prepped dir already has the fixed version, keep it.
        dst = target_re2 / fn
        if fn == "tests.json" and dst.is_file():
            pass  # already written by a prior prep; leave it
        else:
            src = ORIG_DATASET_ROOT / "re2" / fn
            if src.is_file():
                shutil.copy2(src, dst)

    # Build coverage_gaps.json with uncovered_side so the prompt template
    # can tell the LLM which branch side to hit.
    new_gaps = {
        "total_upstream_tests": 3,
        "union_coverage_pct": 8.71,
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
    (target_re2 / "coverage_gaps.json").write_text(json.dumps(new_gaps, indent=2))
    logger.info("prep done", extra={"dataset_root": str(PREPPED_DATASET_ROOT),
                                    "n_gaps_in_prompt": len(shown)})


# ─── Phase 2: synthesis ───────────────────────────────────────────────────
def phase_synthesis(*, skip_existing: bool = False, samples: int = 3,
                    num_inputs: int = 10) -> None:
    """Run 12 ablation cells (4 variants x 3 models)."""
    for variant, flags in VARIANTS.items():
        for model in MODELS:
            seeds_dir = cell_seeds_dir(variant, model)
            if skip_existing and seeds_dir.is_dir() and any(seeds_dir.iterdir()):
                logger.info("skip synthesis (exists)",
                            extra={"variant": variant, "model": model})
                continue
            cmd = [
                PY, "-m", "synthesis.scripts.generate_ablation_inputs",
                "--target", "re2",
                "--model", model,
                "--cell", variant,
                "--dataset-root", str(PREPPED_DATASET_ROOT),
                "--results-root", str(SYNTHESIS_RESULTS_ROOT),
                "--samples", str(samples),
                "--num-inputs", str(num_inputs),
                "--max-gaps", "30",
                "--source-token-budget", str(SOURCE_TOKEN_BUDGET_ALL_MODELS),
            ]
            if flags["include_tests"]:
                cmd.append("--include-tests")
            if flags["include_gaps"]:
                cmd.append("--include-gaps")
            if flags["include_source"]:
                cmd.append("--include-source")
            logger.info("synthesis start", extra={"variant": variant, "model": model})
            r = subprocess.run(cmd, capture_output=True, text=True, env=_env_for_model(model))
            if r.returncode != 0:
                logger.error("synthesis failed", extra={
                    "variant": variant, "model": model,
                    "stderr_tail": r.stderr[-2000:],
                })
                raise RuntimeError(f"synthesis failed for {variant}/{model}")
            logger.info("synthesis done", extra={
                "variant": variant, "model": model,
                "stdout_tail": r.stdout.strip()[-200:],
            })


# ─── Phase 3: random anchor ───────────────────────────────────────────────
def phase_random(*, count: int = 100, skip_existing: bool = False) -> None:
    if skip_existing and RANDOM_SEEDS_DIR.is_dir() and any(RANDOM_SEEDS_DIR.iterdir()):
        logger.info("skip random (exists)")
        return
    RANDOM_SEEDS_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        PY, "-m", "synthesis.scripts.generate_random_inputs",
        "--target", "re2",
        "--count", str(count),
        "--seed", "42",
        "--input-format", "regex",
        "--results-root", str(SYNTHESIS_RESULTS_ROOT),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"random anchor generation failed: {r.stderr[-2000:]}")
    # generate_random_inputs writes to <results-root>/seeds/<target>/random/.
    actual = SYNTHESIS_RESULTS_ROOT / "seeds" / "re2" / "random"
    if actual != RANDOM_SEEDS_DIR:
        for f in actual.iterdir():
            shutil.move(str(f), str(RANDOM_SEEDS_DIR / f.name))
    logger.info("random anchor done", extra={"dir": str(RANDOM_SEEDS_DIR)})


# ─── Phase 4: M1 (corpus-level edges) ─────────────────────────────────────
def _run_m1_one(seeds_dir: Path, label: str, out_path: Path) -> dict:
    cmd = [
        PY, "-m", "synthesis.scripts.measure_coverage",
        "--binary", str(RE2_COVERAGE_BINARY),
        "--seeds-dir", str(seeds_dir),
        "--source-roots", str(REPO_ROOT / "phase1_dataset/targets/src/re2/upstream"),
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
            out_dir = cell_m1_dir(variant, model)
            out_path = out_dir / "summary.json"
            if not seeds_dir.is_dir() or not any(seeds_dir.iterdir()):
                logger.warning("M1 skip: no seeds", extra={"variant": variant, "model": model})
                continue
            if skip_existing and out_path.is_file():
                continue
            logger.info("M1 start", extra={"variant": variant, "model": model})
            _run_m1_one(seeds_dir, f"{variant}/{model}", out_path)
    # Random anchor
    if RANDOM_SEEDS_DIR.is_dir() and any(RANDOM_SEEDS_DIR.iterdir()):
        out_path = RESULTS_ROOT / "m1" / "random" / "summary.json"
        if not (skip_existing and out_path.is_file()):
            logger.info("M1 start", extra={"variant": "random"})
            _run_m1_one(RANDOM_SEEDS_DIR, "random", out_path)


# ─── Phase 5: M2 (target-branch coverage) ─────────────────────────────────
def _run_m2_one(seeds_dir: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        PY, "-m", "analysis.scripts.measure_gap_coverage",
        "--seeds-dir", str(seeds_dir),
        "--out-dir", str(out_dir),
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
            if not seeds_dir.is_dir() or not any(seeds_dir.iterdir()):
                logger.warning("M2 skip: no seeds", extra={"variant": variant, "model": model})
                continue
            if skip_existing and (out_dir / "summary.json").is_file():
                continue
            logger.info("M2 start", extra={"variant": variant, "model": model})
            _run_m2_one(seeds_dir, out_dir)
    if RANDOM_SEEDS_DIR.is_dir() and any(RANDOM_SEEDS_DIR.iterdir()):
        out_dir = RESULTS_ROOT / "m2" / "random"
        if not (skip_existing and (out_dir / "summary.json").is_file()):
            logger.info("M2 start", extra={"variant": "random"})
            _run_m2_one(RANDOM_SEEDS_DIR, out_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["prep", "synthesis", "m1", "m2", "random", "all"],
                        default="all")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--num-inputs", type=int, default=10)
    args = parser.parse_args()

    if args.phase in ("prep", "all"):
        phase_prep()
    if args.phase in ("random", "all"):
        phase_random(skip_existing=args.skip_existing)
    if args.phase in ("synthesis", "all"):
        phase_synthesis(skip_existing=args.skip_existing,
                        samples=args.samples, num_inputs=args.num_inputs)
    if args.phase in ("m1", "all"):
        phase_m1(skip_existing=args.skip_existing)
    if args.phase in ("m2", "all"):
        phase_m2(skip_existing=args.skip_existing)
    print(f"phase={args.phase} done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
