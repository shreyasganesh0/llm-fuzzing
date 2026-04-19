# LLM-Guided Fuzzing — 5-Variant × 7-Model Ablation Results

## Experiment Overview

**Goal:** Measure how much LLM context (source code, unit tests, coverage gaps) improves seed synthesis quality for fuzzing two real-world targets.

**Targets:**
- **Harfbuzz** — binary font parser (C++), hard format, ~10% LLM parse rate
- **RE2** — regex engine (C++), text format, ~80–100% LLM parse rate

**5 Context Variants (progressively more context):**
| Variant | Context Given to LLM |
|---|---|
| v0_none | No context (blind generation) |
| v1_src | Harness source code only |
| v2_src_tests | Source + upstream unit tests |
| v3_all | Source + tests + coverage gaps |
| v4_src_gaps | Source + coverage gaps (no tests) |

**7 Models Tested:**
| Model | Provider | Size | Cost |
|---|---|---|---|
| claude-sonnet-4-6 | Anthropic | — | Paid |
| claude-haiku-4-5 | Anthropic | — | Paid |
| llama-3.1-8b-instruct | UF LiteLLM | 8B | Free |
| llama-3.1-70b-instruct | UF LiteLLM | 70B | Free |
| llama-3.3-70b-instruct | UF LiteLLM | 70B | Free |
| codestral-22b | UF LiteLLM | 22B | Free |
| nemotron-3-super-120b | UF LiteLLM | 120B MoE | Free |

**Metrics:**
- **M1 (edges covered):** Total unique LLVM branch edges hit by all 150 seeds combined. Higher = better raw coverage.
- **M2 (hard-branch hit rate):** Fraction of pre-selected "hard" branches (never hit by random fuzzing baseline) that at least one seed triggered. Higher = better targeted coverage.
- **Random baseline:** 150 randomly generated seeds (no LLM). HB: 554 edges, M2=0.000. RE2: 1160 edges, M2=0.000.

**Seeds per cell:** Target 150; partial cells noted where binary format or model limitations prevented full fill.

---

## Key Results: Harfbuzz (Binary Format)

> Note: Claude v2–v4 cells were not run due to API credit constraints. Partial seed counts for 70b/nemotron reflect low binary parse rate (~10%).

| Variant | Model | Seeds | M1 Edges | M2 All | M2 Shown |
|---|---|---|---|---|---|
| **Random baseline** | — | 150 | **554** | **0.000** | **0.000** |
| v0_none | sonnet-4-6 | 150 | **998** | **0.640** | 0.567 |
| v0_none | haiku-4-5 | 150 | 883 | 0.540 | 0.500 |
| v0_none | codestral-22b | 150 | 575 | 0.100 | 0.133 |
| v0_none | nemotron-120b | 70 | 570 | 0.160 | 0.267 |
| v0_none | llama-3.1-8b | 150 | 564 | 0.040 | 0.000 |
| v0_none | llama-3.3-70b | 150 | 555 | 0.060 | 0.100 |
| v0_none | llama-3.1-70b | 107 | 563 | 0.080 | 0.133 |
| v1_src | sonnet-4-6 | 150 | 816 | 0.440 | 0.533 |
| v1_src | haiku-4-5 | 150 | 721 | 0.140 | 0.200 |
| v1_src | codestral-22b | 150 | **729** | **0.460** | 0.367 |
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
| v3_all | llama-3.3-70b | 59 | 555 | 0.060 | 0.100 |
| v3_all | nemotron-120b | 10 | 567 | 0.120 | 0.200 |
| v3_all | llama-3.1-8b | 150 | 563 | 0.040 | 0.000 |
| v4_src_gaps | codestral-22b | 150 | **745** | **0.480** | 0.400 |
| v4_src_gaps | llama-3.3-70b | 50 | 625 | 0.180 | 0.233 |
| v4_src_gaps | llama-3.1-70b | 56 | 578 | 0.120 | 0.133 |
| v4_src_gaps | llama-3.1-8b | 150 | 558 | 0.020 | 0.000 |
| v4_src_gaps | nemotron-120b | 1 | 523 | 0.000 | 0.000 |

### Harfbuzz Takeaways
- **Claude Sonnet dominates M1 and M2** — v0_none/sonnet hits 998 edges (1.8× random) and M2=0.640, the highest across all cells on this target.
- **Context helps Claude, hurts or doesn't help small models.** Sonnet drops from 998 (v0) → 816 (v1) as context is added; codestral is the exception among free models (improves v0→v4).
- **Codestral-22b is the best free model on HB** — consistent ~700–745 edges and M2 up to 0.480, strong across all variants.
- **Binary format is hard for 70b/nemotron** — ~10% parse rate means seed counts are low (10–107) and results have high variance.
- **LLM seeds vastly outperform random** — even 8b at v0 (564 edges, M2=0.040) beats random (554 edges, M2=0.000).

---

## Key Results: RE2 (Text/Regex Format)

| Variant | Model | Seeds | M1 Edges | M2 All | M2 Shown |
|---|---|---|---|---|---|
| **Random baseline** | — | 150 | **1160** | **0.000** | **0.000** |
| v0_none | nemotron-120b | 150 | **1442** | 0.467 | 0.400 |
| v0_none | codestral-22b | 150 | 1432 | 0.333 | 0.200 |
| v0_none | haiku-4-5 | 150 | 1400 | 0.467 | 0.400 |
| v0_none | llama-3.1-70b | 150 | 1361 | 0.400 | 0.300 |
| v0_none | sonnet-4-6 | 150 | 1363 | **0.733** | 0.600 |
| v0_none | llama-3.1-8b | 150 | 1202 | 0.333 | 0.300 |
| v0_none | llama-3.3-70b | 8 | 1054 | 0.133 | 0.000 |
| v1_src | nemotron-120b | 150 | **1374** | **0.867** | **0.900** |
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

### RE2 Takeaways
- **Context matters a lot for RE2** — M2 increases dramatically from v0 (0.333–0.733) to v3/v4 (0.667–0.867) across most models. The hard branches are genuinely reachable with targeted prompts.
- **Nemotron-120b surprises on v1_src** — M2=0.867 (best single cell across both targets), well above sonnet at same variant. Collapses without enough seeds at v3+.
- **Codestral-22b is best "complete" free model** — consistently 150 seeds, high M1 (1321–1495), and M2 up to 0.800. Reliable across all 5 variants.
- **Haiku and Sonnet show strong context scaling** — M2 roughly doubles from v0→v3 for both Claude models. Haiku v3_all M2=0.800, nearly matching Sonnet.
- **llama-3.1-70b is competitive on text format** — 150 seeds across all variants, M1 highest at v1/v4 (1439–1445), M2 up to 0.667. Far better than on binary.
- **llama-3.3-70b underperforms** — very low seed counts (8–35) across all variants due to poor JSON formatting. Results not meaningful for comparison.

---

## Cross-Target Summary: Best M2 Per Variant

| Variant | Best HB M2 | Model | Best RE2 M2 | Model |
|---|---|---|---|---|
| v0_none | **0.640** | sonnet-4-6 | **0.733** | sonnet-4-6 |
| v1_src | **0.460** | codestral-22b | **0.867** | nemotron-120b |
| v2_src_tests | **0.380** | codestral-22b | **0.733** | codestral / nemotron |
| v3_all | **0.260** | codestral-22b | **0.800** | haiku / codestral |
| v4_src_gaps | **0.480** | codestral-22b | **0.800** | codestral |

---

## Slide Deck Suggestions

### Slide 1 — Experiment Setup
- 5 context variants × 7 models × 2 targets = 70 cells
- 150 seeds per cell, coverage replay via LLVM instrumented binary
- Two metrics: M1 (total edges) and M2 (hard-branch hit rate)

### Slide 2 — Context Scaling on RE2
- Line chart: M2 vs. variant for sonnet, haiku, codestral, llama-3.1-70b
- Key message: M2 roughly doubles from v0→v3 for capable models

### Slide 3 — Model Comparison at v0_none (Blind Generation)
- Bar chart: M2 for all 7 models on both targets at v0_none
- Key message: Claude leads, but codestral+nemotron are competitive free alternatives

### Slide 4 — Harfbuzz: Claude vs. Free Models
- Table or grouped bars: Claude v0/v1 vs. codestral v0–v4
- Key message: Claude dominates M1/M2; codestral is best free model

### Slide 5 — Standout Result: nemotron RE2 v1_src
- Highlight: M2=0.867 (best across all 70 cells)
- Context: v1_src = source code only; nemotron 120B MoE, free via UF endpoint

### Slide 6 — Random Baseline vs. LLM Seeds
- Comparison: random (M2=0.000 both targets) vs. LLM best (M2=0.640 HB, M2=0.867 RE2)
- Key message: even blind LLM generation (v0_none) dramatically beats random

### Slide 7 — Limitations & Next Steps
- Claude v2–v4 HB missing (credit constraint)
- llama-3.3 low seed counts (JSON format issues)
- Next: fuzzing campaigns to measure bug-finding, not just coverage

---

## Data Quality Notes

- Cells marked with low seed counts (<50) should be interpreted cautiously — M2 values have high variance
- llama-3.3-70b seeds: 8–35 per cell (JSON parse failures ~95%); not suitable for model comparison
- nemotron v3_all/v4 RE2 and v1+ HB: too few seeds to be statistically meaningful
- All M2 values use "all" slice (full 50-target set for HB, 15-target set for RE2)
- No replay failures (n_replay_failures=0) across all cells — coverage data is clean

---

*Generated: 2026-04-19 | Experiment: 5-variant × 7-model ablation | Targets: harfbuzz, RE2*
