# Unit Test–Conditioned LLM-Guided Fuzzing: Complete Research & Design Document (v3)

## 1. Executive Summary

This document describes a novel research project that combines Large Language Models (LLMs) with coverage-guided fuzzing. The core idea: **unit tests encode human knowledge about interesting program behavior, and an LLM can learn from that knowledge to generate inputs that reach code regions that both unit tests and blind fuzzers miss.**

No prior work has treated unit tests as structured supervision signals for LLM-guided input synthesis in fuzzing. This project defines a new task, builds a benchmark dataset, and empirically evaluates whether LLMs can reason about code coverage from unit test context alone.

**Critical design constraint:** This project uses *only* existing, upstream unit tests written by the original project maintainers. No test cases are fabricated, generated, or modified for research purposes. The entire experiment rests on the premise that real-world developer-written tests are the supervision signal — manufacturing tests would undermine the core research question. Evaluation follows the FuzzBench gold standard: 20 trials of 23-hour campaigns with Mann-Whitney U tests, Vargha-Delaney effect sizes, and Friedman-Nemenyi cross-benchmark analysis.

---

## 2. Background for Non-Experts

### 2.1 What Is Fuzzing?

Fuzzing is an automated software testing technique. A fuzzer feeds a program semi-random inputs and monitors whether the program crashes, hangs, or behaves unexpectedly. Modern fuzzers like AFL++ and libFuzzer are "coverage-guided" — they track which parts of the code each input exercises (measured as "coverage") and prefer inputs that reach new, previously-unseen code paths.

**Key terms:**

- **Seed:** An initial input fed to the fuzzer. Better seeds reach deeper code faster.
- **Mutator:** A transformation applied to an input to produce a new one (e.g., flip a bit, insert a byte, splice two inputs together). AFL++ has ~20 built-in mutators.
- **Coverage:** A measure of which code paths an input exercises. Typically measured as "edge coverage" — the set of control-flow transitions (branches) executed.
- **Harness:** A small wrapper function that takes raw bytes from the fuzzer and converts them into a valid call to the target library. FuzzBench provides standardized harnesses.
- **Corpus / Queue:** The set of inputs the fuzzer has discovered that each reach at least one unique code path.

### 2.2 What Is a Unit Test?

A unit test is a small, focused test written by developers that exercises a specific function or behavior of the code. For example:

```c
// Unit test for libxml2's XML parser (from upstream runtest.c)
void test_parse_empty_document() {
    xmlDocPtr doc = xmlParseMemory("<root/>", 7);
    assert(doc != NULL);
    assert(xmlDocGetRootElement(doc)->name == "root");
    xmlFreeDoc(doc);
}
```

This test tells us: (a) what the input looks like (`<root/>`), (b) what function processes it (`xmlParseMemory`), (c) what constitutes correct behavior (non-null return, root element named "root"). **Each unit test is an implicit labeled example of input → code behavior.**

### 2.3 What Is a Large Language Model (LLM)?

An LLM (like GPT-4, Claude, or Llama) is a neural network trained on vast text corpora that can understand and generate natural language and code. Key capabilities relevant to this project:

- **Code comprehension:** LLMs can read source code and explain what it does, identify branches, and reason about execution paths.
- **Few-shot learning:** Given a few examples of a pattern (input → output), LLMs can generalize to new instances without retraining.
- **Structured generation:** LLMs can produce outputs in specific formats (JSON, C code, raw bytes as hex strings).

### 2.4 What Is FuzzBench?

FuzzBench is Google's open-source benchmarking platform for fuzzers. It provides:

- A standardized set of ~25 real-world C/C++ library targets (libxml2, libpng, harfbuzz, freetype2, etc.)
- Pre-built **fuzzing harnesses** (implementing `LLVMFuzzerTestOneInput`) for each target
- Infrastructure to run fuzzing campaigns and compare coverage over time
- A rigorous statistical evaluation framework (Mann-Whitney U, Vargha-Delaney, Friedman-Nemenyi)
- Reproducible experimental conditions (single-core VMs, 23-hour campaigns, 20 trials)

**Important distinction:** FuzzBench provides *fuzzing harnesses only* — it does **not** contain or provide unit tests for any target library. The harnesses come from two sources: Google's OSS-Fuzz project (e.g., `openssl_x509`, `sqlite3_ossfuzz`) and the legacy `fuzzer-test-suite` repository (e.g., `libxml2-v2.9.2`, `freetype2-2017`). Unit tests live in the *upstream project repositories* maintained by the original developers.

Since its 2021 ESEC/FSE debut, FuzzBench has become the de facto standard for fuzzing evaluation, conducting 150+ experiments and being cited by 15+ papers at top venues including USENIX Security, IEEE S&P, and ICSE.

### 2.5 What Is libFuzzer?

libFuzzer is LLVM's in-process, coverage-guided fuzzer. Unlike AFL++ (which runs the target as a separate process), libFuzzer links directly into the target library. This gives it:

- Very low per-execution overhead (no fork/exec)
- Direct access to LLVM's coverage instrumentation (SanitizerCoverage)
- A simple API: you write a `LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)` function, and libFuzzer calls it repeatedly with mutated inputs

libFuzzer is the fuzzer used in FuzzBench benchmarks and is the fuzzer we will use in this project.

---

## 3. The Research Gap

### 3.1 What Existing Work Does

| Paper / Project | What the LLM Does | Unit Tests Used? | Coverage Reasoning? |
|---|---|---|---|
| Google OSS-Fuzz + LLM (2023-2024) | Generates harnesses, fixes build errors | No | No |
| ChatFuzz (2024) | Generates API call sequences for Java libs | No | No |
| TitanFuzz (2023) | Generates driver programs for DL libraries | No | No |
| FuzzGPT (2024) | Generates inputs from format descriptions | No | No |
| WhiteFox (2024) | Generates test programs from compiler source | Partial (uses compiler code) | Partial |

### 3.2 What Nobody Has Done

No prior work has:

1. **Used unit tests as labeled training examples** — each test pairs code context with a known-exercised input
2. **Asked an LLM to predict coverage** from code + test context alone (without executing anything)
3. **Generated gap-filling inputs** — inputs specifically designed to reach branches that existing tests miss
4. **Evaluated whether this skill transfers** across different software targets

### 3.2b Methodological Gap in Prior Work (NEW in v3)

Beyond the technical gap above, none of the prior LLM+fuzzing papers address **training data contamination** — the possibility that the LLM has memorized the target codebase or its test suite during pretraining. ChatFuzz, TitanFuzz, FuzzGPT, and WhiteFox all evaluate on popular open-source projects (TensorFlow, PyTorch, Go standard library) that are massively represented in LLM training corpora. None of them probe whether their LLM is *reasoning* about code or *recalling* memorized patterns. Our work introduces a contamination testing protocol as a first-class methodological contribution.

### 3.3 Our Contribution

We define a new task: **Unit Test–Conditioned Coverage Prediction and Input Synthesis.** Given source code and a set of *real, upstream, developer-written* unit tests with measured coverage profiles, can an LLM:

1. Predict which code regions an unseen test covers? (Understanding task)
2. Identify code regions that no test covers? (Gap analysis task)
3. Generate new inputs targeting those gap regions? (Synthesis task)
4. Do this for a target it has never seen before? (Transfer task)

---

## 4. Source of All Test Data: Upstream Project Test Suites

### 4.1 Why Upstream Tests Only

The scientific validity of this project depends entirely on using **real, existing unit tests written by project maintainers** — not tests we create ourselves. The reasons are:

1. **Research sanctity.** If we wrote our own tests, we would encode our own assumptions about what is "interesting" code behavior. The whole point is to evaluate whether LLMs can extract knowledge from *human-written* tests created independently of this research.

2. **Reproducibility.** Any researcher can clone the same upstream repository at the same commit and extract the exact same tests. Nothing is fabricated or ambiguous.

3. **Industry standard.** These are the tests that real developers use to validate real software. They represent genuine domain expertise encoded in code.

4. **No cherry-picking.** We use *all* extractable tests from each target, not a curated subset. Hold-out splits are randomized with fixed seeds for reproducibility.

### 4.2 Verified Upstream Test Suites

We have verified the existence, location, framework, and approximate scale of every test suite listed below. These are organized into tiers by test suite richness, which determines their suitability for our experiment.

#### Tier 1: Rich Test Suites (Primary Targets — Must Use)

These targets have large, well-structured test suites that provide abundant (test, source, coverage) triples for few-shot learning and hold-out evaluation.

**RE2 (re2-2014-12-09)**
- Location: `re2/testing/` in the upstream Google RE2 repository
- Framework: Google Test (`TEST()` macros)
- Scale: ~15-20 `*_test.cc` files; `re2_test.cc` alone contains hundreds of `TEST()` macros; estimated 1,000+ individual test cases
- Input format: Text (regex patterns + test strings) — ideal for LLM comprehension
- FuzzBench harness: `fuzzer-test-suite` legacy target (`target.cc`)
- FuzzBench seeds: None (empty seed corpus)
- Why it's ideal: Text-based inputs, massive test suite, clean Google Test structure, easy to parse programmatically

**HarfBuzz (harfbuzz-1.3.2)**
- Location: `test/api/` and `test/shaping/` in the upstream HarfBuzz repository
- Framework: GLib test framework (C-based)
- Scale: 2,200+ data-driven shaping tests + ~30 API test files covering buffers, fonts, unicode, subsetting
- Input format: Font files + text strings (mixed binary/text)
- FuzzBench harness: `fuzzer-test-suite` legacy target
- FuzzBench seeds: Varies
- Why it's ideal: Massive test count, structured API tests, diverse coverage patterns

**OpenSSL (openssl_x509)**
- Location: `test/` and `test/recipes/` in the upstream OpenSSL repository
- Framework: Custom Perl TAP framework + C pseudo-xUnit (`testutil.h` with `ADD_TEST()`)
- Scale: 250+ Perl TAP test recipes, 100+ C test programs
- Input format: Certificates, keys, crypto structures (binary)
- FuzzBench harness: OSS-Fuzz derived (`openssl_x509`)
- FuzzBench seeds: 2,241 DER certificate files
- Why it's ideal: Enormous test suite, complex code, rich seed corpus for comparison baselines

**SQLite3 (sqlite3_ossfuzz)**
- Location: `test/` in the upstream SQLite repository
- Framework: TCL test harness + proprietary TH3 (only open-source TCL portion usable)
- Scale: 51,445 distinct test cases in the open-source TCL suite across 1,390 files; test code is 590× larger than the library itself
- Input format: SQL statements (text) — excellent for LLM comprehension
- FuzzBench harness: OSS-Fuzz derived (`sqlite3_ossfuzz`)
- FuzzBench seeds: 1,258 inputs
- Caveat: TCL-based tests require special extraction tooling; SQL input format is highly structured
- Why it's ideal: The most extensively tested open-source project in existence; text-based SQL input; massive scale

#### Tier 2: Moderate Test Suites (Secondary Targets — Use for Breadth)

These targets have meaningful but smaller test suites. They provide fewer few-shot examples but are important for cross-target transfer evaluation.

**libxml2 (libxml2-v2.9.2)**
- Location: `runtest.c`, `testapi.c` (auto-generated by `gentest.py`), and `test/` data directory in the upstream libxml2 repository
- Framework: Custom C programs (not a standard test framework)
- Scale: `testapi.c` exercises ~1,100 public API functions; `runtest.c` processes hundreds of XML test files from `test/` subdirectories
- Input format: XML (text) — ideal for LLM comprehension
- FuzzBench harness: `fuzzer-test-suite` legacy target
- FuzzBench seeds: None (empty seed corpus)
- Extraction complexity: Non-standard — `testapi.c` is auto-generated and monolithic; individual test isolation requires parsing the generated code structure
- Why it's valuable: XML is highly structured text, making it the strongest format for LLM-based input synthesis; direct connection to your prior Magma/libxml2 differential fuzzing work

**libjpeg-turbo (libjpeg-turbo-07-2017)**
- Location: `test/` and CMake test infrastructure in the upstream libjpeg-turbo repository
- Framework: CMake/CTest with MD5 checksum comparison
- Scale: ~295-302 CTest cases comparing compression/decompression outputs
- Input format: JPEG images (binary)
- FuzzBench harness: `fuzzer-test-suite` legacy target
- FuzzBench seeds: Varies
- Caveat: Binary input format limits LLM's ability to reason about input structure directly
- Why it's valuable: Binary format provides contrast with text-based targets; tests whether the approach generalizes beyond text

**Little CMS (lcms-2017-03-21)**
- Location: `testbed/testcms2.c` in the upstream Little CMS repository
- Framework: Custom C with `Check()` assertion macros
- Scale: ~200+ `Check()` calls covering ICC profiles, color transforms, formatters
- Input format: ICC color profiles (binary)
- FuzzBench harness: `fuzzer-test-suite` legacy target
- FuzzBench seeds: Varies
- Why it's valuable: Moderately sized, manageable for initial prototyping

**PROJ (proj4-2017-08-14)**
- Location: `test/unit/` in the upstream PROJ (OSGeo) repository
- Framework: Google Test + CTest
- Scale: ~61 CTest-registered suites with thousands of assertions across files like `test_c_api.cpp`, `test_operation.cpp`
- Input format: Coordinate transformation strings (text)
- FuzzBench harness: OSS-Fuzz derived
- FuzzBench seeds: Varies
- Why it's valuable: Google Test framework (easy to parse), text-based input

#### Tier 3: Limited Test Suites (Use Only for Transfer Evaluation)

These targets have too few tests to serve as few-shot training data, but can be used as held-out targets to test cross-target transfer.

**libpng (libpng-1.2.56)**
- Location: `contrib/libtests/` in the upstream libpng repository
- Framework: Custom C programs (`pngvalid.c`, `pngstest.c`)
- Scale: ~33 test targets via `make check`
- Input format: PNG images (binary)
- Too few tests for few-shot training; useful only as a transfer evaluation target

**FreeType (freetype2-2017)**
- Location: `tests/` in upstream + separate `freetype2-testing` repository at Google
- Framework: Meson, standalone C programs
- Scale: Handful of in-tree regression tests (small)
- Input format: Font files (binary)
- Too few in-tree tests for meaningful use; the separate testing repo focuses on fuzz corpus regression, not unit tests

**zlib (zlib_zlib_uncompress_fuzzer)**
- Location: `test/` in upstream zlib repository
- Framework: Standalone C programs
- Scale: 2-3 programs (`example.c`, `minigzip.c`, `infcover.c`) with roughly a dozen internal checks
- Input format: Compressed data (binary)
- Far too few tests; useful only as a minimal transfer target

**libpcap (libpcap_fuzz_both)**
- Location: `testprogs/` in upstream libpcap repository
- Framework: Standalone C programs
- Scale: ~9 test programs for device enumeration, capture, filtering
- Input format: Packet capture data (binary)
- Limited by hardware-dependent nature; marginal utility

### 4.3 Target Selection Rationale

We use **all Tier 1 targets** as primary training/evaluation targets and **all Tier 2 targets** for breadth. Tier 3 targets are used *only* as held-out targets in Phase 3 (cross-target transfer) — we never train on them because their test suites are too small to provide meaningful few-shot examples.

**Minimum viable experiment:** RE2 + HarfBuzz + libxml2 (3 targets with rich text-based tests)
**Full experiment:** All Tier 1 + Tier 2 targets (7-8 targets)
**Transfer evaluation:** Train on Tier 1+2, evaluate on Tier 3 held-outs

### 4.4 What We Do NOT Do

- **We do NOT write our own unit tests.** Every test in the dataset was written by upstream maintainers.
- **We do NOT modify existing tests.** Tests are extracted as-is from the upstream repository at the pinned FuzzBench version.
- **We do NOT cherry-pick tests.** We extract all isolable tests; hold-out splits are randomized.
- **We do NOT use FuzzBench harnesses as unit tests.** FuzzBench harnesses are fuzzing entry points (`LLVMFuzzerTestOneInput`), not unit tests. They serve a different purpose and encode no correctness assertions.
- **We do NOT use the FuzzBench seed corpora as "test inputs."** Seeds are fuzzer inputs, not unit test inputs. When we use seeds, we use them only as fuzzing baselines (e.g., "libFuzzer with FuzzBench-provided seeds" as a baseline configuration).

---

## 5. Detailed Technical Design

### 5.1 Dataset Construction

#### 5.1.1 Data Extraction Pipeline

For each target, we produce a dataset of **triples**: `(unit_test_code, source_code_under_test, coverage_profile)`.

**Step 1: Clone upstream at pinned commit.** Clone the upstream project repository at the exact commit used by the corresponding FuzzBench benchmark. This ensures the source code matches what the FuzzBench harness was built against.

**Step 2: Extract unit tests.** Parse the project's test suite and isolate individual test functions. The extraction method varies by test framework:

| Framework | Extraction method |
|---|---|
| Google Test (RE2, PROJ) | Parse `TEST()` and `TEST_F()` macros with tree-sitter; each macro is one test |
| GLib test (HarfBuzz) | Parse `g_test_add_func()` registrations; each registered function is one test |
| Custom C (libxml2, lcms, libpng) | Parse named test functions (`test_*`, `Check*`) with tree-sitter |
| CTest (libjpeg-turbo) | Parse `CMakeLists.txt` for `add_test()` commands; each command is one test |
| TCL (SQLite) | Parse `.test` files for `do_test` / `do_execsql_test` invocations; each invocation is one test |
| Perl TAP (OpenSSL) | Parse `test/recipes/*.t` files; each `.t` file is one test suite, individual subtests extracted from C test programs via `ADD_TEST()` |

**Step 3: Identify source code under test.** For each test, determine which source files it exercises:
- Static analysis: follow `#include` chains and function calls from the test
- Dynamic analysis: compile with coverage instrumentation, run the test, record which source files have non-zero coverage

**Step 4: Measure coverage profiles.** Compile the target with LLVM's source-based coverage (`-fprofile-instr-generate -fcoverage-mapping`). Run each unit test individually. Extract per-test coverage using `llvm-profdata` and `llvm-cov`. The output is a JSON report mapping each source line and branch to hit/not-hit.

**Step 5: Compute coverage deltas.** For each source file, compute:
- **Union coverage:** all lines/branches covered by *any* unit test
- **Per-test unique coverage:** lines/branches covered by this test but no other test
- **Coverage gaps:** lines/branches in the source that *no* unit test covers

The final dataset structure (per target):

```
dataset/
  re2/
    metadata.json            # target info, build config, upstream commit
    source/                  # relevant source files (copied from upstream)
      re2.cc
      compile.cc
      ...
    tests/
      test_001/
        test_code.cc         # the unit test function, EXTRACTED from upstream
        source_files.json    # list of source files this test touches
        coverage.json        # per-line, per-branch coverage from this test
        upstream_location.json  # exact file:line in upstream repo (provenance)
      test_002/
        ...
    coverage_gaps.json       # branches no unit test covers
    fuzzer_harness.c         # the FuzzBench harness for reference (NOT a test)
```

#### 5.1.2 Coverage Profile Format

Each `coverage.json` contains:

```json
{
  "test_name": "FullMatch_Success",
  "upstream_file": "re2/testing/re2_test.cc",
  "upstream_line": 42,
  "framework": "googletest",
  "files": {
    "re2.cc": {
      "lines_covered": [45, 46, 47, 50, 51, 102, 103],
      "lines_not_covered": [55, 56, 57, 110, 111],
      "branches": {
        "re2.cc:48": { "true": true, "false": false },
        "re2.cc:105": { "true": true, "false": true }
      },
      "functions_covered": ["RE2::FullMatch", "RE2::Init"],
      "functions_not_covered": ["RE2::PartialMatch", "RE2::Replace"]
    }
  },
  "total_lines_covered": 342,
  "total_lines_in_source": 5210,
  "total_branches_covered": 87,
  "total_branches_in_source": 412
}
```

Note the `upstream_file` and `upstream_line` fields — these provide provenance back to the exact location in the upstream repository, ensuring full traceability and reproducibility.

### 5.2 Experimental Phases

#### Phase 1: Coverage Prediction (Can the LLM Understand?)

**Goal:** Determine whether an LLM can predict which functions and branches a unit test exercises, given only the test code and source code (no execution).

**Setup:**
- For each target, hold out K=5 tests as the evaluation set (randomized with a fixed seed)
- Use the remaining N tests as few-shot examples (in-context)
- Present each few-shot example as: test code + source excerpt + coverage summary
- For the held-out test, present: test code + source excerpt (no coverage)
- Ask the LLM: "Which functions does this test call? Which branches does it take?"

**Prompt template (Phase 1):**

```
You are analyzing C/C++ code to predict test coverage.

Here are examples of unit tests from the RE2 project and the code regions
they exercise. These tests were written by the RE2 maintainers at Google.

=== Example 1 ===
[TEST CODE - extracted from re2/testing/re2_test.cc]
TEST(RE2, FullMatch) {
    ASSERT_TRUE(RE2::FullMatch("hello", "h.*o"));
    ASSERT_FALSE(RE2::FullMatch("hello", "e"));
}

[SOURCE CODE EXCERPT - re2.cc lines 200-280]
(relevant source)

[COVERAGE]
Functions covered: RE2::FullMatch, RE2::Init, RE2::DoMatch
Branches taken: re2.cc:220 (true+false), re2.cc:245 (true only)
Functions NOT covered: RE2::Replace, RE2::GlobalReplace
Total: 12.3% line coverage

=== Example 2 ===
(another real test from the same upstream repo)

=== Your Task ===
Predict the coverage for this UNSEEN unit test (also from re2/testing/).

[TEST CODE]
TEST(RE2, PartialMatch) {
    ASSERT_TRUE(RE2::PartialMatch("hello world", "wor"));
}

[SOURCE CODE EXCERPT - re2.cc lines 200-280]
(same source)

Respond ONLY with valid JSON matching this schema:
{
  "functions_covered": ["func1", "func2", ...],
  "functions_not_covered": ["func3", ...],
  "branches": [
    {"location": "file.cc:LINE", "true_taken": bool, "false_taken": bool}
  ],
  "estimated_line_coverage_pct": float,
  "reasoning": "brief explanation of your prediction logic"
}
```

**Metrics:**
- **Function-level accuracy:** Precision/recall/F1 of predicted covered functions vs. actual
- **Branch-level accuracy:** Precision/recall/F1 of predicted taken branches vs. actual
- **Coverage estimation error:** |predicted coverage % - actual coverage %|

**Experimental matrix:**

| Variable | Values |
|---|---|
| LLM | GPT-4o, Claude Sonnet, Llama 3.1 70B |
| Few-shot count | 0, 1, 3, 5, 10 |
| Source context size | Function-only, File, Multi-file |
| Target | Each Tier 1 + Tier 2 target |
| Temperature | 0.0 (deterministic for prediction reproducibility) |
| Top-p | 1.0 |
| Samples | 1 per prompt (deterministic) |

**Prompt sensitivity ablation (NEW v3):**

In addition to the main experimental matrix, we run a prompt sensitivity ablation to address TV5. Using GPT-4o with 5-shot on all Tier 1 targets, we test 2 rephrase variants of the coverage prediction prompt:
- Variant A: free-text output (no JSON schema), post-processed into structured metrics
- Variant B: different framing ("code coverage measurement tool" vs. "analyzing code"), reordered JSON fields, no reasoning field

We report the maximum accuracy delta across variants. This uses 12 additional LLM runs (3 variants × 4 Tier 1 targets).

#### Phase 2: Gap-Targeted Input Synthesis (Can the LLM Generate?)

**Goal:** Given the coverage gaps identified from upstream unit tests, can the LLM generate inputs that reach uncovered code?

**Setup:**
- Compute `coverage_gaps.json` — branches and functions not covered by any upstream unit test
- Present the LLM with: source code containing the uncovered regions + upstream unit test examples (showing what covered code "looks like" as input) + the specific uncovered branches as targets
- Ask the LLM to generate inputs that would exercise the uncovered branches

**Prompt template (Phase 2):**

```
You are generating test inputs for a C/C++ library to reach UNCOVERED code branches.

TARGET: RE2 (regular expression library)
HARNESS: LLVMFuzzerTestOneInput(data, size) interprets data as a regex
pattern followed by a test string.

Here are examples of what real RE2 unit tests (written by Google engineers)
look like and what code they cover:

=== Example: What good inputs look like ===
This test (from re2/testing/re2_test.cc):
TEST(RE2, FullMatch) { ... }
Uses inputs: pattern="h.*o", text="hello"
Covers functions: RE2::FullMatch, RE2::Init, RE2::DoMatch

=== UNCOVERED BRANCHES (your targets) ===
Branch 1: compile.cc:847
Code context:
```cpp
if (foldcase) {
    // Case-folding logic for Unicode ranges
    AddFoldedRange(...)  // <-- NEVER REACHED by any upstream test
}
```
Condition: Requires a regex with case-insensitive Unicode character classes

Generate 10 distinct inputs that target these uncovered branches.
For EACH input provide:
1. The input (regex pattern + test string)
2. Which uncovered branch(es) you expect it to reach
3. Your reasoning (trace the execution path)

Respond in JSON format.
```

**Evaluation protocol (following FuzzBench gold standard):**

1. Collect all LLM-generated inputs
2. Run each through the target compiled with Clang source-based coverage instrumentation (same instrumentation FuzzBench uses)
3. Measure: how many of the targeted uncovered branches did the LLM-generated inputs actually reach?
4. Run libFuzzer campaigns to compare coverage uplift:

| Configuration | Description |
|---|---|
| Baseline 1: Empty | libFuzzer with empty seed corpus |
| Baseline 2: FuzzBench seeds | libFuzzer with FuzzBench-provided seed corpus (where available) |
| Baseline 3: Unit test seeds | libFuzzer with inputs extracted from upstream unit tests (no LLM) |
| Baseline 4: LLM seeds | libFuzzer with LLM-generated gap-filling seeds |
| Baseline 5: Combined | libFuzzer with unit test seeds + LLM seeds |
| Baseline 6: Random valid seeds | libFuzzer with format-aware random syntactically-valid inputs, same count as LLM seeds (NEW v3) |

**LLM generation parameters for input synthesis (NEW v3):** Temperature=0.7, top_p=0.95, 3 independent samples per prompt (take union, deduplicate identical inputs). This balances diversity with quality. Generation wall-clock time is logged per target.

**Campaign parameters (matching FuzzBench methodology):**
- Duration: **23 hours** (82,800 seconds) per campaign
- Trials: **20 trials** per configuration per target (with different random seeds)
- Hardware: Single-core machines with consistent specs
- Coverage measurement: Clang source-based code coverage (collision-free, fuzzer-independent)
- Corpus snapshots: Every 15 minutes for coverage-over-time curves

**Metrics:**
- **Gap closure rate:** % of targeted uncovered branches reached by LLM inputs
- **Precision:** Of LLM inputs that claimed to target branch X, how many actually hit X?
- **Coverage uplift:** Additional edges found when LLM seeds are added, measured over time
- **LLM vs Random delta:** Per-target statistical comparison (Mann-Whitney U) of LLM seeds vs. random valid seeds — the critical ablation proving the LLM contributes reasoning, not just format-aware randomness (NEW v3)
- **Unique bugs found:** Via AddressSanitizer/UndefinedBehaviorSanitizer, deduplicated using stack-hash (top 3 frames) followed by coverage-profile dedup, with manual triage for paper-worthy claims. Bug-finding is a secondary metric; coverage is primary. (NEW v3)
- **Generation wall-clock:** Total end-to-end time for the LLM to generate all seeds for a target, including API latency. Reported alongside API cost for practical viability assessment. (NEW v3)
- **Corpus pollution flag:** Per-target, does adding LLM seeds to the fuzzer make performance *worse* than unit-test-seeds alone? Detected via Mann-Whitney U between Config 5 (combined) and Config 3 (unittest_seeds). Reported honestly if present. (NEW v3)
- **Seed survival rate:** % of LLM-generated seeds still in the libFuzzer corpus at 1h and 23h. Low survival = the fuzzer discards LLM seeds as unproductive. (NEW v3)

#### Phase 3: Cross-Target Transfer (Does It Generalize?)

**Goal:** Test whether the LLM's ability to predict coverage and generate inputs transfers to targets it has never seen.

**Setup:**
- Leave-one-out cross-validation across Tier 1+2 targets
- For target T_held_out: provide few-shot examples only from other targets' upstream tests
- Evaluate coverage prediction accuracy and input generation quality on T_held_out
- Also evaluate on Tier 3 targets (libpng, FreeType, zlib) which are *never* used for training

**Key question:** Does the LLM learn general "code pattern → input strategy" reasoning, or does it just memorize target-specific patterns from the upstream tests?

**Expected finding:** Transfer should work better for targets with similar input formats (e.g., RE2 and PROJ both use text) and worse across format boundaries (text → binary image). Quantifying this relationship is a contribution.

#### Phase 4: Fine-Tuning Evaluation (Do We Need It?)

**Goal:** Determine whether fine-tuning a smaller model on our dataset of *real upstream tests* outperforms few-shot prompting of a larger model.

**Setup:**

| Config | Model | Method |
|---|---|---|
| A | GPT-4o / Claude | Zero-shot (no examples) |
| B | GPT-4o / Claude | 5-shot in-context (real upstream tests) |
| C | GPT-4o / Claude | 10-shot in-context (real upstream tests) |
| D | Llama 3.1 8B | Fine-tuned on our dataset (LoRA) |
| E | Llama 3.1 70B | Fine-tuned on our dataset (LoRA) |
| F | Llama 3.1 8B | Fine-tuned with chain-of-thought traces |

**Fine-tuning data composition:** The training set consists exclusively of (test, source, coverage) triples extracted from upstream repositories. We do NOT augment with synthetic tests. The chain-of-thought traces in config F are annotations we add to explain *why* the upstream test covers what it covers — the test code itself is unmodified.

**Fine-tuning infrastructure:** HuggingFace PEFT (Parameter-Efficient Fine-Tuning) with LoRA (Low-Rank Adaptation). Config: r=16, alpha=32, dropout=0.05, target_modules=["q_proj","v_proj"]. 3 epochs, lr=2e-4.

#### Experiment 2: Source-Code-Only LLM Synthesis (NEW v3.1)

**Goal:** Determine whether unit test conditioning is actually necessary, or whether the LLM can generate equally effective seeds from raw source code alone.

**Motivation:** This is the project's most important ablation. Without it, a reviewer can argue: "The LLM is just reading the source code. The unit tests are irrelevant — it would have generated similar seeds anyway." Experiment 2 directly tests this claim.

**Setup:** For each target, the LLM receives ONLY the library source code and the fuzzer harness — no unit tests, no coverage profiles, no gap descriptions, no few-shot coverage examples. The LLM must independently identify which branches are hard to reach and generate inputs targeting them.

**Two additional fuzzing configurations:**

| Configuration | Description |
|---|---|
| Config 7: Source-only LLM seeds | libFuzzer with LLM seeds generated from source-code-only reasoning (no test conditioning) |
| Config 8: Source-only combined | libFuzzer with source-only LLM seeds + FuzzBench seeds |

**Key comparisons:**
- Config 4 (Exp 1 LLM, test-conditioned) vs Config 7 (Exp 2 LLM, source-only) — does test conditioning improve seed quality?
- Config 7 (Exp 2 source-only) vs Config 6 (random valid) — does source-only LLM reasoning beat random generation?

**Fairness constraint:** Experiment 2 prompts use approximately the same total token budget as Experiment 1 prompts. The space occupied by unit tests and coverage data in Experiment 1 is filled with additional source code context in Experiment 2. The same number of seeds is generated for each target.

**Three possible outcomes (all publishable):**
- **A) Test-conditioned >> source-only:** The unit test extraction pipeline is justified. LLMs need labeled examples to reason about coverage effectively.
- **B) Test-conditioned ≈ source-only:** The simpler source-only approach is sufficient. This is arguably more interesting — it means LLMs can cold-start on any codebase without needing a test suite.
- **C) Source-only >> test-conditioned (unlikely):** Test examples create an anchoring bias, causing the LLM to over-focus on test-adjacent code regions.

**Compute cost:** 2 configs × 20 trials × 8 targets × 23h = 7,360 additional CPU-hours.

---

## 6. Evaluation Framework (FuzzBench Gold Standard)

### 6.1 Research Questions

| # | Question | Measured By |
|---|---|---|
| RQ1 | Can LLMs predict unit test coverage from code alone? | Function/branch prediction accuracy |
| RQ2 | Can LLMs generate inputs that reach coverage gaps? | Gap closure rate, coverage uplift |
| RQ3 | Does code context quality affect performance? | Accuracy vs. context size ablation |
| RQ4 | Does this skill transfer across targets? | Leave-one-out cross-validation accuracy |
| RQ5 | Does fine-tuning improve over in-context learning? | Accuracy/quality comparison across configs A-F |
| RQ6 | Do LLM-generated seeds improve fuzzing campaigns? | Coverage-over-time curves vs. baselines |
| RQ7 | Do LLM seeds outperform format-aware random generation? | LLM vs. random baseline statistical comparison (NEW v3) |
| RQ8 | Are results confounded by training data contamination? | Contamination probe BLEU scores; accuracy with vs. without source code (NEW v3) |
| RQ9 | Are results sensitive to prompt wording? | Accuracy delta across prompt rephrase variants (NEW v3) |
| RQ10 | Does unit test conditioning outperform source-code-only reasoning? | Exp 1 vs Exp 2 fuzzing campaign comparison: Mann-Whitney U + Â₁₂ per target (NEW v3.1) |

### 6.2 Statistical Methodology

We follow the evaluation methodology established by the FuzzBench paper (Metzman et al., ESEC/FSE 2021), Klees et al. ("Evaluating Fuzz Testing", CCS 2018), Böhme et al. ("On the Reliability of Coverage-Based Fuzzer Benchmarking", ICSE 2022), and Schloegel et al. ("SoK: Prudent Evaluation Practices for Fuzzing", IEEE S&P 2024).

**Per-benchmark pairwise comparisons:**
- **Mann-Whitney U test** (two-tailed, α = 0.05) for statistical significance
- **Vargha-Delaney Â₁₂ effect size** — 0.50 = no difference, 1.0 = row always outperforms column

**Cross-benchmark comparisons:**
- **Friedman test** (non-parametric repeated-measures ANOVA) to test whether any configuration differs significantly across all benchmarks
- **Post-hoc Nemenyi test** to identify which configuration pairs differ significantly
- **Critical difference diagram** — configurations connected by bold lines are not statistically distinguishable

**Campaign parameters:**
- **20 trials** per configuration per benchmark (FuzzBench default; Klees et al. recommend 30, minimum acceptable is 10)
- **23-hour campaigns** (FuzzBench standard; shorter durations can produce misleading rankings)
- **Clang source-based code coverage** (collision-free, independent of any fuzzer's internal instrumentation)
- **Corpus snapshots every 15 minutes** for coverage-over-time curves with 95% confidence intervals
- **Fixed random seeds** recorded for every trial for exact reproducibility

**Total compute estimate:**
- Phase 2 fuzzing: 6 configurations × 20 trials × 8 targets × 23 hours = **22,080 CPU-hours** (updated from 18,400 to account for the random baseline configuration added in v3)
- Experiment 2 fuzzing: 2 configurations × 20 trials × 8 targets × 23 hours = **7,360 CPU-hours** (NEW v3.1)
- **Combined total: 29,440 CPU-hours**
- This is comparable to published FuzzBench experiments (e.g., "Dissecting AFL" used ~4,000 CPU-days)

### 6.3 Reporting Standards

Reports will include:
- Coverage-over-time curves with 95% confidence intervals
- Bar plots of median final coverage per configuration
- Violin plots of coverage distributions across trials
- Critical difference diagrams for cross-benchmark ranking
- Raw data published as CSVs with exact experiment configurations and commit hashes
- All upstream repository commits pinned and recorded

### 6.4 Cost and Time Tracking (updated v3)

Track and report LLM API costs AND wall-clock generation time per target. This is critical for practical viability:
- If generating seeds for one target costs $50 in API calls but saves 100 hours of fuzzing compute, that's a clear win
- If it costs $500 and saves 2 hours, it's not viable
- Cost-per-gap-closed-branch is a novel metric we introduce
- **Generation wall-clock time** (NEW v3): total end-to-end time for the LLM phase per target, including network latency, reported alongside API cost. A reviewer will ask: "how long does the LLM step take before fuzzing begins?"
- **LLM parameter logging** (NEW v3): every API call logs model string (exact version, e.g. `gpt-4o-2024-08-06`), temperature, top_p, token counts, cost, and latency

---

## 7. Why This Matters

### 7.1 Practical Impact

Fuzzing campaigns are expensive. Google's OSS-Fuzz runs continuously on thousands of CPU cores. If LLM-generated seeds can reach deep code paths that take hours or days to find by mutation alone, the cost savings compound across every target.

### 7.2 Scientific Impact

This project establishes whether LLMs can perform **semantic code reasoning** in a setting where we have ground-truth evaluation (actual measured coverage against real upstream tests). Most LLM code benchmarks (HumanEval, SWE-Bench) measure output correctness. We measure something different: can the LLM predict *runtime behavior* from static code analysis? This is a harder, more interesting capability to evaluate.

### 7.3 Connection to Existing Work

This builds directly on Shreyas's prior RL fuzzer research, which demonstrated that intelligent mutator selection can improve fuzzing efficiency but faces throughput bottlenecks from online inference. This project sidesteps that problem entirely — the LLM runs *before* the campaign starts, so fuzzing runs at full native speed.

---

## 8. Key Risks and Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| LLM can't predict coverage at all | Medium | Phase 1 detects this early. Even negative results are publishable as a characterization of LLM code reasoning limits. |
| Generated inputs are syntactically invalid | High for binary formats | Focus on text-based targets (RE2, libxml2, SQLite, PROJ) first. Binary targets are secondary. |
| Upstream test extraction is harder than expected | Medium | We've verified test suite locations and frameworks for all targets. Budget extra time for framework-specific parsers. |
| Some upstream tests can't be run in isolation | Medium | Some tests depend on shared fixtures or ordering. Skip non-isolable tests and document the count. |
| Few-shot context window too small | Low (modern LLMs have 128K+ context) | Prioritize relevant source excerpts. Use code summarization if needed. |
| FuzzBench baselines are hard to beat at 23h | High | Measure at multiple time horizons (1min, 10min, 1h, 23h). LLM seeds most likely help early. |
| Insufficient compute for 20×23h campaigns | Medium | Start with 10 trials at 1h for rapid iteration; scale to full 20×23h for final results. |

---

## 9. Threats to Validity (NEW in v3)

This section is distinct from Section 8 (Key Risks). Section 8 addresses *project execution risks* (what if extraction is hard, what if compute is insufficient). This section addresses *threats to the validity of conclusions* — the concerns a reviewer will raise about whether our results actually demonstrate what we claim.

### TV1: Training Data Contamination (CRITICAL)

**Threat:** All Tier 1 targets (RE2, HarfBuzz, OpenSSL, SQLite) are popular open-source projects whose source code and test suites appear widely on GitHub and in LLM training corpora. If the LLM has memorized these test suites during pretraining, its "coverage prediction" may be recall, not reasoning. Its "gap-filling input generation" may be reproducing inputs it has seen in fuzzing corpora rather than reasoning about uncovered branches.

**Severity:** High. This could undermine the core contribution.

**Mitigation protocol:**
1. **Verbatim completion probe:** For each target, give the LLM the first 3 lines of 10 upstream tests and ask it to complete the rest. Measure BLEU score. If BLEU > 0.75 for >50% of tests, flag HIGH contamination.
2. **Metadata recall probe:** Ask the LLM to list test function names without any code provided. If recall > 0.5, the LLM has memorized the test file structure.
3. **No-source prediction probe:** Ask the LLM to predict coverage of a test WITHOUT providing the source code under test. If accuracy is similar to the with-source condition, the LLM is recalling, not reasoning.
4. **Transparent reporting:** All results are tagged with contamination risk level (LOW/MEDIUM/HIGH). Paper presents both full results and the low-contamination subset. If conclusions hold only for HIGH-contamination pairs, we state this limitation explicitly.

**Note on target selection constraint:** We use FuzzBench targets exclusively for reproducibility and evaluation rigor. We cannot introduce novel targets to avoid contamination — doing so would sacrifice the standardized evaluation methodology that gives this work credibility. Instead, we measure contamination and report it transparently.

### TV2: LLM Non-Determinism

**Threat:** LLM outputs vary across runs even at temperature=0, due to GPU kernel non-determinism, API-side batching, and model version updates. This affects reproducibility.

**Mitigation:** We fix temperature=0.0 for all prediction tasks. For generation tasks, we use temperature=0.7 with 3 independent samples and take the union. We record exact model version strings and timestamps. We cache responses keyed by (model, prompt_hash, temperature) so that re-running analysis uses identical outputs.

### TV3: Benchmark Version Age

**Threat:** FuzzBench targets use specific old versions (e.g., `re2-2014-12-09`, `libxml2-v2.9.2`). The LLM was trained on code from much later versions where bugs were fixed and code was refactored. Coverage predictions on old code may benefit from knowledge of newer code.

**Mitigation:** We document the version gap (pinned commit date vs. estimated LLM training cutoff) for each target. This is an inherent limitation of evaluating LLMs on public code that we cannot fully resolve while maintaining FuzzBench compatibility. We note it explicitly and discuss its implications.

### TV4: Seed and Split Sensitivity

**Threat:** Random seeds for hold-out test selection, few-shot example selection, and libFuzzer trials may affect results. A lucky split could produce misleading outcomes.

**Mitigation:** All random seeds are fixed and documented. We run 20 trials per configuration (FuzzBench standard). Hold-out test selection uses stratified random sampling across coverage levels. Mann-Whitney U tests and Vargha-Delaney effect sizes account for inter-trial variance.

### TV5: Prompt Sensitivity

**Threat:** Prediction accuracy may depend heavily on prompt wording rather than the LLM's actual code reasoning ability.

**Mitigation:** We run a prompt sensitivity ablation with 2 rephrase variants of the primary prompt on all Tier 1 targets. If the maximum accuracy delta across variants exceeds 10% absolute, we flag prompt sensitivity as a limitation. If delta < 5%, we report that results are robust to prompt wording.

### TV6: Corpus Pollution

**Threat:** Adding LLM-generated seeds to the fuzzer corpus may hurt performance. Low-quality seeds waste mutation cycles, and the fuzzer may spend time exploring dead-end paths seeded by the LLM.

**Mitigation:** We explicitly test for corpus pollution by comparing Config 5 (combined) vs. Config 3 (unittest_seeds only). If the combined configuration performs significantly worse, that target has corpus pollution and we report it. We also measure seed survival rates at 1h and 23h to quantify how many LLM seeds the fuzzer retains.

### TV7: Experiment 1 vs 2 Comparison Fairness (NEW v3.1)

**Threat:** The test-conditioned vs source-only comparison may be unfair if token budgets differ significantly. If Experiment 1 prompts contain substantially more information (tests + source + coverage) than Experiment 2 (source only), the comparison favors Experiment 1 due to context volume, not test conditioning specifically. Conversely, if Experiment 2 fills its context entirely with source code, it may see more of the codebase than Experiment 1.

**Mitigation:** We approximately match token budgets between experiments. In Experiment 2, the context space freed by removing tests and coverage data is filled with additional source code (deeper call chains, more files). We log exact token counts per target per experiment and flag any pair with >20% token budget divergence.

---

## 10. Glossary

| Term | Definition |
|---|---|
| AFL++ | A popular open-source coverage-guided fuzzer |
| ASan | AddressSanitizer — a memory error detector |
| Â₁₂ | Vargha-Delaney effect size measure (0.5 = no effect, 1.0 = total dominance) |
| BLEU | Bilingual Evaluation Understudy — a text similarity metric used here for contamination testing |
| Contamination probe | A test measuring whether an LLM has memorized training data, using verbatim completion, metadata recall, and no-source prediction |
| Corpus pollution | When adding seeds to a fuzzer corpus makes performance worse by wasting mutation cycles on unproductive inputs |
| Coverage bitmap | A compact representation of which code edges were executed |
| Critical difference diagram | A visualization showing which fuzzers are statistically distinguishable |
| DQN | Deep Q-Network — a reinforcement learning algorithm |
| Edge coverage | The set of control-flow transitions executed by an input |
| Friedman test | Non-parametric test for comparing multiple treatments across multiple benchmarks |
| FuzzBench | Google's standardized fuzzer benchmarking platform |
| Harness | A wrapper that converts raw bytes into valid API calls (NOT a unit test) |
| In-context learning | Teaching an LLM via examples in the prompt (no weight updates) |
| libFuzzer | LLVM's in-process coverage-guided fuzzer |
| llvm-cov | LLVM's coverage reporting tool (source-based, collision-free) |
| LoRA | Low-Rank Adaptation — a parameter-efficient fine-tuning method |
| Mann-Whitney U | Non-parametric test comparing two independent samples |
| Mutator | A transformation that modifies an input to produce a new one |
| Nemenyi test | Post-hoc test used after Friedman test to identify pairwise differences |
| OSS-Fuzz | Google's continuous fuzzing platform for open-source software |
| PEFT | Parameter-Efficient Fine-Tuning |
| Prompt sensitivity | The degree to which LLM output quality changes when the prompt is rephrased without changing the underlying task |
| SanitizerCoverage | LLVM's instrumentation for tracking code coverage |
| Seed | An initial input provided to the fuzzer |
| Seed survival rate | The percentage of initial seeds still retained in the fuzzer corpus after a given time |
| Stack hash | A crash deduplication method using a hash of the top N stack frames |
| UBSan | UndefinedBehaviorSanitizer — detects undefined behavior |
| Upstream | The original open-source project repository (e.g., google/re2, GNOME/libxml2) |
