# LLM-Guided Fuzzing — Experiment Walkthrough

A guided tour of this repository for somebody who has never seen it before.
By the end of this document you should know:

1. What research question the project is answering.
2. What every top-level directory and load-bearing file is for.
3. How an experiment is structured (targets × models × prompt variants × prompt strategies × metrics).
4. The exact commands to run, in order, to reproduce any experiment.
5. Where every output lands on disk.

This document does **not** replace the authoritative specs
(`docs/research_document_v3.md`, `docs/plan_v3.md`) — it stitches them
together with the code so you can read either side and understand the other.

---

## 1. The research question (one paragraph)

Fuzzers like libFuzzer / AFL++ start from a small "seed corpus" of inputs
and mutate them. The quality of those seeds dominates how fast the fuzzer
finds bugs. **Can a Large Language Model, given the source code and unit
tests of a target program, write better seeds than randomly generated
inputs?** And if it can, **how much of the gain comes from the source
code vs. the tests vs. an explicit list of uncovered branches?**

The framework answers that by running an ablation over the LLM's prompt
context and measuring two coverage metrics on a coverage-instrumented
build of the target program:

- **M1** — total union of code edges hit when all 150 LLM-produced seeds
  are executed once.
- **M2** — fraction of "hard" branches hit. A branch qualifies as hard
  iff (a) the upstream test suite hits it at least once, AND (b) a
  matched-count random baseline never hits it. By construction, the
  random baseline scores **exactly 0%** on M2 — the metric is designed
  to isolate cases where the LLM is using semantic context the random
  baseline cannot.

Two targets are wired up: **RE2** (text-format regex engine) and
**harfbuzz** (binary-format font shaper).

---

## 2. The pipeline at a glance

```
 ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
 │ dataset/         │    │ synthesis/       │    │ analysis/        │
 │  pinned target   │ -> │  prompt the LLM  │ -> │  M1 edges +      │
 │  + tests + gaps  │    │  → 150 seeds     │    │  M2 hard-branch  │
 └──────────────────┘    └──────────────────┘    └──────────────────┘
        ▲                          ▲                       │
        │                          │                       ▼
        │                          │              results/ablation_*/
        │                          │              {m1,m2}/<variant>/<model>/
        │                          │                  summary.json
        │                          │
        └──────────────────────────┴──── orchestrated by
                                          scripts/run_ablation_{re2,harfbuzz}.py
                                          (thin wrappers around AblationRunner)
```

A single "cell" of the experiment is `(target, variant, model, strategy)`.
A full ablation run is a cross-product of those four axes plus a random
baseline anchor.

---

## 3. The five experimental axes

### 3.1 Target — `core/targets.py::TARGETS`

| Target | Format | What it parses |
|---|---|---|
| `re2` | `regex` (text — bytes prepended with 2 sha256-derived flag bytes, length 3–64) | Google's RE2 regex engine |
| `harfbuzz` | `binary` (raw bytes, capped at 64 / ≈88 base64 chars) | Font shaper |

Each target is a `TargetSpec` carrying its coverage binary path, source
roots (for DWARF symbolication), fixtures dir (where the frozen M2
hard-branch list lives), prepped dataset root, synthesis seed root, and
results root. Adding a new target means **one** new `TargetSpec` entry
plus one branch in `dataset/scripts/build_instrumented.sh` — no
orchestrator fork.

### 3.2 Variant — `core/variants.py::STANDARD_VARIANTS`

The 5-variant ablation toggles **what context the prompt contains**.
Detailed per-variant M1/M2 themes (which models win, by how much, on what
target) are in [experiment2.md §4.1 (RE2)](experiment2.md#re2-themes) and
[§4.2 (harfbuzz)](experiment2.md#hb-themes).

| Variant | Source code | Upstream tests | Coverage gaps | Reads as |
|---|:-:|:-:|:-:|---|
| `v0_none`      | ❌ | ❌ | ❌ | "Write seeds for this target." Pure prior. [Themes »](experiment2.md#re2-themes) |
| `v1_src`       | ✅ | ❌ | ❌ | LLM sees source only. [Themes »](experiment2.md#re2-themes) |
| `v2_src_tests` | ✅ | ✅ | ❌ | LLM sees source + the upstream unit tests. [Themes »](experiment2.md#re2-themes) |
| `v3_all`       | ✅ | ✅ | ✅ | LLM sees source + tests + a list of uncovered branches. [Themes »](experiment2.md#re2-themes) |
| `v4_src_gaps`  | ✅ | ❌ | ✅ | Source + uncovered-branch list, no tests. [Themes »](experiment2.md#re2-themes) |
| `v5_topk_gaps` | ❌ | ❌ | ✅ | Only the top-K uncovered branches with their code-context windows. Used by [experiment3_1](experiment3.md#experiment3_1). |

Both target wrappers consume the same list. Cell directory names are
load-bearing — analysis scripts key off them.

### 3.3 Model — declared per-wrapper in `MODELS = [...]`

Eight models in active use, served via two providers:

| Provider | Models | Key path |
|---|---|---|
| Anthropic API | `claude-sonnet-4-6`, `claude-haiku-4-5-20251001` | `secrets/claude_key` |
| UF LiteLLM proxy (`https://api.ai.it.ufl.edu`) | `llama-3.1-8b-instruct`, `llama-3.1-70b-instruct`, `llama-3.3-70b-instruct`, `codestral-22b`, `gpt-oss-20b`, `nemotron-3-super-120b-a12b` | none — `UTCF_LITELLM_URL` |

Per-model tuning lives in `core/config.py::MODEL_DEFAULTS` as
`ModelDefaults` records: provider, `inputs_per_call`, `worker_count`,
`synthesis_max_tokens`, `output_capped_on_binary` (true for models that
hit the UF proxy's 2048-char response cap on binary blobs),
`supports_json_object`, `supports_json_schema`, `supports_tool_use`.

### 3.4 Prompt strategy — `core/prompt_strategies.py::STRATEGIES`

Orthogonal axis to the variant. The variant decides **what context** is
in the prompt; the strategy decides **how the prompt is structured**.
Detailed per-strategy rationale (templates, hypothesis, why it might or
might not work) is in [experiment3.md §1.3](experiment3.md#strategy-axis).

| Strategy | Calls/seed | tool-use | What it does |
|---|---|---|---|
| `default`        | 1 | no  | One-shot prompt, JSON output. [Why »](experiment3.md#strategy-default) |
| `cot_strict`     | 1 | no  | Chain-of-thought reasoning before the JSON output. [Why »](experiment3.md#strategy-cot_strict) |
| `few_shot`       | 1 | no  | Adds frozen exemplars from `dataset/fixtures/exemplars/`. [Why »](experiment3.md#strategy-few_shot) |
| `self_critique`  | 2 | no  | Draft → critique → refine. [Why »](experiment3.md#strategy-self_critique) |
| `prompt_chain`   | 3 | no  | Plan → sketch → finalize. [Why »](experiment3.md#strategy-prompt_chain) |
| `tool_use`       | ≤4| yes | Lets the model call a `check_seed` oracle. Only `gpt-oss-20b` and `nemotron-3-super-120b-a12b` are wired up. [Why »](experiment3.md#strategy-tool_use) |
| `tool_use_retrieval` | ≤5| yes | Oracle + retrieval tools (model fetches source / gap context on demand instead of receiving a fixed top-K list). Same model gating as `tool_use`. [Why »](experiment3.md#strategy-tool_use_retrieval) |

The default strategy is byte-identical to pre-strategy code: same cache
key, same on-disk layout, so the ~14k existing cached responses still
hit. Non-default strategies append `,strategy=<name>` to the cache salt
and insert `<name>/` as a leading segment of every output path.

### 3.5 Metric — `analysis/metrics/__init__.py::METRICS`

Two metrics, registered as a list. Each implements the `Metric` protocol
(`name`, `results_subdir`, `compute_cell(seeds_dir, target, out_dir)`):

- `M1EdgesMetric` — runs `synthesis.scripts.measure_coverage` on the
  cell's seed corpus, parses the JSON, writes `summary.json`.
- `M2HardBranchMetric` — runs `analysis.scripts.measure_gap_coverage`
  against the frozen hard-branch list at
  `<target>.fixtures_dir/m2_target_branches.json`.

`--phase <name>` on either orchestrator auto-discovers whatever is in
the registry. Adding M3 = one file + one list entry.

---

## 4. Repository layout — directory by directory

The five top-level directories in execution order are
`dataset/` → `synthesis/` → `analysis/`, with `core/` providing
shared infrastructure and `scripts/` providing the orchestrators.

### 4.1 `core/` — shared infrastructure

| File | Purpose |
|---|---|
| `targets.py` | `TargetSpec` dataclass + `TARGETS` registry. **Single source of truth for per-target paths.** |
| `variants.py` | `VariantSpec` + `STANDARD_VARIANTS` (5 prompt variants). |
| `config.py` | `ModelDefaults` per-model tuning; pricing; fuzzbench campaign params; back-compat re-exports of per-target paths. |
| `prompt_strategies.py` | Strategy registry, cache-salt builder, default `build_messages` glue. |
| `llm_client.py` | Unified Anthropic / OpenAI / LiteLLM client with sha256-keyed disk cache (`.cache/llm/`), RPM throttle (`UTCF_LLM_RPM`), pricing table (`PRICING_USD_PER_MTOK`). Streams by default; loop-detector aborts mid-stream on degenerate output. |
| `loop_detector.py` | Mid-stream degenerate-output guard, consumed every 2048 chars. |
| `dataset_schema.py` | Pydantic models: `GeneratedInput`, `CoverageProfile`, `SynthesisRecord`, `PromptLogEntry`, `Test`. `populate_by_name=True` for `CoverageProfile` is load-bearing for round-trip. |
| `provenance.py` | Verifies first 2 non-empty lines of extracted test code against upstream — provenance is sacred. |
| `logging_config.py` | Structured JSONL logger; required fields (model, tokens, cost, parse_status) must stay present. |
| `coverage_utils.py` | Source-root prefix matching for DWARF paths. |
| `budget.py` | Per-cell spend caps (uses `UTCF_BUDGET_CELL_KEY` env). |
| `build_pptx.py` | Slide-deck generator; not on the critical path. |
| `tests/` | Unit tests for the above (LLM-client routing, cache salt, etc.). |

### 4.2 `dataset/` — upstream targets, build, test extraction

| Path | Purpose |
|---|---|
| `targets/<name>.yaml` | Per-target manifest: pinned upstream commit (resolved via `pinned_versions.yaml`), test-framework name, test file globs, harness/dictionary/seed paths, build flags. RE2 and harfbuzz have real values; the other 9 still have `<FILL>` placeholders that intentionally break. |
| `targets/src/` | Workspace where `fetch_target.sh` clones each target. **Gitignored.** |
| `scripts/fetch_target.sh` | Clones the upstream repo at the pinned SHA. Idempotent. |
| `scripts/build_instrumented.sh` | Builds `coverage/`, `sanitizer/`, `fuzzer/` variants under `targets/src/<target>/build/`. **Currently only RE2 and harfbuzz branches are wired.** Produces `seed_replay`, the standalone driver that reads one byte file and calls `LLVMFuzzerTestOneInput`. |
| `scripts/extract_tests.py` | Per-framework test extraction. |
| `scripts/extractors/{googletest,glib,custom_c,ctest,tcl,perl_tap}.py` | Framework-specific parsers. |
| `scripts/run_test_coverage.py` | Runs each test under `llvm-cov`, writes per-test coverage profiles. |
| `scripts/compute_gaps.py` | Diffs union test coverage against the binary's edge map → `coverage_gaps.json`. |
| `scripts/build_dataset.py` | Final assembly: writes `tests.json`, `metadata.json`, `coverage_gaps.json` into `dataset/data/<target>/`. |
| `scripts/contamination_probe.py` | Optional memorization probe (does the LLM already know these tests?). |
| `scripts/pinned_loader.py` | YAML loader that resolves `!from_pinned …` against `pinned_versions.yaml` and raises on `<FILL>` placeholders. |
| `fixtures/` | **Frozen** experiment fixtures. Checked in because they are small JSON with high reproducibility value: `m2_target_branches.json` (the hard-branch list — re-freezing it would invalidate prior runs), `upstream_union_profile.json`, `_ablation_*_dataset/<target>/` (the prepped dataset root each ablation run consumes), `exemplars/` (frozen few-shot exemplars). |
| `data/` | Per-target `tests.json`/`metadata.json`/`coverage_gaps.json`. **Gitignored.** |
| `tests/` | Tests for extractors, provenance, schema. No network, no LLVM. |

### 4.3 `synthesis/` — LLM seed generation

| Path | Purpose |
|---|---|
| `prompts/ablation_synthesis_regex.j2` | Default RE2 prompt template (text format). |
| `prompts/ablation_synthesis_binary.j2` | Default harfbuzz prompt template (base64 blobs). |
| `prompts/ablation_synthesis_{regex,binary}_{cot,fewshot,plan,sketch,finalize,refine}.j2` | One template per non-default strategy × format. |
| `prompts/system_prompt.txt` | System prompt referencing upstream provenance. |
| `prompts/system_prompt_source_only.txt` | Used by Experiment 2 (legacy source-only path). |
| `scripts/generate_ablation_inputs.py` | **Per-cell driver.** Loads tests/gaps/source, renders the strategy's template, calls the LLM, parses the response, writes `seed_*.bin` to `synthesis/results/ablation_<target>/seeds/<target>/ablation/<variant>/<model>/`. |
| `scripts/build_synthesis_prompt.py` | Pulls test/gap/source fixtures together for the default templates. |
| `scripts/build_source_prompt.py` | Source-only prompt builder; `assert_no_tests` guard is the primary defence of ablation validity — do not silence it. |
| `scripts/extract_source_context.py` | Token-budgeted source extraction (`SOURCE_TOKEN_BUDGET_ALL_MODELS = 2_000`). |
| `scripts/parse_synthesis.py` | `parse_regex_response` (RE2 — prepends 2 sha256 flag bytes, clips to 3–64 bytes, dedupes) and `parse_synthesis_response` (binary — base64 decode, clip to 64 bytes). |
| `scripts/oracles.py` | The `check_seed` oracle that the `tool_use` strategy can call mid-generation. |
| `scripts/generate_random_inputs.py` | The matched-count random baseline. **Must not** read source/tests/gaps — invariant for M2 correctness. RNG is seeded `random.Random(42)`; never `secrets.*`. |
| `scripts/measure_coverage.py` | Runs `seed_replay` over a corpus, emits `CoverageProfile` JSON consumed by M1. |
| `scripts/generate_inputs.py`, `generate_source_inputs.py`, `run_source_prediction.py`, `run_source_fuzzing.py`, `run_fuzzing.py`, `run_afl_fuzzing.py`, `compare_baselines.py`, `compare_experiments.py`, `validate_inputs.py`, `failure_analysis.py`, `dedup_crashes.py` | Pre-rearch (Phase-3 / Experiment-2) scripts. Still load and run for the legacy A/B reproductions but **not** invoked by the current ablation runners. |
| `campaign_configs/*.yaml` | libFuzzer campaign configs (FuzzBench gold standard: 20 trials × 23h). Not on the active critical path. |
| `results/` | Per-cell seeds (`ablation_<target>/seeds/<target>/ablation/<variant>/<model>/seed_*.bin`). **Gitignored.** |
| `tests/` | Unit tests for the parsers and prompt builders. |

### 4.4 `analysis/` — metrics, statistics, figures

| Path | Purpose |
|---|---|
| `metrics/base.py` | The `Metric` protocol. |
| `metrics/m1.py` | M1 implementation — wraps `measure_coverage.py`. |
| `metrics/m2.py` | M2 implementation — wraps `measure_gap_coverage.py`. |
| `metrics/__init__.py` | Exports `METRICS = [M1EdgesMetric(), M2HardBranchMetric()]` — adding M3 means one new file + one new entry. |
| `scripts/freeze_target_branches.py` | Computes the hard-branch set (`struct_hits ≥ 1 AND rand_hits == 0`) from the upstream union profile and the random baseline; writes `m2_target_branches.json` into the target's fixtures dir. **Run once per target** — re-freezing breaks comparability with prior runs. |
| `scripts/measure_gap_coverage.py` | M2 evaluator — runs `seed_replay` over a seed corpus, reports hit-rate against the frozen hard-branch set. |
| `scripts/ablation_summary.py` | Walks `results/ablation_*/m{1,2}/<variant>/<model>/summary.json` → markdown comparison tables for `docs/*_RESULTS.md`. |
| `scripts/ab_coverage_diff.py` | Set-wise edge/line diff between two seed corpora. |
| `scripts/ablation_diff.py` | N-way diff for the prompt ablation experiment B. |
| `scripts/cost_audit.py` | Walks `.cache/llm/`, sums `cost_usd` per (model, day, target) using `core.llm_client.PRICING_USD_PER_MTOK`. **Sole source of historical dollar figures.** |
| `scripts/estimate_cost.py` | Pre-experiment estimator using the same pricing table; default token-count means come from the audit's historical per-model data. |
| `scripts/mann_whitney.py`, `vargha_delaney.py`, `friedman_nemenyi.py` | Statistical tests (Vargha-Delaney is hand-rolled — not in scipy). |
| `scripts/probe_json_mode.py`, `probe_tool_use.py` | Capability probes that populate the `supports_*` flags on `ModelDefaults`. |
| `scripts/citation_usage.py`, `harvest_exemplars.py`, `verify_grounding.py` | Auditing tools. |
| `scripts/plot_coverage.py`, `threat_analysis.py` | Figure generators. |
| `figures/` | Generated PNG/PDF (gitignored). |
| `metrics/` (output dir, distinct from the package above) | Per-target M2 evaluation artifacts. |
| `tests/` | Unit tests for the metrics + scripts. |

### 4.5 `scripts/` — top-level orchestrators

| File | Purpose |
|---|---|
| `_ablation_base.py` | **`AblationRunner` — the single source of truth for how to run a cell.** Phases: `prep` → `synthesis` → `random` → `m1` → `m2`. Handles parallelism via `ThreadPoolExecutor` with `worker_count` workers per cell, attempt caps (300 default / 100 for output-capped models on binary), 45s subprocess timeout per synthesis call, early-exit after 20 consecutive parse failures, deterministic 150-seed subsampling at RNG seed 42. CLI flags: `--phase`, `--skip-existing`, `--only-models`, `--strategy`, `--variants`, `--num-seeds`, `--attempt-offset`, `--list-strategies`, `--dry-run`. |
| `run_ablation_re2.py` | ~50-line wrapper: declares the RE2 model list and `SONNET_ONLY_VARIANTS = {"v4_src_gaps"}`, instantiates `AblationRunner(target=TARGETS["re2"], …)`. |
| `run_ablation_harfbuzz.py` | Same shape for harfbuzz. |
| `archive/` | Superseded drivers (e.g. `run_ablation_experiment_v3.py.bak`) kept for diff reference. |
| `run_campaigns.sh`, `run_claude_ab.sh`, `run_e1_stochasticity.sh` | Shell wrappers for one-off named experiments — see `docs/STATUS.md` to know whether any are live. |
| `check_claude_credits.{py,sh}` | Run before kicking off a paid Anthropic ablation. |
| `tests/` | Tests for the CLI surface. |

### 4.6 Other top-level directories

| Dir | Purpose |
|---|---|
| `prediction/` | Phase-2 coverage-prediction baseline (predict which branches each test covers without running it). Pre-rearch path; templates here are still read by some sanity scripts. |
| `transfer/` | Cross-target leave-one-out experiments and Tier 3 evaluation. Skeleton present, not on the active critical path. |
| `finetuning/` | LoRA fine-tuning skeleton (HF PEFT + Transformers). Requires GPU; not in active use. |
| `sanity/` | Mini exp1_b / exp2_b runs against the LiteLLM proxy at ~$1 budget. `make sanity-fixture` builds the RE2 mini fixture; `make sanity-exp1-b` and `make sanity-exp2-b` run the two halves. |
| `docker/` | Containerization assets (build environment). |
| `tests/` | Top-level cross-package tests, including `test_phase9_integration.py` which guards strategy/template/cache-salt/CLI composition. |
| `secrets/` | API keys (`claude_key` for Anthropic). **Gitignored — never log, never commit.** |
| `results/` | All experiment outputs (`ablation_<target>/`, `cost_audit/`, `probes/`, `prediction/`, `synthesis/`, `transfer/`, etc.). **Gitignored** — large JSON / `.profraw`. |
| `.cache/llm/` | LLM response cache, sha256-keyed by `(model, messages, temperature, top_p, max_tokens, cache_salt)`. **Gitignored.** |
| `docs/` | Specs (`research_document_v3.md`, `plan_v3.md`), living handoff (`STATUS.md`), per-experiment writeups (`experiment{1,2,3}.md`), pending experiments (`FUTURE_DIRECTIONS.md`), this walkthrough. **Read `STATUS.md` first when resuming.** |
| `.venv/` | Pre-populated virtualenv. **Do not reinstall**; `requirements.txt` is recorded for reference. |

### 4.7 Key root files

| File | Purpose |
|---|---|
| `Makefile` | Top-level orchestration for the **full v3 plan** (Phase 1 dataset → Phase 2 prediction → Phase 3 synthesis+campaigns → Phase 4 finetuning → Experiment 2 source-only → reporting). Mostly aspirational — many phases require external resources (29,440 CPU-hour cluster allocation for 24h libFuzzer campaigns, A100 GPUs for finetuning). The active flow uses `scripts/run_ablation_*.py` directly, not `make`. |
| `pyproject.toml` | Python 3.10+. Ruff line-length 100; `E402` ignored in `*/scripts/` because they bootstrap `sys.path`. Pytest discovery limited to `<pkg>/tests/`. |
| `pinned_versions.yaml` | One block per target with upstream commit SHA, FuzzBench harness paths, dictionary/seed paths. RE2 and harfbuzz are real; others are `<FILL>` and will hard-fail at load time on purpose. |
| `requirements.txt` | Pinned deps (anthropic, openai, jinja2, pydantic, etc.). |
| `conftest.py` | Pins repo root on `sys.path` for tests. |
| `README.md` | Quick start + minimal pipeline overview. |
| `pinned_versions.yaml`, `default.profraw` | (`default.profraw` is incidental — left over from a coverage run; safe to ignore.) |

---

## 5. The two ablation experiments — what's actually running

The repo has accumulated multiple experiments, fully cataloged in
`experiment{1,2,3}.md`. The **current, active** experiment is the
5-variant × 7-model × 2-target ablation (`experiment2_1`) orchestrated
by `scripts/run_ablation_{re2,harfbuzz}.py`. Older experiments
(the 2026-04-13 regex A/B, now `experiment1_1`) are archived but still
reproducible from their fixtures.

### 5.1 Live ablation status snapshot (per `experiment2_1`)

Best per-target M1 / M2 from the current 5-variant × 7-model ablation. Each
row links to the per-target themes section in `experiment2.md` for the full
breakdown across all 7 models.

| Target | Format | Best M1 vs random | Best M2 | Detail |
|---|---|---|---|---|
| RE2 v2 | text | **+335 edges (+28.9%)** at `v2_src_tests` (codestral-22b) | **0.867** at `v1_src` (nemotron-120b) | [§4.1 RE2 themes »](experiment2.md#re2-themes) |
| harfbuzz | binary | **+444 edges (+80%)** at `v0_none` (sonnet) | **0.640** at `v0_none` (sonnet) | [§4.2 harfbuzz themes »](experiment2.md#hb-themes) |

LiteLLM-served open models are fully run on both targets; Claude
Sonnet/Haiku on harfbuzz is partial. Cumulative spend on disk
($86.25 Anthropic + $13.83 LiteLLM-accounted = $100.09 across 14k
cached responses) per `results/cost_audit/summary.md` —
regenerate via `python -m analysis.scripts.cost_audit`.

### 5.2 Active per-cell file layout

```
synthesis/results/ablation_<target>/
   seeds/
      <target>/
         ablation/
            <variant>/<model>/
               seed_*.bin            # exactly 150 after subsampling
         random/                     # the matched-count random baseline
            seed_*.bin

results/ablation_<target>/
   m1/<variant>/<model>/summary.json # M1: total union edges
   m2/<variant>/<model>/summary.json # M2: hard-branch hit rate
   m1/random/summary.json            # Random-baseline anchor
   m2/random/summary.json            # Always 0% by construction
```

For non-default strategies a `<strategy>/` segment is inserted under
`seeds/<target>/` and under `results/ablation_<target>/`. Default
strategy paths are unchanged so the ~14k existing cache entries stay
valid.

---

## 6. How to run the experiments — exact commands

### 6.1 First-time setup

```bash
# Activate the pre-populated venv (do not reinstall).
source .venv/bin/activate    # or just call .venv/bin/python directly.

# Sanity check: fast local tests, no network, no LLM, no LLVM required.
.venv/bin/pytest -q
# Expect 100+ passing.
```

You also need:

- **LLVM 15** (`clang-15`, `llvm-cov-15`, `llvm-profdata-15`) — for
  building the coverage-instrumented target binary.
- **Anthropic API key** at `secrets/claude_key` (only if you want to
  run Claude cells). Plain text, single line.
- **UF LiteLLM proxy access** — the proxy is at
  `https://api.ai.it.ufl.edu`; export `UTCF_LITELLM_URL` to point at it.
  No key file needed.

### 6.2 Build the coverage-instrumented target (per target)

```bash
# RE2.
./dataset/scripts/fetch_target.sh       dataset/targets/re2.yaml
./dataset/scripts/build_instrumented.sh dataset/targets/re2.yaml

# harfbuzz.
./dataset/scripts/fetch_target.sh       dataset/targets/harfbuzz.yaml
./dataset/scripts/build_instrumented.sh dataset/targets/harfbuzz.yaml
```

This produces, per target:

- `dataset/targets/src/<target>/build/coverage/seed_replay` — the
  standalone single-input driver used by M1 / M2 measurement.
- `…/build/sanitizer/` — ASan/UBSan build (used for crash dedup).
- `…/build/fuzzer/` — libFuzzer build (used for full campaigns; not
  on the critical path of the current ablation).

### 6.3 Freeze the M2 hard-branch set (once per target, per dataset)

```bash
.venv/bin/python -m analysis.scripts.freeze_target_branches --target re2_v2
.venv/bin/python -m analysis.scripts.freeze_target_branches --target harfbuzz
```

Writes `m2_target_branches.json` and `upstream_union_profile.json` into
the target's fixtures dir. Re-running this **invalidates** prior
ablation results — only do it when the upstream commit or the random
baseline RNG seed changes.

### 6.4 Run the ablation (the main event)

```bash
# Full pipeline (prep → synthesis → random → M1 → M2) for both targets.
# Always launch under nohup; full runs take hours.
nohup .venv/bin/python scripts/run_ablation_re2.py \
    --phase all --skip-existing \
    >> /tmp/re2_ablation.log 2>&1 &

nohup .venv/bin/python scripts/run_ablation_harfbuzz.py \
    --phase all --skip-existing \
    >> /tmp/hb_ablation.log 2>&1 &

tail -f /tmp/re2_ablation.log    # watch progress.
```

Useful sub-phase invocations:

```bash
# Just rebuild seed corpora (skip already-150 cells).
.venv/bin/python scripts/run_ablation_harfbuzz.py --phase synthesis --skip-existing

# Just (re)compute M1 for cells that already have seeds.
.venv/bin/python scripts/run_ablation_re2.py --phase m1 --skip-existing

# Restart synthesis without replaying cached failures (bump offset by ≥5000).
.venv/bin/python scripts/run_ablation_harfbuzz.py \
    --phase synthesis --skip-existing --attempt-offset 15000

# Smoke run: 5 seeds, one variant, one model, one strategy.
.venv/bin/python scripts/run_ablation_harfbuzz.py --phase synthesis \
    --variants v1_src --only-models gpt-oss-20b --strategy tool_use \
    --num-seeds 5 --attempt-offset 70000

# Dry-run the full strategy x variant x model matrix (preflight only).
.venv/bin/python scripts/run_ablation_re2.py --phase all --skip-existing --dry-run

# List registered prompt strategies and exit.
.venv/bin/python scripts/run_ablation_harfbuzz.py --list-strategies
```

### 6.5 Inspect / audit running state

Inspection commands:

```bash
# Live seed-count matrix.
for v in v0_none v1_src v2_src_tests v3_all v4_src_gaps; do
  for m in claude-sonnet-4-6 claude-haiku-4-5-20251001 \
           llama-3.1-8b-instruct llama-3.1-70b-instruct codestral-22b; do
    hb=$(find synthesis/results/ablation_harfbuzz/seeds/harfbuzz/ablation/$v/$m/ -type f 2>/dev/null | wc -l)
    re2=$(find synthesis/results/ablation_re2_v2/seeds/re2/ablation/$v/$m/ -type f 2>/dev/null | wc -l)
    echo "$v/$m  hb=$hb  re2=$re2"
  done
done

# Cumulative cost since the cache was populated.
.venv/bin/python -m analysis.scripts.cost_audit
cat results/cost_audit/summary.md

# Pre-experiment estimator.
.venv/bin/python -m analysis.scripts.estimate_cost \
    --model claude-sonnet-4-6 --n-calls 400 --mean-in 3000 --mean-out 800
```

### 6.6 Aggregate and report

```bash
# `analysis.scripts.ablation_summary` is currently pinned to the
# invalidated experiment2_0 results path (results/ablation_v3/...) and
# takes NO command-line flags. Running it produces
# results/ablation_v3/summary.md only.
.venv/bin/python -m analysis.scripts.ablation_summary

# For the current experiment2_1 run, aggregation is manual: per-cell
# results live at
#   results/ablation_re2_v2/{m1,m2}/<variant>/<model>/summary.json
#   results/ablation_harfbuzz/{m1,m2}/<variant>/<model>/summary.json
# Rolling these up into a single markdown table for the headline run is
# a known gap (see docs/STATUS.md).
```

Per-experiment writeups live in `docs/experiment1.md` (RE2 A/B +
generalization, formerly `AB_RE2_REPORT.md` plus the held-out / 7-cell
follow-ups), `docs/experiment2.md` (multi-model 5-variant ablation +
real-fuzzer campaigns, formerly `ABLATION_RESULTS_SUMMARY.md` /
`LLAMA_ABLATION_RESULTS.md` / `EXPERIMENT_HANDOFF.md`), and
`docs/experiment3.md` (prompt-strategy axis + 5-cell prompt optimization,
formerly `PROMPT_STRATEGIES_HANDOFF.md` / `PROMPT_OPTIMIZATION_STATUS.md`).

---

## 7. Critical invariants — do not relax

These are load-bearing for research validity. Each has a concrete past
incident behind it.

1. **Provenance is sacred.** Every test object in `dataset/` traces back
   to `upstream_repo:commit:file:line`. Extractors only read upstream
   code; if the extractor can't find it, **raise**, do not synthesize.
2. **Hard-branch filter for M2: `struct_hits ≥ 1 AND rand_hits == 0`.**
   Loosening `rand_hits == 0` invalidated a prior RE2 ablation run
   (`experiment2_0`; see `docs/experiment2.md §3`). The random baseline must score
   exactly 0% by construction.
3. **Deterministic seeds.** RNG seed = 42 for all subsampling; RE2
   flag bytes are sha256-derived. Changing either breaks comparability.
4. **150-seed normalization.** Synthesis retries until exactly 150
   seeds (300-attempt cap, 100 for binary-output-capped models), then
   deterministically subsamples. Don't short-circuit — seed count is
   otherwise a confound.
5. **Cache salt must include `attempt+offset`.** Format:
   `f"model={model},sample={k},ablation={cell},run={attempt+offset}"`.
   When restarting, **bump `--attempt-offset` by ≥ 5000** to avoid
   replaying cached failures.
6. **Per-target constants live in `TargetSpec`.** Don't scatter
   `HB_*` / `RE2_V2_*` constants across `core/config.py` or the
   orchestrators.
7. **Dollar figures cite a command.** No hand-estimated cost prose;
   everything goes through `cost_audit.py` or `estimate_cost.py`,
   both backed by `PRICING_USD_PER_MTOK`.
8. **64-byte cap on harfbuzz binary blobs (≈88 base64 chars).** The UF
   LiteLLM proxy truncates responses at 2048 chars; bigger blobs cause
   JSON parse failures counted as loop aborts.
9. **`assert_no_tests` in `build_source_prompt.py` must not be silenced.**
   It is the primary defence of the ablation's validity.

---

## 8. Where to look when something goes wrong

| Symptom | Look at |
|---|---|
| Synthesis aborts after 20 attempts with 0 seeds | Loop detector triggered. Inspect raw response in `.cache/llm/`; `core/loop_detector.py` for the heuristic. |
| Cell reports "synthesis capped: cell skipped" | Hit `MAX_ATTEMPTS_DEFAULT=300` (or 100 on capped). Bump `--attempt-offset` and retry; check if the model is producing parseable JSON at all. |
| M2 random-baseline ≠ 0% | Hard-branch fixture is stale or `rand_hits == 0` filter was relaxed. Re-freeze with `freeze_target_branches.py`. |
| M1 numbers don't match prior runs | DWARF source-roots mismatch. RE2's baked binary uses the pre-rearch `phase1_dataset/...` prefix — pass `--source-roots` accordingly or rebuild. |
| Cache hit rate too high after restart | `--attempt-offset` was not bumped. The cache salt collides with prior failures. |
| Costs surprise high | Check `analysis/scripts/cost_audit.py` output; compare to `estimate_cost.py` projection. |

---

## 9. Where to read next

In priority order if you are picking this up cold:

1. `docs/STATUS.md` — living handoff, what's actively running.
2. `docs/experiment2.md` — current headline (multi-model ablation, both targets).
3. `docs/experiment1.md` — earlier RE2 A/B + generalization follow-ups.
4. `docs/experiment3.md` — prompt-strategy axis (most recent addition).
5. `docs/research_document_v3.md` — authoritative research design.
6. `docs/plan_v3.md` — authoritative execution plan (the full roadmap;
   the ablation is one slice of it).
7. `docs/FUTURE_DIRECTIONS.md` — pending experiments with cost
   estimates.
8. The per-directory `README.md` files under `dataset/`, `synthesis/`,
   `analysis/`, `prediction/`, `transfer/`, `finetuning/` — package-level
   notes on conventions and invariants.
