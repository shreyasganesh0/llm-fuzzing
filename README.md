# UTCF — LLM-Guided Fuzzing Seed Synthesis

Research framework for LLM-guided fuzzing seed corpus synthesis. Given a coverage-instrumented
target binary and its upstream test suite, the framework identifies hard-to-reach branches,
prompts an LLM to synthesize inputs targeting those branches, and evaluates the resulting seeds
via seed-time coverage metrics (M1: total edges, M2: hard-branch hit rate).

**Resuming work?** Read `docs/STATUS.md` first (living handoff), then
`docs/experiment2.md` for the latest results and
`docs/FUTURE_DIRECTIONS.md` for pending experiments.

## Current status

Two targets, 7 models, 5-variant ablation (150 seeds/cell). LiteLLM-served
open models are fully run on both targets; Claude Sonnet/Haiku on harfbuzz
is partial. Numbers below are from `experiment2_1` (the current headline);
each row links to the per-target themes section in `experiment2.md`.

| Target | Format | Best M1 vs random | Best M2 | Detail |
|---|---|---|---|---|
| RE2 (regex engine) | text | **+335 edges (+28.9%)** at `v2_src_tests` (codestral-22b) | **0.867** at `v1_src` (nemotron-120b) | [§4.1 RE2 themes »](docs/experiment2.md#re2-themes) |
| harfbuzz (font shaper) | binary | **+444 edges (+80%)** at `v0_none` (sonnet) | **0.640** at `v0_none` (sonnet) | [§4.2 harfbuzz themes »](docs/experiment2.md#hb-themes) |

Cumulative LLM spend on disk (`.cache/llm/`): **$100.09** across 14,161
cached responses (Anthropic $86.25, UF LiteLLM-accounted $13.83). See
`results/cost_audit/summary.md` — regenerate with
`.venv/bin/python -m analysis.scripts.cost_audit`.

## Quick start (env + tests)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run tests (no network, no LLM, no LLVM required).
.venv/bin/pytest -q
```

To run any actual experiment you also need the dependencies in the next
section.

## External dependencies and where they go

| Dep | Required by | Where it lives | How to install |
|---|---|---|---|
| **LLVM 15** (`clang-15`, `clang++-15`, `llvm-cov-15`, `llvm-profdata-15`) | every experiment (build + coverage replay) | `$PATH` | `apt install clang-15 llvm-15` (override via `CC` / `CXX` / `LLVM_PROFDATA` / `LLVM_COV` env vars). |
| **Ragel** (`ragel`) | harfbuzz build only (Ragel-generated `.hh` files) | `$PATH` | `apt install ragel`. The build script aborts with a clear error if missing. |
| **Autotools** (`autoconf`, `automake`, `libtool`, `pkg-config`, `make`) | harfbuzz build (`./autogen.sh && ./configure`) | `$PATH` | `apt install autoconf automake libtool pkg-config make`. |
| **AFL++** | `experiment2_2` campaigns only (skip if you only run seed-time experiments) | `/home/shreyasganesh/tools/aflpp/` (`afl-clang-fast`, `afl-clang-fast++`, `afl-fuzz`) — hardcoded in `dataset/scripts/build_instrumented.sh` lines 43–44 and overridable via `AFL_CC` / `AFL_CXX` env vars | Build from source: `git clone https://github.com/AFLplusplus/AFLplusplus ~/tools/aflpp && cd ~/tools/aflpp && make`. |
| **Target source code** (RE2, harfbuzz upstream) | every experiment | `dataset/targets/src/<name>/upstream/` (gitignored) | Auto-fetched by `dataset/scripts/fetch_target.sh dataset/targets/<name>.yaml` from the upstream repo at the SHA pinned in `pinned_versions.yaml`. |
| **FuzzBench harness** (`fuzzer-test-suite/<benchmark>/target.cc`, dictionaries) | every experiment | `dataset/targets/src/<name>/harness/` (gitignored) | Auto-fetched by `fetch_target.sh` via `curl` against `raw.githubusercontent.com/google/fuzzer-test-suite` (or `oss-fuzz`) at the commit pinned in `pinned_versions.yaml::fuzzer_test_suite.commit`. **No separate clone.** |
| **Build artefacts** (`seed_replay`, `<t>_fuzzer`, `<t>_afl_fuzzer`) | every experiment (coverage replay needs `seed_replay`; `experiment2_2` needs `_fuzzer` and `_afl_fuzzer`) | `dataset/targets/src/<name>/build/{coverage,sanitizer,fuzzer,afl}/` (gitignored) | Built by `dataset/scripts/build_instrumented.sh dataset/targets/<name>.yaml`. |
| **Anthropic API key** | only Claude cells (Sonnet, Haiku) | `secrets/claude_key` (single line, gitignored) | Generate at console.anthropic.com. |
| **UF LiteLLM proxy URL** | every UF LiteLLM-served model (llama, codestral, nemotron, gpt-oss) | env var only | `export UTCF_LITELLM_URL=https://api.ai.it.ufl.edu` |
| **UF LiteLLM proxy virtual key** | same as above | `secrets/llm_key` (single line, gitignored; the key must start with `sk-`) | Issued by the proxy operator. Without this, the proxy returns `401 Authentication Error, LiteLLM Virtual Key expected`. Vendor invoice on the LiteLLM side is $0 but responses truncate at 2048 chars. |
| **Frozen M2 hard-branch sets** | every M2 measurement | `dataset/fixtures/{re2_ab_v2,harfbuzz_ab}/<target>/m2_target_branches.json` (checked in — small JSON) | Already present. **Do not regenerate** — re-running `analysis.scripts.freeze_target_branches` invalidates all prior comparisons. |
| **LLM cache** | optional (cheap-replay) | `.cache/llm/` (gitignored) | Populated lazily; persists across runs. |

## Usage — pick an experiment

Three experiment lines have run on this codebase. Each has a one-doc
writeup with **all flag detail, every sub-version, all per-cell
numbers**, and full reproduction commands. The blocks below are the
**default-flags happy path** — enough to start the canonical run
without reading anything else.

### Experiment 1 — RE2 single-model A/B (RE2 + llama-3.1-8b)

End-to-end reproduction of the 2026-04-13 regex-format A/B headline
(`experiment1_1`): gap-targeted prompt vs source-only prompt on RE2
with llama-3.1-8b. About **$0.01** spend, **~2 min** wall-clock for the
synthesis stage; coverage replay is single-digit seconds.

```bash
source .venv/bin/activate

# Step 1 — build the coverage-instrumented RE2 binary (skip if already built).
./dataset/scripts/fetch_target.sh       dataset/targets/re2.yaml
./dataset/scripts/build_instrumented.sh dataset/targets/re2.yaml

# Step 2 — synthesize the two cells via the UF LiteLLM proxy.
export UTCF_LITELLM_URL=https://api.ai.it.ufl.edu
export UTCF_LLM_RPM=12

.venv/bin/python -m synthesis.scripts.generate_inputs \
    --target re2 --model llama-3.1-8b-instruct --samples 3 --experiment exp1 \
    --input-format regex \
    --dataset-root dataset/fixtures/re2_ab \
    --results-root dataset/fixtures/re2_ab/phase3_results

.venv/bin/python -m synthesis.scripts.generate_source_inputs \
    --target re2 --model llama-3.1-8b-instruct --samples 3 \
    --source-max-files 4 --source-token-budget 14000 --max-tokens 8192 \
    --input-format regex \
    --results-root dataset/fixtures/re2_ab/exp2_results

# Step 3 — replay each corpus under coverage instrumentation.
.venv/bin/python -m synthesis.scripts.measure_coverage \
    --binary dataset/targets/src/re2/build/coverage/seed_replay \
    --seeds-dir dataset/fixtures/re2_ab/phase3_results/seeds/re2/exp1/llama-3.1-8b-instruct \
    --source-roots "$(pwd)/dataset/targets/src/re2/upstream" \
    --profile-out dataset/fixtures/re2_ab/ab_coverage/exp1.json

.venv/bin/python -m synthesis.scripts.measure_coverage \
    --binary dataset/targets/src/re2/build/coverage/seed_replay \
    --seeds-dir dataset/fixtures/re2_ab/exp2_results/seeds/re2/source_only/llama-3.1-8b-instruct \
    --source-roots "$(pwd)/dataset/targets/src/re2/upstream" \
    --profile-out dataset/fixtures/re2_ab/ab_coverage/exp2.json

# Step 4 — compute the set-wise differential.
.venv/bin/python -m analysis.scripts.ab_coverage_diff \
    --exp1-profile dataset/fixtures/re2_ab/ab_coverage/exp1.json \
    --exp2-profile dataset/fixtures/re2_ab/ab_coverage/exp2.json \
    --out-dir dataset/fixtures/re2_ab/ab_coverage \
    --upstream-root "$(pwd)/dataset/targets/src/re2/upstream" \
    --model llama-3.1-8b-instruct --target re2 \
    --exp1-seed-count 20 --exp2-seed-count 30
# Result: dataset/fixtures/re2_ab/ab_coverage/ab_coverage_diff.{json,md}
# Headline: exp1 1243 edges, exp2 1133 edges, +110 in-distribution.
```

**Full doc:** [`docs/experiment1.md`](docs/experiment1.md) — covers
[`1_0`](docs/experiment1.md#experiment1_0) (archived bytes-format),
[`1_1`](docs/experiment1.md#experiment1_1) (regex headline — the run above),
[`1_2`](docs/experiment1.md#experiment1_2) (random-baseline three-way),
[`1_3`](docs/experiment1.md#experiment1_3) (held-out source subset),
[`1_4`](docs/experiment1.md#experiment1_4) (7-cell prompt ablation),
[`1_5`](docs/experiment1.md#experiment1_5) (deferred libFuzzer campaign).
[§9](docs/experiment1.md#cli-reference) is the complete CLI flag
reference for all six scripts used above.
[Verdict »](docs/experiment1.md#combined-verdict)

### Experiment 2 — Multi-model 5-variant context ablation (current headline)

Runs the headline `experiment2_1`: 5 prompt variants × 7 models × 2
targets, 150 seeds/cell. **Hours of wall-clock** — always launch under
`nohup`. About **$30–50** if the Anthropic models run end-to-end; ~$5 on
UF LiteLLM only.

```bash
source .venv/bin/activate

# Build coverage-instrumented binaries (once per target).
# build_instrumented.sh produces coverage/, sanitizer/, fuzzer/, and afl/
# variants. The afl/ variant is skipped automatically if AFL++ is not
# installed at the path in the external-deps table.
./dataset/scripts/fetch_target.sh       dataset/targets/re2.yaml
./dataset/scripts/build_instrumented.sh dataset/targets/re2.yaml
./dataset/scripts/fetch_target.sh       dataset/targets/harfbuzz.yaml
./dataset/scripts/build_instrumented.sh dataset/targets/harfbuzz.yaml

# The frozen M2 hard-branch sets are SHIPPED in this repo at
#   dataset/fixtures/re2_ab_v2/re2/m2_target_branches.json
#   dataset/fixtures/harfbuzz_ab/harfbuzz/m2_target_branches.json
# Do NOT run analysis.scripts.freeze_target_branches — it would
# regenerate them and invalidate all prior comparisons.

# Run the full headline ablation. Default flags: --phase all,
# --skip-existing (idempotent: re-running picks up where it left off).
nohup .venv/bin/python scripts/run_ablation_re2.py \
    --phase all --skip-existing >> /tmp/re2.log 2>&1 &
nohup .venv/bin/python scripts/run_ablation_harfbuzz.py \
    --phase all --skip-existing >> /tmp/hb.log 2>&1 &

# Per-cell results land at:
#   results/ablation_re2_v2/{m1,m2}/<variant>/<model>/summary.json
#   results/ablation_harfbuzz/{m1,m2}/<variant>/<model>/summary.json
# The bundled aggregator (analysis.scripts.ablation_summary) only covers the
# invalidated experiment2_0 run for now; rolling these JSONs up for the
# current headline is a known gap.
```

#### Optional: experiment2_2 — real-fuzzer campaigns

The above runs the seed-time experiment (`experiment2_1`). To also drive
the seeds through libFuzzer + AFL++ (`experiment2_2`, concept-proof with
[documented rigor bugs »](docs/experiment2.md#rigor-bugs)) you need
**AFL++ at `~/tools/aflpp/`** (see external-deps table) and the
`fuzzer/` + `afl/` build variants. `build_instrumented.sh` produces all
four variants in one pass.

```bash
# One example cell — RE2 / libFuzzer / Sonnet seeds (the headline cell).
.venv/bin/python -m synthesis.scripts.run_fuzzing \
    --config synthesis/campaign_configs/llm_seeds.yaml \
    --target re2 \
    --binary dataset/targets/src/re2/build/fuzzer/re2_fuzzer \
    --trials 3 --duration-s 600 --snapshot-interval-s 60 \
    --seed-corpus-dir dataset/fixtures/re2_ab/claude_sonnet_results/seeds/re2/ablation/exp1_full/claude-sonnet-4-6/ \
    --work-root synthesis/results/campaigns

# Full 36-campaign matrix (2 targets × 2 engines × 3 conditions × 3 trials):
# see scripts/run_campaigns.sh and experiment2.md §5.

# Aggregate libFuzzer + AFL++ JSONs into a single markdown summary.
.venv/bin/python -m analysis.scripts.campaign_summary \
    --results-dirs synthesis/results/campaigns synthesis/results/campaigns_afl \
    --output-dir results/campaigns
```

**Parallelism warning.** Running >2 fuzzer processes concurrently with
`rss_limit_mb=2048` OOMs on a 16GB box. Keep parallelism ≤2.

**Full doc:** [`docs/experiment2.md`](docs/experiment2.md) — covers
[`2_0`](docs/experiment2.md#experiment2_0) (invalidated 4×2 predecessor),
[`2_1`](docs/experiment2.md#experiment2_1) (the seed-time run above),
[`2_2`](docs/experiment2.md#experiment2_2) (the campaigns above).
[§4.5](docs/experiment2.md#how-to-reproduce-experiment2_1) lists every
orchestrator flag with semantics; [§5.7](docs/experiment2.md#experiment2_2)
is the campaign-driver flag table.
[Verdict »](docs/experiment2.md#experiment2-verdict)

### Experiment 3 — Prompt-strategy axis (orthogonal to context)

Adds 7 strategies (`default`, `cot_strict`, `few_shot`, `self_critique`,
`prompt_chain`, `tool_use`, `tool_use_retrieval`) on top of
`experiment2`'s context grid. Two sub-versions:

- **`experiment3_0`** — the strategy axis itself (code shipped in commit
  `e64bf5e`; no canonical numerical run defined yet).
- **`experiment3_1`** — a targeted 5-cell ablation isolating CoT × RAG ×
  tool-use on RE2 with `gpt-oss-20b`. **Currently blocked on UF
  LiteLLM credit** (`400 Budget has been exceeded`).

#### Canonical experiment3_1 push-button (run when credit returns)

```bash
source .venv/bin/activate
export UTCF_LITELLM_URL=https://api.ai.it.ufl.edu

# Build coverage binary (skip if already built for experiment 2).
# Frozen M2 set ships at dataset/fixtures/re2_ab_v2/re2/m2_target_branches.json — do not regenerate.
./dataset/scripts/fetch_target.sh       dataset/targets/re2.yaml
./dataset/scripts/build_instrumented.sh dataset/targets/re2.yaml

# Cells A–D (no tool use). Per-call / per-cell / total budget caps via env vars.
UTCF_BUDGET_RUN_ID=ab_ce_abcd \
UTCF_MAX_SPEND_USD=1.00 \
UTCF_PER_CELL_CAP_USD=0.30 \
UTCF_PER_CALL_CAP_USD=0.02 \
nohup .venv/bin/python scripts/run_ablation_re2.py --phase synthesis \
    --only-models gpt-oss-20b \
    --variants v3_all,v5_topk_gaps \
    --strategy default,cot_strict \
    --num-seeds 150 --attempt-offset 92000 \
    >> /tmp/abcd.log 2>&1 &

# Cell E (tool-driven retrieval — separate run to keep logs clean).
UTCF_BUDGET_RUN_ID=ab_ce_e \
UTCF_MAX_SPEND_USD=1.00 \
UTCF_PER_CELL_CAP_USD=0.30 \
UTCF_PER_CALL_CAP_USD=0.02 \
nohup .venv/bin/python scripts/run_ablation_re2.py --phase synthesis \
    --only-models gpt-oss-20b \
    --variants v0_none \
    --strategy tool_use_retrieval \
    --num-seeds 150 --attempt-offset 93000 \
    >> /tmp/cell_e.log 2>&1 &

# After synthesis, compute M1/M2 across all 5 cells.
.venv/bin/python scripts/run_ablation_re2.py --phase m1 --skip-existing \
    --only-models gpt-oss-20b \
    --variants v3_all,v5_topk_gaps,v0_none \
    --strategy default,cot_strict,tool_use_retrieval
.venv/bin/python scripts/run_ablation_re2.py --phase m2 --skip-existing \
    --only-models gpt-oss-20b \
    --variants v3_all,v5_topk_gaps,v0_none \
    --strategy default,cot_strict,tool_use_retrieval

# Grounding audit: verify each seed's CoT Step-1 quote actually appears in source.
.venv/bin/python -m analysis.scripts.verify_grounding --walk \
    --results-root synthesis/results/ablation_re2_v2 \
    --dataset-root dataset/fixtures/_ablation_re2_v2_dataset \
    --target re2
```

#### Quick sample sweep (no canonical numerical claim)

If you just want to exercise the strategy axis end-to-end on free
models — no specific result claim — run a 3-strategy × 2-model sweep:

```bash
.venv/bin/python scripts/run_ablation_re2.py --list-strategies     # sanity check (no LLM calls).

nohup .venv/bin/python scripts/run_ablation_re2.py \
    --phase all --skip-existing \
    --strategy default,cot_strict,few_shot \
    --only-models llama-3.1-8b-instruct codestral-22b \
    >> /tmp/re2_strategies.log 2>&1 &
```

**Full doc:** [`docs/experiment3.md`](docs/experiment3.md) — covers
[`3_0`](docs/experiment3.md#experiment3_0) (the strategy axis itself,
with [per-strategy rationale](docs/experiment3.md#strategy-axis)) and
[`3_1`](docs/experiment3.md#experiment3_1) (the 5-cell run above,
including the [push-button command](docs/experiment3.md#exp3_1-push-button)
and the [budget-cap env vars](docs/experiment3.md#exp3_1-budget-env)).

## Supporting docs

- [`docs/EXPERIMENT_WALKTHROUGH.md`](docs/EXPERIMENT_WALKTHROUGH.md) — directory-by-directory framework reference.
- [`docs/STATUS.md`](docs/STATUS.md) — living handoff for the current state.
- [`docs/FUTURE_DIRECTIONS.md`](docs/FUTURE_DIRECTIONS.md) — pending experiments + cost estimates.
- [`docs/research_document_v3.md`](docs/research_document_v3.md), [`docs/plan_v3.md`](docs/plan_v3.md) — authoritative research spec + execution plan.

## Cost accounting

Two commands, one pricing table. Never put dollar figures in docs without citing one of these.

```bash
# What have we spent so far? (walks .cache/llm/, sums cost_usd by model/day/target)
.venv/bin/python -m analysis.scripts.cost_audit

# What will this cost? (multiplies PRICING_USD_PER_MTOK by expected calls/tokens)
.venv/bin/python -m analysis.scripts.estimate_cost \
    --model claude-sonnet-4-6 --n-calls 400 --mean-in 3000 --mean-out 800
```

Both read `core.llm_client.PRICING_USD_PER_MTOK`. The estimator seeds its default mean
token counts from the audit's historical per-model means, so future estimates cite
observed behaviour rather than guesses.

## How it works

The pipeline runs through a few swappable components. Each axis of variation (target,
model, prompt variant, metric) is a registry entry — adding one never forks an orchestrator.

```
 ┌─────────────────┐    ┌─────────────────┐     ┌─────────────────┐
 │ core/targets.py │    │  core/config.py │     │ core/variants.py│
 │ TargetSpec(...) │    │ ModelDefaults   │     │ VariantSpec(...)│
 │ TARGETS={re2,hb}│    │ (per-model tune)│     │ STANDARD_VARIANTS│
 └────────┬────────┘    └────────┬────────┘     └────────┬────────┘
          │                      │                       │
          └──────────────────────┼───────────────────────┘
                                 ▼
                    scripts/_ablation_base.py
                      AblationRunner
                         │
         ┌───────┬───────┼───────┬───────────┐
         ▼       ▼       ▼       ▼           ▼
        prep  random  synthesis  M1          M2 …   <- analysis/metrics/METRICS
```

`scripts/run_ablation_{re2,harfbuzz}.py` are ~50-line wrappers that pick a
`TARGETS[...]` entry, a model list, and policy flags (`FREE_ONLY`,
`SONNET_ONLY_VARIANTS`) and hand them to `AblationRunner`.

### 5-variant ablation

| Variant | Source? | Tests? | Gaps? |
|---|:---:|:---:|:---:|
| v0_none        | ❌ | ❌ | ❌ |
| v1_src         | ✅ | ❌ | ❌ |
| v2_src_tests   | ✅ | ✅ | ❌ |
| v3_all         | ✅ | ✅ | ✅ |
| v4_src_gaps    | ✅ | ❌ | ✅ |

Single source of truth: `core/variants.py::STANDARD_VARIANTS`.

### Metrics

`analysis/metrics/` exports a `METRICS` registry of classes implementing the `Metric`
protocol (`compute_cell(seeds_dir, target, out_dir)`). Currently:

- `M1EdgesMetric` — total union edges hit.
- `M2HardBranchMetric` — fraction of the frozen hard-branch set that the seed corpus hits.
  Hard branch = `struct_hits >= 1 AND rand_hits == 0`, so the random baseline scores exactly
  0% by construction.

`--phase <name>` on either orchestrator auto-discovers whatever is in `METRICS`.

### Seed normalisation

Synthesis retries until the cell has exactly 150 seeds (300-attempt cap, 100 for models
that hit the UF LiteLLM 2048-char response cap). Before measurement, each cell is
deterministically subsampled to 150 (RNG seed = 42) so seed count is never a confound.

## Extending the framework

**Add a new target** (e.g. libxml2):
1. One `TargetSpec` entry in `core/targets.py::TARGETS`.
2. One branch in `dataset/scripts/build_instrumented.sh`.
3. Freeze the M2 set: `python -m analysis.scripts.freeze_target_branches --target <name>`.
4. A ~40-line `scripts/run_ablation_<name>.py` wrapper — no orchestrator logic,
   just `AblationRunner(target=TARGETS["<name>"], …)`.

**Add a new model** (e.g. gpt-4o):
1. One row in `core.llm_client.PRICING_USD_PER_MTOK`.
2. One `ModelDefaults` entry in `core/config.py` (provider, `inputs_per_call`,
   `worker_count`, `synthesis_max_tokens`, `output_capped_on_binary`).
3. One line in each wrapper's `MODELS` list.

**Add a new metric** (e.g. M3 bug-time-to-first):
1. One file under `analysis/metrics/` implementing the `Metric` protocol.
2. Append to `METRICS` in `analysis/metrics/__init__.py`.
   `--phase <new-name>` works immediately on every target.

## Repository layout

```
scripts/
  _ablation_base.py         AblationRunner — shared orchestration core
  run_ablation_re2.py       50-LOC wrapper (RE2)
  run_ablation_harfbuzz.py  50-LOC wrapper (harfbuzz)
  archive/                  Superseded drivers, kept for diff reference

core/
  targets.py                TargetSpec + TARGETS registry
  variants.py               VariantSpec + STANDARD_VARIANTS
  config.py                 ModelDefaults per-model tuning + env-driven config
  llm_client.py             Unified Anthropic/OpenAI/LiteLLM client + disk cache + pricing
  loop_detector.py          Mid-stream degenerate-output abort
  dataset_schema.py         GeneratedInput / CoverageProfile / SynthesisRecord
  logging_config.py         Structured JSONL ledger

synthesis/
  prompts/                  Jinja2 templates (ablation_synthesis_{regex,binary}.j2)
  scripts/                  Synthesis drivers, coverage measurement, random inputs

analysis/
  metrics/                  Metric protocol + M1EdgesMetric + M2HardBranchMetric
  scripts/
    cost_audit.py           Walks .cache/llm/ → results/cost_audit/summary.{md,json,csv}
    estimate_cost.py        Pre-experiment dollar estimator (uses PRICING_USD_PER_MTOK)
    freeze_target_branches.py  Computes + freezes the M2 hard-branch set
    measure_gap_coverage.py    M2 evaluator
    ablation_summary.py        Per-cell summary → docs tables

dataset/
  targets/                  Target YAML definitions (re2.yaml, harfbuzz.yaml)
  fixtures/                 Frozen M2 target branches, upstream union profiles
  scripts/                  fetch_target.sh, build_instrumented.sh

results/                    Experiment outputs (gitignored — large JSON / profraw)
synthesis/results/          Synthesised seed corpora (gitignored)
.cache/llm/                 LLM response cache, keyed by sha256(model, messages, …)
secrets/                    API keys (gitignored — never commit, never log)

docs/
  STATUS.md                 Living handoff (read first)
  experiment1.md            RE2 single-model A/B + generalization (sub-versions experiment1_0..1_5)
  experiment2.md            Multi-model 5-variant ablation + real-fuzzer campaigns (experiment2_0..2_2)
  experiment3.md            Prompt-strategy axis + 5-cell prompt optimization (experiment3_0..3_1)
  EXPERIMENT_WALKTHROUGH.md Framework reference (directories, commands, axes)
  FUTURE_DIRECTIONS.md      Pending experiments with cost estimates
  research_document_v3.md   Authoritative research spec
  plan_v3.md                Authoritative execution plan
```

## Critical constraints (load-bearing — do not relax)

- **Provenance is sacred.** Every test object traces back to `upstream_repo:commit:file:line`.
  Extractors only read upstream code; fabrication is not a fallback.
- **Hard-branch filter.** Never relax `rand_hits == 0` in M2 — doing so invalidated a prior
  RE2 ablation run (`experiment2_0`; see `docs/experiment2.md` §3). The random baseline must
  score exactly 0% by construction.
- **Deterministic seeds.** RNG seed = 42 everywhere; RE2 seed-flag bytes are sha256-derived.
  Changing either breaks comparability with previous runs.
- **150-seed normalisation.** Synthesis retries to exactly 150, then deterministically
  subsamples. Don't short-circuit.
- **Cache salt must include `attempt+offset`.** Format:
  `f"model={model},sample={k},ablation={cell},run={attempt+offset}"`. When restarting a run,
  bump `--attempt-offset` by ≥ 5000 to avoid replaying cached failures.
- **Per-target constants live in `TargetSpec`.** Do not scatter new `HB_*` / `RE2_V2_*`
  constants across `core/config.py` or the orchestrators — add a field to `TargetSpec`.
- **Dollar figures cite a command.** No hand-estimated cost prose; use `cost_audit.py` or
  `estimate_cost.py`, both backed by `PRICING_USD_PER_MTOK`.
