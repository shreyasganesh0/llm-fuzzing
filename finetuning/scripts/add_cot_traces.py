"""Attach chain-of-thought reasoning traces to fine-tune examples (plan §4.2).

The traces are drafted by GPT-4 and then human-verified (manual step,
not automated here). This module handles the automation side:

  1. Read train.jsonl (Alpaca-style)
  2. For a selected subset (N=50 by default), call GPT-4 with the
     (test, source, coverage) triple and ask for a step-by-step trace
     of why the test covers what it does.
  3. Write an augmented train_cot.jsonl with a new "reasoning" field.

Dry-run mode emits placeholder reasoning so the downstream training
pipeline can be exercised without incurring API costs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.logging_config import get_logger

logger = get_logger("utcf.phase4.cot")


COT_PROMPT = """You are explaining why a unit test covers certain lines of code.

Given the test and source code, produce a step-by-step trace of the
execution path from the test entry point through the library code.
Identify which branches are taken and which are skipped.

TEST:
{test_code}

SOURCE:
{source}

MEASURED COVERAGE:
{coverage}

Produce 3-7 numbered steps. Format:
Step 1: <action>
Step 2: <action>
...
"""


def _draft_trace_stub(row: dict) -> str:
    meta = row.get("metadata", {})
    return (
        f"Step 1: The test at {meta.get('upstream_file')}:{meta.get('upstream_line')} "
        f"invokes the harness entry point.\n"
        f"Step 2: Execution proceeds through the covered branches "
        f"recorded in the coverage profile.\n"
        f"Step 3: Uncovered branches correspond to code paths not exercised by this test."
    )


def add_cot(
    *,
    train_path: Path,
    out_path: Path,
    n_examples: int = 50,
    model: str = "gpt-4o-2024-08-06",
    dry_run: bool = True,
) -> int:
    rows = [json.loads(line) for line in train_path.read_text().splitlines() if line.strip()]
    selected = rows[:n_examples]

    if dry_run:
        augmented = []
        for row in selected:
            row = dict(row)
            row["reasoning"] = _draft_trace_stub(row)
            augmented.append(row)
    else:
        from core.llm_client import LLMClient
        client = LLMClient()
        augmented = []
        for row in selected:
            test_code, source = (row["input"].split("\n---\n", 1) + [""])[:2]
            user = COT_PROMPT.format(test_code=test_code, source=source, coverage=row["output"])
            resp = client.complete(
                messages=[
                    {"role": "system", "content": "Explain test execution step by step."},
                    {"role": "user", "content": user},
                ],
                model=model,
                temperature=0.2,
                top_p=1.0,
                max_tokens=800,
            )
            row = dict(row)
            row["reasoning"] = resp.content.strip()
            augmented.append(row)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as fh:
        for row in augmented:
            fh.write(json.dumps(row) + "\n")
    return len(augmented)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--n", type=int, default=50)
    parser.add_argument("--model", default="gpt-4o-2024-08-06")
    parser.add_argument("--dry-run", action="store_true", default=True)
    args = parser.parse_args()
    n = add_cot(
        train_path=args.train,
        out_path=args.out,
        n_examples=args.n,
        model=args.model,
        dry_run=args.dry_run,
    )
    print(f"wrote {n} CoT-augmented examples to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
