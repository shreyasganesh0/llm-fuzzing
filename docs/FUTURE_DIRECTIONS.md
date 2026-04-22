# Future Directions — LLM-Guided Fuzzing Ablation

## How to Read This Document

This document is written for a reader with zero prior context. It first explains the project, the experiment, and the results, then derives each future direction from specific observations in the data with explicit reasoning. Nothing is assumed.

---

## 1. Project Background

### What is UTCF?

UTCF (LLM-Guided Fuzzing) is a research project that uses large language models to generate seed inputs for coverage-guided fuzzing. Instead of starting with random bytes, we ask an LLM to synthesize inputs likely to exercise hard-to-reach code paths in a target library.

The core pipeline:
1. **Seed Synthesis** — LLM generates candidate inputs given a prompt describing the target
2. **Coverage Measurement** — inputs are run through an LLVM-instrumented binary; branch coverage is recorded
3. **Fuzzing Campaigns** — high-coverage seeds are used as starting points for AFL++/libFuzzer

### What problem does this solve?

Coverage-guided fuzzers (AFL++, libFuzzer) start from a corpus of seed inputs and mutate them. Seed quality matters enormously — a good seed that already exercises deep code paths gives the fuzzer a head start. Random bytes rarely get past the first few input validation checks of a well-written parser. LLMs, having been trained on source code, test suites, and documentation, can generate structurally plausible inputs that navigate complex conditional logic.

### What is the ablation experiment?

We ran a **5-variant × 7-model × 2-target** experiment to answer: *how much does the context given to the LLM affect seed quality?*

**5 context variants (progressively more information in the prompt):**
- `v0_none` — LLM gets only a description of the task and output format. No code.
- `v1_src` — LLM gets the harness source code (what function is being fuzzed).
- `v2_src_tests` — LLM gets source + upstream unit tests showing API usage patterns.
- `v3_all` — LLM gets source + tests + specific coverage gap branches (uncovered code).
- `v4_src_gaps` — LLM gets source + coverage gaps but NOT tests.

**7 models tested:**
- `claude-sonnet-4-6` — Anthropic frontier model, paid API
- `claude-haiku-4-5` — Anthropic smaller/faster model, paid API
- `llama-3.1-8b-instruct` — Meta 8B open model via University of Florida LiteLLM proxy (free)
- `llama-3.1-70b-instruct` — Meta 70B open model via UF proxy (free)
- `llama-3.3-70b-instruct` — Meta newer 70B model via UF proxy (free)
- `codestral-22b` — Mistral code-specialized 22B model via UF proxy (free)
- `nemotron-3-super-120b-a12b` — NVIDIA 120B MoE model via UF proxy (free)

**2 targets:**
- `harfbuzz` — OpenType/TrueType font shaping engine (C++). Input: raw binary font bytes fed to `hb_blob_create()`. Hard format — structurally malformed binary data is needed.
- `re2` — Google's regular expression engine (C++). Input: regex strings. Text format — LLMs are comfortable generating these.

**Metrics:**
- **M1 (edges covered)** — total unique LLVM branch edges exercised by all 150 seeds in a cell, unioned. Measures raw coverage breadth. Higher is better.
- **M2 (hard-branch hit rate)** — fraction of pre-selected "hard" branches (branches that random fuzzing *never* hits) that at least one seed triggered. Measures targeted coverage. Higher is better.
- **Random baseline** — 150 randomly generated seeds with no LLM. Harfbuzz: M1=554 edges, M2=0.000. RE2: M1=1160 edges, M2=0.000. Both M2 baselines are zero by construction (we selected only branches random fuzzing can't reach).

---

## 2. Full Results

### Harfbuzz (binary format)

| Variant | Model | Seeds | M1 Edges | M2 All | M2 Shown |
|---|---|---|---|---|---|
| random | — | 150 | 554 | 0.000 | 0.000 |
| v0_none | sonnet-4-6 | 150 | **998** | **0.640** | 0.567 |
| v0_none | haiku-4-5 | 150 | 883 | 0.540 | 0.500 |
| v0_none | nemotron-120b | 70 | 570 | 0.160 | 0.267 |
| v0_none | codestral-22b | 150 | 575 | 0.100 | 0.133 |
| v0_none | llama-3.1-70b | 107 | 563 | 0.080 | 0.133 |
| v0_none | llama-3.3-70b | 150 | 555 | 0.060 | 0.100 |
| v0_none | llama-3.1-8b | 150 | 564 | 0.040 | 0.000 |
| v1_src | sonnet-4-6 | 150 | 816 | 0.440 | 0.533 |
| v1_src | haiku-4-5 | 150 | 721 | 0.140 | 0.200 |
| v1_src | codestral-22b | 150 | 729 | **0.460** | 0.367 |
| v1_src | llama-3.3-70b | 70 | 555 | 0.060 | 0.100 |
| v1_src | llama-3.1-8b | 150 | 551 | 0.000 | 0.000 |
| v1_src | llama-3.1-70b | 54 | 551 | 0.000 | 0.000 |
| v1_src | nemotron-120b | 10 | 533 | 0.060 | 0.100 |
| v2_src_tests | codestral-22b | 150 | 742 | 0.380 | 0.400 |
| v2_src_tests | llama-3.1-70b | 75 | 575 | 0.100 | 0.133 |
| v2_src_tests | llama-3.1-8b | 150 | 560 | 0.020 | 0.000 |
| v2_src_tests | llama-3.3-70b | 50 | 559 | 0.060 | 0.100 |
| v2_src_tests | nemotron-120b | 15 | 532 | 0.060 | 0.100 |
| v3_all | codestral-22b | 150 | 731 | 0.260 | 0.300 |
| v3_all | llama-3.1-70b | 43 | 610 | 0.120 | 0.133 |
| v3_all | nemotron-120b | 10 | 567 | 0.120 | 0.200 |
| v3_all | llama-3.3-70b | 59 | 555 | 0.060 | 0.100 |
| v3_all | llama-3.1-8b | 150 | 563 | 0.040 | 0.000 |
| v4_src_gaps | codestral-22b | 150 | **745** | **0.480** | 0.400 |
| v4_src_gaps | llama-3.3-70b | 50 | 625 | 0.180 | 0.233 |
| v4_src_gaps | llama-3.1-70b | 56 | 578 | 0.120 | 0.133 |
| v4_src_gaps | llama-3.1-8b | 150 | 558 | 0.020 | 0.000 |
| v4_src_gaps | nemotron-120b | 1 | 523 | 0.000 | 0.000 |

*Claude v2–v4 HB cells are missing due to API credit exhaustion during the run.*

### RE2 (text/regex format)

| Variant | Model | Seeds | M1 Edges | M2 All | M2 Shown |
|---|---|---|---|---|---|
| random | — | 150 | 1160 | 0.000 | 0.000 |
| v0_none | sonnet-4-6 | 150 | 1363 | **0.733** | 0.600 |
| v0_none | nemotron-120b | 150 | **1442** | 0.467 | 0.400 |
| v0_none | haiku-4-5 | 150 | 1400 | 0.467 | 0.400 |
| v0_none | llama-3.1-70b | 150 | 1361 | 0.400 | 0.300 |
| v0_none | codestral-22b | 150 | 1432 | 0.333 | 0.200 |
| v0_none | llama-3.1-8b | 150 | 1202 | 0.333 | 0.300 |
| v0_none | llama-3.3-70b | 8 | 1054 | 0.133 | 0.000 |
| v1_src | nemotron-120b | 150 | 1374 | **0.867** | **0.900** |
| v1_src | llama-3.1-70b | 150 | **1445** | 0.467 | 0.400 |
| v1_src | sonnet-4-6 | 150 | 1382 | 0.600 | 0.500 |
| v1_src | haiku-4-5 | 150 | 1358 | 0.533 | 0.400 |
| v1_src | llama-3.1-8b | 150 | 1346 | 0.533 | 0.400 |
| v1_src | codestral-22b | 150 | 1379 | 0.400 | 0.300 |
| v1_src | llama-3.3-70b | 13 | 1097 | 0.133 | 0.000 |
| v2_src_tests | codestral-22b | 150 | **1495** | **0.733** | 0.800 |
| v2_src_tests | nemotron-120b | 150 | 1461 | 0.733 | 0.700 |
| v2_src_tests | llama-3.1-8b | 150 | 1376 | 0.533 | 0.400 |
| v2_src_tests | llama-3.1-70b | 150 | 1366 | 0.467 | 0.400 |
| v2_src_tests | sonnet-4-6 | 150 | 1366 | 0.667 | 0.500 |
| v2_src_tests | haiku-4-5 | 150 | 1300 | 0.533 | 0.500 |
| v2_src_tests | llama-3.3-70b | 12 | 1097 | 0.133 | 0.000 |
| v3_all | haiku-4-5 | 150 | 1129 | **0.800** | **0.900** |
| v3_all | codestral-22b | 150 | 1366 | 0.800 | 0.900 |
| v3_all | sonnet-4-6 | 150 | 1152 | 0.733 | 0.900 |
| v3_all | llama-3.1-70b | 150 | **1417** | 0.667 | 0.700 |
| v3_all | llama-3.1-8b | 150 | 1249 | 0.600 | 0.500 |
| v3_all | llama-3.3-70b | 35 | 1195 | 0.400 | 0.400 |
| v3_all | nemotron-120b | 14 | 632 | 0.400 | 0.600 |
| v4_src_gaps | codestral-22b | 150 | 1321 | **0.800** | **0.900** |
| v4_src_gaps | sonnet-4-6 | 150 | 1223 | 0.733 | 0.900 |
| v4_src_gaps | llama-3.1-70b | 150 | **1439** | 0.600 | 0.600 |
| v4_src_gaps | haiku-4-5 | 88 | 1068 | 0.667 | 0.800 |
| v4_src_gaps | llama-3.1-8b | 150 | 1262 | 0.533 | 0.400 |
| v4_src_gaps | llama-3.3-70b | 25 | 1105 | 0.267 | 0.200 |
| v4_src_gaps | nemotron-120b | 2 | 348 | 0.000 | 0.000 |

---

## 3. What the Data Actually Shows

Before deriving future directions, it is important to state the core empirical findings plainly, because all future directions are derived from these observations.

### Finding A: LLM seeds categorically beat random

Every capable model at every variant achieves M2 > 0 on at least one target. Random fuzzing achieves M2=0.000 on both targets by construction. This is not a marginal improvement — it is a qualitative difference in kind. The LLM understands that fonts have table headers, that regex engines have state machines, and generates inputs that are plausible rather than random. This is the foundational claim of the project and the data supports it cleanly.

### Finding B: Input format is the dominant factor in model ranking

On RE2 (text), the model ranking is roughly: sonnet ≈ nemotron > haiku ≈ codestral ≈ llama-70b > llama-8b >> llama-3.3.
On Harfbuzz (binary), the ranking collapses to: sonnet >> haiku > codestral >> everyone else.

The same models that perform well on text inputs fail on binary inputs — not because they are dumb, but because understanding binary struct layout (sfnt headers, table tags, byte offsets) requires knowledge that correlates strongly with model size and training data quality. A 120B model that knows regex syntax well does not necessarily know TrueType table parsing.

This is a crucial finding: **the difficulty of the input format, not just the target's code complexity, determines which models are viable.**

### Finding C: Context scales well on text targets, erratically on binary

On RE2: haiku goes from M2=0.467 (v0) → 0.800 (v3). Codestral goes from 0.333 (v0) → 0.800 (v3/v4). The gains are consistent and large. The gap-targeting prompts (v3, v4) that describe specific uncovered branches allow models to deliberately aim at hard code paths.

On Harfbuzz: sonnet *drops* from M2=0.640 (v0) → 0.440 (v1) when source code is added. Codestral improves (0.100 → 0.480) but the improvement trajectory is not monotone. Adding more context to a binary prompt increases the cognitive load of generating valid binary output, which seems to hurt capable models that were already doing well with minimal context.

### Finding D: Source code alone (v1) is often sufficient or optimal

On RE2, nemotron achieves its best result at v1_src (M2=0.867), not at v3 or v4. llama-3.1-70b achieves its highest M1 at v1_src (1445 edges). The additional context in v2/v3 does not consistently help and sometimes hurts (more tokens = higher chance of truncation, more complex reasoning required in output).

### Finding E: Format compliance separates usable from unusable models

llama-3.3-70b is a more capable model by standard benchmarks than llama-3.1-8b, yet achieves almost no usable seeds (8–35 per cell due to ~95% JSON parse failure rate). The 8b model consistently fills 150 seeds per cell. The bottleneck is not intelligence but instruction-following for structured output. This is a qualitatively different failure mode from low semantic quality — the model is failing before its output can even be evaluated.

---

## 4. Future Directions (with full reasoning)

---

### Direction 1: Fuzzing Campaigns — Close the Coverage-to-Bug Gap

**What it is:**
Run actual AFL++/libFuzzer campaigns using the LLM seeds as initial corpus. Measure bugs found (unique crashes, unique sanitizer violations) across campaign time.

**Why it matters:**
All current results (M1, M2) measure *coverage potential* — how many branches the seeds touch. Coverage is a proxy for bug-finding ability, but the actual goal is finding bugs. Coverage does not guarantee bugs: you can cover a branch without triggering a memory corruption at that branch. Conversely, a single well-crafted seed that covers a shallow but vulnerable path might find more bugs than 150 seeds with high aggregate coverage.

The central empirical claim of this paper is: *LLM-generated seeds find more bugs than random seeds*. The current data supports the necessary condition (higher coverage) but not the sufficient condition (more bugs). Without campaign data, we cannot make this claim.

**Why now:**
Seeds exist for all 7 models × 2 targets × (most) variants. The infrastructure for campaigns (AFL++ integration, scripts) is already partially built (`scripts/run_campaigns.sh`, `synthesis/scripts/run_afl_fuzzing.py`). This is the highest-priority experiment because it directly validates or invalidates the core thesis.

**What to expect:**
Based on prior work in coverage-guided fuzzing, higher initial coverage typically does translate to earlier crash discovery, especially in the first few hours of a campaign. We expect Claude seeds to outperform random seeds on both targets, with the gap being larger on Harfbuzz (where the coverage lift is larger). The RE2 results (M2 up to 0.867) suggest many hard regex state machine paths are being covered — these are precisely the paths where RE2 has historically had bugs (catastrophic backtracking, etc.).

**Confounders to control:**
- Campaign duration: use FuzzBench standard (23h, 20 trials) to match the field.
- Seed count normalization: all cells should start with exactly 150 seeds.
- Same binary: use AFL++-instrumented builds, not the LLVM coverage binary.

---

### Direction 2: Complete Claude v2–v4 on Harfbuzz

**What it is:**
Run synthesis for claude-sonnet-4-6 and claude-haiku-4-5 on harfbuzz variants v2_src_tests, v3_all, and v4_src_gaps. These 6 cells are currently empty (0 seeds) due to API credit exhaustion during the experiment.

**Why it matters:**
The Harfbuzz results table has a structural gap: Claude is only evaluated at v0/v1, while free models are evaluated at v2/v3/v4. This makes a fair cross-variant comparison impossible for Claude on the binary target. We know Claude is the strongest model on Harfbuzz (v0: M2=0.640, far above everyone else). The question of whether Claude *improves further* with gap-targeting context (v3/v4) — or *degrades* as suggested by the v0→v1 drop — is unanswered.

This is not just a completeness concern. The pattern on RE2 shows that gap-targeting raises M2 by 0.10–0.33 for most capable models. If Claude on Harfbuzz with v4_src_gaps reaches M2=0.70+, it would be the strongest result in the entire experiment. If it degrades (continues the v0→v1 trend), that is also a scientifically important finding about binary format prompting.

**Cost estimate:**
Derived from `analysis/scripts/estimate_cost.py`, which multiplies `core.llm_client.PRICING_USD_PER_MTOK` by historical per-call token means from `results/cost_audit/summary.json`. Six empty cells (3 variants × 2 Claude models). Assuming the attempt cap will dominate (~100 calls/cell on binary — the binary parse rate is ~10–30%):

```
# Sonnet on 3 variants × ~100 calls/cell
$ python -m analysis.scripts.estimate_cost --model claude-sonnet-4-6 --n-calls 300
# Per-call: $0.0560  →  Total: $16.81

# Haiku on 3 variants × ~100 calls/cell
$ python -m analysis.scripts.estimate_cost --model claude-haiku-4-5-20251001 --n-calls 300
# Per-call: $0.0135  →  Total: $4.04
```

**Realistic envelope: $20 ≤ total ≤ $30** for all 6 missing cells, depending on how many attempts each cell needs to reach 150 seeds. For context, the 4 completed Claude HB cells (v0 + v1) cost $11.04 on the cache audit, giving ~$2.76/cell average — but v3/v4 cells use longer gap-context prompts, so the per-call input tokens will run higher. The pre-experiment estimator is the source of truth; do not hand-estimate.

**Implementation:**
Set `FREE_ONLY = False` in `scripts/run_ablation_harfbuzz.py`, set `SONNET_ONLY_VARIANTS = {"v2_src_tests", "v3_all", "v4_src_gaps"}` to run both Claude models on the missing variants. Run with `--skip-existing --attempt-offset 60000`. Re-run `python -m analysis.scripts.cost_audit` after completion to confirm the envelope.

---

### Direction 3: Fix llama-3.3-70b JSON Compliance

**What it is:**
Diagnose and fix the ~95% JSON parse failure rate for llama-3.3-70b. The model produces malformed output that cannot be parsed into seed inputs, leaving 8–35 seeds per cell instead of 150.

**Why it matters:**
llama-3.3-70b is supposedly a stronger model than llama-3.1-70b by standard benchmarks (MMLU, HumanEval, etc.). Yet it produced almost no usable output. If this is fixable, it becomes one of the strongest free models in the experiment. If it is not fixable, that is itself an important finding: benchmark performance on standard tasks does not predict instruction-following on specialized structured output tasks.

**Root cause hypothesis:**
The model produces JSON that is syntactically malformed — likely unterminated strings, trailing commas, or markdown code fences wrapping the JSON despite the prompt explicitly forbidding them. This is a prompt-compliance failure, not a semantic failure. The model understands the task but ignores the format constraints.

**Approaches to try (in order of cost):**
1. **Few-shot format examples in the system prompt** — show 2 examples of correctly formatted responses before the task. This is the cheapest fix and often resolves instruction-following failures.
2. **Grammar-constrained decoding** — if the UF LiteLLM endpoint supports `response_format: {"type": "json_object"}`, enabling it forces schema-compliant output. Try passing this parameter.
3. **Post-processing repair** — add a JSON repair step using `json-repair` or a regex extraction pass that extracts the `inputs` array even from partially malformed responses. This is a fallback if the model fundamentally cannot output clean JSON.
4. **Reduce `num_inputs` to 1** — requesting 1 input instead of 3 per call reduces the output complexity and may prevent truncation-induced malformation.

**Why this is a priority:**
If llama-3.3 achieves even half the performance of llama-3.1-70b on RE2 (M2=0.467–0.667), it becomes a meaningful data point for the cost-effectiveness analysis. A newer model underperforming an older one due to a fixable format issue would also be a cautionary finding for practitioners.

---

### Direction 4: Binary Format Prompting for Larger Free Models

**What it is:**
Design a revised binary synthesis prompt that makes the structure of TrueType/OpenType fonts explicit enough for mid-size models (nemotron-120b, codestral-22b) to generate valid partial blobs. Test whether better prompting closes the gap between Claude and free models on Harfbuzz.

**Why it matters:**
On Harfbuzz, nemotron-120b at v0_none produces M2=0.160 with only 70 seeds. This is already better than llama-8b at 150 seeds (M2=0.040) — suggesting some latent binary understanding. But it collapses at v1_src (10 seeds, M2=0.060) because the larger prompt exceeds the UF endpoint's 2048-character response limit, forcing 1 input/call and grinding to the attempt cap.

The current binary prompt asks models to reason about sfnt headers, table tags, and byte offsets from scratch. A prompt that *shows* the binary layout explicitly — e.g., hex byte sequences for a minimal valid sfnt header, with field annotations — would reduce the cognitive load of generating compliant binary output.

**Specific changes to test:**
- Add 2–3 concrete hex examples with byte-by-byte annotation in the prompt (e.g., "bytes 0–3: magic number 0x00010000 for TrueType, 4–5: number of tables, ...").
- Reduce `max_gaps` for binary targets when using non-Claude models — showing fewer but simpler gaps may improve parse rate more than showing all 30.
- Try a two-stage approach: stage 1 asks model for the *structure* of the input (what tables, what values), stage 2 asks it to encode those as base64. This keeps individual outputs shorter and within the UF response cap.

**Expected difficulty:**
Medium. The UF response cap (2048 chars) is a hard constraint that cannot be engineered around for complex prompts. The fundamental issue is that generating 64 bytes of valid binary requires more precision than generating a regex string. Even with perfect prompting, mid-size models may plateau at ~20–30% parse rate. But even that would yield ~30 seeds per 100 attempts, which is enough for M1/M2 measurement.

---

### Direction 5: Transfer Learning — Leave-One-Out Evaluation

**What it is:**
Test whether seeds generated for one target (e.g., RE2) improve fuzzing performance on a structurally related target (e.g., libxml2 — both are text parsers). This is the "transfer" phase of the UTCF pipeline.

**Why it matters:**
If LLM seeds transfer across targets, it suggests the LLM has learned general fuzzing strategies, not just target-specific patterns. This would justify using seeds from any text-format target as a warm start for a new target, dramatically reducing the cost of seed synthesis for new targets.

The experiment infrastructure for this is already scaffolded:
- `LOO_FEW_SHOT = 5` and `LOO_MIN_DISTINCT_SOURCE_TARGETS = 3` are defined in `core/config.py`
- The tier-1 target set includes: re2, libxml2, sqlite3, libjpeg-turbo, lcms, harfbuzz, proj, ffmpeg

**Methodology:**
For each target T, train a few-shot prompt using seeds from all other targets (leave-one-out). Synthesize seeds for T using only cross-target few-shot examples. Compare M1/M2 against the within-target synthesis results from the ablation.

**Expected finding:**
Text-to-text transfer (RE2 → libxml2 → sqlite3) should show positive transfer. Binary-to-text or text-to-binary transfer is expected to fail or be minimal. This would establish a clear boundary for when transfer is useful.

---

### Direction 6: Cost-Effectiveness Analysis

**What it is:**
Compute the cost per unit of M2 improvement for each model and variant, and plot a Pareto frontier of M2 vs. total cost.

**Why it matters:**
The experiment was expensive in both time and API cost. A practitioner choosing a model for LLM-guided fuzzing does not just want the highest M2 — they want the best M2 achievable within a budget. This analysis answers: *for $10 of API calls, what is the best model and variant to use?*

**Data source:**
Run `python -m analysis.scripts.cost_audit` — walks `.cache/llm/*.json`, sums `cost_usd`, and produces `results/cost_audit/summary.{md,json,csv}`. This is authoritative on-disk spend and supersedes any prose estimate in older docs.

Current actual spend (2026-04-21 audit, 14,161 cached responses):

| provider | spend |
|---|---:|
| Anthropic (`claude-sonnet-4-6` + `claude-haiku-4-5`) | **$86.25** |
| UF LiteLLM (llama + codestral + nemotron + gpt-oss) | $13.83* |
| **Total on-disk** | **$100.09** |

\* UF LiteLLM bills the user $0 — the $13.83 is an *internal accounting figure* produced by `PRICING_USD_PER_MTOK` (which assigns llama-8b $0.22/MTok, etc. per the UF dashboard). It is useful for apples-to-apples comparisons, but the actual invoice paid by the researcher is $0 for those rows. Any "free model" claim should specify which number is meant.

**Expected findings:**
- Codestral-22b at v3/v4 on RE2 (M2=0.800) is vendor-invoice-free and accounting-cost ~$0.17 per 150-seed cell — it dominates the Pareto frontier for text targets on either definition of "cost."
- Claude Sonnet on Harfbuzz (M2=0.640, ~$1.93/cell audited) is the only viable option for binary targets — no free model comes close.
- Haiku is likely dominated by codestral on RE2 (similar M2, but haiku actually invoices and costs ~$0.26/cell).
- The "free model + text target" combination is cost-dominant for text input formats under both the vendor-invoice and accounting-cost definitions.

**Why this matters for the paper:**
One of the strongest practical claims of this work is that LLM-guided fuzzing is accessible without frontier model API access. This analysis quantifies that claim precisely. Report both the vendor-invoice number (for the "accessible without paid APIs" claim) and the accounting number (for fair cross-model cost comparisons).

---

### Direction 7: Structured Output Enforcement

**What it is:**
Use grammar-constrained decoding or enforced JSON mode (where supported) to eliminate parse failures across all models, making every API call produce a valid seed candidate.

**Why it matters:**
The current system wastes a large fraction of API calls due to parse failures:
- llama-3.3-70b: ~95% failure rate
- nemotron HB v3+: ~99% failure rate (empty response from UF cap)
- llama-8b on Harfbuzz: ~70% failure rate (generates text strings instead of base64)

Every failed call is a wasted API call. For models being used on a fixed budget (UF endpoint), wasted calls still consume rate limit quota. Fixing parse failures would effectively multiply usable throughput by 3–20× for affected models.

**Implementation options:**
1. **`response_format: json_object`** — supported by OpenAI API and most LiteLLM proxies. Forces the model to output valid JSON. Does not enforce schema beyond valid JSON, but eliminates markdown fences and truncation-induced syntax errors.
2. **JSON Schema enforcement** — newer LiteLLM versions support `response_format: {"type": "json_schema", "schema": {...}}`. Enforces the exact `inputs[].content_b64/reasoning/target_gaps` schema. Eliminates all structural parse failures.
3. **Outlines / LMQL** — if running local models, grammar-constrained decoding with Outlines guarantees schema-valid output at every token. Not applicable to UF LiteLLM proxy.
4. **Retry with format correction** — if a response fails to parse, append the raw response to the prompt and ask the model to fix the formatting. One correction pass often succeeds. This is a fallback for APIs that don't support structured output.

**Which to prioritize:**
Option 1 (json_object mode) is the lowest-effort change and likely fixes 80% of failures. Try it first by adding `response_format={"type": "json_object"}` to the LiteLLM call in `core/llm_client.py` for non-Claude models. The UF LiteLLM proxy version needs to be checked for support.

---

### Direction 8: Nemotron RE2 v1_src Deep Dive

**What it is:**
Investigate *why* nemotron-3-super-120b achieves M2=0.867 at v1_src on RE2 — the single best result in the entire experiment — and determine whether this is reproducible, generalizable, or an artifact.

**Why it matters:**
M2=0.867 means that 86.7% of the hard target branches — branches that random fuzzing *never* hits — were triggered by at least one of the 150 seeds. This is an extraordinary result. The next best result at v1_src is codestral at M2=0.400 — less than half. Sonnet at v1_src is M2=0.600. Something specific about nemotron's knowledge of regex internals, or the interaction between its training data and the RE2 source code, produces a qualitatively different outcome.

This could be:
- **Coincidence** — the 150 seeds happen to be well-distributed, and re-running with a different random seed would give different results. Test: regenerate nemotron v1_src RE2 with a different `--attempt-offset` and compare M2.
- **Nemotron knows RE2** — nemotron may have been trained on the RE2 source code, RE2 test suite, or documents describing RE2's internals. This would be a form of data contamination. Test: ask nemotron directly about RE2 internals and compare its explanations to what actually appears in the source.
- **Nemotron is especially good at regex generation** — the model may have seen more regex-related content in training. Test: replicate on a different regex engine (e.g., PCRE2) where nemotron is less likely to have direct training exposure.
- **v1_src is the sweet spot for nemotron** — adding source code (v1) triggers domain expertise that v0 (no code) cannot. The MoE architecture may have specialized experts activated by C++ code that are not activated by abstract task descriptions. Test: ablate the source code content (e.g., replace RE2 source with a different C++ file) and measure the effect on seed quality.

**Why this is worth investigating:**
If the result is real and generalizable, it suggests that large MoE models with broad training data coverage can match frontier closed models on specific domains at zero cost. That is a practically significant finding. If it is not reproducible, documenting that is equally important for scientific integrity.

---

### Direction 9: Automated Seed Quality Triage

**What it is:**
Build a lightweight pre-screening step that filters LLM-generated seeds before running them through the full coverage binary. Use fast syntactic or semantic checks to discard seeds that are obviously invalid before spending coverage replay time on them.

**Why it matters:**
The current pipeline runs every generated seed through an LLVM-instrumented binary via `seed_replay`. For Harfbuzz, this binary parses the font, runs the shaping pipeline, and records coverage — taking ~100–500ms per seed. With 150 seeds × 7 models × 5 variants × 2 targets = 10,500 replay runs, this is already expensive. As the experiment scales to more targets and models, replay cost becomes a bottleneck.

For Harfbuzz specifically, ~70–90% of seeds from non-Claude models are structurally invalid (not valid base64, wrong size, incorrect sfnt magic bytes). These seeds reliably produce zero new coverage because the parser rejects them immediately. Filtering these out before replay would save substantial replay time.

**Pre-screening checks (cheap to implement):**
- **Base64 validity** — is `content_b64` valid base64? Decodes cleanly? Zero cost.
- **Size check** — decoded bytes within 12–64 byte limit (Harfbuzz). Rejects oversized blobs before replay.
- **Magic byte check** — first 4 bytes match a known sfnt magic (0x00010000, 0x4F54544F for CFF, etc.)? This alone would filter ~40% of bad seeds for Harfbuzz.
- **Regex validity** — for RE2, attempt to compile the regex with a lightweight checker (e.g., `re2::RE2(pattern).ok()`). Invalid regex patterns cannot exercise the state machine.

**Expected impact:**
Reducing replay runs from 10,500 to ~3,000–5,000 (by filtering obvious invalid seeds) makes iteration much faster and allows more variants/models to be tested on tighter schedules.

---

## 5. Priority Order

Given the current state of the project, the recommended order of execution is:

1. **Direction 1 (Fuzzing Campaigns)** — validates the core thesis. Nothing else matters if campaigns don't show bug-finding improvement. Do this first.
2. **Direction 2 (Claude HB v2–v4)** — cheap, fills the biggest gap in the data table. Run in parallel with campaigns.
3. **Direction 3 (llama-3.3 JSON fix)** — cheap fix, potentially converts a failing model into a useful data point.
4. **Direction 6 (Cost-effectiveness analysis)** — purely analytical, requires no new experiments. Write this up from existing data.
5. **Direction 8 (Nemotron deep dive)** — important for scientific validity. Run a replication before claiming the result.
6. **Directions 4, 5, 7, 9** — medium-effort improvements that strengthen the system but are not blocking for a first paper submission.

---

*Document written: 2026-04-19 | Author: experimental session log | Intended audience: any future researcher or agent resuming this project without prior context*

---

## How the cost numbers in this document were produced

- **Historical spend** — `analysis/scripts/cost_audit.py` walks
  `.cache/llm/*.json` and sums the `cost_usd` field stored in each
  response record. The grand total is authoritative; the per-target
  breakdown is best-effort (response messages are not cached, so target
  attribution falls back to a substring match on the cached content).
- **Pre-experiment estimates** — `analysis/scripts/estimate_cost.py`
  multiplies `core.llm_client.PRICING_USD_PER_MTOK` by a requested
  `(n_calls, mean_in, mean_out)`. Default means come from the audit
  output, so estimates cite *observed* per-call averages rather than
  hand-guessed token counts.
- **Single source of truth** — `PRICING_USD_PER_MTOK` in
  `core/llm_client.py` is the only place vendor rates live. Updating
  rates there re-prices both the audit and the estimator automatically.
- Prior versions of this document contained hand-estimated cost numbers
  that were wrong by an order of magnitude. If you catch a cost figure
  in prose that is not backed by one of the two scripts above, flag it
  — it is probably stale.
