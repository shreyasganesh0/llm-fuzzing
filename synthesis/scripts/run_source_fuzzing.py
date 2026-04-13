"""libFuzzer campaigns with source-only seeds (plan §E2.5).

Two new configs mirror Phase 3 configs 4 and 5:
  - source_only_llm_seeds     = source-only LLM seeds, no FuzzBench seeds
  - source_only_combined      = source-only LLM seeds + FuzzBench seeds

Delegates actual execution to synthesis.scripts.run_fuzzing so
there's a single libFuzzer driver.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from synthesis.scripts.run_fuzzing import (
    load_campaign_config,
    run_campaign,
)

CONFIG_DIR = REPO_ROOT / "synthesis" / "campaign_configs"


def run_source_campaign(
    *,
    config_name: str,
    target: str,
    binary: Path,
    work_dir: Path,
    dry_run: bool = False,
):
    cfg_path = CONFIG_DIR / f"{config_name}.yaml"
    cfg = load_campaign_config(cfg_path, target=target, binary=binary)
    return run_campaign(cfg, work_dir=work_dir, dry_run=dry_run)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, choices=["source_only_llm_seeds", "source_only_combined"])
    parser.add_argument("--target", required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = run_source_campaign(
        config_name=args.config,
        target=args.target,
        binary=args.binary,
        work_dir=args.work_dir,
        dry_run=args.dry_run,
    )
    print(f"trials={len(result.trials)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
