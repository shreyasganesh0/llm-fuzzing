#!/usr/bin/env bash
# E1 — n=6 llama re-run of the 4 load-bearing ablation cells to tighten the
# ±noise bars on `exp2_plus_gaps > exp1_full` and the `exp1_gaps_only` collapse.
#
# UF LiteLLM proxy; RPM=12 throttle already tuned in prior runs.
# Results root isolates this experiment from prior n=3 artefacts.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

export UTCF_LITELLM_URL="${UTCF_LITELLM_URL:-https://api.ai.it.ufl.edu}"
export UTCF_LLM_RPM="${UTCF_LLM_RPM:-12}"

ROOT="dataset/fixtures/re2_ab/n6_llama_results"
MODEL="llama-3.1-8b-instruct"
DATASET_ROOT="dataset/fixtures/re2_ab"
SAMPLES="${SAMPLES:-6}"

PY=".venv/bin/python"
COMMON=(--target re2 --model "$MODEL" --dataset-root "$DATASET_ROOT"
        --results-root "$ROOT" --samples "$SAMPLES")
# llama-3.1-8b has 32k context; source-heavy cells need a token budget cap
# to leave room for the system prompt + max_tokens output.
SOURCE_OPTS=(--source-token-budget 20000 --max-tokens 2048)

echo "[e1] exp1_full"
$PY -m synthesis.scripts.generate_ablation_inputs "${COMMON[@]}" \
    --cell exp1_full --include-tests --include-gaps

echo "[e1] exp2_source"
$PY -m synthesis.scripts.generate_ablation_inputs "${COMMON[@]}" "${SOURCE_OPTS[@]}" \
    --cell exp2_source --include-source

echo "[e1] exp2_plus_gaps"
$PY -m synthesis.scripts.generate_ablation_inputs "${COMMON[@]}" "${SOURCE_OPTS[@]}" \
    --cell exp2_plus_gaps --include-source --include-gaps

echo "[e1] exp1_gaps_only"
$PY -m synthesis.scripts.generate_ablation_inputs "${COMMON[@]}" \
    --cell exp1_gaps_only --include-gaps

echo "[e1] done — seeds under $ROOT/seeds/re2/ablation/*/$MODEL/"
