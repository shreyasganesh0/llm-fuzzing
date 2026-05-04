# Experiment 1 — Single-model RE2 A/B and generalization follow-ups

**Scope.** RE2 only, llama-3.1-8b-instruct only (via UF LiteLLM proxy), 2026-04-12 to 2026-04-13.
**Question.** Does giving an LLM a list of *uncovered branches* on top of the source code help it write better fuzzing seeds, and if so, does that advantage transfer beyond the files the gap list pointed at?
**Headline.** Gap-targeted prompting beats source-only by **+110 edges in distribution** — but the advantage **collapses to +3 edges on held-out files**, and the most efficient single recipe turns out to be **source + gaps without tests**.

This document covers six sub-versions (`experiment1_0` … `experiment1_5`).
For framework setup commands (build, freeze, env), see
`EXPERIMENT_WALKTHROUGH.md §6`.

---

## 1. The two prompts under test

Both versions of the A/B compare:

- **`exp1`** — gap-targeted prompt. Renders `synthesis/prompts/input_synthesis_regex.j2`
  with the upstream tests (5 few-shot examples) **and** a list of uncovered branches
  pulled from `coverage_gaps.json`. The model is asked to write regexes that target
  those specific `(file, line)` gaps.
- **`exp2`** — source-only prompt. Renders `synthesis/prompts/source_only_synthesis_regex.j2`
  with the harness + library source code only. No tests, no gap list.
  `synthesis/scripts/build_source_prompt.py::assert_no_tests` enforces the no-tests guard.

Constants: T=0.7, top_p=0.95, 3 samples per cell, harness format
`[2 sha256-derived flag bytes][UTF-8 regex, 1–62 chars]`, total 3–64 bytes.

---

<a id="experiment1_0"></a>
## 2. experiment1_0 — RE2 A/B, bytes-format prompt (2026-04-12, archived)

The first end-to-end run. Prompts asked the LLM to emit base64-encoded raw
bytes that the harness reads as `[flag][pattern]`. Loop-abort rate hit
5 of 6 samples — the model rarely landed on the harness's byte shape by luck.

| Metric | exp1 | exp2 | union | intersection |
|---|---:|---:|---:|---:|
| Seeds produced | 5 | **10** | — | — |
| Loop-aborted samples | 3 / 3 | 2 / 3 | — | — |
| Edges covered | 475 | **513** | 530 | 458 |
| Lines covered | 1243 | **1314** | 1337 | 1220 |
| Edges only in this cell | 17 | 55 | — | — |
| Jaccard (edges) | — | — | 0.864 | — |

Result flipped the wrong way and motivated the prompt rewrite. Retained as the
"why the rewrite mattered" story; **not cited as evidence**.

**Artefacts.**
`dataset/fixtures/re2_ab/phase3_results_bytes_v1/`,
`dataset/fixtures/re2_ab/exp2_results_bytes_v1/`.

**How to reproduce.** Disabled in current code (input format auto-detects to `regex` for
RE2). To replay from the archived fixture, point a coverage measurement at the bytes
seeds:

```bash
.venv/bin/python -m synthesis.scripts.measure_coverage \
    --binary dataset/targets/src/re2/build/coverage/seed_replay \
    --seeds-dir dataset/fixtures/re2_ab/phase3_results_bytes_v1/seeds/re2/exp1/llama-3.1-8b-instruct \
    --source-roots "$(pwd)/dataset/targets/src/re2/upstream"
```

---

<a id="experiment1_1"></a>
## 3. experiment1_1 — RE2 A/B, regex-format prompt (2026-04-13, headline)

Same fixture, same model. Prompts rewritten to emit **raw regex strings**; the
tooling now prepends 2 sha256-derived flag bytes per seed. Loop-abort rate
dropped from 5/6 to 1/6.

| Metric | exp1 (gap) | exp2 (source) | union | intersection |
|---|---:|---:|---:|---:|
| Seeds produced | 20 | 30 | — | — |
| Edges covered | **1243** | 1133 | 1308 | 1068 |
| Lines covered | **2530** | 2385 | 2661 | 2254 |
| Edges only in this cell | **175** | 65 | — | — |
| Jaccard (edges) | — | — | 0.817 | — |

Exp1's exclusive-edge concentration (top files):
`re2/parse.cc` (+86), `re2/regexp.cc` (+50), `re2/simplify.cc` (+14), `re2/compile.cc` (+9), `re2/prog.cc` (+6).
Bulk in the parser/simplifier — exactly where the gap list pointed. **+110 edges,
in distribution.**

**Cost.** ≈ $0.010. **Wall-clock.** ~2 min (UF proxy throttle dominated).

**Artefacts.**
- Seeds: `dataset/fixtures/re2_ab/phase3_results/seeds/re2/exp1/llama-3.1-8b-instruct/`,
  `dataset/fixtures/re2_ab/exp2_results/seeds/re2/source_only/llama-3.1-8b-instruct/`
- Coverage profiles: `dataset/fixtures/re2_ab/ab_coverage/{exp1,exp2}.json`
- Diff: `dataset/fixtures/re2_ab/ab_coverage/ab_coverage_diff.{json,md}`

**How to reproduce.**

```bash
# exp1 (gap-targeted)
UTCF_LITELLM_URL=https://api.ai.it.ufl.edu UTCF_LLM_RPM=12 \
.venv/bin/python -m synthesis.scripts.generate_inputs \
    --target re2 --model llama-3.1-8b-instruct --samples 3 --experiment exp1 \
    --input-format regex \
    --dataset-root dataset/fixtures/re2_ab \
    --results-root dataset/fixtures/re2_ab/phase3_results

# exp2 (source-only)
UTCF_LITELLM_URL=https://api.ai.it.ufl.edu UTCF_LLM_RPM=12 \
.venv/bin/python -m synthesis.scripts.generate_source_inputs \
    --target re2 --model llama-3.1-8b-instruct --samples 3 \
    --source-max-files 4 --source-token-budget 14000 --max-tokens 8192 \
    --input-format regex \
    --results-root dataset/fixtures/re2_ab/exp2_results

# Per-cell coverage
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

# Differential
.venv/bin/python -m analysis.scripts.ab_coverage_diff \
    --exp1-profile dataset/fixtures/re2_ab/ab_coverage/exp1.json \
    --exp2-profile dataset/fixtures/re2_ab/ab_coverage/exp2.json \
    --out-dir dataset/fixtures/re2_ab/ab_coverage \
    --upstream-root "$(pwd)/dataset/targets/src/re2/upstream" \
    --model llama-3.1-8b-instruct --target re2 \
    --exp1-seed-count 20 --exp2-seed-count 30
```

---

<a id="experiment1_2"></a>
## 4. experiment1_2 — Random baseline (three-way, 2026-04-13)

`[2 random flag bytes][random ASCII 1–62]`, `random.Random(42)`, 30 seeds via
`synthesis/scripts/generate_random_inputs.py --input-format regex`.

| Cell | Seeds | Edges | Δ vs random |
|---|---:|---:|---:|
| `exp1` (gap) | 20 | **1243** | **+263** |
| `exp2` (source) | 30 | 1133 | +153 |
| Random baseline | 30 | 980 | (floor) |

Both LLM cells clear the floor decisively. exp1's lead over exp2 (+110) is ≈ 42% of
exp1's lead over random (+263) — most of exp1's edge is above-random signal already
captured by exp2.

Top files exp1 reaches that random misses:
`re2/parse.cc` (+145), `re2/regexp.cc` (+57), `re2/simplify.cc` (+44), `re2/compile.cc` (+23).

Top files exp2 reaches that random misses:
`re2/parse.cc` (+82), `re2/simplify.cc` (+49), `re2/compile.cc` (+27), `re2/onepass.cc` (+14).

Both LLM variants concentrate their edge over random in the parser/simplifier/compiler.

**Artefact.** `dataset/fixtures/re2_ab/ab_coverage/three_way_summary.md` (and
`exp1_vs_random/`, `exp2_vs_random/` subdirs of the same dir).

**How to reproduce.**

```bash
.venv/bin/python -m synthesis.scripts.generate_random_inputs \
    --target re2 --count 30 --seed 42 --input-format regex \
    --results-root dataset/fixtures/re2_ab/random_results

# Then measure_coverage and ab_coverage_diff against exp1/exp2 (same form as 1_1).
```

---

<a id="experiment1_3"></a>
## 5. experiment1_3 — Held-out source-file subset (2026-04-13)

Tests whether `exp1`'s in-distribution +110 transfers when the gap list is
restricted to a *disjoint* file set.

**Setup.** Split RE2 source files:

- **Set A** (visible to gap prompt, parser/simplifier): `re2/parse.cc`,
  `re2/regexp.cc`, `re2/simplify.cc`, `re2/tostring.cc`. Gap list for
  `exp1_heldout` filtered to these files (611/2042 gap branches retained).
- **Set B** (held out, coverage measured): `re2/compile.cc`, `re2/prog.cc`,
  `re2/dfa.cc`, `re2/nfa.cc`, `re2/onepass.cc`, `re2/bitstate.cc`, `re2/re2.cc`,
  `util/rune.cc`, `util/strutil.cc`.

`exp2_source` always sees all source files via call-graph priority — the
"no partitioning" baseline.

| Cell | Seeds | B-edges | B-lines | Δ vs random |
|---|---:|---:|---:|---:|
| `exp1_full` | 20 | **599** | 1533 | +42 |
| `exp2_source` | 30 | 596 | 1540 | +39 |
| `exp1_heldout` | 30 | 581 | 1502 | +24 |
| Random | 30 | 557 | 1436 | 0 |

**Findings.**

1. exp1's in-distribution +110 collapses to **+3** on held-out files. The parser
   files absorbed almost all of it.
2. When exp1's gap list is restricted to a *disjoint* file set (`exp1_heldout`),
   it **loses** to exp2 by 15 edges. exp2's recipe transfers across the file
   boundary; exp1's does not.

**Confound.** `exp1_heldout` still sees the 5 few-shot test examples, which
reference RE2 APIs that implicitly exercise set-B files; that's why it still
beats random (+24) rather than matching it.

**Artefacts.** `dataset/fixtures/re2_ab/ab_coverage/heldout_summary.md`,
`heldoutB_diff.md`, `dataset/fixtures/re2_ab_heldout/`.

---

<a id="experiment1_4"></a>
## 6. experiment1_4 — 7-cell prompt ablation (2026-04-13)

Decomposes exp1's +110 into contributions from {gaps, tests, source}.
All n=20–30, llama-8b, regex format, same fixture.

| Cell | Gaps | Tests | Source | Seeds | Edges | Δ vs `exp1_full` |
|---|:-:|:-:|:-:|---:|---:|---:|
| **`exp2_plus_gaps`** | ✅ | ❌ | ✅ | 30 | **1250** | **+7** |
| `exp1_full` | ✅ | ✅ | ❌ | 20 | 1243 | 0 |
| `exp2_plus_tests` | ❌ | ✅ | ✅ | 30 | 1210 | −33 |
| `exp2_source` | ❌ | ❌ | ✅ | 30 | 1133 | −110 |
| `exp1_tests_only` | ❌ | ✅ | ❌ | 30 | 1093 | −150 |
| Random | — | — | — | 30 | 980 | −263 |
| `exp1_gaps_only`* | ✅ | ❌ | ❌ | 10 | 879 | −364 |

*`exp1_gaps_only` produced only 10 seeds — loop detector aborted 2/3 samples.
Dense gap list without tests or source triggers llama's degenerate-repetition
mode. A data point about the prompt itself, not a bug.

**Decision rules.**

- "If `exp1_gaps_only` ≈ `exp1_full`, the win is from the gaps." → **NOT supported**
  (collapses 364 below).
- "If `exp2_plus_gaps` > `exp1_full`, gaps stack with source." → **Supported**
  (+7 edges; 142 exclusive edges over `exp1_full`).
- "If `exp2_plus_tests` ≥ `exp1_full`, tests are the generalizing piece." →
  **NOT supported** (33 edges below).

**What this says.**

1. **Source code is the best information carrier.** Every source-carrying cell ≥1133
   edges; source-less cells span 879–1243 (dominated by loop issues).
2. **Gaps amplify source.** `exp2_plus_gaps` beats `exp2_source` by **+117** — the
   largest single-variable effect in the table.
3. **Tests alone ≈ source alone for this fixture.** `exp1_tests_only` (1093) ≈
   `exp2_source` (1133) — 5 test examples ≈ 14k tokens of source on this fixture.
4. **`exp1_gaps_only` collapse is model-specific.** Strip source, force gap-by-gap
   enumeration, llama derails.

**Artefacts.** `dataset/fixtures/re2_ab/ab_coverage/ablation_summary.md`,
`ablation_diff.{json,md}`.

**How to reproduce.** Each ablation cell renders `synthesis/prompts/ablation_synthesis_regex.j2`
with different combinations of `include_gaps`, `include_tests`, `include_source`:

```bash
# Example: exp2_plus_gaps (source + gaps, no tests)
.venv/bin/python -m synthesis.scripts.generate_ablation_inputs \
    --target re2 --model llama-3.1-8b-instruct \
    --cell exp2_plus_gaps \
    --include-source --include-gaps \
    --samples 3 --num-inputs 10 --max-gaps 30 \
    --source-token-budget 8000 --max-tokens 4096 \
    --input-format regex \
    --dataset-root dataset/fixtures/re2_ab \
    --results-root dataset/fixtures/re2_ab/ablation_results
```

---

<a id="experiment1_5"></a>
## 7. experiment1_5 — 1h × 3-trial libFuzzer campaign (DEFERRED)

Original plan: replay each seed corpus into a 1-hour libFuzzer campaign × 3 trials
to measure campaign-time coverage rather than seed-time.

Required: `build/fuzzer/` variant (~9 CPU-hours) plus campaign infrastructure.
Deferred 2026-04-13; A+B evidence judged sufficient to ship the writeup.
Picked up later as `experiment2_2` with different scope (different model and
target choices, not a direct continuation).

Tracked in `docs/FUTURE_DIRECTIONS.md` if/when re-prioritised.

---

<a id="combined-verdict"></a>
## 8. Combined verdict

The hypothesis "exp2 (source-only) generalizes better than exp1 (gap-targeted)"
is **supported with nuance**:

- On the same files the gap list points at, exp1 wins decisively (+110).
- On held-out files within the same target, exp1's advantage vanishes (+3 with
  full prompt; −15 when gap list restricted to disjoint files).
- The most efficient single recipe on this fixture is **`exp2_plus_gaps`** (source
  code + coverage gaps, no tests) — +7 over pure exp1, +117 over pure exp2.

Clean take: *the right baseline is not exp1 or exp2, it's source + cheap coverage
annotations. exp1's +110 was mostly the annotation doing work exp2's source
already covered, and on held-out files the annotation stops generalizing.*

This finding informed the design of `experiment2`'s 5-variant prompt grid
(`v0_none, v1_src, v2_src_tests, v3_all, v4_src_gaps`), which lifts the
single-fixture llama A/B to a multi-model multi-target experiment.

---

<a id="cli-reference"></a>
## 9. CLI script reference

Every reproduction command in §2–§6 invokes one of these scripts. Each
flag column lists the keyword name; type / default / choices are in the
script's `--help` (or in the inline `add_argument` calls).

### `synthesis.scripts.generate_inputs` — exp1 (gap-targeted) driver
Used in [§3 (`experiment1_1`)](#experiment1_1).

| Flag | Required | Default | Purpose |
|---|:-:|---|---|
| `--target` | yes | — | Target name (`re2`). |
| `--model` | yes | — | LLM model id (e.g. `llama-3.1-8b-instruct`). |
| `--samples` | no | `SYNTHESIS_SAMPLES = 3` | Number of independent LLM samples per cell. Cache-salted per sample. |
| `--experiment` | no | `exp1` | One of `{exp1, exp2}`. exp1 = gap-targeted; exp2 = source-only-style fallback. |
| `--input-format` | no | `bytes` | `bytes` or `regex`. **For RE2 always pass `regex`** — `bytes` is the format-mismatch path that produced [`experiment1_0`](#experiment1_0)'s archived flop. |
| `--dataset-root` | no | `dataset/dataset` | Root containing `tests.json`, `coverage_gaps.json`, `metadata.json` for the target. |
| `--results-root` | no | `synthesis/results` | Root under which seeds + synthesis records are written. |

### `synthesis.scripts.generate_source_inputs` — exp2 (source-only) driver
Used in [§3 (`experiment1_1`)](#experiment1_1) for the source-only cell.

| Flag | Required | Default | Purpose |
|---|:-:|---|---|
| `--target` | yes | — | Target name. |
| `--model` | yes | — | LLM model id. |
| `--samples` | no | `SYNTHESIS_SAMPLES = 3` | Independent LLM samples per cell. |
| `--num-inputs` | no | `10` | Inputs requested per LLM call. |
| `--source-max-files` | no | `SOURCE_CONTEXT_MAX_FILES = 40` | Cap on source files included in the prompt. |
| `--source-token-budget` | no | `None` | Token budget for source slice. `14000` was used for the 2026-04-13 A/B. |
| `--max-tokens` | no | `SYNTHESIS_MAX_TOKENS = 4096` | Per-call response cap. `8192` for the A/B because llama-8b's source-only outputs are verbose. |
| `--input-format` | no | `bytes` | Pass `regex` for RE2. |
| `--results-root` | no | `synthesis/results` | Where to write seeds + synthesis records. |
| `--dry-run` | no | off | Render the prompt + skip LLM calls; useful for inspecting the source slice. |

### `synthesis.scripts.generate_random_inputs` — random baseline
Used in [§4 (`experiment1_2`)](#experiment1_2).

| Flag | Required | Default | Purpose |
|---|:-:|---|---|
| `--target` | yes | — | Target name. |
| `--count` | yes | — | Number of random seeds to produce. |
| `--seed` | no | `42` | RNG seed. **Do not change** — pinning makes the baseline reproducible. |
| `--input-format` | no | `bytes` | `bytes` or `regex`. For RE2 use `regex` to get `[2 random flag bytes][random ASCII 1–62]` shape. |
| `--results-root` | no | `synthesis/results` | Where to write seeds. |

### `synthesis.scripts.generate_ablation_inputs` — 7-cell ablation driver
Used in [§6 (`experiment1_4`)](#experiment1_4) for cells with arbitrary
combinations of `{include_source, include_tests, include_gaps}`. Also used
by `experiment2_1`'s orchestrator under the hood.

| Flag | Required | Default | Purpose |
|---|:-:|---|---|
| `--target` | yes | — | Target name. |
| `--model` | yes | — | LLM model id. |
| `--cell` | yes | — | Cell name (e.g. `exp2_plus_gaps`). Used as a path component and in the cache salt. |
| `--include-source` | no | off | Render the source-files block in the prompt. |
| `--include-tests` | no | off | Render the few-shot upstream tests block. |
| `--include-gaps` | no | off | Render the uncovered-branch list. |
| `--samples` | no | `SYNTHESIS_SAMPLES = 3` | Independent LLM samples per cell. |
| `--num-inputs` | no | `DEFAULT_INPUTS_PER_PROMPT = 10` | Inputs requested per LLM call. |
| `--max-gaps` | no | `DEFAULT_GAPS_PER_PROMPT = 20` | Cap on gap entries shown in the prompt. |
| `--source-max-files` | no | `SOURCE_CONTEXT_MAX_FILES = 40` | Cap on source files. |
| `--source-token-budget` | no | `None` | Token budget for the source slice. |
| `--max-tokens` | no | `SYNTHESIS_MAX_TOKENS = 4096` | Response token cap. |
| `--input-format` | no | auto-detect | `regex` or `binary`; auto-detect from target name (`re2 → regex`, else `binary`). |
| `--run-id` | no | `0` | Integer added to the cache salt to bust cached failures on restart. **Bump by ≥5000 when restarting** to avoid replaying loop aborts. |
| `--strategy` | no | `default` | One of the strategies in [`experiment3.md §1`](experiment3.md#strategy-axis). For [`experiment1_4`](#experiment1_4) leave at `default`. |
| `--dataset-root` | no | `dataset/dataset` | Prepped dataset root. |
| `--results-root` | no | `synthesis/results` | Where to write seeds + records. |

### `synthesis.scripts.measure_coverage` — coverage replay
Used everywhere ([`§3`](#experiment1_1), [`§4`](#experiment1_2),
[`§5`](#experiment1_3), [`§6`](#experiment1_4)) to replay a corpus and
emit a `CoverageProfile` JSON.

| Flag | Required | Default | Purpose |
|---|:-:|---|---|
| `--binary` | yes | — | Path to `seed_replay` binary. |
| `--seeds-dir` | yes | — | Directory of `*.bin` seed files. |
| `--source-roots` | no | `None` | One or more DWARF prefix paths. RE2's baked binary needs `phase1_dataset/...`. |
| `--timeout-s` | no | `10` | Per-seed replay timeout. |
| `--profile-out` | no | stdout | Where to write the JSON profile. |
| `--profdata-bin` | no | `llvm-profdata-15` | Override via `LLVM_PROFDATA` env. |
| `--cov-bin` | no | `llvm-cov-15` | Override via `LLVM_COV` env. |

### `analysis.scripts.ab_coverage_diff` — set-wise differential
Used in [§3 (`experiment1_1`)](#experiment1_1).

| Flag | Required | Default | Purpose |
|---|:-:|---|---|
| `--exp1-profile` | yes | — | Path to first cell's coverage profile JSON. |
| `--exp2-profile` | yes | — | Path to second cell's coverage profile JSON. |
| `--out-dir` | yes | — | Where to write `ab_coverage_diff.{json,md}`. |
| `--upstream-root` | no | `""` | Upstream source root (used to label per-file diff rows). |
| `--exp1-seed-count` | no | `0` | Seed count for the exp1 cell (annotates the markdown table). |
| `--exp2-seed-count` | no | `0` | Same for exp2. |
| `--model` | no | `""` | Annotates the markdown header. |
| `--target` | no | `""` | Annotates the markdown header. |

For exhaustive options call any script with `--help`.

---

## 10. Numbers that must not drift

| Context | Cell | Seeds | Edges | Lines |
|---|---|---:|---:|---:|
| 2026-04-12 bytes | `exp1` | 5 | 475 | 1243 |
| 2026-04-12 bytes | `exp2` | 10 | 513 | 1314 |
| 2026-04-13 regex (headline) | `exp1` | 20 | 1243 | 2530 |
| 2026-04-13 regex (headline) | `exp2` | 30 | 1133 | 2385 |
| Random baseline | random | 30 | 980 | 2081 |
| Held-out on Set B | `exp1_full` | 20 | 599 | 1533 |
| Held-out on Set B | `exp2_source` | 30 | 596 | 1540 |
| Held-out on Set B | `exp1_heldout` | 30 | 581 | 1502 |
| Held-out on Set B | random | 30 | 557 | 1436 |
| Ablation (full source) | `exp2_plus_gaps` | 30 | 1250 | — |
| Ablation (full source) | `exp1_full` | 20 | 1243 | — |
| Ablation (full source) | `exp2_plus_tests` | 30 | 1210 | — |
| Ablation (full source) | `exp2_source` | 30 | 1133 | — |
| Ablation (full source) | `exp1_tests_only` | 30 | 1093 | — |
| Ablation (full source) | random | 30 | 980 | — |
| Ablation (full source) | `exp1_gaps_only` | 10 | 879 | — |
