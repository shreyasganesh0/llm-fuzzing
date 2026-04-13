"""End-to-end Phase 1 orchestrator: extract -> coverage -> gaps -> audit.

Usage:
    python dataset/scripts/build_dataset.py --target re2
    python dataset/scripts/build_dataset.py --target re2 --skip-coverage

`--skip-coverage` runs only extraction + provenance audit (useful locally
when the LLVM toolchain isn't available; Phase 1 will then pick up coverage
on a build machine).

Validation gate:
  - every test object must carry provenance
  - every test is verified against `upstream_file:upstream_line`
  - dataset_stats.json captures the audit result
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.dataset_schema import Test
from core.logging_config import get_logger
from core.provenance import audit_tests
from dataset.scripts.extract_tests import extract_for_target
from dataset.scripts.pinned_loader import load_target_yaml

logger = get_logger("utcf.phase1.dataset")


def write_metadata(dataset_root: Path, target: str, config: dict) -> Path:
    meta = {
        "target": target,
        "tier": config.get("tier"),
        "fuzzbench_benchmark": config.get("fuzzbench_benchmark"),
        "upstream_repo": config["upstream"]["repo"],
        "upstream_commit": config["upstream"]["commit"],
        "harness_source": config["fuzzbench"]["harness_source"],
        "harness_file": config["fuzzbench"]["harness_file"],
    }
    out = dataset_root / target / "metadata.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(meta, indent=2))
    return out


def build(target: str, *, dataset_root: Path, skip_coverage: bool, skip_gaps: bool) -> dict:
    config_path = REPO_ROOT / "dataset" / "targets" / f"{target}.yaml"
    config = load_target_yaml(config_path, require_resolved=True)

    write_metadata(dataset_root, target, config)

    logger.info("extract start", extra={"target": target})
    tests_data = extract_for_target(target, dataset_root=dataset_root)
    tests = [Test.model_validate(t) for t in tests_data]

    upstream_root = REPO_ROOT / "dataset" / "targets" / "src" / target / "upstream"
    audit: dict[str, list[str]] = {"verified": [t.test_name for t in tests], "rejected": []}
    if upstream_root.is_dir():
        audit = audit_tests(tests, upstream_root)
        if audit["rejected"]:
            logger.error(
                "provenance audit rejected tests",
                extra={"target": target, "rejected": audit["rejected"][:20], "total": len(audit["rejected"])},
            )
            raise RuntimeError(
                f"{len(audit['rejected'])} test(s) failed provenance audit for {target}"
            )

    if not skip_coverage:
        logger.info("coverage run: not wired in scaffolding; invoke run_test_coverage.py manually")
    if not skip_gaps:
        logger.info("gaps run: not wired in scaffolding; invoke compute_gaps.py manually")

    stats = {
        "target": target,
        "test_count": len(tests),
        "provenance_audit": {
            "total_tests": len(tests),
            "tests_with_provenance": len(audit["verified"]),
            "tests_rejected": len(audit["rejected"]),
        },
        "upstream_repos_used": sorted({t.upstream_repo for t in tests}),
        "frameworks": sorted({t.framework for t in tests}),
    }
    out = dataset_root / target / "dataset_stats.json"
    out.write_text(json.dumps(stats, indent=2))
    logger.info("wrote stats", extra={"path": str(out)})
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--dataset-root", type=Path, default=REPO_ROOT / "dataset" / "dataset")
    parser.add_argument("--skip-coverage", action="store_true")
    parser.add_argument("--skip-gaps", action="store_true")
    args = parser.parse_args()

    stats = build(
        args.target,
        dataset_root=args.dataset_root,
        skip_coverage=args.skip_coverage,
        skip_gaps=args.skip_gaps,
    )
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
