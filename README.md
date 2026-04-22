# UTCF — LLM-Guided Fuzzing Seed Synthesis

Research framework for LLM-guided fuzzing seed corpus synthesis. Given a coverage-instrumented
target binary and its upstream test suite, the framework identifies hard-to-reach branches,
prompts an LLM to synthesize inputs targeting those branches, and evaluates the resulting seeds
via seed-time coverage metrics (M1: total edges, M2: hard-branch hit rate).

**Resuming work?** Read `docs/STATUS.md` first (living handoff), then
`docs/WEEKLY_REVIEW_PROMPT.md` for the latest results and
`docs/FUTURE_DIRECTIONS.md` for pending experiments.

## Current status

Two targets, 7 models, 5-variant ablation (150 seeds/cell). LiteLLM-served
open models are fully run on both targets; Claude Sonnet/Haiku on harfbuzz is partial.

| Target | Format | Best M1 vs random | Best M2 |
|---|---|---|---|
| RE2 (regex engine) | text | +19% (v2_src_tests) | 60% of hard branches (v3_all) |
| harfbuzz (font shaper) | binary | +2% (v0_none) | 4% of hard branches (v0/v3) |

Cumulative LLM spend on disk (`.cache/llm/`): **$100.09** across 14,161 cached responses
(Anthropic $86.25, UF LiteLLM-accounted $13.83). See
`results/cost_audit/summary.md` — regenerate with `.venv/bin/python -m analysis.scripts.cost_audit`.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run tests (no network, no LLM, no LLVM required)
.venv/bin/pytest -q
```

## Running the ablation experiments

Requires: LLVM 15+ (`clang-15`, `llvm-cov-15`, `llvm-profdata-15`), git, network access.

```bash
# Step 1 — fetch and build coverage-instrumented target
./dataset/scripts/fetch_target.sh       dataset/targets/re2.yaml   # or harfbuzz.yaml
./dataset/scripts/build_instrumented.sh dataset/targets/re2.yaml

# Step 2 — freeze hard-branch M2 targets (struct_hits >= 1 AND rand_hits == 0)
.venv/bin/python -m analysis.scripts.freeze_target_branches --target re2_v2
# or: --target harfbuzz

# Step 3 — run the full ablation (prep -> synthesis -> random -> m1 -> m2)
.venv/bin/python scripts/run_ablation_re2.py      --phase all --skip-existing
.venv/bin/python scripts/run_ablation_harfbuzz.py --phase all --skip-existing

# Results: results/ablation_{re2_v2,harfbuzz}/{m1,m2}/<variant>/<model>/summary.json
```

Long runs should be launched under `nohup` — see `scripts/CLAUDE.md` for the pattern.

**LLM keys:**
- `secrets/claude_key` — Anthropic API key (Claude Sonnet/Haiku).
- UF LiteLLM proxy at `https://api.ai.it.ufl.edu` — llama + codestral + nemotron + gpt-oss,
  no key file; set `UTCF_LITELLM_URL`. Vendor invoice is $0 but responses truncate at 2048 chars.

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

### Cost accounting

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
  WEEKLY_REVIEW_PROMPT.md   Current results summary
  FUTURE_DIRECTIONS.md      Pending experiments with cost estimates
  LLAMA_ABLATION_RESULTS.md Standalone llama results (RE2 v2 + harfbuzz)
  EXPERIMENT_HANDOFF.md     Resume commands + DWARF source-root notes
  research_document_v3.md   Authoritative research spec
  plan_v3.md                Authoritative execution plan
```

## Critical constraints (load-bearing — do not relax)

- **Provenance is sacred.** Every test object traces back to `upstream_repo:commit:file:line`.
  Extractors only read upstream code; fabrication is not a fallback.
- **Hard-branch filter.** Never relax `rand_hits == 0` in M2 — doing so invalidated a prior
  RE2 ablation run (`docs/WEEKLY_REVIEW_PROMPT.md` §6). The random baseline must score
  exactly 0% by construction.
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
