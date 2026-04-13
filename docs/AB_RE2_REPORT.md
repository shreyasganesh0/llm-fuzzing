# A/B experiment — exp1 (gap-targeted) vs exp2 (source-only) on RE2

_Runs: 2026-04-12 (bytes-format, archived) and **2026-04-13 (regex-format,
headline)**. One target (RE2), one model (llama-3.1-8b-instruct via UF
LiteLLM proxy), shared 3-test fixture. Total LLM spend: ≈ $0.010._

## Headline (regex-text synthesis, 2026-04-13)

We rewrote the synthesis prompts to emit **raw regex strings** (the tooling
prepends 2 deterministic flag bytes to match the RE2 libFuzzer harness
layout). This is a far better fit for an RE2 target than the original
base64-encoded-bytes format, which asked the LLM to produce arbitrary
binary that had to hit `[flag][pattern]` structure by luck.

| metric | exp1 (gap) | exp2 (source) | union | intersection |
|---|---:|---:|---:|---:|
| seeds produced | 20 | 30 | — | — |
| ok samples / total | 2 / 3 | 3 / 3 | — | — |
| edges covered | **1243** | 1133 | 1308 | 1068 |
| lines covered | **2530** | 2385 | 2661 | 2254 |
| edges ONLY in this cell | **175** | 65 | — | — |
| Jaccard (edges) | — | — | 0.817 | — |

**Exp1 (gap-targeted) now wins decisively** (+110 edges vs exp2, 175 vs 65
exclusive edges), reversing the previous bytes-format result. The bulk of
exp1's exclusive edges land in `re2/parse.cc` (+86), `re2/regexp.cc` (+50),
and `re2/simplify.cc` (+14) — the parser/simplifier paths that the gap
prompt specifically highlights.

Loop-abort rate also dropped from 5/6 (bytes) to 1/6 (regex), which is the
bigger lesson: matching the prompt format to the target's native input
shape makes the model's job tractable for even a small instruction-tuned
model.

---

# Generalization follow-up (2026-04-13)

The +110-edge headline is measured on the same fixture exp1's gap list is
computed from. That's in-distribution performance. The user's hypothesis:
exp2 (source-only) generalizes better because exp1 is "cheating" — the
gap list is a flashlight on a known neighborhood, not a transferable
skill. Three follow-up experiments probe this. Full plan in
`/home/shreyasganesh/.claude/plans/zazzy-dazzling-quilt.md`.

## P0 — Random baseline (three-way)

`[2 random flag bytes][random ASCII 1-62]`, `random.Random(42)`, 30 seeds.
Artifact: `dataset/fixtures/re2_ab/ab_coverage/three_way_summary.md`.

| cell | seeds | edges | Δ vs random |
|---|---:|---:|---:|
| exp1 (gap-targeted) | 20 | **1243** | **+263** |
| exp2 (source-only)  | 30 | 1133 | +153 |
| random baseline     | 30 |  980 | 0 |

Both LLM cells clear the floor meaningfully; exp1's gap over exp2 (+110)
is ≈ 42% of exp1's gap over random (+263) — i.e. most of exp1's edge is
above-random signal, but the exp2 recipe already captures the majority
of that signal for free.

## Experiment A — Held-out source-file subset

Split RE2 files into:
- **Set A** (gap list visible to exp1): `re2/parse.cc`, `re2/regexp.cc`,
  `re2/simplify.cc`, `re2/tostring.cc` — the parser/simplifier.
- **Set B** (held out; coverage measured): `re2/compile.cc`, `re2/prog.cc`,
  `re2/dfa.cc`, `re2/nfa.cc`, `re2/onepass.cc`, `re2/bitstate.cc`,
  `re2/re2.cc`, `util/rune.cc`, `util/strutil.cc`.

exp1_heldout is exp1 with gap list filtered to set A (611/2042 branches).
exp2 is unchanged (always sees all files via call-graph priority) — it's
the "no partitioning" baseline. All cells measured on set B only.
Artifact: `heldout_summary.md`.

| cell | seeds | B-edges | Δ vs exp2 on B |
|---|---:|---:|---:|
| exp1_full       | 20 | **599** | **+3** |
| exp2_source     | 30 | 596 | 0 |
| exp1_heldout    | 30 | 581 | **−15** |
| random baseline | 30 | 557 | −39 |

Two conclusions:
1. exp1_full's **+110-edge in-distribution advantage collapses to +3**
   when restricted to held-out files. The parser files absorbed almost
   all of it.
2. When exp1's gap list is restricted to a *disjoint* file set, it
   **loses** to exp2 on the held-out measurement by 15 edges.
   exp2's recipe transfers across the file boundary; exp1's does not.

Confound: exp1_heldout still sees the 5 few-shot test examples, which
reference RE2 APIs that implicitly exercise set-B files. That's why it
still beats random (+24) rather than matching it.

## Experiment B — Prompt ablation (7 cells)

Decomposes exp1's +110-edge win into contributions from {gaps, tests,
source}. All n=20–30 samples, llama-8b, regex format, same fixture.
Artifact: `ablation_summary.md`.

| cell | gaps | tests | source | edges | Δ vs exp1_full |
|---|:-:|:-:|:-:|---:|---:|
| **exp2_plus_gaps** | ✅ | ❌ | ✅ | **1250** | **+7** |
| exp1_full         | ✅ | ✅ | ❌ | 1243 | 0 |
| exp2_plus_tests   | ❌ | ✅ | ✅ | 1210 | −33 |
| exp2_source       | ❌ | ❌ | ✅ | 1133 | −110 |
| exp1_tests_only   | ❌ | ✅ | ❌ | 1093 | −150 |
| random            | — | — | — |  980 | −263 |
| exp1_gaps_only*   | ✅ | ❌ | ❌ |  879 | −364 |

*`exp1_gaps_only` produced only 10 seeds — 2/3 samples aborted by the
loop detector. A data point about the prompt itself (dense gaps without
source/tests triggers llama's degenerate-repetition mode), not a bug.

Decision rules from the plan:
- "if `exp1_gaps_only` ≈ `exp1_full`, the win is from the gaps" — **not
  supported** (gaps-only falls 364 edges below).
- "if `exp2_plus_gaps` > `exp1_full`, gaps stack with source" —
  **supported** (+7 edges, 142 exclusive edges over exp1_full).
- "if `exp2_plus_tests` ≥ `exp1_full`, tests are the generalizing piece"
  — **not supported** (tests+source is 33 below exp1_full).

What the ablation says:
1. **Source is the load-bearing context.** Every source-carrying cell
   covers ≥1133 edges; source-less cells span 879–1243 (wider and
   dominated by loop issues).
2. **Gaps amplify source.** `exp2_plus_gaps` (1250) > `exp2_source`
   (1133) by +117 — the largest single-variable effect in the table.
3. **Tests alone ≈ source alone.** 5 few-shot tests and 14k tokens of
   source produce roughly the same coverage (1093 vs 1133).
4. The `exp1_gaps_only` collapse is model-specific evidence for the
   hypothesis direction: strip source, force gap enumeration, llama
   derails. A frontier model might not.

## Experiment C — deferred

1-hour × 3-trial libFuzzer campaigns. Deferred 2026-04-13: requires
building `build/fuzzer/` (currently empty) + ~9 CPU-hours. A+B evidence
was judged sufficient for the write-up; revisit if reviewers want
campaign-time data.

## Combined verdict

The user's hypothesis — "exp2 generalizes better than exp1" — is
**supported with nuance**:
- On the same files the gap list points at, exp1 wins decisively (+110).
- On held-out files within the same target, exp1's advantage vanishes
  (+3 when it still sees the full prompt, −15 when it doesn't).
- The most efficient single recipe on this fixture is `exp2_plus_gaps`
  (source code + coverage gaps, no tests) — it beats both pure exp1
  (+7) and pure exp2 (+117).

The clean take: "the right baseline is not exp1 or exp2, it's
source + cheap coverage annotations; exp1's +110 was mostly the
annotation doing work exp2's source already covered, and on held-out
files the annotation stops generalizing."

---

## Archived run — bytes-format synthesis (2026-04-12)

## Setup

- **Target**: RE2 commit `499ef7e…`, coverage-instrumented build produced by
  `dataset/scripts/build_instrumented.sh` (patched in this session to
  auto-detect libstdc++ 13 on Ubuntu 24.04).
- **Shared test fixture**: 3 tests from `re2/testing/regexp_test.cc` —
  `Regexp.BigRef`, `Regexp.NamedCaptures`, `Regexp.CaptureNames`. Identical
  context for both experiments, so the comparison is apples-to-apples.
- **Upstream-tests baseline** (for gap computation only):
  `regexp_test` with 3 tests → 865 / 9222 lines, 410 / 2513 branches =
  **8.71% branch coverage**. Produces 2042 gap branches and 894 gap
  functions fed into exp1's prompt.
- **Evaluation binary**: custom `seed_replay` driver (new file
  `dataset/targets/src/re2/harness/seed_replay_main.cc`) linked
  against the same `target.cc` `LLVMFuzzerTestOneInput`. Source set is
  `re2/*` + `util/*` = 31 files, 7325 lines, 3380 edges.
- **Model**: `llama-3.1-8b-instruct`, T=0.7, top_p=0.95, 3 samples per
  experiment, streaming abort on degenerate loops.

## Pipeline (reproducibility)

```bash
# One-time fixture setup
# (already in dataset/fixtures/re2_ab/re2/tests.json + metadata.json)

# Phase 1: measure baseline coverage
.venv/bin/python -m dataset.scripts.run_test_coverage \
    --target re2 --tests-json dataset/fixtures/re2_ab/re2/tests.json \
    --dataset-root dataset/fixtures/re2_ab \
    --test-binary dataset/targets/src/re2/build/coverage/obj/test/regexp_test

.venv/bin/python -m dataset.scripts.compute_gaps --target re2 \
    --dataset-root dataset/fixtures/re2_ab --no-llm

# Phase 2 (exp1 context only): gap-targeted prediction
UTCF_LITELLM_URL=https://api.ai.it.ufl.edu UTCF_LLM_RPM=12 \
.venv/bin/python -m prediction.scripts.run_prediction \
    --target re2 --model llama-3.1-8b-instruct --few-shot 0 \
    --dataset-root dataset/fixtures/re2_ab \
    --results-root dataset/fixtures/re2_ab/phase2_results

# Phase 3 exp1 synthesis (gap-targeted)
UTCF_LITELLM_URL=https://api.ai.it.ufl.edu UTCF_LLM_RPM=12 \
.venv/bin/python -m synthesis.scripts.generate_inputs \
    --target re2 --model llama-3.1-8b-instruct --samples 3 --experiment exp1 \
    --dataset-root dataset/fixtures/re2_ab \
    --results-root dataset/fixtures/re2_ab/phase3_results

# Phase 3 exp2 synthesis (source-only)
UTCF_LITELLM_URL=https://api.ai.it.ufl.edu UTCF_LLM_RPM=12 \
.venv/bin/python -m synthesis.scripts.generate_source_inputs \
    --target re2 --model llama-3.1-8b-instruct --samples 3 \
    --source-max-files 4 --source-token-budget 14000 --max-tokens 8192 \
    --results-root dataset/fixtures/re2_ab/exp2_results

# Evaluation: per-corpus coverage on the seed_replay binary
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
    --out-dir   dataset/fixtures/re2_ab/ab_coverage \
    --upstream-root "$(pwd)/dataset/targets/src/re2/upstream" \
    --model llama-3.1-8b-instruct --target re2 \
    --exp1-seed-count 5 --exp2-seed-count 10
```

## Seed production (synthesis phase)

| experiment | samples | aborted by loop detector | unique seeds written |
|---|---:|---:|---:|
| exp1 gap-targeted | 3 | 3 (all) | **5** (salvaged from sample 2) |
| exp2 source-only  | 3 | 2 | **10** (sample 0 complete) |

The loop detector aborted 5 of 6 synthesis samples — classic llama-3.1-8b
degenerate-repetition failure. Salvaged five individual `{content_b64, …}`
objects from exp1 sample 2 (all six entries were near-duplicates of the
same ~48-byte payload; four were structurally complete and passed JSON
repair). Exp2 sample 0 fit in one non-degenerate response and produced
10 distinct seeds.

**This is a small-model artifact, not a pipeline bug.** A stronger model
would produce more seeds per sample.

## Coverage evaluation (seed_replay binary)

| metric | exp1 | exp2 | union | intersection |
|---|---:|---:|---:|---:|
| lines covered | 1243 | 1314 | 1337 | 1220 |
| edges covered | 475  | 513  | 530  | 458  |

- Edges reached **only** by exp1: **17**
- Edges reached **only** by exp2: **55**
- Jaccard (edges): 0.864
- Jaccard (lines): 0.913

## Where each experiment wins

Top files exp1 (gap-targeted) reaches that exp2 misses:

| file | unique edges |
|---|---:|
| `util/rune.cc`  | 10 |
| `re2/re2.cc`    |  4 |
| `re2/parse.cc`  |  1 |
| `re2/prog.h`    |  1 |
| `re2/onepass.cc`|  1 |

Top files exp2 (source-only) reaches that exp1 misses:

| file | unique edges |
|---|---:|
| `re2/parse.cc`  | 22 |
| `re2/regexp.cc` | 18 |
| `re2/compile.cc`|  5 |
| `re2/onepass.cc`|  4 |
| `re2/re2.cc`    |  4 |
| `re2/prog.h`    |  1 |
| `re2/dfa.cc`    |  1 |

## Interpretation

1. **Exp2 (source-only) wins on raw coverage at this scale** — 38 more
   edges, 71 more lines. Almost entirely driven by having 2× the seed
   count (10 vs 5) after salvage.
2. **Exp1 (gap-targeted) finds exclusive coverage in UTF-8 rune handling**
   (`util/rune.cc`, 10 unique edges) — something exp2 seeds don't hit.
   This is consistent with exp1's prompt highlighting the UTF-8 gap
   branches in `rune.cc`.
3. **Exp2's exclusive coverage concentrates in the parser/compiler path**
   (`parse.cc` + `regexp.cc` + `compile.cc` = 45 unique edges), which is
   unsurprising when the prompt is literally the regexp parser source.
4. **Jaccard 0.86 (edges)** says the two experiments reach mostly the
   same code. The differential is in the tails, not the core.

## What this does NOT establish

- **Fair seed-count parity**: we should hold seed count equal. Exp1
  produced 5 "real" seeds after loop-abort salvage; exp2 produced 10
  from a single non-aborted sample. A budget-matched rerun (either
  trim exp2 to 5 random seeds or sample more aggressively from exp1)
  is the first thing to fix before drawing any conclusion.
- **Statistical significance**: single run, 3 samples per cell, one
  target. Need ≥ 20 independent trials per cell for any p-value claim.
- **Fuzzer amplification**: this measures raw seed coverage, not
  24-hour libFuzzer-campaign coverage starting from each seed set. The
  whole point of a "seed corpus" is that it seeds a campaign — campaigns
  pending cluster time.
- **Stronger models**: llama-3.1-8b loops on 5/6 samples. Even one GPT-4o
  run would likely flip the seed-count ratio.

## Fixes landed this session (enabling this run)

- `dataset/scripts/build_instrumented.sh` — auto-detect libstdc++
  13 headers + gcc driver path when clang-15 defaults fail on Ubuntu 24.04.
- `dataset/scripts/run_test_coverage.py` — drop `--format=json`
  (llvm-cov-15 export produces JSON by default; the flag only accepts
  text/html).
- `core/dataset_schema.py` — add `populate_by_name=True` to the base
  `ConfigDict` so `CoverageProfile.model_dump_json()` → `model_validate()`
  round-trips (previously the writer emitted `true_taken`/`false_taken`
  field names but the reader only accepted the `true`/`false` aliases).
- `synthesis/scripts/parse_synthesis.py` — salvage individual
  `{content_b64, …}` objects when the outer JSON is truncated by a
  loop-detector abort. Without this, aborted samples produce zero seeds
  even when earlier entries are structurally complete.
- `synthesis/scripts/measure_coverage.py` — same llvm-cov-15 flag
  fix; added `--profile-out` and LLVM tool-path CLI overrides.
- `synthesis/scripts/generate_source_inputs.py` — expose
  `--samples`, `--source-max-files`, `--source-token-budget`,
  `--max-tokens` CLI flags (previously hard-coded to module defaults).
- `dataset/targets/src/re2/harness/seed_replay_main.cc` — new
  standalone driver so we can replay a seed corpus under coverage without
  building with `-fsanitize=fuzzer` (which injects main() and collides
  with gtest).
- `analysis/scripts/ab_coverage_diff.py` — new script; turns two
  `CoverageProfile` JSONs into set-wise line/edge diff + per-file table
  + Jaccard.

## Cost

`$0.0025` in unique LLM calls across Phase 2 (3) + Phase 3 exp1 (3) +
exp2 (3). Cache hits on replay: free.

## Next steps

1. ~~Re-run exp1 with a stronger model or more samples so the seed count
   matches exp2's.~~ **Done 2026-04-13** via regex-format rewrite; the
   loop rate dropped to 1/6 and exp1 now produces 20 seeds / 3 samples.
2. Add a "random-baseline" corpus (e.g., random bytes of matching
   length) so we can say whether either experiment meaningfully beats
   uniform random input.
3. Wire the libFuzzer campaign driver to each corpus and measure
   24-hour coverage divergence. That's the research-relevant number;
   this report is only seed-corpus quality.
4. Expand the test fixture once we have a fair single-target result.
5. Reproduce regex-format run with a stronger model (Claude Sonnet /
   GPT-4o) to confirm the gap-targeted advantage holds beyond llama-8b.

## Reproducibility for the 2026-04-13 regex-format run

```bash
# Phase 3 exp1 synthesis (gap-targeted, regex text)
UTCF_LITELLM_URL=https://api.ai.it.ufl.edu UTCF_LLM_RPM=12 \
.venv/bin/python -m synthesis.scripts.generate_inputs \
    --target re2 --model llama-3.1-8b-instruct --samples 3 --experiment exp1 \
    --input-format regex \
    --dataset-root dataset/fixtures/re2_ab \
    --results-root dataset/fixtures/re2_ab/phase3_results

# Phase 3 exp2 synthesis (source-only, regex text)
UTCF_LITELLM_URL=https://api.ai.it.ufl.edu UTCF_LLM_RPM=12 \
.venv/bin/python -m synthesis.scripts.generate_source_inputs \
    --target re2 --model llama-3.1-8b-instruct --samples 3 \
    --source-max-files 4 --source-token-budget 14000 --max-tokens 8192 \
    --input-format regex \
    --results-root dataset/fixtures/re2_ab/exp2_results

# Per-corpus coverage + differential — same commands as the bytes run above
# (both measure_coverage and ab_coverage_diff). Seeds are now 2 flag bytes +
# a UTF-8 regex body, total size 3-64 bytes.
```
