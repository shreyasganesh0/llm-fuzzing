"""Run each extracted test under LLVM source-based coverage and record results.

Per test:
  1. Set LLVM_PROFILE_FILE to a unique path under `dataset/<target>/tests/<id>/`.
  2. Invoke the coverage-instrumented test binary with
     `--gtest_filter=<Suite>.<Name>` (googletest) or the framework-specific
     equivalent for other extractors (not implemented in this session).
  3. `llvm-profdata-15 merge -sparse` -> per-test `.profdata`.
  4. `llvm-cov-15 export --format=json --skip-expansions` -> coverage JSON.
  5. Parse via `core.coverage_utils.parse_llvm_cov_json` and write
     `coverage.json` to the test's dataset directory.

Handles crash/timeout by marking status="crash"/"timeout" while still writing
whatever partial profile was captured.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.coverage_utils import parse_llvm_cov_json
from core.dataset_schema import CoverageProfile, Test
from core.logging_config import get_logger
from dataset.scripts.pinned_loader import load_target_yaml

logger = get_logger("utcf.phase1.coverage")

LLVM_PROFDATA = os.environ.get("LLVM_PROFDATA", "llvm-profdata-15")
LLVM_COV = os.environ.get("LLVM_COV", "llvm-cov-15")


def _tool(name: str, fallback: str) -> str:
    return name if shutil.which(name) else fallback


def run_single_test(
    test: Test,
    *,
    target: str,
    test_binary: Path,
    out_dir: Path,
    source_roots: list[str],
    timeout_s: int = 60,
) -> CoverageProfile:
    out_dir.mkdir(parents=True, exist_ok=True)
    profraw = out_dir / "test.profraw"
    profdata = out_dir / "test.profdata"
    cov_json_path = out_dir / "llvm_cov_export.json"

    if test.framework != "googletest":
        logger.warning("non-googletest run not implemented", extra={"framework": test.framework})
        return CoverageProfile(
            test_name=test.test_name,
            upstream_file=test.upstream_file,
            upstream_line=test.upstream_line,
            framework=test.framework,
            status="skipped",
            status_detail=f"runner for {test.framework} not wired",
        )

    env = os.environ.copy()
    env["LLVM_PROFILE_FILE"] = str(profraw)
    cmd = [str(test_binary), f"--gtest_filter={test.test_name}"]

    status = "ok"
    status_detail = None
    try:
        subprocess.run(cmd, env=env, check=True, capture_output=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        status, status_detail = "timeout", f"timeout after {timeout_s}s"
    except subprocess.CalledProcessError as exc:
        status, status_detail = "crash", exc.stderr.decode("utf-8", errors="replace")[:2000]

    if not profraw.exists():
        return CoverageProfile(
            test_name=test.test_name,
            upstream_file=test.upstream_file,
            upstream_line=test.upstream_line,
            framework=test.framework,
            status=status if status != "ok" else "skipped",
            status_detail=status_detail or "no profraw produced",
        )

    profdata_tool = _tool(LLVM_PROFDATA, "llvm-profdata")
    cov_tool = _tool(LLVM_COV, "llvm-cov")
    subprocess.run(
        [profdata_tool, "merge", "-sparse", str(profraw), "-o", str(profdata)],
        check=True, capture_output=True,
    )
    export = subprocess.run(
        [cov_tool, "export", str(test_binary), f"-instr-profile={profdata}",
         "--skip-expansions"],
        check=True, capture_output=True,
    )
    cov_json_path.write_bytes(export.stdout)

    profile = parse_llvm_cov_json(
        cov_json_path,
        test_name=test.test_name,
        upstream_file=test.upstream_file,
        upstream_line=test.upstream_line,
        framework=test.framework,
        source_roots=source_roots,
    )
    profile.status = status  # preserve partial-coverage on crash/timeout
    profile.status_detail = status_detail

    (out_dir / "coverage.json").write_text(profile.model_dump_json(indent=2))
    return profile


def run_all(target: str, *, tests_json: Path, dataset_root: Path, test_binary: Path) -> list[CoverageProfile]:
    # Validate target config exists and resolves cleanly before running.
    load_target_yaml(
        REPO_ROOT / "dataset" / "targets" / f"{target}.yaml",
        require_resolved=True,
    )
    # Restrict coverage to project-owned files so system headers don't leak in.
    source_roots = [str(REPO_ROOT / "dataset" / "targets" / "src" / target / "upstream")]

    with open(tests_json) as f:
        tests_raw = json.load(f)
    tests = [Test.model_validate(t) for t in tests_raw]

    profiles: list[CoverageProfile] = []
    for idx, test in enumerate(tests):
        out_dir = dataset_root / target / "tests" / f"test_{idx:04d}"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "test_code.cc").write_text(test.test_code)
        (out_dir / "upstream_location.json").write_text(json.dumps({
            "upstream_repo": test.upstream_repo,
            "upstream_commit": test.upstream_commit,
            "upstream_file": test.upstream_file,
            "upstream_line": test.upstream_line,
            "test_name": test.test_name,
        }, indent=2))

        profile = run_single_test(
            test,
            target=target,
            test_binary=test_binary,
            out_dir=out_dir,
            source_roots=source_roots,
        )
        profiles.append(profile)
    return profiles


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--tests-json", type=Path, default=None)
    parser.add_argument("--dataset-root", type=Path, default=REPO_ROOT / "dataset" / "dataset")
    parser.add_argument("--test-binary", type=Path, required=True)
    args = parser.parse_args()
    tests_json = args.tests_json or args.dataset_root / args.target / "tests.json"
    run_all(args.target, tests_json=tests_json, dataset_root=args.dataset_root, test_binary=args.test_binary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
