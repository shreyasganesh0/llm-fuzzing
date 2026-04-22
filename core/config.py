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
    # Ablation-runner tuning (see scripts/_ablation_base.py).
    provider: str = "litellm"          # anthropic | litellm | openai
    inputs_per_call: int = 3           # seeds requested per synthesis API call
    synthesis_max_tokens: int = 4096   # --max-tokens for synthesis calls
    worker_count: int = 4              # parallel synthesis calls per cell
    # Models whose UF LiteLLM response tends to hit the 2048-char cap when
    # asked for multi-blob binary output. Runner drops inputs_per_call to 1
    # on binary targets (harfbuzz). On text targets (RE2) this flag is only
    # consulted when the variant adds gap-reasoning that inflates output.
    output_capped_on_binary: bool = False
    # Phase 3 — constrained decoding support. Values reflect
    # results/probes/probe_json_mode.json (2026-04-21). LiteLLM-proxied
    # open-weights models accept all three flavors (plain, json_object,
    # json_schema, guided_json). Anthropic keys had zero credits during the
    # probe, so the flags stay False until Phase 7 wires tool-use emulation.
    # Downstream: `LLMClient.complete(response_format=...)` raises ValueError
    # when a caller tries to use structured output on a model whose flag is
    # False — this is the invariant that keeps constraints from being
    # silently dropped.
    supports_json_object: bool = False
    supports_json_schema: bool = False
    # Phase 7 — OpenAI-style tool-use support. True only for models the
    # Phase 0 probe (`results/probes/probe_tool_use.json`) verified as
    # emitting well-formed tool_calls via the LiteLLM proxy. The UF
    # LiteLLM proxy rejects llama-3.1/3.3 and codestral with
    # `--enable-auto-tool-choice` errors (proxy-side vLLM config issue);
    # Anthropic models had zero credits during the probe so we leave
    # them False pragmatically — not a capability limit. Downstream:
    # `LLMClient.complete(tools=...)` raises ValueError when a caller
    # tries to use tools on a model whose flag is False.
    supports_tool_use: bool = False


MODEL_DEFAULTS: dict[str, ModelDefaults] = {
    # OpenAI
    "gpt-4o-2024-08-06": ModelDefaults("gpt-4o-2024-08-06", provider="openai"),
    "gpt-4o-mini-2024-07-18": ModelDefaults("gpt-4o-mini-2024-07-18", provider="openai"),
    "o1-2024-12-17": ModelDefaults("o1-2024-12-17", max_tokens=4096, provider="openai"),
    # Anthropic
    "claude-3-5-sonnet-20241022": ModelDefaults(
        "claude-3-5-sonnet-20241022", provider="anthropic", inputs_per_call=4,
        synthesis_max_tokens=1200),
    "claude-sonnet-4-6": ModelDefaults(
        "claude-sonnet-4-6", provider="anthropic", inputs_per_call=4,
        synthesis_max_tokens=1200),
    "claude-haiku-4-5-20251001": ModelDefaults(
        "claude-haiku-4-5-20251001", provider="anthropic", inputs_per_call=4,
        synthesis_max_tokens=1200),
    "claude-opus-4-6": ModelDefaults(
        "claude-opus-4-6", provider="anthropic", inputs_per_call=4,
        synthesis_max_tokens=1200),
    # UF LiteLLM proxy — small/verbose models need headroom above the 1600 default.
    # supports_json_object / supports_json_schema = True only for models that
    # the Phase 0 probe actually verified (results/probes/probe_json_mode.json).
    "llama-3.1-8b-instruct": ModelDefaults(
        "llama-3.1-8b-instruct", max_tokens=8192, synthesis_max_tokens=8192,
        supports_json_object=True, supports_json_schema=True),
    "llama-3.1-70b-instruct": ModelDefaults(
        "llama-3.1-70b-instruct", max_tokens=4096, synthesis_max_tokens=4096,
        output_capped_on_binary=True,
        supports_json_object=True, supports_json_schema=True),
    "llama-3.3-70b-instruct": ModelDefaults(
        "llama-3.3-70b-instruct", max_tokens=4096, synthesis_max_tokens=4096,
        output_capped_on_binary=True,
        supports_json_object=True, supports_json_schema=True),
    "gpt-oss-20b": ModelDefaults(
        "gpt-oss-20b", max_tokens=4096,
        supports_json_object=True, supports_json_schema=True,
        supports_tool_use=True),
    "gpt-oss-120b": ModelDefaults("gpt-oss-120b", max_tokens=4096),
    "mistral-small-3.1": ModelDefaults("mistral-small-3.1", max_tokens=4096),
    "codestral-22b": ModelDefaults(
        "codestral-22b", max_tokens=4096, synthesis_max_tokens=4096,
        supports_json_object=True, supports_json_schema=True),
    "gemma-3-27b-it": ModelDefaults("gemma-3-27b-it", max_tokens=4096),
    "granite-3.3-8b-instruct": ModelDefaults("granite-3.3-8b-instruct", max_tokens=4096),
    "nemotron-3-nano-30b-a3b": ModelDefaults("nemotron-3-nano-30b-a3b", max_tokens=4096),
    "nemotron-3-super-120b-a12b": ModelDefaults(
        "nemotron-3-super-120b-a12b", max_tokens=4096, synthesis_max_tokens=4096,
        output_capped_on_binary=True,
        supports_json_object=True, supports_json_schema=True,
        supports_tool_use=True),
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


# ─── 4×2 ablation (variant × coverage-metric, RE2 only) ──────────────────
from pathlib import Path as _Path  # noqa: E402

_REPO_ROOT = _Path(__file__).resolve().parents[1]

M2_TARGETS_PATH = _REPO_ROOT / "dataset/fixtures/re2_ab/re2/m2_target_branches.json"
UPSTREAM_UNION_PROFILE_PATH = _REPO_ROOT / "dataset/fixtures/re2_ab/re2/upstream_union_profile.json"
M2_SMOKE_LOG_PATH = _REPO_ROOT / "dataset/fixtures/re2_ab/re2/m2_smoke_log.json"

# Per-target fixture paths — re-exports for scripts that pre-date `core.targets`.
# TargetSpec in `core/targets.py` is the source of truth; edit there, not here.
from core.targets import TARGETS as _TARGETS  # noqa: E402

RE2_V2_FIXTURES_DIR = _TARGETS["re2"].fixtures_dir
RE2_V2_M2_TARGETS_PATH = _TARGETS["re2"].m2_targets_path
RE2_V2_UPSTREAM_UNION_PROFILE_PATH = _TARGETS["re2"].upstream_union_profile_path
RE2_V2_M2_SMOKE_LOG_PATH = _TARGETS["re2"].m2_smoke_log_path

HB_FIXTURES_DIR = _TARGETS["harfbuzz"].fixtures_dir
HB_M2_TARGETS_PATH = _TARGETS["harfbuzz"].m2_targets_path
HB_UPSTREAM_UNION_PROFILE_PATH = _TARGETS["harfbuzz"].upstream_union_profile_path
HB_M2_SMOKE_LOG_PATH = _TARGETS["harfbuzz"].m2_smoke_log_path

M2_TARGET_COUNT = 50
M2_SHOWN_COUNT = 30
M2_RNG_SEED = 42
BOOTSTRAP_ITERS = 10_000
SOURCE_TOKEN_BUDGET_ALL_MODELS = 2_000  # reduced from 8k — source was 73% of input cost


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
