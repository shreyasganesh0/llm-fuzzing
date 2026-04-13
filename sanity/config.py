"""Mini-experiment (exp1_b, exp2_b) constants — see docs/STATUS.md.

The mini setup is a budget-bounded ~$1 smoke test that exercises the real
LLM pipeline against a few real RE2 tests, without requiring LLVM /
libFuzzer / GPUs. It uses the LiteLLM proxy at UTCF_LITELLM_URL.

Invariants vs the full experiments:
  - Same dataset / prompt / parsing code paths.
  - Same PredictionRecord + SynthesisRecord schemas.
  - Same assert_no_tests guard for Exp 2.
Differences:
  - Fewer tests, fewer samples, smaller few-shot, no libFuzzer step.
  - Models come from the LiteLLM proxy (no GPT-4o / Claude here).
"""
from __future__ import annotations

# Keep fixture_size == HELDOUT_SIZE + SANITY_FEW_SHOT so the split produces
# exactly HELDOUT_SIZE prediction calls with 1 example per prompt.
SANITY_FIXTURE_TESTS = 15
SANITY_FEW_SHOT = 1
SANITY_SAMPLES = 3
SANITY_SEEDS_PER_PROMPT = 5

# UF LiteLLM proxy defaults. Override via CLI flags.
SANITY_MODEL_PRIMARY = "llama-3.1-8b-instruct"
SANITY_MODEL_SECONDARY = "gpt-oss-20b"

SANITY_TARGET = "re2"
SANITY_RPM = 12

# UF proxy enforces ~27904 total tokens (input+output) for llama-3.1-8b, even
# though the model advertises 128K context. Keep inputs ≤ ~14K so exp2
# (13.5K input) + 12K output stays under the cap with headroom.
SANITY_SOURCE_MAX_FILES = 4
SANITY_SOURCE_TOKEN_BUDGET = 20_000
SANITY_EXP2_MAX_OUTPUT_TOKENS = 12_288
