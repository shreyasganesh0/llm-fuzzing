#!/usr/bin/env bash
# Campaign launcher: runs libFuzzer + AFL++ campaigns on RE2 and harfbuzz.
#
# Configuration: short concept-proof campaigns (1h, 5 trials, 60s snapshots).
# Override with env vars: DURATION, TRIALS, SNAP, PARALLEL.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

PY="${REPO_ROOT}/.venv/bin/python"
DURATION="${DURATION:-3600}"
TRIALS="${TRIALS:-5}"
SNAP="${SNAP:-60}"
PARALLEL="${PARALLEL:-4}"
WORK_LF="${REPO_ROOT}/synthesis/results/campaigns"
WORK_AFL="${REPO_ROOT}/synthesis/results/campaigns_afl"

# Binaries
RE2_LF="dataset/targets/src/re2/build/fuzzer/re2_fuzzer"
RE2_AFL="dataset/targets/src/re2/build/afl/re2_afl_fuzzer"
HB_LF="dataset/targets/src/harfbuzz/build/fuzzer/harfbuzz_fuzzer"
HB_AFL="dataset/targets/src/harfbuzz/build/afl/harfbuzz_afl_fuzzer"

# Seed directories
RE2_LLM_SEEDS="dataset/fixtures/re2_ab/claude_sonnet_results/seeds/re2/ablation/exp1_full/claude-sonnet-4-6"
RE2_RANDOM_SEEDS="synthesis/results/seeds/re2/seeds/re2/random"
HB_LLM_SEEDS="synthesis/results/seeds/harfbuzz/seeds/harfbuzz/exp1/claude-haiku-4-5-20251001"
HB_RANDOM_SEEDS="synthesis/results/seeds/harfbuzz/seeds/harfbuzz/random"

# Config files
EMPTY_CFG="synthesis/campaign_configs/empty.yaml"
LLM_CFG="synthesis/campaign_configs/llm_seeds.yaml"
RANDOM_CFG="synthesis/campaign_configs/random_seeds.yaml"

run_libfuzzer() {
  local target="$1" binary="$2" config="$3" seed_dir="$4"
  local args=(
    "$PY" -m synthesis.scripts.run_fuzzing
    --config "$config" --target "$target" --binary "$binary"
    --trials "$TRIALS" --duration-s "$DURATION" --snapshot-interval-s "$SNAP"
    --work-root "$WORK_LF"
  )
  [ -n "$seed_dir" ] && args+=(--seed-corpus-dir "$seed_dir")
  echo "[libFuzzer] ${target} $(basename "$config" .yaml) starting..."
  "${args[@]}" 2>&1 | tail -1
}

run_aflpp() {
  local target="$1" binary="$2" config="$3" seed_dir="$4"
  local args=(
    "$PY" -m synthesis.scripts.run_afl_fuzzing
    --config "$config" --target "$target" --binary "$binary"
    --trials "$TRIALS" --duration-s "$DURATION" --snapshot-interval-s "$SNAP"
    --work-root "$WORK_AFL"
  )
  [ -n "$seed_dir" ] && args+=(--seed-corpus-dir "$seed_dir")
  echo "[AFL++] ${target} $(basename "$config" .yaml) starting..."
  "${args[@]}" 2>&1 | tail -1
}

echo "==> Campaign matrix: 2 targets x 2 engines x 3 configs x ${TRIALS} trials"
echo "==> Duration: ${DURATION}s, Snapshots: every ${SNAP}s"
echo "==> Parallel slots: ${PARALLEL}"

# Run all 12 cells. Use GNU parallel if available, otherwise sequential with
# background jobs.

run_all() {
  # RE2 libFuzzer
  run_libfuzzer re2 "$RE2_LF" "$EMPTY_CFG" "" &
  run_libfuzzer re2 "$RE2_LF" "$LLM_CFG" "$RE2_LLM_SEEDS" &
  run_libfuzzer re2 "$RE2_LF" "$RANDOM_CFG" "$RE2_RANDOM_SEEDS" &

  # RE2 AFL++
  run_aflpp re2 "$RE2_AFL" "$EMPTY_CFG" "" &
  wait  # throttle to PARALLEL jobs
  run_aflpp re2 "$RE2_AFL" "$LLM_CFG" "$RE2_LLM_SEEDS" &
  run_aflpp re2 "$RE2_AFL" "$RANDOM_CFG" "$RE2_RANDOM_SEEDS" &

  # Harfbuzz libFuzzer
  run_libfuzzer harfbuzz "$HB_LF" "$EMPTY_CFG" "" &
  run_libfuzzer harfbuzz "$HB_LF" "$LLM_CFG" "$HB_LLM_SEEDS" &
  wait
  run_libfuzzer harfbuzz "$HB_LF" "$RANDOM_CFG" "$HB_RANDOM_SEEDS" &

  # Harfbuzz AFL++
  run_aflpp harfbuzz "$HB_AFL" "$EMPTY_CFG" "" &
  run_aflpp harfbuzz "$HB_AFL" "$LLM_CFG" "$HB_LLM_SEEDS" &
  run_aflpp harfbuzz "$HB_AFL" "$RANDOM_CFG" "$HB_RANDOM_SEEDS" &

  wait
}

run_all
echo "==> All campaigns complete. Results in:"
echo "    ${WORK_LF}"
echo "    ${WORK_AFL}"
