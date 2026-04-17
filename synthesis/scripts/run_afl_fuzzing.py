"""Drive AFL++ campaigns with the same schema as run_fuzzing.py (libFuzzer).

AFL++ differences from libFuzzer:
  - Uses ``afl-fuzz`` binary with different CLI args
  - Writes stats to ``fuzzer_stats`` and ``plot_data`` in the output dir
  - Requires at least one seed (empty campaigns use a 1-byte file)
  - Campaign duration via ``-V`` flag (seconds)
  - Edge coverage read from ``plot_data`` (edges_found column)

Usage:
    python synthesis/scripts/run_afl_fuzzing.py \
        --config synthesis/campaign_configs/llm_seeds.yaml \
        --target re2 \
        --binary dataset/targets/src/re2/build/afl/re2_afl_fuzzer \
        --trials 5 --duration-s 3600
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
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

logger = get_logger("utcf.phase3.afl")

AFL_FUZZ = os.environ.get(
    "AFL_FUZZ",
    str(Path.home() / "tools" / "aflpp" / "afl-fuzz"),
)


def load_campaign_config(config_path: Path, target: str, binary: Path) -> CampaignConfig:
    raw = yaml.safe_load(config_path.read_text())
    raw["target"] = target
    raw["afl_binary"] = str(binary.resolve())
    raw["fuzzer_engine"] = "aflpp"
    if "libfuzzer_binary" not in raw:
        raw["libfuzzer_binary"] = ""
    return CampaignConfig.model_validate(raw)


AFL_STATUS_RE = re.compile(
    r"corpus_count.*?:\s*(?P<corpus>\d+)|"
    r"edges_found.*?:\s*(?P<edges>\d+)|"
    r"total_execs.*?:\s*(?P<execs>\d+)|"
    r"cvg=(?P<cvg>[\d.]+)%"
)


def _parse_plot_data(plot_file: Path) -> list[tuple[int, int, int, int]]:
    """Parse AFL++ plot_data. Returns [(relative_time_s, corpus_count, edges_found, total_execs)]."""
    rows: list[tuple[int, int, int, int]] = []
    if not plot_file.is_file():
        return rows
    for line in plot_file.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split(",")
        if len(parts) < 13:
            continue
        try:
            rel_time = int(parts[0].strip())
            corpus_count = int(parts[3].strip())
            total_execs = int(float(parts[11].strip()))
            edges_found = int(parts[12].strip())
            rows.append((rel_time, corpus_count, edges_found, total_execs))
        except (ValueError, IndexError):
            continue
    return rows


def _read_fuzzer_stats(stats_file: Path) -> dict[str, str]:
    """Parse AFL++ fuzzer_stats key-value file."""
    result: dict[str, str] = {}
    if not stats_file.is_file():
        return result
    for line in stats_file.read_text().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            result[key.strip()] = val.strip()
    return result


def run_single_trial(
    config: CampaignConfig,
    trial_index: int,
    work_dir: Path,
    *,
    dry_run: bool = False,
) -> TrialResult:
    seed = SEED_BASE + trial_index
    work_dir.mkdir(parents=True, exist_ok=True)

    binary = config.afl_binary
    if dry_run or not Path(binary).is_file():
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

    work_dir = work_dir.resolve()
    seed_dir = work_dir / "seeds"
    seed_dir.mkdir(parents=True, exist_ok=True)
    output_dir = work_dir / "afl_out"
    if output_dir.exists():
        shutil.rmtree(output_dir)

    if config.seed_corpus_dir and Path(config.seed_corpus_dir).is_dir():
        copied = 0
        for sf in Path(config.seed_corpus_dir).iterdir():
            if sf.is_file():
                (seed_dir / sf.name).write_bytes(sf.read_bytes())
                copied += 1
        if copied == 0:
            (seed_dir / "empty").write_bytes(b"\x00")
    else:
        (seed_dir / "empty").write_bytes(b"\x00")

    args = [
        AFL_FUZZ,
        "-i", str(seed_dir),
        "-o", str(output_dir),
        "-t", str(config.timeout_s * 1000),
        "-m", "none",  # ASAN binaries need unlimited virtual memory
        "-V", str(config.duration_s),
        "-s", str(seed),
    ]
    if config.dictionary and Path(config.dictionary).is_file():
        args.extend(["-x", config.dictionary])
    args.extend(["--", binary])

    env = os.environ.copy()
    env["AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES"] = "1"
    env["AFL_SKIP_CPUFREQ"] = "1"
    env["AFL_NO_UI"] = "1"
    env["AFL_MAP_SIZE"] = "262144"
    env["AFL_SKIP_CRASHES"] = "1"
    env["AFL_IGNORE_PROBLEMS"] = "1"
    env["AFL_IGNORE_SEED_PROBLEMS"] = "1"

    start = time.perf_counter()
    snapshots: list[CoverageSnapshot] = []
    crashes: list[str] = []
    next_snapshot = config.snapshot_interval_s

    logger.info(
        "starting AFL++ trial",
        extra={
            "config": config.name,
            "target": config.target,
            "trial": trial_index,
            "seed": seed,
            "cmd": " ".join(args),
        },
    )

    log_file = work_dir / "afl_stdout.log"
    log_fh = open(log_file, "w")
    proc = subprocess.Popen(
        args, stdout=log_fh, stderr=subprocess.STDOUT, env=env, cwd=work_dir
    )

    default_dir = output_dir / "default"
    plot_file = default_dir / "plot_data"

    try:
        while proc.poll() is None:
            elapsed = time.perf_counter() - start
            if elapsed >= next_snapshot:
                rows = _parse_plot_data(plot_file)
                if rows:
                    _, corpus, edges, execs = rows[-1]
                    snapshots.append(CoverageSnapshot(
                        elapsed_s=int(elapsed),
                        edges_covered=edges,
                        features_covered=edges,
                        corpus_size=corpus,
                        execs=execs,
                    ))
                next_snapshot += config.snapshot_interval_s

            if elapsed > config.duration_s + 120:
                proc.terminate()
                break
            time.sleep(min(5.0, max(1.0, next_snapshot - elapsed)))
    finally:
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=30)
        log_fh.close()

    wall = time.perf_counter() - start

    # Final stats from plot_data.
    rows = _parse_plot_data(plot_file)
    if rows:
        _, corpus, edges, execs = rows[-1]
        if not snapshots or snapshots[-1].elapsed_s < int(wall) - 10:
            snapshots.append(CoverageSnapshot(
                elapsed_s=int(wall),
                edges_covered=edges,
                features_covered=edges,
                corpus_size=corpus,
                execs=execs,
            ))

    # Count crash files.
    crash_dir = default_dir / "crashes"
    if crash_dir.is_dir():
        crash_files = [f.name for f in crash_dir.iterdir() if f.is_file() and f.name != "README.txt"]
        crashes = crash_files

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
        work_dir = work_root / config.target / config.name / f"trial_{k:02d}"
        trial = run_single_trial(config, k, work_dir, dry_run=dry_run)
        trials.append(trial)
        logger.info(
            "trial completed",
            extra={
                "config": config.name,
                "trial": k,
                "final_edges": trial.final_edges,
                "wall_s": f"{trial.wall_clock_s:.0f}",
            },
        )
    return CampaignResult(config_name=config.name, target=config.target, trials=trials)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--binary", type=Path, required=True, help="AFL++-instrumented binary")
    parser.add_argument("--trials", type=int, default=CAMPAIGN_TRIALS)
    parser.add_argument("--duration-s", type=int, default=CAMPAIGN_DURATION_S)
    parser.add_argument("--snapshot-interval-s", type=int, default=CAMPAIGN_SNAPSHOT_S)
    parser.add_argument("--seed-corpus-dir", type=Path, default=None)
    parser.add_argument("--dictionary", type=Path, default=None)
    parser.add_argument("--work-root", type=Path, default=REPO_ROOT / "synthesis" / "results" / "campaigns_afl")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = load_campaign_config(args.config, args.target, args.binary)
    config = config.model_copy(update={
        "trials": args.trials,
        "duration_s": args.duration_s,
        "snapshot_interval_s": args.snapshot_interval_s,
        "seed_corpus_dir": str(args.seed_corpus_dir) if args.seed_corpus_dir else config.seed_corpus_dir,
        "dictionary": str(args.dictionary) if args.dictionary else config.dictionary,
    })

    result = run_campaign(config, work_root=args.work_root, dry_run=args.dry_run)

    out = args.work_root / config.target / f"{config.name}_aflpp.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result.model_dump_json(indent=2))
    print(f"wrote {out} (trials={len(result.trials)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
