"""Build a tiny RE2 fixture for the sanity experiments (exp1_b / exp2_b).

Produces `<fixture_root>/re2/{tests.json,coverage_gaps.json,metadata.json}`
and ensures RE2 upstream is cloned at the pinned commit. No LLVM / libFuzzer
needed; we extract real tests with the googletest extractor and invent a
small coverage_gaps.json that references lines present in the upstream.

Usage:
    python -m sanity.build_fixture                    # default fixture path
    python -m sanity.build_fixture --fixture-root path # override
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.logging_config import get_logger
from dataset.scripts.extract_tests import extract_for_target
from dataset.scripts.pinned_loader import load_target_yaml
from sanity.config import SANITY_FIXTURE_TESTS, SANITY_TARGET

logger = get_logger("utcf.sanity.fixture")

DEFAULT_FIXTURE_ROOT = REPO_ROOT / "dataset" / "fixtures" / "re2_mini"


def _ensure_upstream(target: str) -> Path:
    upstream = REPO_ROOT / "dataset" / "targets" / "src" / target / "upstream"
    if upstream.is_dir() and any(upstream.iterdir()):
        return upstream
    fetch = REPO_ROOT / "dataset" / "scripts" / "fetch_target.sh"
    target_yaml = REPO_ROOT / "dataset" / "targets" / f"{target}.yaml"
    logger.info("cloning upstream via fetch_target.sh", extra={"target": target})
    subprocess.run(["bash", str(fetch), str(target_yaml)], check=True, cwd=REPO_ROOT)
    return upstream


def _pick_tests(all_tests: list[dict], n: int) -> list[dict]:
    """Pick the first N tests with non-trivial test_code (≥3 lines)."""
    picked: list[dict] = []
    for t in all_tests:
        code = t.get("test_code", "")
        if len([ln for ln in code.splitlines() if ln.strip()]) < 3:
            continue
        picked.append(t)
        if len(picked) >= n:
            break
    if len(picked) < n:
        picked = all_tests[:n]
    return picked


def _write_metadata(fixture_dir: Path, target: str) -> Path:
    cfg = load_target_yaml(REPO_ROOT / "dataset" / "targets" / f"{target}.yaml", require_resolved=True)
    meta = {
        "target": target,
        "tier": cfg.get("tier"),
        "fuzzbench_benchmark": cfg.get("fuzzbench_benchmark"),
        "upstream_repo": cfg["upstream"]["repo"],
        "upstream_commit": cfg["upstream"]["commit"],
        "harness_source": cfg["fuzzbench"]["harness_source"],
        "harness_file": cfg["fuzzbench"]["harness_file"],
    }
    out = fixture_dir / "metadata.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(meta, indent=2))
    return out


def _write_coverage_gaps(fixture_dir: Path, tests: list[dict]) -> Path:
    """Invent a small CoverageGapsReport that schema-validates.

    Fine for the sanity test because Phase 3 synthesis only needs *some* gaps
    to populate the prompt; the LLM is not being graded on gap targeting
    here — only on pipeline flow.
    """
    files = sorted({t["upstream_file"] for t in tests})[:3] or ["re2/re2.cc"]
    gap_branches = [
        {
            "file": files[i % len(files)],
            "line": 100 + i * 17,
            "code_context": f"if (cond_{i}) {{ ... }}",
            "condition_description": f"branch {i} never taken in held-out tests",
            "reachability_score": 0.5,
        }
        for i in range(3)
    ]
    report = {
        "total_upstream_tests": len(tests),
        "union_coverage_pct": 42.0,
        "gap_branches": gap_branches,
        "gap_functions": [
            {"file": files[0], "function": f"UncoveredFn{i}"} for i in range(2)
        ],
        "per_test_unique_coverage": {t["test_name"]: 1 for t in tests},
        "coverage_overlap_matrix": {},
    }
    out = fixture_dir / "coverage_gaps.json"
    out.write_text(json.dumps(report, indent=2))
    return out


def build_fixture(*, target: str = SANITY_TARGET, fixture_root: Path = DEFAULT_FIXTURE_ROOT, n_tests: int = SANITY_FIXTURE_TESTS) -> Path:
    _ensure_upstream(target)
    logger.info("extracting tests for fixture", extra={"target": target})
    all_tests = extract_for_target(target, dry_run=True)
    picked = _pick_tests(all_tests, n_tests)
    if not picked:
        raise RuntimeError(f"no tests extracted for {target}")

    target_dir = fixture_root / target
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "tests.json").write_text(json.dumps(picked, indent=2, ensure_ascii=False))
    _write_metadata(target_dir, target)
    _write_coverage_gaps(target_dir, picked)
    (target_dir / "dataset_stats.json").write_text(json.dumps({"target": target, "test_count": len(picked), "source": "sanity_fixture"}, indent=2))
    logger.info("fixture written", extra={"target": target, "n_tests": len(picked), "path": str(target_dir)})
    return target_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=SANITY_TARGET)
    parser.add_argument("--fixture-root", type=Path, default=DEFAULT_FIXTURE_ROOT)
    parser.add_argument("--n-tests", type=int, default=SANITY_FIXTURE_TESTS)
    args = parser.parse_args()
    path = build_fixture(target=args.target, fixture_root=args.fixture_root, n_tests=args.n_tests)
    print(f"wrote sanity fixture to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
