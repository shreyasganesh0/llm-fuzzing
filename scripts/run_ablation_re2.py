"""RE2 5-variant × 7-model ablation experiment driver (v2 design).

Thin wrapper around `scripts._ablation_base.AblationRunner`. Per-target
paths live in `core.targets.TARGETS["re2"]`; the variant grid in
`core.variants.STANDARD_VARIANTS`; per-model tuning in
`core.config.MODEL_DEFAULTS`.

v2 design: hard-branch filter `rand_hits == 0 AND struct_hits >= 1`,
150-seed normalization per cell, same 5 variants as harfbuzz.

Phases: prep | random | synthesis | m1 | m2 | all.
Outputs land under `results/ablation_re2_v2/`.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.targets import TARGETS  # noqa: E402
from core.variants import STANDARD_VARIANTS  # noqa: E402
from scripts._ablation_base import AblationRunner  # noqa: E402

MODELS = [
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
    "llama-3.1-8b-instruct",
    "llama-3.1-70b-instruct",
    "llama-3.3-70b-instruct",
    "codestral-22b",
    "nemotron-3-super-120b-a12b",
]

SONNET_ONLY_VARIANTS = {"v4_src_gaps"}
FREE_ONLY = True


def main() -> int:
    runner = AblationRunner(
        target=TARGETS["re2"],
        variants=STANDARD_VARIANTS,
        models=MODELS,
        sonnet_only_variants=SONNET_ONLY_VARIANTS,
        free_only=FREE_ONLY,
    )
    return runner.main()


if __name__ == "__main__":
    raise SystemExit(main())
