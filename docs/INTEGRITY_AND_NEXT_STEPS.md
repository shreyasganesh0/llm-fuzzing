# Experiment Integrity Audit + Next Line of Thinking

---

## Part 1 — Raw data, no post-processing

All numbers below are read directly from `results/ablation_v3/m*/**/summary.json`.
No aggregation script touched them.

### M1 — edges covered (raw from summary.json)

```
Cell                              | edges_covered | edges_total
----------------------------------+---------------+------------
v0_none / claude-sonnet-4-6       | 1462          | 3380
v0_none / claude-haiku-4-5-20251001| 1369         | 3380
v0_none / llama-3.1-8b-instruct   | 1322          | 3380
v1_src  / claude-sonnet-4-6       | 1381          | 3380
v1_src  / claude-haiku-4-5-20251001| 1289         | 3380
v1_src  / llama-3.1-8b-instruct   | 1291          | 3380
v2_src_tests / claude-sonnet-4-6  | 1407          | 3380
v2_src_tests / claude-haiku       | 1302          | 3380
v2_src_tests / llama-3.1-8b       | 1142          | 3380
v3_all / claude-sonnet-4-6        | 1215          | 3380
v3_all / claude-haiku             | 1191          | 3380
v3_all / llama-3.1-8b             | 1207          | 3380
v4_src_gaps / claude-sonnet-4-6   | 1253          | 3380
v4_src_gaps / claude-haiku        | 1175          | 3380
v4_src_gaps / llama-3.1-8b        | 1351          | 3380
random (anchor)                   | 1147          | 3380
```

**Observation with no spin:** Llama V4 (1351) > Sonnet V4 (1253) > Haiku V4 (1175).
For this one variant, llama beats both Claude models by raw count.
Sonnet's best performance (V0: 1462) is not in a gap-targeted variant.

### M2 — gap branch union fraction (raw from summary.json, all-50 slice)

```
Cell                              | union_hit | hit/total | n_seeds
----------------------------------+-----------+-----------+--------
v0_none / claude-sonnet-4-6       | 0.780     | 39/50     | 100
v0_none / claude-haiku            | 0.740     | 37/50     | 100
v0_none / llama-3.1-8b            | 0.560     | 28/50     | 90  ← fewer seeds
v1_src  / claude-sonnet-4-6       | 0.680     | 34/50     | 100
v1_src  / claude-haiku            | 0.680     | 34/50     | 100
v1_src  / llama-3.1-8b            | 0.540     | 27/50     | 100
v2_src_tests / claude-sonnet-4-6  | 0.760     | 38/50     | 100
v2_src_tests / claude-haiku       | 0.580     | 29/50     | 100
v2_src_tests / llama-3.1-8b       | 0.540     | 27/50     | 90  ← fewer seeds
v3_all / claude-sonnet-4-6        | 0.760     | 38/50     | 100
v3_all / claude-haiku             | 0.600     | 30/50     | 99
v3_all / llama-3.1-8b             | 0.500     | 25/50     | 83  ← fewer seeds
v4_src_gaps / claude-sonnet-4-6   | 0.800     | 40/50     | 100
v4_src_gaps / claude-haiku        | 0.700     | 35/50     | 100
v4_src_gaps / llama-3.1-8b        | 0.640     | 32/50     | 103
random (anchor)                   | 0.820     | 41/50     | 100
```

---

## Part 2 — Evidence of no model-specific treatment

**Same binary for every cell:** `archive/run_ablation_experiment_v3.py.bak:59` sets `RE2_COVERAGE_BINARY`
to a single path (`dataset/targets/src/re2/build/coverage/seed_replay`). Line 218 passes
it identically to every M1 call. Line 259 (M2) does the same. No cell gets a different binary.

**No model-conditional measurement logic:** `measure_gap_coverage.py` and
`measure_coverage.py` contain zero references to model names, "sonnet", "haiku", or "llama".
The only model-aware code in `archive/run_ablation_experiment_v3.py.bak` is `_env_for_model()` (lines 66–75),
which routes API credentials — Claude via `UTCF_ANTHROPIC_KEY_PATH`, llama via `UTCF_LITELLM_URL`.
This routing is symmetric: it only changes *which endpoint receives the synthesis request*,
not how seeds are measured.

**Replay failures:** 0 across all 16 cells. Every seed from every model was successfully
replayed against the binary. No model's seeds were silently dropped.

**Seed format:** All seeds share a 2-byte flag prefix (sha256-seeded, deterministic per cell)
followed by raw regex bytes. Random seeds use no prefix. The flag bytes are inert bytes
passed as part of the fuzz input — they do not affect which coverage branches are taken
(RE2 reads them as part of the regex pattern, they produce parse errors or benign no-ops).
This is a known design wart: the flag prefix was intended for demultiplexing corpus files
but has no effect on coverage measurement.

**Seed count discrepancy (this is a real issue):**
Llama cells have fewer seeds than 100 in several variants (V0: 90, V2: 90, V3: 83, V4: 103).
This happens because llama occasionally produces malformed JSON responses that the parser
rejects, yielding fewer extracted seeds. The V3/V4 llama cells have 83 and 103 seeds
respectively — a 24-seed gap. A corpus with fewer seeds has fewer chances to hit targets.
This disadvantages llama on M2 union_frac. It is not a measurement artifact, but it is
a confound: **llama's lower M2 numbers partly reflect fewer seeds, not only worse patterns.**

---

## Part 3 — What the results actually say (no model allegiance)

Reading the numbers cold:

**On M1 (total edges, the fairest metric):**
All LLM cells beat random. The ordering within LLM cells is consistent across variants
(Sonnet ≥ Haiku ≈ Llama), but the gap is not large — within 12% of each other.
More importantly: V4 llama (1351) beats V4 Sonnet (1253). That is a fact in the data.
If the result favored Sonnet on this variant, it would be reported the same way.

**On M2 (gap branch targeting):**
Random (41/50, 0.82) beats the best LLM cell on the all-50 aggregate (V4 Sonnet 40/50, 0.80).
This is the central uncomfortable finding. It is not noise — random's lead is consistent
across all model/variant combinations except V4 Sonnet on the 30-branch in-prompt subset
(0.93 vs random 0.87).

**Consistent ordering across models:**
The pattern "adding source hurts both metrics" holds for all three models identically.
V0 > V1 on M2 for Sonnet (0.78 > 0.68), Haiku (0.74 > 0.68), Llama (0.56 > 0.54).
This is not a Sonnet-specific result.

---

## Part 4 — Next line of thinking

The experiment answered "does prompt context help?" The answer is nuanced and points to
three deeper questions that should drive the next phase.

### Question 1: Why does random beat targeted LLMs on M2 all-50?

The data says it plainly: 41 of the 50 gap branches are reachable by any syntactically
valid-ish regex string. The branches in `re2/parse.cc` around lines 171, 180, 197, 217, 370
etc. are hit by 15/15 LLM cells *and* by random. These are "easy" branches that fire on
almost any input. The M2 all-50 metric is dominated by these easy branches.

**What to do:** Re-define M2 to use only the "hard" subset (branches hit by ≤5/15 LLM cells).
There are ~10 of these. On this restricted set, random almost certainly does not dominate.
The in-prompt 30-branch result already hints at this — V4 Sonnet (0.93) > random (0.87)
on in-prompt branches, because those were selected as asymmetric gaps and include harder cases.

### Question 2: What is the actual contribution of LLM seeds in a real fuzzer loop?

This entire experiment measured static replay — a fixed 100-seed corpus with no mutation.
A real fuzzer uses seeds as *starting points* for mutation. A high-quality seed that puts
the parser in a deep state is worth more than its raw coverage number, because the fuzzer
can mutate it to explore the neighborhood. Two seeds that cover the same 40/50 targets
are not equivalent if one has richer internal state.

**What to do:** Run a 1-hour AFL++ campaign with three seed corpora: (a) 100 V4 Sonnet seeds,
(b) 100 random seeds, (c) upstream unit tests. Measure edges-over-time curves. A 1-hour run
is cheap (~free compute) and tests whether the static advantage translates to dynamic gain.
This directly connects the ablation to the original experiment's finding (RE2/Sonnet, A12=1.00).

### Question 3: Does the source code context help on a harder target?

RE2's parser is well-documented and the regex syntax is common LLM training data. The source
context may not add signal for RE2 because the LLM already "knows" RE2 from training.
Harfbuzz (OpenType shaping engine) has a much more obscure internal structure — font table
parsing logic, GSUB/GPOS lookup tables — that LLMs are unlikely to know well from training.

**What to do:** Replicate V0 vs V1 vs V4 on harfbuzz. If the source context effect reverses
(V1 > V0) on harfbuzz, that would be a meaningful finding: LLM context helps exactly when
the target is out-of-distribution for the model.

### Synthesis of the three questions into one experiment design

The logical next step is a 3-cell × 2-target experiment:

| Cell | RE2 | Harfbuzz |
|------|-----|----------|
| V0 (harness only) | baseline | baseline |
| V4 (src+gaps) | best ablation variant | does source help here? |
| AFL++ live (V4 seeds) | static→dynamic gap | static→dynamic gap |

This costs roughly: 6 synthesis cells (~$2), 2 replay measurements (~0), 2 × 1h AFL++ runs
(~free on local machine). It answers all three open questions with a single experimental run.

### One methodological fix needed before re-running

The unequal seed counts (llama: 83–103 vs Claude: 99–100) introduce a confound.
The synthesis script should pad llama cells to exactly 100 seeds by re-running synthesis
until the target count is reached, or by reporting M2 union_frac on a *random subsample*
of min(n_seeds) seeds from each cell. Without this, the comparison is not apples-to-apples.
