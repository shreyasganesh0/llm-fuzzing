"""Drive libFuzzer campaigns following the FuzzBench gold standard.

Per plan §FuzzBench methodology:
  - 20 trials per (config, target)
  - 23-hour campaigns (82,800 s)
  - Corpus snapshots every 15 min
  - Clang source-based coverage for comparison (done by measure_coverage.py)

Usage:
    python synthesis/scripts/run_fuzzing.py \
        --config synthesis/campaign_configs/llm_seeds.yaml \
        --target re2 \
        --trials 20 --duration-s 82800

The actual fuzzer run uses `subprocess.Popen` with a time budget; each trial
gets its own work dir. When the libfuzzer binary isn't available, the script
runs in `--dry-run` mode to produce stub TrialResults for the rest of the
pipeline to exercise.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import (
    CAMPAIGN_DURATION_S,
    CAMPAIGN_SNAPSHOT_S,
    CAMPAIGN_TRIALS,
    SEED_BASE,
)
from core.dataset_schema import (
    CampaignConfig,
    CampaignResult,
    CoverageSnapshot,
    TrialResult,
)
from core.logging_config import get_logger

logger = get_logger("utcf.phase3.fuzz")

# libFuzzer progress line example:
# "#12345 NEW    cov: 234 ft: 567 corp: 45/1234b exec/s: 12345 rss: 123Mb"
LIBFUZZER_LINE_RE = re.compile(
    r"#(?P<execs>\d+)\s+\S+\s+"
    r"cov:\s*(?P<cov>\d+)\s+"
    r"ft:\s*(?P<ft>\d+)\s+"
    r"corp:\s*(?P<corp>\d+)"
)
CRASH_LINE_RE = re.compile(r"==\d+==\s*ERROR:\s*(\S+)\s*:")


def load_campaign_config(config_path: Path, target: str, binary: Path) -> CampaignConfig:
    raw = yaml.safe_load(config_path.read_text())
    raw["target"] = target
    raw["libfuzzer_binary"] = str(binary)
    return CampaignConfig.model_validate(raw)


def _parse_line(line: str) -> CoverageSnapshot | None:
    m = LIBFUZZER_LINE_RE.search(line)
    if not m:
        return None
    return CoverageSnapshot(
        elapsed_s=0,  # filled by caller
        edges_covered=int(m.group("cov")),
        features_covered=int(m.group("ft")),
        corpus_size=int(m.group("corp")),
        execs=int(m.group("execs")),
    )


def run_single_trial(
    config: CampaignConfig,
    trial_index: int,
    work_dir: Path,
    *,
    dry_run: bool = False,
) -> TrialResult:
    seed = SEED_BASE + trial_index
    work_dir.mkdir(parents=True, exist_ok=True)

    if dry_run or not Path(config.libfuzzer_binary).is_file():
        logger.info(
            "dry-run trial",
            extra={"config": config.name, "target": config.target, "trial": trial_index},
        )
        return TrialResult(
            config_name=config.name,
            target=config.target,
            trial_index=trial_index,
            seed=seed,
            snapshots=[],
            final_edges=0,
            final_execs=0,
            wall_clock_s=0.0,
            crashes=[],
            status="ok",
        )

    corpus_dir = work_dir / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    if config.seed_corpus_dir:
        src = Path(config.seed_corpus_dir)
        if src.is_dir():
            for seed_file in src.iterdir():
                if seed_file.is_file():
                    (corpus_dir / seed_file.name).write_bytes(seed_file.read_bytes())

    args = [
        config.libfuzzer_binary,
        str(corpus_dir),
        f"-max_total_time={config.duration_s}",
        f"-timeout={config.timeout_s}",
        f"-rss_limit_mb={config.rss_limit_mb}",
        f"-max_len={config.max_len}",
        f"-seed={seed}",
        "-print_pcs=0",
        "-print_final_stats=1",
    ]
    if config.dictionary:
        args.append(f"-dict={config.dictionary}")

    env = os.environ.copy()
    start = time.perf_counter()
    snapshots: list[CoverageSnapshot] = []
    crashes: list[str] = []
    next_snapshot = config.snapshot_interval_s

    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, cwd=work_dir
    )
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            elapsed = time.perf_counter() - start
            snap = _parse_line(line)
            if snap and elapsed >= next_snapshot:
                snap_with_time = CoverageSnapshot(
                    elapsed_s=int(elapsed),
                    edges_covered=snap.edges_covered,
                    features_covered=snap.features_covered,
                    corpus_size=snap.corpus_size,
                    execs=snap.execs,
                )
                snapshots.append(snap_with_time)
                next_snapshot += config.snapshot_interval_s
            crash_match = CRASH_LINE_RE.search(line)
            if crash_match:
                crashes.append(crash_match.group(1))
            if elapsed > config.duration_s + 60:
                proc.terminate()
                break
    finally:
        proc.wait(timeout=30)

    wall = time.perf_counter() - start
    last = snapshots[-1] if snapshots else CoverageSnapshot(
        elapsed_s=int(wall), edges_covered=0, features_covered=0, corpus_size=0, execs=0
    )
    return TrialResult(
        config_name=config.name,
        target=config.target,
        trial_index=trial_index,
        seed=seed,
        snapshots=snapshots,
        final_edges=last.edges_covered,
        final_execs=last.execs,
        wall_clock_s=wall,
        crashes=crashes,
        status="ok" if proc.returncode == 0 else "error",
    )


def run_campaign(
    config: CampaignConfig,
    *,
    work_root: Path,
    dry_run: bool = False,
) -> CampaignResult:
    trials: list[TrialResult] = []
    for k in range(config.trials):
        work_dir = work_root / config.name / f"trial_{k:02d}"
        trial = run_single_trial(config, k, work_dir, dry_run=dry_run)
        trials.append(trial)
    return CampaignResult(config_name=config.name, target=config.target, trials=trials)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--binary", type=Path, required=True, help="libFuzzer-instrumented binary")
    parser.add_argument("--trials", type=int, default=CAMPAIGN_TRIALS)
    parser.add_argument("--duration-s", type=int, default=CAMPAIGN_DURATION_S)
    parser.add_argument("--snapshot-interval-s", type=int, default=CAMPAIGN_SNAPSHOT_S)
    parser.add_argument("--seed-corpus-dir", type=Path, default=None)
    parser.add_argument("--dictionary", type=Path, default=None)
    parser.add_argument("--work-root", type=Path, default=REPO_ROOT / "synthesis" / "results" / "campaigns")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = load_campaign_config(args.config, args.target, args.binary)
    # CLI overrides win over YAML defaults.
    config = config.model_copy(update={
        "trials": args.trials,
        "duration_s": args.duration_s,
        "snapshot_interval_s": args.snapshot_interval_s,
        "seed_corpus_dir": str(args.seed_corpus_dir) if args.seed_corpus_dir else config.seed_corpus_dir,
        "dictionary": str(args.dictionary) if args.dictionary else config.dictionary,
    })

    result = run_campaign(config, work_root=args.work_root, dry_run=args.dry_run)

    out = args.work_root / config.target / f"{config.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result.model_dump_json(indent=2))
    print(f"wrote {out} (trials={len(result.trials)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
