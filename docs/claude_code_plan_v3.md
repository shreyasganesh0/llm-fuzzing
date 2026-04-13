# CLAUDE CODE EXECUTION PLAN v3: Unit Test–Conditioned LLM-Guided Fuzzing

## Changelog from v2

| # | Gap Identified | Fix Applied | Location in Plan |
|---|---|---|---|
| 1 | No threats-to-validity section | Added Section "Threats to Validity" with 6 explicit threats + mitigations | §Threats to Validity |
| 2 | No random-generation baseline | Added Baseline 6: random syntactically-valid inputs (same count as LLM) | §Phase 3 configs |
| 3 | No crash dedup strategy | Added dedup protocol: stack-hash + coverage-profile + manual triage | §Phase 3 bug analysis |
| 4 | No LLM generation wall-clock metric | Added `generation_wall_clock_s` to mandatory logging | §Phase 2 + Phase 3 |
| 5 | No prompt sensitivity ablation | Added Phase 2.5: prompt rephrase ablation (3 variants) | §Phase 2.5 |
| 6 | Training data contamination unaddressed | Added contamination test protocol + memorization probe | §Threats to Validity + Phase 1.10 |
| 7 | No failure mode analysis | Added `corpus_pollution_check` to Phase 3 | §Phase 3.4 |
| 8 | Temperature not specified | Fixed temperature=0 for all deterministic runs, temperature=0.7 for generation with 3 samples | §LLM Parameters |
| 9 | No source-only ablation | Added Experiment 2: source-code-only LLM synthesis (no unit tests, no coverage metadata) to isolate the value of test conditioning | §Experiment 2 (NEW v3.1) |

---

## Project Overview

Build an experimental framework that:
1. Extracts **real, existing upstream unit tests** from FuzzBench target projects (NO fabricated tests)
2. Measures per-test coverage profiles using LLVM source-based instrumentation
3. Constructs a dataset of (unit_test, source_code, coverage_profile) triples
4. Prompts LLMs to predict coverage of held-out tests and generate gap-filling inputs
5. Evaluates LLM-generated inputs via libFuzzer campaigns following the **FuzzBench gold standard** (20 trials, 23h, Mann-Whitney U, Vargha-Delaney Â₁₂, Friedman-Nemenyi)
6. **(NEW v3.1) Experiment 2:** Runs a parallel source-code-only experiment where the LLM receives only raw source code and the harness — no unit tests, no coverage metadata — to isolate whether test conditioning is the key contribution or whether LLMs can reason about coverage from source alone

The project is structured in 4 phases + Experiment 2 + 2 cross-cutting concerns (contamination testing, failure mode analysis). Each phase produces artifacts that the next phase consumes. All code lives in a single repository.

---

## CRITICAL CONSTRAINTS

### No Fabricated Tests
Every unit test in this experiment MUST come from an upstream project repository written by the project's own maintainers. The extraction scripts must preserve **provenance metadata** (upstream repo, commit hash, file path, line number) for every test. This is non-negotiable for research validity.

### FuzzBench Evaluation Methodology
All fuzzing campaign evaluations MUST follow the FuzzBench gold standard:
- **20 trials** per configuration per benchmark
- **23-hour campaigns** (82,800 seconds)
- **Clang source-based code coverage** (collision-free, fuzzer-independent)
- **Corpus snapshots every 15 minutes**
- **Mann-Whitney U test** (two-tailed, α = 0.05) for pairwise significance
- **Vargha-Delaney Â₁₂** for effect size
- **Friedman test + Nemenyi post-hoc** for cross-benchmark comparison
- **Critical difference diagrams** for final ranking visualization

These parameters come from: Metzman et al. (ESEC/FSE 2021), Klees et al. (CCS 2018), Böhme et al. (ICSE 2022), Schloegel et al. (IEEE S&P 2024).

### LLM Parameters (NEW in v3)

All LLM calls MUST record and use these parameters:

| Context | Temperature | Top-p | Samples | Rationale |
|---|---|---|---|---|
| Coverage prediction (Phase 2 RQ1) | 0.0 | 1.0 | 1 | Deterministic for reproducibility |
| Prompt sensitivity ablation (Phase 2.5) | 0.0 | 1.0 | 1 | Isolate prompt wording effect |
| Input synthesis (Phase 3) | 0.7 | 0.95 | 3 | Sample diversity for seed variety; take union of all 3 |
| Contamination probe (Phase 1.10) | 0.0 | 1.0 | 1 | Deterministic for measuring memorization |
| Fine-tuning inference (Phase 4) | 0.0 | 1.0 | 1 | Fair comparison with few-shot |

These are recorded in every API call log entry alongside model string, token counts, and cost.

---

## Repository Structure

```
utcf/                              # Unit Test–Conditioned Fuzzing
├── README.md
├── Makefile                       # Top-level orchestration
├── pyproject.toml                 # Python 3.10+
├── requirements.txt
├── pinned_versions.yaml           # [NEW audit] Pinned upstream + FuzzBench commit SHAs, dict paths, harness paths for all 11 targets
│
├── dataset/                # Phase 1: Dataset Construction
│   ├── README.md
│   ├── targets/                   # Target configurations (verified upstream test locations)
│   │   ├── re2.yaml               # Tier 1: Google Test, ~1000+ tests
│   │   ├── harfbuzz.yaml          # Tier 1: GLib, 2200+ tests
│   │   ├── openssl.yaml           # Tier 1: TAP + C testutil, 350+ tests
│   │   ├── sqlite3.yaml           # Tier 1: TCL, 51,445 tests
│   │   ├── libxml2.yaml           # Tier 2: Custom C, ~1100 API tests
│   │   ├── libjpeg_turbo.yaml     # Tier 2: CTest, ~300 tests
│   │   ├── lcms.yaml              # Tier 2: Custom C, ~200 tests
│   │   ├── proj.yaml              # Tier 2: Google Test, ~61 suites
│   │   ├── libpng.yaml            # Tier 3: Custom C, ~33 tests (transfer only)
│   │   ├── freetype2.yaml         # Tier 3: Meson, handful (transfer only)
│   │   └── zlib.yaml              # Tier 3: Custom C, 2-3 programs (transfer only)
│   ├── scripts/
│   │   ├── fetch_target.sh        # Clone upstream repo at pinned FuzzBench commit
│   │   ├── build_instrumented.sh  # Build with LLVM source-based coverage
│   │   ├── extract_tests.py       # Extract tests PER FRAMEWORK (see below)
│   │   ├── extractors/            # Framework-specific test extractors
│   │   │   ├── googletest.py      # For RE2, PROJ: parse TEST()/TEST_F() macros
│   │   │   ├── glib.py            # For HarfBuzz: parse g_test_add_func() registrations
│   │   │   ├── custom_c.py        # For libxml2, lcms, libpng: parse test_*/Check* functions
│   │   │   ├── ctest.py           # For libjpeg-turbo: parse CMakeLists add_test() commands
│   │   │   ├── tcl.py             # For SQLite: parse do_test/do_execsql_test in .test files
│   │   │   └── perl_tap.py        # For OpenSSL: parse test/recipes/*.t + C ADD_TEST()
│   │   ├── run_test_coverage.py   # Run each test individually under llvm-cov
│   │   ├── compute_gaps.py        # Compute union coverage and gap analysis
│   │   ├── build_dataset.py       # Assemble final dataset with provenance metadata
│   │   └── contamination_probe.py # [NEW v3] Test LLM memorization of upstream tests
│   ├── dataset/                   # OUTPUT: generated dataset (gitignored)
│   │   └── .gitkeep
│   └── tests/
│       ├── test_extract.py        # Verify extraction produces valid test objects WITH provenance
│       ├── test_provenance.py     # Verify every test traces back to upstream file:line
│       ├── test_coverage.py       # Verify coverage JSON schema
│       ├── test_dataset.py        # Verify final dataset structure + no fabricated tests
│       └── test_contamination.py  # [NEW v3] Verify contamination probe ran + results recorded
│
├── prediction/             # Phase 2: LLM Coverage Prediction
│   ├── README.md
│   ├── prompts/
│   │   ├── coverage_prediction.j2 # Jinja2 template: predict coverage from test+source
│   │   ├── coverage_prediction_rephrase_A.j2  # [NEW v3] Rephrase variant A
│   │   ├── coverage_prediction_rephrase_B.j2  # [NEW v3] Rephrase variant B
│   │   ├── input_synthesis.j2     # Jinja2 template: generate gap-filling inputs
│   │   └── system_prompt.txt      # System prompt (references upstream test provenance)
│   ├── scripts/
│   │   ├── build_prompt.py        # Assemble prompt from dataset + template + few-shot config
│   │   ├── run_prediction.py      # Send prompts to LLM API, collect responses
│   │   ├── parse_response.py      # Parse LLM JSON responses, validate schema
│   │   ├── evaluate_prediction.py # Compare predicted coverage to ground truth
│   │   ├── prompt_sensitivity.py  # [NEW v3] Run prompt rephrase ablation
│   │   └── config.py              # API keys, model selection, hyperparameters
│   ├── results/                   # OUTPUT: prediction results (gitignored)
│   │   └── .gitkeep
│   └── tests/
│       ├── test_prompt_build.py   # Verify prompt includes real upstream test code only
│       ├── test_parse.py          # Verify response parsing handles edge cases
│       ├── test_evaluate.py       # Verify metric calculations match expected formulas
│       └── test_sensitivity.py    # [NEW v3] Verify sensitivity ablation ran for all models
│
├── synthesis/              # Phase 3: Gap-Targeted Input Synthesis & Fuzzing
│   ├── README.md
│   ├── scripts/
│   │   ├── generate_inputs.py     # Use LLM to generate gap-filling inputs
│   │   ├── generate_random_inputs.py  # [NEW v3] Random syntactically-valid baseline
│   │   ├── validate_inputs.py     # Run generated inputs, check for crashes
│   │   ├── measure_coverage.py    # Measure coverage of LLM-generated inputs
│   │   ├── run_fuzzing.py         # Run libFuzzer campaigns (FuzzBench methodology)
│   │   ├── compare_baselines.py   # Statistical tests + plots
│   │   ├── dedup_crashes.py       # [NEW v3] Stack-hash + coverage-profile crash dedup
│   │   ├── failure_analysis.py    # [NEW v3] Detect when LLM seeds hurt performance
│   │   └── campaign_configs/
│   │       ├── empty.yaml              # libFuzzer with empty corpus
│   │       ├── fuzzbench_seeds.yaml    # libFuzzer with FuzzBench-provided seeds
│   │       ├── unittest_seeds.yaml     # libFuzzer with upstream unit test inputs
│   │       ├── llm_seeds.yaml          # libFuzzer with LLM-generated seeds
│   │       ├── combined_seeds.yaml     # libFuzzer with unittest + LLM seeds
│   │       └── random_seeds.yaml       # [NEW v3] libFuzzer with random valid inputs
│   ├── results/
│   │   └── .gitkeep
│   └── tests/
│       ├── test_input_format.py   # Verify generated inputs are valid for target
│       ├── test_campaign.py       # Verify fuzzing campaign runner parameters
│       ├── test_statistics.py     # Verify Mann-Whitney U + Vargha-Delaney calculations
│       ├── test_dedup.py          # [NEW v3] Verify dedup logic
│       └── test_random_gen.py     # [NEW v3] Verify random baseline produces valid inputs
│
├── transfer/                # [NEW audit] Cross-Target Transfer (RQ4)
│   ├── README.md
│   ├── scripts/
│   │   ├── build_loo_prompt.py        # Leave-one-out prompt assembly (few-shot pool excludes target-under-test)
│   │   ├── run_transfer_prediction.py # Coverage prediction on held-out target using cross-target examples
│   │   ├── run_transfer_synthesis.py  # Input synthesis for held-out target using cross-target examples
│   │   ├── run_tier3_evaluation.py    # Evaluate on Tier 3 targets (never used for training)
│   │   └── evaluate_transfer.py       # Compute LOO matrix + format-similarity stratification
│   ├── results/
│   │   └── .gitkeep
│   └── tests/
│       ├── test_loo_exclusion.py      # Verify few-shot pool never includes the held-out target
│       └── test_tier3_isolation.py    # Verify Tier 3 targets never appear in any training context
│
├── finetuning/             # Phase 4: Fine-Tuning Evaluation
│   ├── README.md
│   ├── scripts/
│   │   ├── prepare_finetune_data.py  # Convert dataset to Alpaca JSONL (upstream tests only)
│   │   ├── add_cot_traces.py         # Annotate WHY upstream tests cover what they cover
│   │   ├── finetune.py               # LoRA fine-tuning (HF PEFT + Transformers)
│   │   ├── run_finetuned.py          # Run fine-tuned model on eval set
│   │   └── compare_all.py            # Final comparison across all configs A-I
│   ├── configs/
│   │   ├── lora_8b.yaml
│   │   └── lora_70b.yaml
│   └── tests/
│       ├── test_data_format.py        # Verify training data contains only upstream tests
│       ├── test_lora_load.py
│       └── test_finetuned_output.py   # [NEW audit] Verify fine-tuned model produces valid JSON predictions end-to-end
│
├── synthesis/       # [NEW v3.1] Experiment 2: Source-Code-Only LLM Synthesis
│   ├── README.md
│   ├── prompts/
│   │   ├── source_only_analysis.j2      # Prompt: analyze source code, identify hard branches
│   │   ├── source_only_synthesis.j2     # Prompt: generate inputs from source + harness only
│   │   └── system_prompt_source_only.txt
│   ├── scripts/
│   │   ├── extract_source_context.py    # Extract source files + harness for each target
│   │   ├── build_source_prompt.py       # Assemble prompt: source code + harness, NO tests
│   │   ├── run_source_prediction.py     # LLM predicts hard-to-cover branches (no test examples)
│   │   ├── generate_source_inputs.py    # LLM generates seeds from source-only reasoning
│   │   ├── evaluate_source_prediction.py # Compare source-only predictions to Phase 1 ground truth
│   │   ├── run_source_fuzzing.py        # Run 23h campaigns with source-only seeds
│   │   └── compare_experiments.py       # Exp 1 vs Exp 2 statistical comparison
│   ├── campaign_configs/
│   │   ├── source_only_llm_seeds.yaml   # libFuzzer with source-only LLM seeds
│   │   └── source_only_combined.yaml    # libFuzzer with source-only LLM + FuzzBench seeds
│   ├── results/
│   │   └── .gitkeep
│   └── tests/
│       ├── test_source_prompt.py        # Verify prompts contain NO unit test code
│       ├── test_source_inputs.py        # Verify generated inputs are valid
│       └── test_experiment_comparison.py # Verify Exp 1 vs Exp 2 stats computed
│
├── analysis/                      # Cross-phase analysis and figures
│   ├── scripts/
│   │   ├── mann_whitney.py        # Mann-Whitney U test implementation
│   │   ├── vargha_delaney.py      # Â₁₂ effect size implementation
│   │   ├── friedman_nemenyi.py    # Cross-benchmark ranking + critical difference diagrams
│   │   ├── plot_coverage.py       # Coverage-over-time curves with 95% CI
│   │   └── threat_analysis.py     # [NEW v3] Generate threat-to-validity evidence tables
│   ├── notebooks/
│   │   ├── 01_dataset_stats.ipynb
│   │   ├── 02_prediction_results.ipynb
│   │   ├── 03_synthesis_results.ipynb
│   │   ├── 04_finetuning_comparison.ipynb
│   │   ├── 05_paper_figures.ipynb
│   │   ├── 06_contamination_analysis.ipynb   # [NEW v3]
│   │   ├── 07_failure_mode_analysis.ipynb    # [NEW v3]
│   │   ├── 08_exp1_vs_exp2_comparison.ipynb  # [NEW v3.1]
│   │   └── 09_transfer_evaluation.ipynb      # [NEW audit] Cross-target transfer results
│   └── figures/
│       └── .gitkeep
│
├── scripts/                       # Shared utilities
│   ├── llm_client.py              # Unified LLM API client (OpenAI, Anthropic, local)
│   ├── coverage_utils.py          # Coverage JSON parsing, comparison, metrics
│   ├── dataset_schema.py          # Pydantic models (including provenance fields)
│   └── logging_config.py          # Structured logging
│
└── docker/
    ├── Dockerfile.build           # LLVM 15+, clang, compiler-rt, libFuzzer, llvm-cov
    ├── Dockerfile.fuzz            # Minimal image for 23h campaigns
    └── docker-compose.yaml
```

---

## pinned_versions.yaml (NEW audit — BLOCKING)

Every target must have its upstream commit SHA, FuzzBench commit SHA, harness path, dictionary path, and libFuzzer-specific flags pinned in a single source of truth. All `targets/*.yaml` files and all scripts read from this file. Without this, Claude Code will have to guess commit SHAs and miss target-specific flags (dictionaries, timeouts, memory limits) that can shift coverage by 20%+.

```yaml
# pinned_versions.yaml — Single source of truth for all upstream + FuzzBench version pins
#
# INSTRUCTIONS: Fill in every <FILL> placeholder before any script runs.
# To find the correct upstream commit for a FuzzBench benchmark:
#   1. Check google/fuzzbench repo → benchmarks/<benchmark_name>/Makefile
#      or benchmarks/<benchmark_name>/benchmark.yaml for the pinned version.
#   2. For fuzzer-test-suite targets, check google/fuzzer-test-suite → <target>/Makefile
#      for the download URL / git tag.
#   3. Record the exact commit hash from `git log --oneline -1` after checkout.

fuzzbench:
  repo: https://github.com/google/fuzzbench.git
  commit: <FILL: pinned FuzzBench commit hash>

fuzzer_test_suite:
  repo: https://github.com/google/fuzzer-test-suite.git
  commit: <FILL: pinned fuzzer-test-suite commit hash>

targets:
  re2:
    upstream_repo: https://github.com/google/re2.git
    upstream_commit: <FILL: commit matching FuzzBench re2-2014-12-09>
    fuzzbench_benchmark: re2-2014-12-09
    harness_source: fuzzer-test-suite
    harness_path: re2-2014-12-09/target.cc
    dictionary_path: null  # No dictionary shipped for RE2
    libfuzzer_extra_flags:
      timeout: 25          # Per-input timeout in seconds
      rss_limit_mb: 2048
      max_len: 4096        # RE2 inputs are regex+string, relatively short
    seeds: none

  harfbuzz:
    upstream_repo: https://github.com/harfbuzz/harfbuzz.git
    upstream_commit: <FILL: commit matching FuzzBench harfbuzz-1.3.2>
    fuzzbench_benchmark: harfbuzz-1.3.2
    harness_source: fuzzer-test-suite
    harness_path: harfbuzz-1.3.2/target.cc
    dictionary_path: null
    libfuzzer_extra_flags:
      timeout: 25
      rss_limit_mb: 2048
      max_len: 65536       # Font files can be large
    seeds: varies

  openssl:
    upstream_repo: https://github.com/openssl/openssl.git
    upstream_commit: <FILL: commit matching FuzzBench openssl_x509>
    fuzzbench_benchmark: openssl_x509
    harness_source: oss-fuzz
    harness_path: projects/openssl/fuzz/x509.c
    dictionary_path: projects/openssl/fuzz/x509.dict  # ASN.1/DER tokens
    libfuzzer_extra_flags:
      timeout: 25
      rss_limit_mb: 2048
      max_len: 10240
    seeds: 2241  # DER certificate files

  sqlite3:
    upstream_repo: https://github.com/sqlite/sqlite.git
    upstream_commit: <FILL: commit matching FuzzBench sqlite3_ossfuzz>
    fuzzbench_benchmark: sqlite3_ossfuzz
    harness_source: oss-fuzz
    harness_path: projects/sqlite3/ossfuzz.c
    dictionary_path: projects/sqlite3/sql.dict  # SQL keyword tokens
    libfuzzer_extra_flags:
      timeout: 25
      rss_limit_mb: 2048
      max_len: 16384       # SQL statements can be complex
    seeds: 1258

  libxml2:
    upstream_repo: https://gitlab.gnome.org/GNOME/libxml2.git
    upstream_commit: <FILL: commit matching FuzzBench libxml2-v2.9.2>
    fuzzbench_benchmark: libxml2-v2.9.2
    harness_source: fuzzer-test-suite
    harness_path: libxml2-v2.9.2/target.cc
    dictionary_path: libxml2-v2.9.2/xml.dict  # XML tokens if available, else null
    libfuzzer_extra_flags:
      timeout: 25
      rss_limit_mb: 2048
      max_len: 65536
    seeds: none

  libjpeg_turbo:
    upstream_repo: https://github.com/libjpeg-turbo/libjpeg-turbo.git
    upstream_commit: <FILL: commit matching FuzzBench libjpeg-turbo-07-2017>
    fuzzbench_benchmark: libjpeg-turbo-07-2017
    harness_source: fuzzer-test-suite
    harness_path: libjpeg-turbo-07-2017/target.cc
    dictionary_path: null
    libfuzzer_extra_flags:
      timeout: 25
      rss_limit_mb: 2048
      max_len: 1048576     # JPEG files can be large
    seeds: varies

  lcms:
    upstream_repo: https://github.com/mm2/Little-CMS.git
    upstream_commit: <FILL: commit matching FuzzBench lcms-2017-03-21>
    fuzzbench_benchmark: lcms-2017-03-21
    harness_source: fuzzer-test-suite
    harness_path: lcms-2017-03-21/target.cc
    dictionary_path: null
    libfuzzer_extra_flags:
      timeout: 25
      rss_limit_mb: 2048
      max_len: 65536
    seeds: varies

  proj:
    upstream_repo: https://github.com/OSGeo/PROJ.git
    upstream_commit: <FILL: commit matching FuzzBench proj4-2017-08-14>
    fuzzbench_benchmark: proj4-2017-08-14
    harness_source: oss-fuzz
    harness_path: projects/proj4/proj_crs_to_crs_fuzzer.c
    dictionary_path: null
    libfuzzer_extra_flags:
      timeout: 25
      rss_limit_mb: 2048
      max_len: 4096
    seeds: varies

  libpng:
    upstream_repo: https://github.com/glennrp/libpng.git
    upstream_commit: <FILL: commit matching FuzzBench libpng-1.2.56>
    fuzzbench_benchmark: libpng-1.2.56
    harness_source: fuzzer-test-suite
    harness_path: libpng-1.2.56/target.cc
    dictionary_path: libpng-1.2.56/png.dict  # PNG chunk tokens
    libfuzzer_extra_flags:
      timeout: 25
      rss_limit_mb: 2048
      max_len: 1048576
    seeds: varies

  freetype2:
    upstream_repo: https://gitlab.freedesktop.org/freetype/freetype.git
    upstream_commit: <FILL: commit matching FuzzBench freetype2-2017>
    fuzzbench_benchmark: freetype2-2017
    harness_source: fuzzer-test-suite
    harness_path: freetype2-2017/target.cc
    dictionary_path: null
    libfuzzer_extra_flags:
      timeout: 25
      rss_limit_mb: 2048
      max_len: 1048576
    seeds: varies

  zlib:
    upstream_repo: https://github.com/madler/zlib.git
    upstream_commit: <FILL: commit matching FuzzBench zlib_zlib_uncompress_fuzzer>
    fuzzbench_benchmark: zlib_zlib_uncompress_fuzzer
    harness_source: oss-fuzz
    harness_path: projects/zlib/uncompress_fuzzer.c
    dictionary_path: null
    libfuzzer_extra_flags:
      timeout: 25
      rss_limit_mb: 2048
      max_len: 65536
    seeds: none
```

**IMPORTANT:** Every `<FILL>` must be resolved before `fetch_target.sh` can run. To find the correct commit hash:
1. For `fuzzer-test-suite` targets: check the Makefile download URL in `google/fuzzer-test-suite/<target>/Makefile` → download the tarball → extract the version → look up that version's git tag in the upstream repo.
2. For `oss-fuzz` targets: check `google/oss-fuzz/projects/<project>/Dockerfile` for the version pin → look up the corresponding commit hash.
3. All target YAMLs (`targets/*.yaml`) MUST read `upstream.commit` from this file rather than specifying it inline.

---

## Phase 1: Dataset Construction

### Goal
Extract real upstream unit tests and produce (test, source, coverage) triples for 8+ targets.

### Tasks

#### 1.1 Target Configuration Files

Each YAML config must specify the EXACT upstream test locations verified by our research. The `upstream.commit` field MUST be read from `pinned_versions.yaml` (not hardcoded).

```yaml
# targets/re2.yaml
name: re2
tier: 1  # Rich test suite — primary target
version: "2014-12-09"
fuzzbench_benchmark: re2-2014-12-09

upstream:
  repo: https://github.com/google/re2.git
  commit: !from_pinned re2.upstream_commit   # Resolved from pinned_versions.yaml at load time
  license: BSD-3-Clause

tests:
  framework: googletest
  locations:
    - re2/testing/re2_test.cc
    - re2/testing/parse_test.cc
    - re2/testing/compile_test.cc
    - re2/testing/search_test.cc
    - re2/testing/set_test.cc
    - re2/testing/required_prefix_test.cc
    - re2/testing/dfa_test.cc
    - re2/testing/exhaustive_test.cc
    - re2/testing/exhaustive1_test.cc
    - re2/testing/exhaustive2_test.cc
    - re2/testing/exhaustive3_test.cc
    - re2/testing/mimics_pcre_test.cc
    - re2/testing/random_test.cc
    - re2/testing/string_generator_test.cc
    - re2/testing/filtered_re2_test.cc
  discovery_pattern: "TEST\\(|TEST_F\\("
  estimated_test_count: "1000+"
  build_cmd: "cmake -DCMAKE_CXX_COMPILER=clang++ -DCMAKE_C_COMPILER=clang && make"
  run_cmd: "./{test_binary} --gtest_filter={test_name}"

source_files:
  primary:
    - re2/re2.cc
    - re2/compile.cc
    - re2/dfa.cc
    - re2/nfa.cc
    - re2/parse.cc
    - re2/prog.cc
    - re2/simplify.cc
  secondary:
    - re2/bitstate.cc
    - re2/onepass.cc
    - re2/set.cc

fuzzbench:
  harness_source: !from_pinned re2.harness_source  # fuzzer-test-suite, NOT oss-fuzz
  harness_file: !from_pinned re2.harness_path       # target.cc
  dictionary: !from_pinned re2.dictionary_path       # null for RE2
  seeds: !from_pinned re2.seeds                      # none
  input_format: "regex_pattern + test_string (text)"
  libfuzzer_extra_flags: !from_pinned re2.libfuzzer_extra_flags

build:
  coverage_flags: "-fprofile-instr-generate -fcoverage-mapping"
  sanitizer_flags: "-fsanitize=address,undefined"
  fuzzer_flags: "-fsanitize=fuzzer"
```

Create similar configs for ALL targets. Key differences per target:

```yaml
# targets/harfbuzz.yaml
tests:
  framework: glib
  locations:
    - test/api/test-buffer.c
    - test/api/test-font.c
    - test/api/test-shape.c
    - test/api/test-unicode.c
    - test/api/test-blob.c
    - test/api/test-subset.c
    # Plus ~2200 data-driven shaping tests in test/shaping/
  discovery_pattern: "g_test_add_func\\("
  estimated_test_count: "2200+ shaping + 30 API files"

# targets/libxml2.yaml
tests:
  framework: custom_c
  locations:
    - testapi.c           # Auto-generated by gentest.py, exercises ~1100 API functions
    - runtest.c           # Processes XML test files from test/ subdirectories
  data_directories:
    - test/               # XML test input files used by runtest.c
  discovery_pattern: "test_.*\\(|static int .*test"
  estimated_test_count: "~1100 API tests + hundreds of XML file tests"
  extraction_notes: >
    testapi.c is auto-generated and monolithic. Individual test isolation requires
    parsing the generated code structure. Each generated function tests one API call.
    runtest.c iterates over test/ subdirectories — each XML file is effectively one test.

# targets/sqlite3.yaml
tests:
  framework: tcl
  locations:
    - test/*.test         # 1,390 TCL test files
  discovery_pattern: "do_test|do_execsql_test|do_catchsql_test"
  estimated_test_count: "51,445 (open-source TCL suite only)"
  extraction_notes: >
    TCL test files require a TCL interpreter. Each do_test invocation is one test.
    The proprietary TH3 suite (50,362 additional tests) is NOT available.
    Only the open-source TCL suite in test/ is used.
  run_cmd: "tclsh {test_file}"
  input_format: "SQL statements (text)"

# targets/openssl.yaml
tests:
  framework: perl_tap
  locations:
    - test/recipes/*.t        # 250+ Perl TAP test recipes
    - test/*test.c            # 100+ C test programs
  discovery_pattern: "ADD_TEST\\(|simple_test\\("
  estimated_test_count: "350+ (250 Perl + 100+ C)"
  extraction_notes: >
    Two-layer architecture. Perl .t files orchestrate C test programs.
    C test programs use testutil.h with ADD_TEST() macros.
    Extract at the C ADD_TEST() level for finest granularity.
```

#### 1.2 fetch_target.sh

```bash
#!/bin/bash
# Usage: ./fetch_target.sh <target_config.yaml>
#
# Clones the UPSTREAM project repository at the EXACT commit matching
# the FuzzBench benchmark version. Also fetches the FuzzBench harness.
#
# CRITICAL: This clones the UPSTREAM repo (e.g., github.com/google/re2),
# NOT the FuzzBench repo. FuzzBench only provides harnesses, not tests.
#
# Steps:
# 1. Parse YAML config for repo URL; read commit hash from pinned_versions.yaml
# 2. Verify pinned_versions.yaml has no <FILL> placeholders for this target
# 3. Clone upstream repo at exact commit
# 4. Verify commit hash matches (git rev-parse HEAD)
# 5. Record provenance: {repo, commit, clone_date, git_log_head}
# 6. Fetch FuzzBench harness from google/fuzzbench or fuzzer-test-suite
#    (using harness_path from pinned_versions.yaml)
# 7. If dictionary_path is set in pinned_versions.yaml, fetch the dictionary file
# 8. Store in targets/src/<target_name>/
```

#### 1.3 build_instrumented.sh

```bash
#!/bin/bash
# Usage: ./build_instrumented.sh <target_config.yaml>
#
# Builds the target with LLVM source-based coverage instrumentation.
# Uses clang/clang++ (LLVM 15+) with:
#   -fprofile-instr-generate -fcoverage-mapping
#
# IMPORTANT: Builds THREE variants:
# 1. Coverage build: for measuring per-test coverage (Phase 1)
# 2. Sanitizer build: for crash detection (Phase 3 validation)
# 3. Fuzzer build: -fsanitize=fuzzer for libFuzzer campaigns (Phase 3)
#
# Also builds the upstream test runner/binary (not just the library).
```

#### 1.4 extract_tests.py + Framework-Specific Extractors

```python
"""
extract_tests.py — Orchestrator that dispatches to framework-specific extractors.

Input: target config YAML
Output: JSON list of test objects, each containing:
  - test_name: str
  - test_code: str (the function body, UNMODIFIED from upstream)
  - test_file: str (source file path)
  - upstream_repo: str (e.g., "https://github.com/google/re2.git")
  - upstream_commit: str (exact commit hash)
  - upstream_file: str (path within upstream repo)
  - upstream_line: int (line number where test starts)
  - framework: str (e.g., "googletest", "glib", "custom_c")
  - input_data: Optional[str] (literal input values extracted from test)
  - called_functions: List[str] (statically extracted function calls)

PROVENANCE IS MANDATORY. Every test object MUST include upstream_repo,
upstream_commit, upstream_file, and upstream_line. Tests without
provenance metadata are rejected.

INPUT DATA EXTRACTION (NEW audit — addresses hallucination risk):
  input_data is best-effort extracted via tree-sitter string-literal
  harvesting with target-specific rules:
    - RE2: first two string arguments of RE2::FullMatch/PartialMatch/
      FindAndConsume calls (pattern + test string)
    - SQLite: SQL string argument of do_execsql_test / do_test
    - libxml2: file path argument from test data directories, or
      inline XML string literals
    - OpenSSL: file path arguments from test data (test/certs/*.pem, etc.)
    - HarfBuzz: font file path + text string from shaping test data
    - libjpeg-turbo: JPEG file path from CTest command arguments
    - lcms: ICC profile paths from Check*() test bodies
    - PROJ: coordinate transform string literals
  If no literal input can be extracted (e.g., input is computed
  programmatically), set input_data = null and log the skip.

CALLED FUNCTIONS EXTRACTION (NEW audit — addresses tooling ambiguity):
  called_functions are extracted using tree-sitter AST analysis ONLY
  (no semantic resolution). The algorithm:
    1. Parse the test function body with tree-sitter C/C++ parser
    2. Extract all call_expression nodes
    3. For C++: extract the callee as-is (e.g., "RE2::FullMatch",
       "xmlParseMemory") — do NOT attempt to resolve virtual dispatch
       or template instantiations
    4. Filter out standard library calls (assert, printf, malloc, etc.)
    5. Filter out test framework calls (ASSERT_TRUE, g_assert, etc.)
  This is intentionally shallow — deep resolution would require clangd
  or cscope and is out of scope. The called_functions field is used for
  source context prioritization, not for precise call-graph analysis.
"""
```

**Framework-specific extractors:**

```python
# extractors/googletest.py
"""
Extract tests from Google Test files (RE2, PROJ).

Parses TEST() and TEST_F() macros using tree-sitter C++ parser.
Each macro invocation becomes one test object.

Example extraction from re2/testing/re2_test.cc:
  TEST(RE2, FullMatch) {
    ASSERT_TRUE(RE2::FullMatch("hello", "h.*o"));
  }
  ->
  {
    "test_name": "RE2.FullMatch",
    "test_code": "TEST(RE2, FullMatch) { ASSERT_TRUE(...); }",
    "upstream_file": "re2/testing/re2_test.cc",
    "upstream_line": 42,
    "framework": "googletest",
    ...
  }
"""
```

```python
# extractors/glib.py
"""
Extract tests from GLib test files (HarfBuzz).

Parses g_test_add_func() registrations to find test function names,
then extracts the corresponding function bodies.

Also handles data-driven shaping tests from test/shaping/:
each .tests file contains expected shaping output for a font+text pair.
"""
```

```python
# extractors/custom_c.py
"""
Extract tests from custom C test files (libxml2, lcms, libpng).

For libxml2: Parse testapi.c (auto-generated, each function tests one API)
             Parse runtest.c (each XML file in test/ is one test case)
For lcms:    Parse testbed/testcms2.c (functions with Check() assertions)
For libpng:  Parse contrib/libtests/pngvalid.c and pngstest.c

Uses tree-sitter C parser to find functions matching test_* or Check* patterns.
"""
```

```python
# extractors/tcl.py
"""
Extract tests from SQLite TCL test files.

Parses .test files for do_test, do_execsql_test, do_catchsql_test invocations.
Each invocation is one test. Extracts the SQL input and expected output.

NOTE: SQLite has 51,445 tests. We extract ALL of them but MUST subsample
for LLM few-shot prompts due to context window limits. Subsampling uses
stratified random selection (FIXED SEED=42) with the following algorithm:
  1. Group tests by test type: do_test, do_execsql_test, do_catchsql_test
  2. Within each type, bin by test file (the .test file it comes from)
  3. Select proportionally from each (type, file) stratum
  4. Default subsample size: 500 tests (configurable via --sqlite-subsample-n)
  5. For coverage measurement, run ALL 51,445 tests (no subsampling)
  6. For few-shot prompt construction, use the 500-test subsample
  7. Tie-break within a stratum: sort by (file_name, line_number), take first N
"""
```

```python
# extractors/perl_tap.py
"""
Extract tests from OpenSSL's two-layer test architecture.

Layer 1: Perl .t files in test/recipes/ (250+ test recipes)
Layer 2: C test programs in test/ (100+ programs using ADD_TEST())

We extract at the C ADD_TEST() granularity for finest coverage resolution.
Each ADD_TEST() registration points to a C function that becomes one test.
"""
```

```python
# extractors/ctest.py
"""
Extract tests from CMake/CTest projects (libjpeg-turbo).

Parses CMakeLists.txt for add_test() commands. Each command defines
a test with a name and a command to run.
"""
```

#### 1.5 run_test_coverage.py

```python
"""
Run each extracted upstream test under LLVM coverage instrumentation.

For each test:
  1. Set LLVM_PROFILE_FILE to a unique path
  2. Run the test binary with only this test enabled
     - Google Test: --gtest_filter=TestSuite.TestName
     - GLib: -p /path/to/test
     - Custom C: depends on target (may need to build per-test binaries)
     - TCL: tclsh specific_test.test
     - Perl TAP: prove -v test/recipes/specific.t
  3. Run llvm-profdata merge to convert raw profile
  4. Run llvm-cov export --format=json to get coverage data
  5. Parse JSON and extract per-file line/branch/function coverage
  6. Write coverage.json to the test's dataset directory
  7. Record provenance: which upstream test produced this coverage

Key llvm-cov commands:
  LLVM_PROFILE_FILE="test_%m.profraw" ./test_binary --gtest_filter=RE2.FullMatch
  llvm-profdata merge -sparse test_*.profraw -o test.profdata
  llvm-cov export ./test_binary -instr-profile=test.profdata \
    --format=json --skip-expansions --skip-functions > coverage.json

IMPORTANT: Uses Clang source-based coverage (same as FuzzBench).
This is collision-free and independent of any fuzzer's instrumentation.

Handles:
  - Tests that crash: record as "crash" with ASan output, still useful data
  - Tests that timeout: 60s default timeout, configurable
  - Tests that require specific env vars or working directory
  - Tests that can't run in isolation: skip and log (with count reported)
"""
```

#### 1.6 compute_gaps.py

```python
"""
Compute coverage gaps across all upstream unit tests for a target.

Input: All per-test coverage.json files (from real upstream tests)
Output: coverage_gaps.json containing:
  - union_coverage: all lines/branches covered by ANY upstream test
  - gap_branches: branches in source that NO upstream test covers
  - gap_functions: functions that NO upstream test calls
  - per_branch_context: ±10 lines of source surrounding each gap branch
    (i.e., 21 lines total: the branch line ±10). If the branch is within
    10 lines of file start/end, extend in the other direction to maintain
    21 lines. This context window is chosen to fit multiple gaps within
    the LLM's token budget while providing enough surrounding code for
    the LLM to understand the condition.
  - condition_description: per-gap, a one-sentence natural-language
    description of what input condition would trigger this branch

Also computes:
  - total_upstream_tests: int (count of all tests with valid coverage)
  - union_coverage_pct: float (overall % of branches covered by any test)
  - per_test_unique_coverage: branches covered by exactly one test
  - coverage_overlap_matrix: pairwise Jaccard similarity between tests

CONDITION DESCRIPTION GENERATION (NEW audit — addresses template variable gap):
  gap.condition_description is produced by a lightweight LLM pass:
    1. For each gap branch, extract the per_branch_context (±10 lines)
    2. Send a batch prompt to GPT-4o (temperature=0, cached by branch hash):
       "Given this code context, describe in one sentence what input
        condition would cause execution to take this branch."
    3. Cache results keyed by sha256(per_branch_context).
    4. If the LLM API is unavailable, fall back to a heuristic:
       extract the if-condition expression and format as
       "Requires <condition_expression> to be true/false."
  This is a one-time cost per target and is cached across runs.

NAMING CONSISTENCY (NEW audit):
  The output JSON uses field names total_upstream_tests and union_coverage_pct
  (matching the input_synthesis.j2 template variables). These replace the
  previous total_coverage_pct field.
"""
```

#### 1.7 build_dataset.py

```python
"""
Assemble the final dataset directory structure.

Orchestrates: fetch -> build -> extract -> coverage -> gaps

VALIDATION CHECKS (all must pass):
  1. Every test object has complete provenance metadata
  2. Every test traces back to a real upstream file that exists in the cloned repo
  3. No test_code field is empty or looks auto-generated by this project
  4. Coverage JSONs match the schema in dataset_schema.py
  5. coverage_gaps.json exists and has non-empty gap lists
  6. Dataset summary statistics are computed and saved

Produces dataset_stats.json:
  - per_target: {test_count, isolable_test_count, skipped_count,
                  total_coverage_pct, gap_branch_count, gap_function_count}
  - provenance_audit: {total_tests, tests_with_provenance, upstream_repos_used}
"""
```

#### 1.10 contamination_probe.py (NEW in v3)

```python
"""
Training Data Contamination Test.

This script probes whether the LLM has memorized the upstream test suites
used in this experiment. This is a CRITICAL threat to validity: if the LLM
has seen RE2's test suite during pretraining, its "coverage prediction" may
be recall, not reasoning.

Protocol (adapted from Golchin & Surdeanu, "Time Travel in LLMs", 2023):

  PROBE 1 — Verbatim Completion:
    For each target, select 10 upstream tests using stratified random
    sampling (FIXED SEED=123): stratify by coverage decile (10 equal-width
    bins of total_coverage_pct), select 1 test per decile. If a decile has
    no tests, select from the nearest occupied decile. Tests are drawn from
    the HELD-OUT evaluation set (not the training/few-shot set).
    Give the LLM the first 3 lines of the test function and ask it to
    complete the rest. Measure:
      - BLEU score between LLM completion and actual upstream test
      - Exact-match rate of assertion lines
      - Character-level edit distance (normalized)
    Threshold: if BLEU > 0.75 for >50% of tests in a target, that target
    has HIGH contamination risk.

  PROBE 2 — Metadata Recall:
    Ask the LLM: "List the test functions in re2/testing/re2_test.cc"
    (no code provided). Measure:
      - Precision/recall of function names vs. actual
    If recall > 0.5, the LLM has memorized the test file structure.

  PROBE 3 — Coverage Recall (the critical one):
    Give the LLM a test function and ask it to predict coverage WITHOUT
    providing the source code. If accuracy is similar to the with-source
    condition, the LLM is recalling memorized associations, not reasoning
    about the code.

Output: contamination_report.json per target per model, containing:
  - verbatim_bleu_scores: List[float]
  - metadata_recall: float
  - no_source_prediction_accuracy: float
  - contamination_risk_level: "LOW" | "MEDIUM" | "HIGH"

MANDATORY: This runs BEFORE Phase 2. If a target shows HIGH contamination
for a model, that (target, model) pair is FLAGGED in all subsequent results.
We do NOT exclude contaminated pairs — we report them transparently and
let the reader assess. But we MUST also show that results hold for
LOW-contamination pairs.

The contamination report is included as a table in the paper.
"""
```

#### test_contamination.py specification (NEW audit)

```python
"""
Verify that the contamination probe ran correctly and results are recorded.

Assertions:
  1. contamination_report.json exists for every (target, model) pair
     in the experiment matrix (8 targets × 3 models = 24 reports)
  2. Each report contains all three probe results:
     - verbatim_bleu_scores: list of exactly 10 floats (one per probe test)
     - metadata_recall: float in [0.0, 1.0]
     - no_source_prediction_accuracy: float in [0.0, 1.0]
     - contamination_risk_level: one of "LOW", "MEDIUM", "HIGH"
  3. contamination_risk_level is computed correctly:
     - HIGH: BLEU > 0.75 for >50% of probe tests
     - MEDIUM: BLEU > 0.75 for 20-50% of probe tests OR metadata_recall > 0.5
     - LOW: otherwise
  4. No (target, model) pair was silently excluded from results
  5. The 10 probe tests per target are drawn from the held-out set
     (verified by cross-referencing with the Phase 2 held-out split)
"""
```

### Phase 1 Verification

After Phase 1 completes, verify:
- [ ] All `<FILL>` placeholders in `pinned_versions.yaml` have been resolved to actual commit SHAs (NEW audit)
- [ ] At least 8 targets have been processed (4 Tier 1, 4 Tier 2)
- [ ] Each Tier 1 target has >= 50 extracted tests with valid coverage
- [ ] Each Tier 2 target has >= 10 extracted tests with valid coverage
- [ ] **Every test has provenance metadata linking to upstream repo:file:line**
- [ ] **No test was written by this project — all test_code originates from upstream; verified by diffing each test_code against the upstream file at upstream_file:upstream_line** (NEW audit — concrete check)
- [ ] Coverage JSON matches the schema in dataset_schema.py
- [ ] coverage_gaps.json exists and has non-empty gap lists for each target
- [ ] coverage_gaps.json contains `total_upstream_tests` and `union_coverage_pct` fields (NEW audit)
- [ ] coverage_gaps.json contains `condition_description` for each gap branch (NEW audit)
- [ ] **Contamination probe ran for all (target, model) pairs** (NEW v3)
- [ ] **Contamination report generated and no target silently excluded** (NEW v3)
- [ ] `python -m pytest dataset/tests/` passes (including test_provenance.py AND test_contamination.py)

---

## Phase 2: LLM Coverage Prediction

### Goal
Evaluate whether LLMs can predict upstream unit test coverage from code context alone.

### Tasks

#### 2.1 Prompt Templates

**CRITICAL: Prompts must clearly state that all tests are real upstream tests, not fabricated.**

**coverage_prediction.j2:**

```jinja2
{{ system_prompt }}

You are analyzing C/C++ code to predict which code regions a unit test exercises.

The following tests are REAL unit tests written by the {{ target_name }} project
maintainers, extracted from the upstream repository at commit {{ upstream_commit }}.

{% for example in few_shot_examples %}
=== Example {{ loop.index }} ===
[UNIT TEST - from {{ example.upstream_file }}:{{ example.upstream_line }}]
{{ example.test_code }}

[SOURCE CODE]
{{ example.source_excerpt }}

[MEASURED COVERAGE]
Functions covered: {{ example.functions_covered | join(', ') }}
Functions NOT covered: {{ example.functions_not_covered | join(', ') }}
Branch coverage summary:
{% for branch in example.branches %}
  {{ branch.location }}: {{ branch.status }}
{% endfor %}
Total: {{ example.coverage_pct }}% line coverage

{% endfor %}

=== YOUR TASK ===
Predict the coverage for this UNSEEN unit test (also from the upstream {{ target_name }} repository).

[UNIT TEST - from {{ target_test.upstream_file }}:{{ target_test.upstream_line }}]
{{ target_test.test_code }}

[SOURCE CODE]
{{ target_test.source_excerpt }}

Respond ONLY with valid JSON matching this schema:
{
  "functions_covered": ["func1", "func2", ...],
  "functions_not_covered": ["func3", ...],
  "branches": [
    {"location": "file.c:LINE", "true_taken": bool, "false_taken": bool}
  ],
  "estimated_line_coverage_pct": float,
  "reasoning": "brief explanation of your prediction logic"
}
```

**input_synthesis.j2:**

```jinja2
{{ system_prompt }}

You are generating test inputs for a C/C++ library to reach UNCOVERED code branches.

TARGET: {{ target_name }}
HARNESS: {{ harness_code }}

The following are examples from REAL unit tests written by the {{ target_name }}
developers (not generated for this experiment):

{% for example in few_shot_examples %}
=== Example: What real test inputs look like ===
Test from {{ example.upstream_file }}:{{ example.upstream_line }}:
{{ example.test_code }}

Input used: {{ example.input_data }}
Functions covered: {{ example.functions_covered | join(', ') }}
{% endfor %}

=== COVERAGE GAPS ===
The upstream {{ target_name }} test suite ({{ total_upstream_tests }} tests by
project maintainers) achieves {{ union_coverage_pct }}% line coverage.
The following branches are NOT covered by ANY upstream test:

{% for gap in coverage_gaps[:max_gaps] %}
Gap {{ loop.index }}: {{ gap.file }}:{{ gap.line }}
Code context:
```{{ source_language }}
{{ gap.code_context }}
```
Condition: {{ gap.condition_description }}

{% endfor %}

Generate {{ num_inputs }} distinct inputs (as {{ input_format }}) that target
these uncovered branches. For EACH input:
1. The input itself
2. Which gap branch(es) you expect it to reach
3. Your reasoning (trace the execution path from harness entry)

Respond in JSON.
```

#### 2.2 build_prompt.py

```python
"""
Assemble a complete prompt from dataset + template + experiment config.

VALIDATION: Before building any prompt, verify that:
  1. All few-shot examples have valid provenance metadata
  2. All test_code comes from upstream (not generated by this project)
  3. Source code excerpts come from the upstream repo (not modified)

Handles:
  - Token counting (tiktoken for OpenAI, approximate for others)
  - Source context size selection (NEW audit — RQ3 ablation):
    Three context sizes, controlled by --context-size parameter:
      function_only: Include only the function bodies called by the test
                     (identified via called_functions from extract_tests.py)
      file:          Include the entire source file(s) containing those functions
      multi_file:    Include the source file(s) plus files they #include
                     (one level of transitive includes, excluding system headers)
    Default: file (used unless running the context-size ablation sweep)
  - Source context truncation (within a context size tier, prioritize
    functions listed in called_functions, then surrounding code)
  - Few-shot example selection (NEW audit — explicit algorithm):
    Strategy: stratified by coverage decile.
      1. Compute total_coverage_pct for each candidate few-shot example
      2. Bin into 5 equal-width coverage buckets:
         [0-20%), [20-40%), [40-60%), [60-80%), [80-100%]
      3. For N-shot prompt, select ceil(N/5) examples from each bucket
         (round-robin across buckets if N is not divisible by 5)
      4. Within each bucket, select uniformly at random (FIXED SEED=42)
      5. If a bucket has fewer candidates than needed, backfill from
         the nearest non-empty bucket
    This ensures the few-shot examples span the full coverage spectrum.
  - Binary input encoding as hex with format annotations
  - Held-out test selection: randomized with FIXED SEED=42 for reproducibility
"""
```

#### 2.3 run_prediction.py

```python
"""
Send prompts to LLM APIs and collect responses.

Supports: OpenAI (GPT-4o), Anthropic (Claude Sonnet), local (Llama 3.1 via vLLM)

Local model serving specification (NEW audit — addresses infra gap):
  - Server: vLLM (preferred over Ollama for batch throughput + OpenAI-compatible API)
  - GPU: minimum 1× A100 80GB for 70B (4-bit quantized via bitsandbytes)
         or 1× A100 40GB for 8B (full precision)
  - Quantization: 4-bit NF4 for 70B (same as fine-tuning QLoRA config);
                  none for 8B
  - Batch size: max_num_seqs=16 for 70B, max_num_seqs=32 for 8B
  - Endpoint: http://localhost:8000/v1 (OpenAI-compatible)
  - Launch command (70B):
      python -m vllm.entrypoints.openai.api_server \
        --model meta-llama/Llama-3.1-70B-Instruct \
        --quantization bitsandbytes --load-format bitsandbytes \
        --max-num-seqs 16 --max-model-len 4096
  - All local-model runs MUST log the same per-call fields as API calls
    (model string, tokens, latency). Cost is computed as GPU-hours × $/hour
    (document the GPU rate used).

MANDATORY LOGGING per API call:
  - model: exact model string (e.g., "gpt-4o-2024-08-06")
  - temperature: float (MUST be 0.0 for prediction, see §LLM Parameters)
  - top_p: float
  - input_tokens: int
  - output_tokens: int
  - cost_usd: float
  - latency_ms: float
  - prompt_hash: sha256 of full prompt (for caching)
  - timestamp: ISO 8601
  - generation_wall_clock_s: float  # [NEW v3] End-to-end time including network

Response caching: keyed by (model, prompt_hash, temperature). Never re-query same prompt.
Rate limiting: exponential backoff with jitter.
"""
```

#### 2.4 evaluate_prediction.py

```python
"""
Compare predicted coverage to ground-truth measured coverage.

Metrics:
  - Function-level: precision, recall, F1
  - Branch-level: precision, recall, F1
  - Coverage estimation: MAE of predicted vs actual %
  - Ranking quality (NEW audit fix): Spearman rank correlation of
    predicted branch difficulty ordering. NOTE: The original NDCG metric
    was uncomputable because the coverage-prediction JSON schema returns
    branches as a set (with boolean taken/not-taken), not a ranked list.
    Fix: the predicted "ordering" is derived by sorting branches by the
    LLM's confidence (branches it predicts as both true+false taken rank
    higher than single-direction). Ground-truth ordering is by actual
    execution count from coverage.json (if available) or binary
    covered/not-covered. Spearman correlation is more appropriate than
    NDCG for this binary-heavy ranking.

Aggregation dimensions:
  - Per-target (mean across held-out tests)
  - Per-model (mean across all targets)
  - Per-few-shot-count (learning curve as N increases)
  - Per-context-size (function_only vs file vs multi_file)  # [NEW audit — RQ3]
  - Per-tier (Tier 1 vs Tier 2 vs Tier 3)
  - Per-contamination-level (LOW vs MEDIUM vs HIGH)  # [NEW v3]

Output: metrics.json + summary CSV tables
"""
```

#### 2.5 prompt_sensitivity.py (NEW in v3)

```python
"""
Prompt Sensitivity Ablation.

Measures how much prediction accuracy changes when the prompt is rephrased.
This addresses the reviewer concern: "is the prompt wording the secret sauce?"

Protocol:
  - Take the primary coverage_prediction.j2 template
  - Create 2 rephrase variants that ask the same question differently:

  Variant A (coverage_prediction_rephrase_A.j2):
    - Removes the structured JSON schema from the prompt
    - Instead asks: "Describe which functions this test will call and
      which branches it will take. Then estimate line coverage %."
    - Post-process free-text response into the same JSON schema using
      a DETERMINISTIC two-step extraction (NEW audit — no secondary LLM):
        Step 1: Regex extraction.
          - Function names: match patterns like "calls <func>", "<func> is called",
            "covers <func>", "exercises <func>" using regex:
            r'(?:calls?|covers?|exercises?|invokes?|enters?)\s+[`"]?(\w+(?:::\w+)*)[`"]?'
          - Branch predictions: match "line \d+" or "file.cc:\d+" patterns
          - Coverage estimate: match "<number>%" pattern
        Step 2: Validation.
          - Filter extracted function names against the known function list
            from the target's source files (reject hallucinated names)
          - Convert to the standard JSON schema
      This deterministic pipeline ensures the ablation measures PROMPT
      sensitivity, not post-processor noise. If regex extraction fails
      to parse the response (<3 function names extracted), mark that
      response as "parse_failure" and exclude from accuracy computation
      (report the parse failure rate as a separate metric).

  Variant B (coverage_prediction_rephrase_B.j2):
    - Keeps JSON output but changes the framing:
      "You are a code coverage measurement tool" instead of
      "You are analyzing C/C++ code to predict test coverage"
    - Removes the "reasoning" field from the JSON schema
    - Reorders the JSON fields (branches before functions)

  Run all 3 variants (original + A + B) on:
    - 1 model (GPT-4o, to control cost)
    - All Tier 1 targets
    - 5-shot configuration only

  Report:
    - Accuracy delta per variant vs. original (with CI)
    - If max delta > 10% absolute on any metric, flag prompt sensitivity
      as a threat to validity in the paper
    - If max delta < 5%, state that results are robust to prompt wording

Output: prompt_sensitivity_report.json
  - per_target: {original_f1, variant_a_f1, variant_b_f1, max_delta}
  - aggregate: {mean_delta, max_delta, sensitivity_flag: bool}
"""
```

### Phase 2 Verification

- [ ] All prompts contain only real upstream test code (verified by provenance check)
- [ ] At least 3 LLMs queried across all targets
- [ ] **Temperature=0.0 for all prediction runs** (NEW v3)
- [ ] Response parsing handles malformed JSON gracefully
- [ ] Metrics computed for all (model, target, few-shot-count) combinations
- [ ] **Context-size ablation completed: all 3 context sizes × all Tier 1 targets × GPT-4o 5-shot** (NEW audit — RQ3)
- [ ] **Metrics aggregated by context_size dimension in metrics.json** (NEW audit)
- [ ] **Metrics broken down by contamination level** (NEW v3)
- [ ] **Prompt sensitivity ablation completed for all Tier 1 targets** (NEW v3)
- [ ] **prompt_sensitivity_report.json exists with sensitivity_flag** (NEW v3)
- [ ] **Variant A parse failure rate documented** (NEW audit)
- [ ] Cost log complete with per-call token counts, USD costs, **and wall-clock seconds** (NEW v3)
- [ ] `python -m pytest prediction/tests/` passes (including test_sensitivity.py)

---

## Phase 3: Gap-Targeted Input Synthesis & Fuzzing Evaluation

### Goal
Generate gap-filling inputs and evaluate them via libFuzzer campaigns following FuzzBench methodology.

### Tasks

#### 3.1 generate_inputs.py

```python
"""
Use input_synthesis.j2 template to generate gap-filling inputs.

For each target:
  1. Load coverage_gaps.json (gaps relative to upstream test coverage)
  2. Prioritize gaps by estimated reachability (NEW audit — explicit algorithm):
     Reachability score = 1.0 / (min_call_depth_from_harness + 1)
     where min_call_depth_from_harness is the shortest path in the static
     call graph from LLVMFuzzerTestOneInput to the function containing the
     gap branch. Computed using tree-sitter call-expression extraction
     (same shallow analysis as extract_tests.py's called_functions).
     If no path is found (unreachable or analysis too shallow), assign
     score = 0.1 (lowest priority, still included).
     Sort gaps by reachability score descending; break ties by file:line.
     Select the top max_gaps (default=20) for the prompt.
  3. Build prompt with gap descriptions + upstream test examples
  4. Query LLM for N=20 inputs per target
     - Temperature=0.7, top_p=0.95, 3 samples per prompt (see §LLM Parameters)
     - Take UNION of all 3 samples (deduplicate identical inputs)
  5. Parse, validate, and save to seeds/<target>/<model>/
  6. Log generation_wall_clock_s for the entire target (NEW v3)

Text targets (RE2, libxml2, SQLite, PROJ): inputs are raw strings
Binary targets (libjpeg-turbo, lcms, libpng): inputs are hex strings -> bytes

MANDATORY per-target timing log:
  {
    "target": "re2",
    "model": "gpt-4o-2024-08-06",
    "num_inputs_generated": 47,
    "num_unique_after_dedup": 38,
    "generation_wall_clock_s": 142.7,   # Total end-to-end for this target
    "total_api_calls": 6,
    "total_cost_usd": 2.34,
    "total_input_tokens": 45200,
    "total_output_tokens": 8900
  }
"""
```

#### 3.1b generate_random_inputs.py (NEW in v3)

```python
"""
Random Syntactically-Valid Input Baseline.

Generates the SAME NUMBER of inputs as the LLM for each target,
but using format-aware random generation instead of LLM reasoning.

This is the critical ablation: if random valid inputs perform as well
as LLM inputs, the LLM is not contributing meaningful reasoning.

Strategy per input format:
  - RE2 (regex + string): random regex from a PCRE grammar sampler
    (e.g., exrex or rstr library) + random alphanumeric string
  - libxml2 (XML): random XML from a simple DTD-free XML grammar
    (random tag names, attributes, nesting depth 1-5)
  - SQLite (SQL): random SQL from a simplified SQL grammar
    (SELECT/INSERT/UPDATE with random table/column names, random WHERE clauses)
  - PROJ (coordinate strings): random coordinate transform strings
    from PROJ's documented format (random EPSG codes, random coordinates)
  - libjpeg-turbo (JPEG): random valid JPEG headers + random pixel data
  - lcms (ICC profiles): random ICC profile headers + random tag data
  - libpng (PNG): random valid PNG with IHDR + random IDAT
  - OpenSSL (DER certs): random ASN.1 DER structures from a basic schema
  - HarfBuzz (fonts): random minimal OTF/TTF with random glyph tables

CRITICAL: The random inputs must be syntactically valid enough to pass
the harness's initial parsing. If the harness immediately rejects >90%
of random inputs, the baseline is meaningless. Validate and report
the parse success rate.

Output: seeds/<target>/random/ with the same count as seeds/<target>/<model>/
Also output: random_generation_stats.json with parse success rates
"""
```

#### 3.2 run_fuzzing.py

```python
"""
Run libFuzzer campaigns following the FuzzBench gold standard.

CAMPAIGN PARAMETERS (MANDATORY — these match FuzzBench exactly):
  - Duration: 82,800 seconds (23 hours)
  - Trials: 20 per configuration per target
  - Random seeds: Recorded per trial for reproducibility
  - Coverage: Clang source-based (same build as Phase 1)
  - Snapshot interval: Every 900 seconds (15 minutes)

CONFIGURATIONS (6 total — was 5 in v2):
  1. empty:          libFuzzer with empty initial corpus
  2. fuzzbench_seeds: libFuzzer with FuzzBench-provided seed corpus
                      (e.g., 2241 DER certs for openssl, 1258 for sqlite)
                      NOTE: Some targets have NO FuzzBench seeds (re2, libxml2, zlib)
  3. unittest_seeds: libFuzzer with inputs extracted from upstream unit tests
                     (NOT LLM-generated — these are literal input values from tests)
  4. llm_seeds:      libFuzzer with LLM-generated gap-filling seeds (from 3.1)
  5. combined:       libFuzzer with unittest_seeds + llm_seeds
  6. random_seeds:   libFuzzer with random syntactically-valid inputs (from 3.1b)  # [NEW v3]

libFuzzer invocation (NEW audit — complete flag set):
  ./target_fuzzer corpus_dir/ seeds_dir/ \
    -max_total_time=82800 \
    -print_final_stats=1 \
    -jobs=1 -workers=1 \
    -seed=<recorded_random_seed> \
    -timeout=<from pinned_versions.yaml: targets.<target>.libfuzzer_extra_flags.timeout> \
    -rss_limit_mb=<from pinned_versions.yaml: targets.<target>.libfuzzer_extra_flags.rss_limit_mb> \
    -max_len=<from pinned_versions.yaml: targets.<target>.libfuzzer_extra_flags.max_len> \
    [-dict=<from pinned_versions.yaml: targets.<target>.dictionary_path, if not null>]

  NOTE: The -timeout, -rss_limit_mb, -max_len, and -dict flags are
  CRITICAL for matching FuzzBench baselines. Dictionary use alone can
  shift coverage by 20%+. All flags are read from pinned_versions.yaml
  so they are consistent across all 8 configs (including Experiment 2).
  If a target has no dictionary (dictionary_path: null), omit -dict.

Coverage measurement (separate from fuzzer's internal tracking):
  POST-HOC replay (not inline during fuzzing). After each 15-minute
  snapshot interval:
    1. Copy the current corpus directory to a timestamped snapshot
    2. After the campaign ends, replay ALL snapshots through the
       Phase 1 COVERAGE BUILD (build variant #1 from build_instrumented.sh,
       compiled with -fprofile-instr-generate -fcoverage-mapping)
    3. For each snapshot:
       a. llvm-profdata merge all .profraw files
       b. llvm-cov export to count unique source-based edges
    4. Record (timestamp, edges_covered, corpus_size) in coverage_over_time.csv
  This is the SAME measurement method FuzzBench uses. Post-hoc replay
  avoids instrumenting the fuzzer build and ensures coverage numbers are
  comparable across all configurations.

Output per trial:
  - coverage_over_time.csv: timestamp, edges_covered, corpus_size
  - final_stats.json: total_edges, total_crashes, total_timeouts
  - crash_inputs/: any crashing inputs found (with ASan reports)

TOTAL COMPUTE (updated for 6 configs):
  6 configs × 20 trials × 8 targets × 23h = 22,080 CPU-hours
  (Comparable to published FuzzBench experiments)
"""
```

#### 3.3 compare_baselines.py

```python
"""
Generate FuzzBench-style statistical analysis and visualizations.

STATISTICAL TESTS (matching FuzzBench methodology):

  1. Per-benchmark pairwise comparisons:
     - Mann-Whitney U test (two-tailed, α = 0.05)
     - Vargha-Delaney Â₁₂ effect size
     - Interpretation: Â₁₂ = 0.50 → no difference
                       Â₁₂ > 0.71 → large effect (FuzzBench convention)

  2. Cross-benchmark ranking:
     - Friedman test (non-parametric repeated-measures ANOVA)
       H₀: all configurations perform equally across benchmarks
     - Post-hoc Nemenyi test for pairwise differences
     - Critical difference diagram

VISUALIZATIONS:
  - Coverage-over-time curves (one line per config, 95% CI shaded)
  - Box/violin plots of final coverage per configuration
  - Heatmap: Â₁₂ matrix (config × config, per benchmark)
  - Critical difference diagram (cross-benchmark ranking)
  - Gap closure bar chart (% of targeted branches reached by LLM inputs)
  - Per-target breakdown tables
  - LLM vs Random comparison table (NEW v3)

NOVEL METRICS (beyond FuzzBench standard):
  - Gap closure rate: % of coverage_gaps.json branches reached
  - Prediction precision: of LLM inputs claiming to target branch X,
    how many actually hit X?
  - Cost efficiency: edges gained per dollar of LLM API cost
  - Time-to-gap-closure: wall-clock time to reach each gap branch
  - LLM vs Random delta: per-target, is LLM statistically better than random? (NEW v3)
  - Seed survival rate (NEW audit — promoted from failure_analysis.py):
    % of LLM seeds still present in the libFuzzer corpus at 1h and 23h.
    Identification method: compute sha256 content hash of each LLM seed
    before campaign start. At each snapshot, hash all corpus entries and
    check membership. A seed "survives" if its exact content hash is
    found OR if any corpus entry's coverage profile is a strict superset
    of the seed's coverage profile (indicating the fuzzer mutated it
    into something better but didn't discard the coverage contribution).
    Report: survival_rate_1h, survival_rate_23h per target per config.

All figures use matplotlib + seaborn. Output to analysis/figures/
All raw data published as CSVs with experiment configuration hashes.
"""
```

#### 3.4 dedup_crashes.py (NEW in v3)

```python
"""
Crash Deduplication Pipeline.

Fuzzing campaigns may find crashing inputs. To report meaningful bug
counts, crashes must be deduplicated.

Protocol (following best practices from Klees et al. CCS 2018):

  Step 1 — Stack Hash Dedup:
    - Run each crash input under ASan to get a stack trace
    - Hash the top N frames (N=3 by default, configurable)
    - Group crashes by stack hash
    - Select one representative crash per group (smallest input)

  Step 2 — Coverage Profile Dedup:
    - For each unique stack hash group, measure coverage profile
    - If two groups have identical coverage profiles, merge them
    - This catches cases where the same bug has different stack traces
      due to ASLR or non-determinism

  Step 3 — Manual Triage (for paper-worthy claims):
    - For each deduplicated crash, produce a one-line classification:
      - heap-buffer-overflow, use-after-free, null-deref, etc.
    - Check if the crash reproduces on the latest upstream version
    - Check CVE databases for known bugs
    - Flag truly new bugs for upstream reporting

Output: dedup_report.json per target:
  {
    "target": "libxml2",
    "total_crashes": 142,
    "unique_stack_hashes": 8,
    "unique_after_coverage_dedup": 5,
    "classifications": [
      {"id": "crash_001", "type": "heap-buffer-overflow", "known_cve": null, ...}
    ]
  }

SCOPE DECISION: This project is primarily about COVERAGE, not bug-finding.
Bug counts are reported as secondary metrics. We do NOT claim bug-finding
as a primary contribution unless we find genuinely new bugs. If we find
only known bugs, we say so honestly.
"""
```

#### 3.5 failure_analysis.py (NEW in v3)

```python
"""
Failure Mode Analysis: When Do LLM Seeds Hurt?

A reviewer will ask: "Are there cases where adding LLM seeds makes
fuzzing WORSE?" This script answers that question.

Analysis 1 — Corpus Pollution Detection:
  For each target, compare:
    - Config 3 (unittest_seeds) final coverage
    - Config 5 (combined = unittest + LLM seeds) final coverage
  If combined < unittest_seeds with statistical significance (Mann-Whitney
  p < 0.05), that target has CORPUS POLLUTION: the LLM seeds are actively
  hurting by adding junk that wastes fuzzer cycles on unproductive mutations.

  Report per target:
    - coverage_delta: combined - unittest (median across 20 trials)
    - is_polluted: bool (significant negative delta)
    - pollution_magnitude: Â₁₂ effect size

Analysis 2 — Wasted Seed Analysis:
  For each LLM-generated seed, measure:
    - Does it parse? (harness doesn't crash immediately)
    - Does it contribute unique coverage? (new edges not in unittest_seeds)
    - Does it survive in the corpus after 1 hour? (libFuzzer didn't discard it)
  Seed identification (NEW audit — disambiguation):
    Seeds are tracked by sha256 content hash computed before the campaign.
    A seed "survives" at time T if its exact hash appears in the corpus
    snapshot at T, OR if any corpus entry at T has a coverage profile
    that is a strict superset of the seed's initial coverage (indicating
    productive mutation). The latter check prevents undercounting when
    libFuzzer mutates a good seed into a better variant.
  Report:
    - parse_rate: % of LLM seeds that don't immediately crash
    - unique_coverage_rate: % that contribute at least 1 new edge
    - survival_rate_1h: % still in corpus after 1 hour
    - survival_rate_23h: % still in corpus at campaign end

Analysis 3 — Time-Segment Comparison:
  Does the LLM help early but hurt late (or vice versa)?
  Compare configs at multiple time horizons:
    - 1 minute, 10 minutes, 1 hour, 6 hours, 23 hours
  Plot delta(LLM_seeds - empty) over time per target.
  Hypothesis: LLM seeds help most in the first hour (faster warm-up)
  but the advantage may vanish or reverse at 23 hours.

Output: failure_analysis.json + failure_mode_plots/
"""
```

### Phase 3 Verification

- [ ] LLM-generated seeds exist for all Tier 1+2 targets
- [ ] **Random-baseline seeds exist for all Tier 1+2 targets** (NEW v3)
- [ ] **Random seed parse success rate > 10% for all targets** (NEW v3; rationale: if the harness immediately rejects >90% of random inputs, the baseline is meaningless per research doc §5.2 — the random generator needs redesign for that target)
- [ ] Validation report shows % of inputs parseable by target
- [ ] Fuzzing campaigns completed: **6** configs × 20 trials × 8 targets = **960** campaigns
- [ ] Each campaign ran for exactly 82,800 seconds (23 hours)
- [ ] Coverage snapshots exist at 15-minute intervals for all campaigns
- [ ] Mann-Whitney U + Vargha-Delaney computed for all pairwise comparisons
- [ ] **LLM vs Random pairwise comparison explicitly computed** (NEW v3)
- [ ] Friedman + Nemenyi computed for cross-benchmark ranking
- [ ] Critical difference diagram renders correctly
- [ ] Coverage-over-time plots have 95% CI bands
- [ ] **Crash dedup pipeline ran; dedup_report.json exists per target** (NEW v3)
- [ ] **failure_analysis.json exists; corpus pollution flagged where present** (NEW v3)
- [ ] **generation_wall_clock_s logged for all targets** (NEW v3)
- [ ] `python -m pytest synthesis/tests/` passes

---

## Phase Transfer: Cross-Target Transfer Evaluation (NEW audit — BLOCKING for RQ4)

### Motivation

The research document §5.2 Phase 3 and §6.1 RQ4 define an entire experiment: "Does this skill transfer across targets?" Without this phase, RQ4 is unanswerable and a reviewer will flag the omission. This phase tests whether the LLM's ability to predict coverage and generate inputs generalizes to targets it has never seen.

### Goal

Evaluate cross-target transfer via (a) leave-one-out cross-validation across Tier 1+2 targets and (b) zero-shot evaluation on Tier 3 held-out targets (libpng, FreeType, zlib).

### Tasks

#### T.1 build_loo_prompt.py

```python
"""
Build leave-one-out (LOO) prompts for cross-target transfer evaluation.

For each held-out target T_held_out in Tier 1+2:
  1. Collect few-shot examples ONLY from OTHER targets' upstream tests
     (i.e., the few-shot pool EXCLUDES all tests from T_held_out)
  2. Select N few-shot examples using the same stratified-by-coverage
     algorithm as build_prompt.py (5 coverage bins, round-robin)
     EXCEPT: stratify across targets too — ensure examples come from
     at least 3 different source targets (if N >= 3)
  3. Build the coverage_prediction.j2 prompt with these cross-target examples
  4. For input synthesis: build the input_synthesis.j2 prompt with
     cross-target examples, using T_held_out's coverage_gaps.json

For Tier 3 targets (libpng, FreeType, zlib):
  1. These targets are NEVER used in any few-shot pool (they are held-out)
  2. Few-shot examples come from Tier 1+2 targets only
  3. Source context comes from the Tier 3 target's upstream code
  4. Coverage gaps come from the Tier 3 target's (limited) test suite

VALIDATION:
  - Assert that T_held_out's tests NEVER appear in the few-shot pool
  - Assert that Tier 3 targets NEVER appear in any training context
  - Log which source targets contributed each few-shot example

Output: loo_prompts/<held_out_target>/<model>/
        tier3_prompts/<tier3_target>/<model>/
"""
```

#### T.2 run_transfer_prediction.py

```python
"""
Run coverage prediction using cross-target examples.

For each held-out target (8 Tier 1+2 LOO + 3 Tier 3):
  1. Load LOO prompts from build_loo_prompt.py
  2. Run prediction with each LLM (GPT-4o, Claude, Llama 70B)
  3. Temperature=0.0 (same as Phase 2)
  4. Compare predictions against Phase 1 ground-truth coverage

Key comparison: LOO prediction accuracy vs within-target prediction
accuracy (from Phase 2). The DELTA measures how much accuracy degrades
when the LLM doesn't see any examples from the target under test.

Output: transfer_prediction_results/<target>/<model>/
"""
```

#### T.3 run_transfer_synthesis.py

```python
"""
Generate gap-filling inputs using cross-target examples.

For each held-out target:
  1. Build input synthesis prompt with cross-target few-shot examples
  2. Generate inputs: temperature=0.7, 3 samples, same as Phase 3
  3. Validate inputs against the target's harness
  4. Compare immediate coverage with within-target LLM seeds (Phase 3)

This measures whether the LLM can generalize input-generation strategies
learned from one target's tests to a completely different target.

Output: transfer_seeds/<held_out_target>/<model>/
"""
```

#### T.4 run_tier3_evaluation.py

```python
"""
Special evaluation for Tier 3 targets (never used in training).

Tier 3 targets (libpng, FreeType, zlib) have too few tests to serve
as few-shot sources but serve as pure held-out evaluation targets.

For each Tier 3 target:
  1. Extract the (few) upstream tests + coverage profiles (Phase 1 data)
  2. Build LOO prompts using only Tier 1+2 examples
  3. Run prediction + synthesis
  4. If the target has enough tests for coverage measurement:
     run a small fuzzing campaign (5 trials × 6h instead of 20 × 23h,
     to save compute — Tier 3 is secondary)

Tier 3 results are reported in a SEPARATE table, not mixed with
Tier 1+2 results. They answer: "Can the LLM generalize to a target
it has literally never seen?"

Output: tier3_results/<target>/
"""
```

#### T.5 evaluate_transfer.py

```python
"""
Compute the cross-target transfer evaluation metrics.

LOO MATRIX (Tier 1+2):
  An 8×8 matrix where entry [i,j] represents the prediction accuracy
  on target j when trained on all targets EXCEPT j. The diagonal is
  the LOO accuracy; off-diagonal shows which source targets help which
  held-out targets.

  Actually: row = held-out target, column = metric.
  Columns: function_F1, branch_F1, coverage_MAE, gap_closure_rate.
  Plus a "within-target" row showing Phase 2 accuracy for comparison.

FORMAT-SIMILARITY STRATIFICATION (from research doc §5.2 expected finding):
  Group target pairs by input format similarity:
    - text↔text: RE2, libxml2, SQLite, PROJ (all text-based)
    - text↔binary: RE2→libjpeg-turbo, libxml2→libpng, etc.
    - binary↔binary: libjpeg-turbo, lcms, libpng, HarfBuzz
  Report transfer accuracy separately for same-format and cross-format
  pairs. Expected: same-format transfer > cross-format transfer.

TIER 3 RESULTS:
  Separate table with Tier 3 target prediction accuracy and (if available)
  gap closure rates. These are the strictest test of generalization.

Output:
  transfer_evaluation.json:
    - loo_matrix: dict[target, dict[metric, float]]
    - within_target_baseline: dict[target, dict[metric, float]]
    - format_stratification: {same_format_mean_f1, cross_format_mean_f1, delta}
    - tier3_results: dict[target, dict[metric, float]]
  analysis/notebooks/09_transfer_evaluation.ipynb
"""
```

### Phase Transfer Verification

- [ ] LOO prompts generated for all 8 Tier 1+2 targets
- [ ] **No held-out target's tests appear in its own LOO prompt** (verified programmatically)
- [ ] **Tier 3 targets never appear in any training/few-shot context** (verified programmatically)
- [ ] Transfer prediction results exist for all (target, model) combinations
- [ ] LOO matrix is complete (8 targets × 4 metrics)
- [ ] Format-similarity stratification computed (text↔text vs text↔binary vs binary↔binary)
- [ ] Tier 3 evaluation completed for at least libpng and zlib
- [ ] Transfer accuracy compared against within-target Phase 2 baseline
- [ ] `python -m pytest transfer/tests/` passes

---

## Phase 4: Fine-Tuning Evaluation

### Goal
Determine if fine-tuning on real upstream test data outperforms few-shot prompting.

### Tasks

#### 4.1 prepare_finetune_data.py

```python
"""
Convert Phase 1 dataset into fine-tuning format.

CRITICAL: Training data contains ONLY upstream tests. Verify provenance
for every example before including it in the training set.

Output format (Alpaca-style JSONL):
{
  "instruction": "Predict coverage for this unit test given the source code.",
  "input": "<test_code from upstream repo>\n---\n<source_code>",
  "output": "<measured coverage JSON>",
  "metadata": {
    "upstream_repo": "...",
    "upstream_commit": "...",
    "upstream_file": "...",
    "upstream_line": ...
  }
}

Split: 80% train, 10% val, 10% test
Stratified by target to ensure each split contains tests from all targets.
Test split MUST match the held-out tests used in Phase 2 for fair comparison.
"""
```

#### 4.1b LoRA Configuration Files (NEW audit — fills placeholder gap)

The LoRA configs referenced at `finetuning/configs/` must contain these hyperparameters (from research doc §5.2):

```yaml
# configs/lora_8b.yaml — Llama 3.1 8B (Config E / Config G with CoT)
model_name: meta-llama/Llama-3.1-8B-Instruct
lora:
  r: 16
  alpha: 32
  dropout: 0.05
  target_modules: ["q_proj", "v_proj"]
training:
  epochs: 3
  learning_rate: 2e-4
  batch_size: 4
  gradient_accumulation_steps: 4
  warmup_ratio: 0.03
  weight_decay: 0.01
  bf16: true
  max_seq_length: 4096
data:
  train_split: 0.8
  val_split: 0.1
  test_split: 0.1
  stratify_by: target  # Ensure each split has tests from all targets
```

```yaml
# configs/lora_70b.yaml — Llama 3.1 70B (Config F)
model_name: meta-llama/Llama-3.1-70B-Instruct
lora:
  r: 16
  alpha: 32
  dropout: 0.05
  target_modules: ["q_proj", "v_proj"]
training:
  epochs: 3
  learning_rate: 2e-4
  batch_size: 1
  gradient_accumulation_steps: 16
  warmup_ratio: 0.03
  weight_decay: 0.01
  bf16: true
  max_seq_length: 4096
quantization:
  load_in_4bit: true   # QLoRA for 70B to fit on consumer GPUs
  bnb_4bit_compute_dtype: bfloat16
  bnb_4bit_quant_type: nf4
data:
  train_split: 0.8
  val_split: 0.1
  test_split: 0.1
  stratify_by: target
```

#### 4.2 add_cot_traces.py

```python
"""
Add chain-of-thought reasoning traces to training examples.

For 20-50 training examples, generate step-by-step explanations of
WHY the upstream test covers what it covers:

  "Step 1: The test calls RE2::FullMatch, which enters re2.cc at line 200"
  "Step 2: The pattern 'h.*o' compiles via compile.cc..."
  "Step 3: No named capture groups, so re2.cc:350 is never reached..."

Process:
  1. Use GPT-4 to draft initial traces from (test, source, coverage) triples
  2. MANUALLY verify and correct each trace (this is human annotation work)
  3. Save augmented training data with 'reasoning' field

The test code is UNMODIFIED — we are only adding annotations about it.
"""
```

#### 4.3 compare_all.py

```python
"""
Final comparison across ALL configurations:

Config A: GPT-4o zero-shot (0-shot)
Config B: GPT-4o 5-shot (real upstream tests)
Config C: GPT-4o 10-shot (real upstream tests)
Config D: Claude Sonnet 5-shot (real upstream tests)
Config E: Llama 3.1 8B fine-tuned on upstream test data (LoRA)
Config F: Llama 3.1 70B fine-tuned on upstream test data (LoRA)
Config G: Llama 3.1 8B fine-tuned with CoT traces (LoRA)
Config H: GPT-4o source-only (Experiment 2, no tests)  # [NEW v3.1]
Config I: Claude Sonnet source-only (Experiment 2, no tests)  # [NEW v3.1]

NOTE on config list vs research doc (NEW audit):
  The research doc §5.2 Phase 4 lists configs A-F only. This plan extends
  to A-I (adding G=CoT, H/I=source-only). The research doc should be
  synced to match, or a version note added. The plan (v3.1) is authoritative.

For each config, report:
  - Coverage prediction accuracy (Phase 2 / Exp 2 metrics)
  - Gap-filling input quality (Phase 3 / Exp 2 metrics)
  - Fuzzing campaign results (Phase 3 FuzzBench-style analysis)
  - Cost per target (API $ or GPU hours)
  - Latency per prompt
  - Generation wall-clock per target (NEW v3)
  - Contamination risk level per (config, target) pair (NEW v3)
  - Experiment label: "test-conditioned" or "source-only" (NEW v3.1)

Output: final_comparison.json + LaTeX table for paper

NEW v3 — The LaTeX table includes a contamination column.
Results are presented BOTH as full aggregate AND as low-contamination-only
subset. If conclusions change when excluding HIGH contamination pairs,
this is discussed explicitly in the paper.

NEW v3.1 — The LaTeX table includes an Experiment column (1 or 2).
A dedicated sub-table compares Exp 1 vs Exp 2 configs head-to-head:
  Config B (GPT-4o 5-shot, test-conditioned) vs Config H (GPT-4o source-only)
  Config D (Sonnet 5-shot, test-conditioned) vs Config I (Sonnet source-only)
This is the paper's key result table.
"""
```

### Phase 4 Verification

- [ ] Training data provenance audit: 100% of examples have upstream provenance
- [ ] No test_code in training data was written by this project
- [ ] LoRA configs match hyperparameters in configs/lora_8b.yaml and configs/lora_70b.yaml (NEW audit)
- [ ] LoRA adapters load and produce valid outputs
- [ ] **Fine-tuned model produces valid JSON predictions on 5 random eval examples (end-to-end test)** (NEW audit)
- [ ] Fine-tuned models evaluated on same held-out set as Phase 2
- [ ] Comparison table includes all 9 configurations (A-I, including Exp 2) (updated v3.1)
- [ ] **Exp 1 vs Exp 2 head-to-head sub-table exists** (NEW v3.1)
- [ ] **Comparison table includes contamination risk column** (NEW v3)
- [ ] **Results reported both with and without HIGH contamination pairs** (NEW v3)
- [ ] `python -m pytest finetuning/tests/` passes (including test_finetuned_output.py)

---

## Experiment 2: Source-Code-Only LLM Synthesis (NEW v3.1)

### Motivation

Experiment 1 (Phases 1–4) gives the LLM a rich context: upstream unit tests with measured coverage profiles, few-shot examples showing input → coverage mappings, and explicit gap descriptions derived from the coverage analysis. This raises a fundamental question: **is the unit test conditioning actually necessary, or can the LLM reason about coverage from raw source code alone?**

If source-only reasoning performs comparably to test-conditioned reasoning, then the entire Phase 1 extraction pipeline is engineering overhead that doesn't contribute to the core result. If test-conditioned reasoning significantly outperforms source-only reasoning, then the unit test conditioning is the key contribution and the extraction pipeline is justified.

This is the single most important ablation in the entire project. Without it, a reviewer can argue: "You just gave GPT-4o the source code. The unit tests are irrelevant — the LLM would have generated similar seeds anyway."

### What the LLM Sees

**Experiment 1 (test-conditioned — current plan):**
```
Input to LLM:
  - Unit test code (from upstream repo)
  - Source code (library implementation)
  - Few-shot examples: (test, source, measured_coverage) triples
  - Explicit gap descriptions: "branch X at line Y is uncovered"
  - Harness code
```

**Experiment 2 (source-only — this section):**
```
Input to LLM:
  - Source code (library implementation)    ← SAME
  - Harness code                           ← SAME
  - NO unit tests
  - NO coverage profiles
  - NO gap descriptions
  - NO few-shot coverage examples
```

The LLM must reason from scratch: read the source code, understand the control flow, identify which branches are likely hard for a fuzzer to reach, and generate inputs targeting those branches — all without any coverage ground truth.

### Tasks

#### E2.1 extract_source_context.py

```python
"""
Extract the source code context for each target, WITHOUT any test information.

For each target, collect:
  1. The library source files (same files used in Phase 1, from the pinned upstream commit)
  2. The FuzzBench fuzzer harness (target.cc / harness.c)
  3. Key header files that define the public API

CRITICAL: This script must NOT include any unit test code, test file paths,
or coverage data in its output. The entire point is to measure what the
LLM can do WITHOUT test conditioning.

Output: source_context/<target>/ containing:
  - source_files/: the library implementation files
  - harness.cc: the fuzzer harness
  - api_headers/: public API headers
  - source_manifest.json: list of included files with line counts

Token budget management:
  - For large targets (SQLite: ~230K lines), include only the files
    most relevant to the harness entry point
  - File prioritization algorithm (NEW audit — explicit call-graph ordering):
    1. Start from the harness entry point (LLVMFuzzerTestOneInput)
    2. Use tree-sitter to extract all call_expression nodes from the harness
    3. For each called function, find the source file containing its definition
       (using tree-sitter function_definition node matching)
    4. Assign each source file a priority = 1.0 / (min_call_depth + 1)
       where min_call_depth is the shortest call path from the harness
       (depth 0 = directly called from harness, depth 1 = called by a
       function called from harness, etc.)
    5. Sort files by priority descending, break ties by file size ascending
       (smaller files first — more files = more branches covered)
    6. Greedily include files until the token budget is exhausted
    7. Token counting: use tiktoken (cl100k_base) for GPT-4o,
       approximate (chars/3.5) for Claude and Llama
  - Token budget per model: ~100K tokens for GPT-4o (leaving 20K for
    prompt template + output), ~160K for Claude Sonnet, ~100K for Llama 70B
  - Log the exact source files included, their priorities, and total token count
  - The "approximately matched" token budget constraint with Experiment 1
    is enforced as follows: compute total_input_tokens for Experiment 1's
    synthesis prompts per target. Experiment 2's prompts must use
    total_input_tokens ±20%. If the source code for a target doesn't
    fill the budget, pad with additional header files and comments.
    If it exceeds, truncate lower-priority files. Log the delta.
"""
```

#### E2.2 Prompt Templates

**source_only_analysis.j2** — Branch difficulty prediction (no tests):

```jinja2
{{ system_prompt_source_only }}

You are analyzing C/C++ source code to identify branches that would be
difficult for a coverage-guided fuzzer (libFuzzer) to reach through
random mutation alone.

TARGET: {{ target_name }}

[FUZZER HARNESS]
{{ harness_code }}

The harness receives raw bytes via LLVMFuzzerTestOneInput and calls
library functions. A coverage-guided fuzzer will mutate these bytes
to explore new code paths.

[LIBRARY SOURCE CODE]
{% for file in source_files %}
=== {{ file.path }} ({{ file.line_count }} lines) ===
{{ file.content }}

{% endfor %}

Analyze this code and identify the 20 branches that would be HARDEST
for a random-mutation fuzzer to reach. For each branch:

1. The file and line number
2. The condition that must be satisfied
3. WHY random mutation is unlikely to satisfy it
   (e.g., requires specific magic bytes, multi-field consistency,
   state built up over multiple calls, rare numeric relationships)
4. Your confidence level (HIGH / MEDIUM / LOW)

Respond in JSON:
{
  "hard_branches": [
    {
      "file": "compile.cc",
      "line": 847,
      "condition": "foldcase == true",
      "difficulty_reason": "Requires (?i) flag in regex syntax, which is a
                            specific byte sequence the fuzzer won't discover
                            through random bit-flipping",
      "confidence": "HIGH"
    },
    ...
  ]
}
```

**source_only_synthesis.j2** — Input generation (no tests, no gaps):

```jinja2
{{ system_prompt_source_only }}

You are generating test inputs for a C/C++ library to maximize code
coverage. You have ONLY the source code and the fuzzer harness — no
existing test suite or coverage data to work from.

TARGET: {{ target_name }}
INPUT FORMAT: {{ input_format }}

[FUZZER HARNESS]
{{ harness_code }}

[LIBRARY SOURCE CODE]
{% for file in source_files %}
=== {{ file.path }} ===
{{ file.content }}

{% endfor %}

Study the source code carefully. Identify code paths that require
specific, structured inputs to reach — paths that a random-mutation
fuzzer would struggle with.

Generate {{ num_inputs }} distinct inputs (as {{ input_format }}) that
collectively exercise as many DIFFERENT code paths as possible.
Prioritize depth over breadth: target the hard-to-reach branches,
not the shallow ones the fuzzer will find on its own.

For EACH input:
1. The input itself (in {{ input_format }} format)
2. Which code paths you expect it to exercise
3. Your reasoning: trace the execution from harness entry through
   the library code, identifying which branches are taken

Respond in JSON.
```

**system_prompt_source_only.txt:**

```
You are a code analysis expert specializing in software testing and
fuzzing. You reason about C/C++ code execution paths by tracing
control flow from an entry point through library code.

You do NOT have access to any existing test suite or coverage data.
Your analysis is based entirely on reading and understanding the
source code. You should think like a skilled security researcher
doing manual code review to find interesting inputs.

Be precise about file names and line numbers. Trace specific execution
paths through the code. When you say an input will reach a branch,
explain the exact sequence of function calls and conditions.
```

#### E2.3 run_source_prediction.py

```python
"""
Run the source-only branch difficulty prediction.

For each target (Tier 1 + Tier 2):
  1. Load the source context (NO unit tests)
  2. Build the source_only_analysis.j2 prompt
  3. Query each LLM (GPT-4o, Claude Sonnet, Llama 70B)
  4. Parse the predicted hard branches
  5. Compare against Phase 1's ACTUAL coverage gaps:
     - Of the branches the LLM predicted as "hard," how many are
       actually uncovered by the upstream test suite? (precision)
     - Of the actually uncovered branches (from Phase 1 ground truth),
       how many did the LLM identify? (recall)
     - F1 score

This tells us: can the LLM identify hard-to-cover branches
just from reading source code, without ever seeing test coverage?

IMPORTANT: This comparison uses Phase 1's coverage_gaps.json as
ground truth, but the LLM NEVER sees that file. The LLM reasons
purely from source code. We measure how well its reasoning aligns
with measured reality.

Parameters: temperature=0.0, same models as Experiment 1.

Logging: same per-call logging as Phase 2 (model, tokens, cost,
wall-clock, prompt_hash).

Output: source_prediction_results/<target>/<model>/prediction.json
        source_prediction_metrics.json (precision, recall, F1)
"""
```

#### E2.4 generate_source_inputs.py

```python
"""
Generate gap-filling inputs using source-code-only reasoning.

For each target (Tier 1 + Tier 2):
  1. Load source context (NO unit tests, NO coverage data)
  2. Build the source_only_synthesis.j2 prompt
  3. Query the LLM: temperature=0.7, top_p=0.95, 3 samples (same as Exp 1)
  4. Parse, validate, deduplicate inputs
  5. Save to seeds/<target>/source_only/<model>/

CRITICAL CONSTRAINT: Generate the SAME NUMBER of inputs as Experiment 1
for each target. This ensures fair comparison — the LLM gets the same
token budget and same number of generation calls, just different context.

Token budget matching (NEW audit — made explicit):
  - Count the total input tokens used in Experiment 1's synthesis prompts
    for each target (from run_prediction.py logs)
  - The source-only prompts MUST use total tokens within ±20% of
    Experiment 1's total for the same target. This is the fairness
    constraint from TV7.
  - If source code fills less than 80% of Exp 1's budget:
    include additional source files (lower priority from the call-graph
    ordering in extract_source_context.py) until the budget is met
  - If source code exceeds 120% of Exp 1's budget:
    truncate lower-priority files until within budget
  - Log both token counts and the delta percentage per target
  - Flag any target with >20% divergence in experiment_comparison.json

Output per target:
  seeds/<target>/source_only/<model>/
  source_generation_stats.json:
  {
    "target": "re2",
    "model": "gpt-4o-2024-08-06",
    "experiment": "source_only",
    "num_inputs_generated": 47,
    "num_unique_after_dedup": 35,
    "generation_wall_clock_s": 156.3,
    "total_cost_usd": 2.87,
    "total_input_tokens": 48100,
    "total_output_tokens": 9200
  }
"""
```

#### E2.5 run_source_fuzzing.py

```python
"""
Run libFuzzer campaigns with source-only LLM seeds.

SAME evaluation methodology as Phase 3 (FuzzBench gold standard):
  - 20 trials per config per target
  - 23-hour campaigns
  - Clang source-based coverage
  - 15-minute snapshots

TWO additional campaign configurations:
  Config 7: source_only_llm_seeds
    libFuzzer with LLM seeds generated from source-code-only reasoning
    (Experiment 2, no unit test conditioning)

  Config 8: source_only_combined
    libFuzzer with source-only LLM seeds + FuzzBench seeds

These run alongside Configs 1-6 from Phase 3. The KEY comparisons are:
  - Config 4 (Exp 1 LLM seeds) vs Config 7 (Exp 2 source-only LLM seeds)
    → Does test conditioning improve seed quality?
  - Config 5 (Exp 1 combined) vs Config 8 (Exp 2 source-only combined)
    → Does the improvement persist when both get FuzzBench seeds?
  - Config 7 vs Config 6 (random valid seeds)
    → Does source-only LLM reasoning beat random generation?

COMPUTE COST:
  2 configs × 20 trials × 8 targets × 23h = 7,360 additional CPU-hours
  Total project compute: 22,080 (Phase 3) + 7,360 (Exp 2) = 29,440 CPU-hours

Output: same format as Phase 3 (coverage_over_time.csv, final_stats.json)
"""
```

#### E2.6 compare_experiments.py

```python
"""
The central comparison: Experiment 1 vs Experiment 2.

This script answers the project's most important question:
  "Does unit test conditioning actually help, or can the LLM
   figure it out from source code alone?"

COMPARISON 1 — Prediction Quality:
  Experiment 1: LLM predicts coverage of held-out tests
                (given test code + source + few-shot coverage examples)
  Experiment 2: LLM predicts hard-to-cover branches
                (given source code only, no tests)

  The tasks aren't identical (Exp 1 predicts per-test coverage, Exp 2
  predicts aggregate difficulty), so we compare them against the same
  ground truth: Phase 1's coverage_gaps.json.

  Metric: How many of the LLM's predicted "important" branches
          actually correspond to real coverage gaps?

COMPARISON 2 — Seed Quality (pre-fuzzing):
  For each target, measure immediate coverage of:
    - Experiment 1 LLM seeds (test-conditioned)
    - Experiment 2 LLM seeds (source-only)
    - Random baseline seeds
  using the same llvm-cov instrumented build.

  Metric: unique edges covered before any fuzzing starts.
  This isolates seed quality from fuzzer exploration.

COMPARISON 3 — Fuzzing Campaign Results (the main comparison):
  Config 4 (Exp 1 LLM) vs Config 7 (Exp 2 source-only LLM):
    - Mann-Whitney U (two-tailed, α=0.05) per target
    - Vargha-Delaney Â₁₂ per target
    - Friedman + Nemenyi across all targets
    - Coverage-over-time curves with 95% CI, overlaid

  Three possible outcomes:
    A) Exp 1 >> Exp 2: Test conditioning is the key contribution.
       The unit test extraction pipeline is justified.
       Paper narrative: "LLMs need labeled examples to reason effectively
       about code coverage."

    B) Exp 1 ≈ Exp 2: Test conditioning adds little value.
       The LLM can reason about coverage from source alone.
       Paper narrative: "LLMs are surprisingly capable at cold-start
       source analysis. The simpler source-only approach may be
       more practical for deployment."
       This is still publishable — possibly more interesting.

    C) Exp 2 >> Exp 1: Source-only is better (unlikely but possible).
       Unit test examples may constrain/bias the LLM's reasoning.
       Paper narrative: "Test conditioning creates an anchoring bias —
       the LLM over-focuses on test-adjacent code regions."

  All three outcomes are valuable and publishable.

COMPARISON 4 — Generalizability argument:
  Source-only reasoning is more general than test-conditioned reasoning:
    - It works for targets with no test suite
    - It works for closed-source libraries (just need the source)
    - No Phase 1 extraction pipeline needed
  If Exp 2 performs reasonably (even if not as well as Exp 1), this
  generalizability argument strengthens the paper.

Output:
  experiment_comparison.json
  experiment_comparison_plots/
"""
```

### Concrete Example: RE2 in Each Experiment

**Experiment 1 prompt (test-conditioned, abbreviated):**
```
Here are 5 real unit tests from RE2 and their measured coverage:

Example 1: TEST(RE2, FullMatch) { ASSERT_TRUE(FullMatch("hello", "h.*o")); }
  Covers: FullMatch, Init, DoMatch. Branch re2.cc:48 → true only. 12.3%.

[4 more examples...]

Gap: compile.cc:847 — if (foldcase) { AddFoldedRange(...) }
     NOT covered by any of 1000+ upstream tests.

Generate inputs targeting this gap.
```

**Experiment 2 prompt (source-only, abbreviated):**
```
Here is the source code for RE2, a regular expression library.

[HARNESS]
extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  re2::RE2 re(std::string(...));
  re2::RE2::FullMatch(...);
}

[SOURCE: re2.cc — 450 lines]
[SOURCE: compile.cc — 920 lines]
[SOURCE: dfa.cc — 1800 lines]

Generate 20 inputs that exercise hard-to-reach branches.
For each, explain your reasoning.
```

The LLM must *discover* that `compile.cc:847` is interesting on its own. It has to read the `if (foldcase)` branch, understand that `(?i)` sets that flag, and generate the right input — without being told this branch is uncovered.

### Experiment 2 Verification

- [ ] Source-only prompts contain NO unit test code (verified programmatically)
- [ ] Source-only prompts contain NO coverage data or gap descriptions
- [ ] Source-only seeds generated for all Tier 1+2 targets, same count as Exp 1
- [ ] Token budget approximately matched between Exp 1 and Exp 2 prompts
- [ ] Source-only prediction compared against Phase 1 ground truth
- [ ] Fuzzing campaigns completed: 2 configs × 20 trials × 8 targets = 320 campaigns
- [ ] Mann-Whitney U computed for all Exp 1 vs Exp 2 pairwise comparisons, applied to final edge count at 23h (FuzzBench convention: edges-at-campaign-end is the primary metric)
- [ ] compare_experiments.py ran; experiment_comparison.json exists
- [ ] Coverage-over-time curves show Exp 1 vs Exp 2 overlaid per target
- [ ] **Outcome A/B/C classification documented per target** with statistical evidence
- [ ] `python -m pytest synthesis/tests/` passes

---

## Threats to Validity (NEW in v3)

This section is written for the paper. Every threat listed here MUST have corresponding evidence from the experiment.

### TV1: Training Data Contamination

**Threat:** All Tier 1 targets (RE2, HarfBuzz, OpenSSL, SQLite) are popular open-source projects whose source code and test suites appear widely on GitHub. LLMs trained on GitHub data may have memorized these test suites. If so, the LLM's "coverage prediction" is not reasoning — it is recall.

**Mitigation:** We run a contamination probe (Phase 1.10) that directly tests whether each LLM can reproduce test code verbatim and predict coverage without source context. Results are reported per (target, model) pair. We present results both in aggregate and restricted to LOW-contamination pairs. If conclusions hold only for HIGH-contamination pairs, the contribution is weakened and we say so.

**Evidence artifact:** `contamination_report.json`, `06_contamination_analysis.ipynb`

### TV2: LLM Non-Determinism

**Threat:** LLM outputs vary across runs even at temperature=0 (due to batching, floating-point non-determinism in GPU kernels, API-side changes). This could affect reproducibility.

**Mitigation:** We fix temperature=0 for all prediction tasks and cache responses keyed by (model, prompt_hash, temperature). For generation tasks (temperature=0.7), we take 3 samples and report union. We record exact model version strings (e.g., `gpt-4o-2024-08-06`) and timestamps. We acknowledge that exact numerical reproduction may not be possible with API-served models but verify that qualitative conclusions are stable across the 3 samples.

**Evidence artifact:** Per-call logs with model string, temperature, timestamp.

### TV3: Benchmark Age and Version Pinning

**Threat:** FuzzBench targets use specific (often old) versions of libraries — e.g., `re2-2014-12-09`, `libxml2-v2.9.2`. The LLM may have been trained on later versions of these projects where bugs were fixed or code was refactored. Coverage predictions on old code may benefit from knowledge of the newer code.

**Mitigation:** We pin upstream commits to match FuzzBench exactly and document the version gap between the pinned commit and the LLM's likely training cutoff. We cannot fully control for this — it is an inherent limitation of evaluating LLMs on public code. We note it explicitly.

**Evidence artifact:** Version gap table in the paper (pinned commit date vs. model training cutoff).

### TV4: Seed Sensitivity

**Threat:** The random seeds used for hold-out test selection, few-shot example selection, and libFuzzer trials may affect results. A lucky seed could produce misleadingly good or bad outcomes.

**Mitigation:** We use fixed seeds (documented in config files) and run 20 trials per configuration. Statistical tests (Mann-Whitney U) account for variance across trials. Hold-out test selection uses stratified random sampling.

**Evidence artifact:** Fixed seed values in config; 20-trial distributions visible in violin plots.

### TV5: Prompt Sensitivity

**Threat:** Results may depend heavily on the specific wording of prompt templates. A different phrasing might yield substantially different accuracy.

**Mitigation:** We run a prompt sensitivity ablation (Phase 2.5) with 2 rephrase variants on all Tier 1 targets. We report the maximum accuracy delta and flag prompt sensitivity if delta > 10%.

**Evidence artifact:** `prompt_sensitivity_report.json`

### TV6: Corpus Pollution

**Threat:** Adding LLM-generated seeds to the fuzzer corpus may hurt performance if the seeds are low-quality. The fuzzer wastes cycles mutating junk inputs instead of exploring new paths.

**Mitigation:** We run a failure mode analysis (Phase 3.5) that explicitly tests for corpus pollution. We report per-target whether the combined configuration underperforms the unit-test-only baseline. We also measure seed survival rates in the corpus over time.

**Evidence artifact:** `failure_analysis.json`, `07_failure_mode_analysis.ipynb`

### TV7: Experiment 2 Comparison Fairness (NEW v3.1)

**Threat:** The Experiment 1 vs Experiment 2 comparison may be unfair if the two experiments give the LLM different amounts of information (measured in tokens). If Experiment 1 prompts contain 80K tokens of context (tests + source + coverage) while Experiment 2 prompts contain only 30K tokens (source only), the comparison favors Experiment 1 simply because it provides more context, not because unit tests are specifically valuable. Conversely, if Experiment 2 fills its context window entirely with source code (because it doesn't need space for tests), it may see more of the codebase than Experiment 1, creating a different unfair advantage.

**Mitigation:** We match token budgets between the two experiments. The total input tokens for Experiment 2's prompts should approximately equal Experiment 1's. In Experiment 2, the space that tests and coverage data occupied in Experiment 1 is filled with additional source code context (deeper call chains, more files). We log and report the exact token counts for both experiments per target. If token budgets differ by more than 20%, we note this as a confound.

**Evidence artifact:** `experiment_comparison.json` (includes `exp1_total_tokens` and `exp2_total_tokens` per target), `08_exp1_vs_exp2_comparison.ipynb`

---

## Environment Setup

### Docker

```dockerfile
# Dockerfile.build
FROM ubuntu:22.04

RUN apt-get update && apt-get install -y \
    clang-15 llvm-15 llvm-15-tools \
    build-essential cmake ninja-build \
    python3 python3-pip python3-venv \
    git wget curl \
    autoconf automake libtool pkg-config \
    tclsh \
    libglib2.0-dev \
    && rm -rf /var/lib/apt/lists/*

# Symlink LLVM tools
RUN ln -s /usr/bin/clang-15 /usr/bin/clang && \
    ln -s /usr/bin/clang++-15 /usr/bin/clang++ && \
    ln -s /usr/bin/llvm-cov-15 /usr/bin/llvm-cov && \
    ln -s /usr/bin/llvm-profdata-15 /usr/bin/llvm-profdata

WORKDIR /workspace
COPY requirements.txt .
RUN pip3 install -r requirements.txt
```

### requirements.txt

```
# Core
pyyaml>=6.0
jinja2>=3.1
pydantic>=2.0
click>=8.0

# LLM APIs
openai>=1.0
anthropic>=0.30
tiktoken>=0.5

# Coverage processing & code parsing
tree-sitter>=0.20
tree-sitter-c>=0.20
tree-sitter-cpp>=0.20

# Random input generation (NEW v3)
exrex>=0.11              # Random regex generation for RE2 baseline
lxml>=4.9                # Random XML generation for libxml2 baseline

# Statistical analysis (FuzzBench methodology)
pandas>=2.0
numpy>=1.24
scipy>=1.11           # Mann-Whitney U, Friedman test
scikit-posthocs>=0.7  # Nemenyi post-hoc test

# Text similarity (NEW v3 — for contamination probe)
nltk>=3.8             # BLEU score for contamination testing
Levenshtein>=0.21     # Edit distance for contamination testing

# Plotting
matplotlib>=3.7
seaborn>=0.12

# Notebooks
jupyter>=1.0

# Testing
pytest>=7.0
pytest-timeout>=2.0

# Fine-tuning (Phase 4 only — install separately on GPU machine)
# torch>=2.1
# transformers>=4.36
# peft>=0.7
# bitsandbytes>=0.41
# datasets>=2.16
# accelerate>=0.25
```

---

## Execution Order

```
Phase 0 — Version Pinning (NEW audit — do this FIRST)
  0.1   Fill in ALL <FILL> placeholders in pinned_versions.yaml
        (upstream commit SHAs, FuzzBench commit SHA, fuzzer-test-suite commit SHA)
  0.2   Verify each commit hash by cloning and checking git log
  0.3   Verify dictionary paths exist in FuzzBench/OSS-Fuzz repos

Phase 1 — Dataset Construction (2-3 weeks)
  1.1   Write target YAML configs (read upstream.commit from pinned_versions.yaml)
  1.2   Implement fetch_target.sh (clone upstream repos at pinned commits)
  1.3   Implement framework-specific extractors (6 extractors)
  1.4   Implement extract_tests.py orchestrator WITH provenance
        + input_data extraction + called_functions extraction
  1.5   Implement build_instrumented.sh
  1.6   Implement run_test_coverage.py (llvm-cov integration)
  1.7   Implement compute_gaps.py (with condition_description generation
        and total_upstream_tests/union_coverage_pct naming)
  1.8   Run build_dataset.py for all targets
  1.9   Run provenance audit: verify 100% of tests trace to upstream
  1.10  [NEW] Run contamination_probe.py for all (target, model) pairs
  1.11  Verify: pytest + manual inspection of dataset/ + contamination_report.json

Phase 2 — LLM Coverage Prediction (1-2 weeks)
  2.1   Write prompt templates (with upstream provenance references)
  2.2   Implement llm_client.py (shared utility with cost + wall-clock logging)
  2.3   Implement build_prompt.py (with provenance validation + context_size parameter)
  2.4   Implement run_prediction.py (temperature=0.0)
  2.5   Run predictions: 3 models × 8 targets × 5 few-shot configs = 120 runs
        (NOTE: 5 few-shot configs: 0, 1, 3, 5, 10 — matching research doc §5.2.
         The "4 few-shot configs" in v3 was incorrect; 0-shot is a distinct config,
         not a Phase 4 Config A alias.)
  2.5b  [NEW] Run context-size ablation: 3 context sizes × 4 Tier 1 targets
        × GPT-4o 5-shot = 12 additional runs (RQ3)
  2.5c  [NEW] Run prompt_sensitivity.py: 3 variants × 4 Tier 1 targets = 12 runs
  2.6   Implement evaluate_prediction.py (with contamination-level + context-size breakdown)
  2.7   Analyze Phase 2 results; decide if Phase 3 is warranted

Phase 3 — Synthesis & Fuzzing (3-4 weeks, dominated by campaign runtime)
  3.1   Implement generate_inputs.py (temperature=0.7, 3 samples)
  3.1b  [NEW] Implement generate_random_inputs.py (format-aware random baseline)
  3.2   Implement validate_inputs.py (+ random seed parse rate check)
  3.3   Generate inputs for all Tier 1+2 targets (LLM + random)
  3.4   Implement run_fuzzing.py (23h campaigns, 20 trials, 6 configs,
        with complete libFuzzer flags from pinned_versions.yaml)
  3.5   Run campaigns: 6 configs × 20 trials × 8 targets = 960 campaigns
        (22,080 CPU-hours — can parallelize across machines)
  3.6   [NEW] Implement dedup_crashes.py (stack-hash + coverage-profile)
  3.7   [NEW] Implement failure_analysis.py (corpus pollution + seed survival)
  3.8   Implement statistical analysis (Mann-Whitney, Vargha-Delaney, Friedman-Nemenyi)
  3.9   Implement compare_baselines.py (plots + critical difference diagrams)
  3.10  Generate all figures

Phase Transfer — Cross-Target Transfer (1-2 weeks, parallel with Phase 3 campaigns)
  T.1   Implement build_loo_prompt.py (LOO few-shot pool construction)
  T.2   Run transfer prediction: 8 LOO targets × 3 models = 24 runs
  T.3   Run transfer synthesis for all LOO targets
  T.4   Run Tier 3 evaluation (libpng, FreeType, zlib)
  T.5   Implement evaluate_transfer.py (LOO matrix + format stratification)
  T.6   Verify: pytest + LOO matrix completeness + Tier 3 results

Phase 4 — Fine-Tuning (1-2 weeks, requires GPU)
  4.1   Prepare fine-tuning data (with provenance audit)
  4.1b  Verify LoRA configs match lora_8b.yaml and lora_70b.yaml
  4.2   Write/generate + manually verify CoT traces
  4.3   Fine-tune models (LoRA)
  4.4   Run fine-tuned models through Phase 2 + 3 evaluation
  4.4b  Run end-to-end test: fine-tuned model → valid JSON predictions
  4.5   Final comparison across all 9 configurations (with contamination + experiment columns)

Experiment 2 — Source-Code-Only Synthesis (1-2 weeks, parallel with Phase 3/4)
  E2.1  Implement extract_source_context.py (source + harness, NO tests,
        with explicit call-graph file prioritization)
  E2.2  Write source-only prompt templates (analysis + synthesis)
  E2.3  Run source-only branch prediction for all Tier 1+2 targets
  E2.4  Compare source-only predictions against Phase 1 ground truth
  E2.5  Generate source-only seeds (same count + token budget ±20% as Exp 1)
  E2.6  Run fuzzing campaigns: 2 configs × 20 trials × 8 targets = 320 campaigns
        (7,360 CPU-hours — can parallelize with Phase 3 campaigns)
  E2.7  Run compare_experiments.py (Exp 1 vs Exp 2, primary metric = edges at 23h)
  E2.8  Classify outcome per target as A (test-conditioned wins),
        B (no significant difference), or C (source-only wins)

Cross-Cutting — Threats to Validity Evidence (ongoing, finalized in writing)
  TV.1  Compile contamination evidence table
  TV.2  Verify model string + temperature logged for every call
  TV.3  Compile version gap table (pinned commit date vs training cutoff)
  TV.4  Verify 20-trial distributions look reasonable (not bimodal/degenerate)
  TV.5  Compile prompt sensitivity results + Variant A parse failure rate
  TV.6  Compile corpus pollution + failure mode evidence
  TV.7  Verify Exp 1 vs Exp 2 token budgets within ±20% per target

Total: 9-14 weeks (adjusted for transfer phase)
Compute: ~29,440 CPU-hours for fuzzing (22,080 Phase 3 + 7,360 Exp 2)
         + ~200 CPU-hours for transfer phase Tier 3 mini-campaigns
         + GPU time for fine-tuning
         + ~$100-300 estimated LLM API costs (logged precisely)
```

### Makefile Dependency Graph (NEW audit)

```makefile
# Dependency order (each target depends on all listed prerequisites):
#
# make pin-versions     → (manual: fill pinned_versions.yaml)
# make dataset          → pin-versions
# make contamination    → dataset
# make predict          → dataset, contamination
# make context-ablation → dataset                    # [NEW audit] RQ3
# make sensitivity      → predict
# make synthesize       → predict
# make random-baseline  → dataset
# make transfer         → dataset, predict           # [NEW audit] RQ4
# make fuzz             → synthesize, random-baseline
# make dedup            → fuzz
# make failure-analysis → fuzz
# make stats            → fuzz, dedup, failure-analysis
# make finetune         → dataset                    # (can run in parallel with Phase 2/3)
# make source-only-predict    → dataset
# make source-only-synthesize → source-only-predict
# make source-only-fuzz       → source-only-synthesize
# make compare-experiments    → fuzz, source-only-fuzz
# make figures          → stats, compare-experiments, transfer
# make audit            → dataset, contamination     # provenance + contamination audit
# make all              → figures, audit              # everything
```

---

## Key Design Decisions for Claude Code

1. **Provenance is sacred.** Every test object MUST trace back to upstream_repo:upstream_commit:upstream_file:upstream_line. No exceptions. Build provenance checks into every stage.

2. **No fabricated tests.** Never generate, modify, or synthesize unit tests. The dataset consists exclusively of upstream developer-written tests.

3. **FuzzBench parameters are non-negotiable.** 20 trials, 23 hours, Clang source-based coverage, Mann-Whitney U, Vargha-Delaney Â₁₂, Friedman-Nemenyi. These are the accepted standards. Deviating invites justified reviewer criticism.

4. **Cache aggressively.** LLM API calls are expensive. Cache responses keyed by (model, prompt_hash, temperature). Never re-query the same prompt.

5. **Fail gracefully.** Some upstream tests won't be isolable. Some will crash. Log errors, count skips, continue. Report the skip count in the paper.

6. **Schema everything.** Use Pydantic models for all data structures. Include provenance fields in every schema.

7. **Log costs AND wall-clock.** Every LLM API call logs model, temperature, tokens, cost_usd, latency_ms, AND generation_wall_clock_s. Every target-level generation run logs total wall-clock time.

8. **Separate compute from analysis.** Campaign scripts write results to disk. Analysis scripts read from disk. Never couple them.

9. **Reproducibility.** Pin all versions: upstream commits, LLM model strings (exact, e.g. `gpt-4o-2024-08-06`), random seeds, Python package versions, Docker base images, temperature and top_p values.

10. **Use the Makefile.** Top-level targets: `make pin-versions`, `make dataset`, `make contamination`, `make predict`, `make context-ablation`, `make sensitivity`, `make synthesize`, `make random-baseline`, `make transfer`, `make fuzz`, `make dedup`, `make failure-analysis`, `make stats`, `make finetune`, `make source-only-predict`, `make source-only-synthesize`, `make source-only-fuzz`, `make compare-experiments`, `make figures`, `make audit` (provenance + contamination audit). Dependencies are spelled out in the Makefile Dependency Graph above.

11. **Contamination is a first-class concern.** (NEW v3) Never silently ignore contamination. Run probes before evaluation. Tag every result with contamination level. Present both full and low-contamination subsets. Let the reader decide.

12. **Report failure modes honestly.** (NEW v3) If LLM seeds hurt, say so. If the random baseline matches the LLM, say so. Negative results about LLM reasoning limits are publishable and valuable.

13. **Experiment 2 is the key ablation.** (NEW v3.1) The test-conditioned vs source-only comparison is the paper's strongest result regardless of outcome. If test conditioning wins, the unit test pipeline is justified. If source-only matches, the approach is more general than we claimed. Either way, it's the slide the audience remembers. Ensure same token budget (±20%), same number of seeds, same models — the ONLY variable is whether the LLM sees test code and coverage data.

14. **Cross-target transfer is a first-class result.** (NEW audit) RQ4 asks whether this skill transfers. The LOO matrix and Tier 3 evaluation are required to answer it. The format-similarity stratification (text↔text vs text↔binary) is a contribution in its own right: it tells practitioners which target types benefit from transfer.

15. **pinned_versions.yaml is the single source of truth for versions.** (NEW audit) All commit SHAs, dictionary paths, harness paths, and libFuzzer flags live in one file. Target YAMLs reference it. Scripts read from it. No commit SHA is ever hardcoded anywhere else.
