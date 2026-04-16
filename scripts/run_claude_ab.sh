#!/usr/bin/env bash
# E2/E3 — Claude ablation run across the 4 load-bearing cells.
#
# Usage:
#   scripts/run_claude_ab.sh claude-sonnet-4-6 claude_sonnet_results
#   scripts/run_claude_ab.sh claude-haiku-4-5-20251001 claude_haiku_results
#
# Runs the smoke check first; aborts if it fails. Unsets the LiteLLM env var
# so the client routes through Anthropic directly, and points
# UTCF_ANTHROPIC_KEY_PATH at the funded key so the existing driver scripts
# pick up Claude without a new --secrets-path flag.

set -euo pipefail

MODEL="${1:?usage: run_claude_ab.sh <model-id> <results-subdir>}"
RESULT_SUBDIR="${2:?usage: run_claude_ab.sh <model-id> <results-subdir>}"

cd "$(git rev-parse --show-toplevel)"

export UTCF_ANTHROPIC_KEY_PATH="${UTCF_ANTHROPIC_KEY_PATH:-secrets/claude_key}"
# Anthropic tier-1: 30k ITPM. Source cells use ~14-28k tokens per call.
# RPM=1 ensures ≥60s between calls, staying under the per-minute cap.
export UTCF_LLM_RPM="${UTCF_LLM_RPM:-1}"
unset UTCF_LITELLM_URL || true
unset UTCF_VLLM_URL || true

ROOT="dataset/fixtures/re2_ab/${RESULT_SUBDIR}"
DATASET_ROOT="dataset/fixtures/re2_ab"
SAMPLES="${SAMPLES:-3}"
PY=".venv/bin/python"

echo "[claude] smoke check on $MODEL"
$PY -m synthesis.scripts.claude_smoke_check --model "$MODEL"

COMMON=(--target re2 --model "$MODEL" --dataset-root "$DATASET_ROOT"
        --results-root "$ROOT" --samples "$SAMPLES")
# Cap source context to ~10k tokens to stay comfortably within 30k ITPM.
SOURCE_OPTS=(--source-token-budget 10000)

echo "[claude] exp1_full"
$PY -m synthesis.scripts.generate_ablation_inputs "${COMMON[@]}" \
    --cell exp1_full --include-tests --include-gaps

echo "[claude] exp2_source"
$PY -m synthesis.scripts.generate_ablation_inputs "${COMMON[@]}" "${SOURCE_OPTS[@]}" \
    --cell exp2_source --include-source

echo "[claude] exp2_plus_gaps"
$PY -m synthesis.scripts.generate_ablation_inputs "${COMMON[@]}" "${SOURCE_OPTS[@]}" \
    --cell exp2_plus_gaps --include-source --include-gaps

echo "[claude] exp1_gaps_only"
$PY -m synthesis.scripts.generate_ablation_inputs "${COMMON[@]}" \
    --cell exp1_gaps_only --include-gaps

echo "[claude] done — seeds under $ROOT/seeds/re2/ablation/*/$MODEL/"
