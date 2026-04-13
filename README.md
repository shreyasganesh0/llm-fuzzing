# UTCF — Unit Test–Conditioned LLM-Guided Fuzzing

Research framework that extracts real upstream unit tests from FuzzBench targets, measures
per-test coverage, prompts LLMs to predict coverage and synthesize gap-filling inputs, and
evaluates the resulting seeds via libFuzzer campaigns following the FuzzBench gold standard.

See `docs/research_document_v3.md` for the research design and `docs/claude_code_plan_v3.md`
for the execution plan. Those are the authoritative specs; this README is a quick-start.

**Resuming work?** Read `docs/STATUS.md` first — it captures what's done vs deferred,
non-obvious design decisions from prior sessions, and the verify-current-state commands.

## Quick start

```bash
# 1. Python env
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. LLM keys (already present: secrets/llm_key)
#    Add secrets/anthropic_key to enable Claude.

# 3. Sanity-check pinned_versions.yaml for the target(s) you plan to run
make pin-versions

# 4. Run tests (no network, no LLM, no LLVM required)
pytest dataset/tests/ prediction/tests/
```

## Running the pipeline for RE2

Requires: LLVM 15+ (`clang`, `llvm-cov`, `llvm-profdata`), git, network access, an LLM key.

```bash
# Phase 1 — dataset construction
./dataset/scripts/fetch_target.sh dataset/targets/re2.yaml
./dataset/scripts/build_instrumented.sh dataset/targets/re2.yaml
python dataset/scripts/build_dataset.py --target re2
python dataset/scripts/contamination_probe.py --target re2 --model gpt-4o-2024-08-06

# Phase 2 — LLM coverage prediction
python prediction/scripts/run_prediction.py \
  --target re2 --model gpt-4o-2024-08-06 --few-shot 5
python prediction/scripts/evaluate_prediction.py --target re2
```

## Status

All pipeline scripts are implemented and green under `make test` (96 tests).
Cluster-bound steps (Phase 3 campaigns, Phase 4 training, Experiment 2 campaigns)
can be exercised with `DRY_RUN=1 make <target>`.

Only **RE2** has real pinned SHAs; other targets have `<FILL>` placeholders
and `build_instrumented.sh` only implements the RE2 branch. See
`docs/STATUS.md` for the full what-works / what-is-blocked breakdown.

## Running the full pipeline (dry-run)

```bash
DRY_RUN=1 make all
```

This walks every phase — Phase 1→2 prediction, Phase 3 synthesis / fuzzing /
dedup / stats, Phase Transfer LOO, Tier 3 held-out, Phase 4 fine-tuning
skeleton, Experiment 2 source-only, and the Config A-I comparison table —
without requiring LLM calls, LLVM instrumentation, a GPU, or the 29,440 CPU
hours of real campaigns.

## Repository layout

Matches `docs/claude_code_plan_v3.md` §Repository Structure.

## Critical constraints

- **Provenance is sacred** — every test object traces back to `upstream_repo:commit:file:line`.
- **No fabricated tests** — extractors only read upstream code.
- **FuzzBench methodology** — 20 trials × 23 h, Mann-Whitney U, Vargha-Delaney Â₁₂, Friedman-Nemenyi.
- **Deterministic LLM params** — temperature=0.0 for prediction, 0.7/0.95/3 samples for synthesis.

## Contribution notes

Only modify upstream extraction to support new frameworks. Never edit upstream test code.
Every result should be accompanied by contamination risk level and provenance provenance.
