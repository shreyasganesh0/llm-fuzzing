"""Unified configuration registry for the UTCF pipeline.

Merged from the previous per-phase config modules:
  prediction/scripts/config.py, synthesis/scripts/config.py,
  transfer/scripts/config.py, and the source-only (exp2) config.

Plan: §LLM Parameters fixes temperature/top_p/samples for synthesis;
campaign params follow FuzzBench gold standard (20 trials × 23h).
"""
from __future__ import annotations

from dataclasses import dataclass

from core.llm_client import PRICING_USD_PER_MTOK, detect_provider  # noqa: F401 — re-export

# ─── Prediction (Phase 2) ────────────────────────────────────────────────
SEED_HELDOUT = 42
SEED_FEW_SHOT = 42
SEED_CONTAMINATION = 123

HELDOUT_SIZE = 5
FEW_SHOT_SIZES = (0, 1, 3, 5, 10)
CONTEXT_SIZES = ("function_only", "file", "multi_file")


@dataclass(frozen=True)
class ModelDefaults:
    model: str
    temperature: float = 0.0
    top_p: float = 1.0
    max_tokens: int = 1600


MODEL_DEFAULTS: dict[str, ModelDefaults] = {
    "gpt-4o-2024-08-06": ModelDefaults("gpt-4o-2024-08-06"),
    "gpt-4o-mini-2024-07-18": ModelDefaults("gpt-4o-mini-2024-07-18"),
    "o1-2024-12-17": ModelDefaults("o1-2024-12-17", max_tokens=4096),
    "claude-3-5-sonnet-20241022": ModelDefaults("claude-3-5-sonnet-20241022"),
    "claude-sonnet-4-6": ModelDefaults("claude-sonnet-4-6"),
    "claude-opus-4-6": ModelDefaults("claude-opus-4-6"),
    # UF LiteLLM proxy — small/verbose models need headroom above the 1600 default.
    "llama-3.1-8b-instruct": ModelDefaults("llama-3.1-8b-instruct", max_tokens=8192),
    "llama-3.1-70b-instruct": ModelDefaults("llama-3.1-70b-instruct", max_tokens=4096),
    "llama-3.3-70b-instruct": ModelDefaults("llama-3.3-70b-instruct", max_tokens=4096),
    "gpt-oss-20b": ModelDefaults("gpt-oss-20b", max_tokens=4096),
    "gpt-oss-120b": ModelDefaults("gpt-oss-120b", max_tokens=4096),
    "mistral-small-3.1": ModelDefaults("mistral-small-3.1", max_tokens=4096),
    "codestral-22b": ModelDefaults("codestral-22b", max_tokens=4096),
    "gemma-3-27b-it": ModelDefaults("gemma-3-27b-it", max_tokens=4096),
    "granite-3.3-8b-instruct": ModelDefaults("granite-3.3-8b-instruct", max_tokens=4096),
    "nemotron-3-nano-30b-a3b": ModelDefaults("nemotron-3-nano-30b-a3b", max_tokens=4096),
    "nemotron-3-super-120b-a12b": ModelDefaults("nemotron-3-super-120b-a12b", max_tokens=4096),
}


def defaults(model: str) -> ModelDefaults:
    return MODEL_DEFAULTS.get(model, ModelDefaults(model))


# ─── Synthesis + campaigns (Phase 3) ─────────────────────────────────────
SYNTHESIS_TEMPERATURE = 0.7
SYNTHESIS_TOP_P = 0.95
SYNTHESIS_SAMPLES = 3
SYNTHESIS_MAX_TOKENS = 4096

DEFAULT_GAPS_PER_PROMPT = 20
DEFAULT_INPUTS_PER_PROMPT = 10

CAMPAIGN_TRIALS = 20
CAMPAIGN_DURATION_S = 82_800
CAMPAIGN_SNAPSHOT_S = 900
SEED_BASE = 1000

VARGHA_DELANEY_THRESHOLDS = (
    (0.56, "small"),
    (0.64, "medium"),
    (0.71, "large"),
)


# ─── Source-only ablation (Exp 2) ────────────────────────────────────────
# Token budgets per model (plan §E2.1).
TOKEN_BUDGET = {
    "gpt-4o-2024-08-06": 100_000,
    "claude-sonnet-4-6": 160_000,
    "claude-3-5-sonnet-20241022": 160_000,
    "llama-3-70b": 100_000,
}
DEFAULT_TOKEN_BUDGET = 100_000

# ±20% budget-matching tolerance vs Experiment 1 (plan §E2.4).
BUDGET_MATCH_TOLERANCE = 0.20

PREDICTION_TEMPERATURE = 0.0
PREDICTION_TOP_P = 1.0
PREDICTION_MAX_TOKENS = 4096

SOURCE_CONTEXT_MAX_FILES = 40


# ─── Transfer (Phase Transfer / LOO + Tier 3) ────────────────────────────
TIER12_TARGETS = ("re2", "libxml2", "sqlite3", "libjpeg-turbo", "lcms", "harfbuzz", "proj", "ffmpeg")
TIER3_TARGETS = ("libpng", "freetype", "zlib")

TEXT_TARGETS = {"re2", "libxml2", "sqlite3", "proj"}
BINARY_TARGETS = {"libjpeg-turbo", "lcms", "libpng", "harfbuzz", "freetype", "zlib", "ffmpeg"}

LOO_FEW_SHOT = 5
LOO_MIN_DISTINCT_SOURCE_TARGETS = 3

TIER3_CAMPAIGN_TRIALS = 5
TIER3_CAMPAIGN_DURATION_S = 21_600  # 6 hours


def format_pair(a: str, b: str) -> str:
    """Return text_text / text_binary / binary_binary for a pair of targets."""
    a_text = a in TEXT_TARGETS
    b_text = b in TEXT_TARGETS
    if a_text and b_text:
        return "text_text"
    if a_text != b_text:
        return "text_binary"
    return "binary_binary"
