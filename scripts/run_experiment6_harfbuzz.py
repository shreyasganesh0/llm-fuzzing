"""experiment6 over-generation driver — harfbuzz / codestral-22b only.

This is the SAME shape as `scripts/run_ablation_harfbuzz.py` (a thin
wrapper around the UNMODIFIED `scripts._ablation_base.AblationRunner` —
all five phases + attempt-offset / cache-salt semantics come from there,
nothing is reimplemented here). The only differences, both forced by the
experiment6 design and approved by the user (see
`docs/experiment6/MANIFEST.json` → `deviations_from_prompt`):

  1. The harfbuzz `TargetSpec` is `dataclasses.replace`'d so
     `synthesis_results_root` / `results_root` point at an experiment6
     sandbox. Every other field — `fixtures_dir`, `coverage_binary`,
     `prep_dataset_root`, `source_roots`, `input_format` — is left
     untouched, so prompts are identical to experiment2_1's and M2 is
     scored against the IDENTICAL frozen 50-branch set. The canonical
     experiment2_1 seed/result dirs are never written.

  2. `UTCF_CAPTURE_LOGPROBS` is exported so the synthesis subprocess
     (default strategy only) requests + persists per-token logprobs.
     This is opt-in and default-off everywhere else; the cache key for
     non-logprob callers is byte-identical (guarded by
     `tests/test_llm_client_logprob_backcompat.py`).

Scope is pinned to codestral-22b × {v1_src, v3_all} × default strategy.
Pool size is driven from the CLI (`--num-seeds 450`); every *scored*
corpus is still exactly 150 (Invariant 4) — the 150-subsampling happens
downstream in `analysis/scripts/experiment6_stratify.py`, not here.

Typical invocation (background, per scripts/CLAUDE.md):

    nohup .venv/bin/python scripts/run_experiment6_harfbuzz.py \
        --phase synthesis --variants v1_src,v3_all \
        --only-models codestral-22b --num-seeds 450 \
        --attempt-offset 600000 >> /tmp/exp6_hb.log 2>&1 &
"""
from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.targets import TARGETS  # noqa: E402
from core.variants import STANDARD_VARIANTS  # noqa: E402
from scripts._ablation_base import AblationRunner  # noqa: E402

# experiment6 sandbox — keeps the 450-seed pool + logprob sidecars +
# per-cell metric output entirely separate from experiment2_1.
EXP6_SYNTH_ROOT = REPO_ROOT / "synthesis" / "results" / "experiment6"
EXP6_RESULTS_ROOT = REPO_ROOT / "results" / "experiment6"

# Pinned scope (MANIFEST.json). codestral-22b is a free LiteLLM model so
# FREE_ONLY=True does not skip it; Sonnet variants are irrelevant here.
MODELS = ["codestral-22b"]
EXP6_VARIANT_NAMES = {"v1_src", "v3_all"}
SONNET_ONLY_VARIANTS: set[str] = set()
FREE_ONLY = True

# Top-K logprob alternatives to request (METHODS §4; Stage 0 verified
# codestral-22b returns the full K=20 head).
LOGPROBS_TOPK = "20"


def _experiment6_target():
    """harfbuzz TargetSpec with output roots redirected to the sandbox.

    Frozen-dataclass-safe via dataclasses.replace. M2 fixture paths are
    @property-derived from `fixtures_dir`, which is intentionally NOT
    replaced, so scoring uses the real frozen artifacts.
    """
    hb = TARGETS["harfbuzz"]
    return dataclasses.replace(
        hb,
        synthesis_results_root=EXP6_SYNTH_ROOT,
        results_root=EXP6_RESULTS_ROOT,
    )


def main() -> int:
    # Export the env BEFORE AblationRunner builds its subprocess env
    # (AblationRunner._env_for_model does os.environ.copy()), so the
    # synthesis driver in the child process sees it.
    os.environ["UTCF_CAPTURE_LOGPROBS"] = "1"
    os.environ.setdefault("UTCF_LOGPROBS_TOPK", LOGPROBS_TOPK)

    variants = [v for v in STANDARD_VARIANTS if v.name in EXP6_VARIANT_NAMES]
    runner = AblationRunner(
        target=_experiment6_target(),
        variants=variants,
        models=MODELS,
        sonnet_only_variants=SONNET_ONLY_VARIANTS,
        free_only=FREE_ONLY,
    )
    return runner.main()


if __name__ == "__main__":
    raise SystemExit(main())
