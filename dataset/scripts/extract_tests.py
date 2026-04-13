"""Dispatch test extraction to framework-specific extractors.

Usage:
    python dataset/scripts/extract_tests.py --target re2
    python dataset/scripts/extract_tests.py --target re2 --dry-run

Writes `dataset/data/<target>/tests.json` on success. With --dry-run,
prints the number of tests that would be extracted without writing output.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make repo importable when invoked as a script.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.logging_config import get_logger
from dataset.scripts import extractors
from dataset.scripts.pinned_loader import load_target_yaml

logger = get_logger("utcf.phase1.extract")


def extract_for_target(
    target: str,
    *,
    repo_root: str | Path | None = None,
    dataset_root: str | Path = "dataset/data",
    dry_run: bool = False,
) -> list[dict]:
    config_path = REPO_ROOT / "dataset" / "targets" / f"{target}.yaml"
    config = load_target_yaml(config_path, require_resolved=True)

    if repo_root is None:
        repo_root = REPO_ROOT / "dataset" / "targets" / "src" / target / "upstream"
    repo_root = Path(repo_root)

    framework = config["tests"]["framework"]
    extractor = extractors.get(framework)

    if not repo_root.is_dir():
        if dry_run:
            logger.info(
                "dry_run: skipping actual extraction (upstream not cloned)",
                extra={"target": target, "repo_root": str(repo_root)},
            )
            return []
        raise FileNotFoundError(
            f"Upstream repo missing at {repo_root}. Run fetch_target.sh first."
        )

    tests = extractor(config, repo_root)
    logger.info("extracted", extra={"target": target, "framework": framework, "n": len(tests)})

    serialised = [t.model_dump() for t in tests]
    if dry_run:
        return serialised

    out_dir = Path(dataset_root) / target
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "tests.json"
    out_path.write_text(json.dumps(serialised, indent=2, ensure_ascii=False))
    logger.info("wrote", extra={"path": str(out_path)})
    return serialised


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="Target name (e.g., re2)")
    parser.add_argument("--dry-run", action="store_true", help="Print counts, do not write")
    args = parser.parse_args()

    results = extract_for_target(args.target, dry_run=args.dry_run)
    print(f"target={args.target} tests={len(results)} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
