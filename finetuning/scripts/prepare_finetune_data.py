"""Convert Phase 1 dataset into Alpaca-style fine-tuning JSONL (plan §4.1).

CRITICAL: every example must carry provenance (upstream_repo, commit,
file, line). Examples without full provenance are dropped so the training
set never includes synthetic data we generated ourselves.

Splits are stratified by target so each split has tests from every target.
The test split MUST match Phase 2's held-out set (seed=42) — fine-tune
evaluation is compared against Phase 2 evaluation on the same tests.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.dataset_schema import CoverageProfile, Test
from core.logging_config import get_logger
from prediction.scripts.build_prompt import (
    _load_coverage,
    _load_tests,
    _source_excerpt,
    split_heldout,
)

logger = get_logger("utcf.phase4.prepare")

INSTRUCTION = "Predict coverage for this unit test given the source code."


def _has_provenance(t: Test) -> bool:
    return bool(t.upstream_repo and t.upstream_commit and t.upstream_file and t.upstream_line)


def _to_alpaca(test: Test, coverage: CoverageProfile | None, source: str) -> dict:
    cov_dict = coverage.model_dump() if coverage else {}
    return {
        "instruction": INSTRUCTION,
        "input": f"{test.test_code}\n---\n{source}",
        "output": json.dumps(cov_dict),
        "metadata": {
            "upstream_repo": test.upstream_repo,
            "upstream_commit": test.upstream_commit,
            "upstream_file": test.upstream_file,
            "upstream_line": test.upstream_line,
            "target": getattr(test, "target", None),
            "test_name": test.test_name,
        },
    }


def prepare(
    *,
    dataset_root: Path,
    out_dir: Path,
    targets: list[str],
    upstream_root: Path | None = None,
    seed: int = 42,
    train_ratio: float = 0.8,
    val_ratio: float = 0.1,
) -> dict[str, int]:
    assert abs(train_ratio + val_ratio + (1 - train_ratio - val_ratio) - 1.0) < 1e-9
    rng = random.Random(seed)

    per_target_train: dict[str, list[dict]] = defaultdict(list)
    per_target_val: dict[str, list[dict]] = defaultdict(list)
    per_target_test: dict[str, list[dict]] = defaultdict(list)
    dropped_no_provenance = 0

    for target in targets:
        tests = _load_tests(dataset_root, target)
        coverage = _load_coverage(dataset_root, target)
        # Phase 2 held-out = test split for fine-tuning to keep splits aligned.
        heldout, train_pool = split_heldout(tests)

        target_upstream = (upstream_root or REPO_ROOT / "dataset" / "targets" / "src") / target / "upstream"
        for t in heldout:
            if not _has_provenance(t):
                dropped_no_provenance += 1
                continue
            per_target_test[target].append(
                _to_alpaca(t, coverage.get(t.test_name), _source_excerpt(t, target_upstream, "file"))
            )

        rng.shuffle(train_pool)
        n_val = max(1, int(len(train_pool) * val_ratio / (train_ratio + val_ratio)))
        val_tests = train_pool[:n_val]
        tr_tests = train_pool[n_val:]
        for t in tr_tests:
            if not _has_provenance(t):
                dropped_no_provenance += 1
                continue
            per_target_train[target].append(
                _to_alpaca(t, coverage.get(t.test_name), _source_excerpt(t, target_upstream, "file"))
            )
        for t in val_tests:
            if not _has_provenance(t):
                dropped_no_provenance += 1
                continue
            per_target_val[target].append(
                _to_alpaca(t, coverage.get(t.test_name), _source_excerpt(t, target_upstream, "file"))
            )

    out_dir.mkdir(parents=True, exist_ok=True)

    def _write(path: Path, rows_by_target: dict[str, list[dict]]) -> int:
        rows = [row for items in rows_by_target.values() for row in items]
        rng.shuffle(rows)
        with open(path, "w") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        return len(rows)

    counts = {
        "train": _write(out_dir / "train.jsonl", per_target_train),
        "val": _write(out_dir / "val.jsonl", per_target_val),
        "test": _write(out_dir / "test.jsonl", per_target_test),
        "dropped_no_provenance": dropped_no_provenance,
    }
    (out_dir / "split_counts.json").write_text(json.dumps(counts, indent=2))
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, default=REPO_ROOT / "dataset" / "dataset")
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "finetuning" / "results" / "finetune_data")
    parser.add_argument("--targets", nargs="+", required=True)
    args = parser.parse_args()
    counts = prepare(dataset_root=args.dataset_root, out_dir=args.out_dir, targets=args.targets)
    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
