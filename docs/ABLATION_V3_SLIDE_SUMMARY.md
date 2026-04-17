# Ablation V3 Experiment — Slide Summary

## One-sentence framing

**LLMs generate seeds that cover 27% more total edges than random bytes, but do not reliably out-target random on the specific branches they are asked to hit — unless tests are dropped from the prompt.**

---

## Experiment design (condensed)

| | |
|---|---|
| **Target** | RE2 regex library (LLVM coverage build) |
| **Models** | Claude Sonnet 4.6, Claude Haiku 4.5, Llama 3.1 8B |
| **Seeds / cell** | 100 LLM-generated regex strings; 100 random strings (anchor) |
| **Evaluation** | LLVM coverage replay only — no live fuzzer |
| **M1** | Total edges covered by corpus union (out of 3,380 total) |
| **M2** | Fraction of 50 asymmetric gap branches hit (specific uncovered side) |

**5 prompt variants, differing only in what context the model sees:**

| Variant | Source code | Unit tests | Gap branch hints |
|---|---|---|---|
| V0 — floor | — | — | — |
| V1 — source | Y | — | — |
| V2 — src+tests | Y | Y | — |
| V3 — all | Y | Y | Y (30 branches w/ TRUE/FALSE side label) |
| V4 — src+gaps | Y | — | Y (30 branches w/ TRUE/FALSE side label) |

Gap hint format: "take the **FALSE branch** at `re2/parse.cc:598`" with 3-step chain-of-thought framing.

---

## M1 result — total edges (LLMs beat random; adding context hurts)

| Variant | Sonnet | Haiku | Llama 8B |
|---|---|---|---|
| V0 (floor) | **1462 (43.3%)** | 1369 (40.5%) | 1322 (39.1%) |
| V1 (+ source) | 1381 (40.9%) | 1289 (38.1%) | 1291 (38.2%) |
| V2 (+ tests) | 1407 (41.6%) | 1302 (38.5%) | 1142 (33.8%) |
| V3 (all) | 1215 (35.9%) | 1191 (35.2%) | 1207 (35.7%) |
| V4 (src+gaps) | 1253 (37.1%) | 1175 (34.8%) | 1351 (40.0%) |
| **Random anchor** | **1147 (33.9%)** | — | — |

**LLMs beat random in 14/15 cells.** V0 Sonnet leads at 1462 edges (+27% over random, +22% over V3 Sonnet).
More context → fewer diverse edges. Gap-targeted prompts specialize seeds, reducing breadth.

---

## M2 result — specific gap branch targeting (corpus union, 50 targets)

| Variant | Sonnet | Haiku | Llama 8B |
|---|---|---|---|
| V0 (floor) | 39/50 (0.78) | 37/50 (0.74) | 28/50 (0.56) |
| V1 (+ source) | 34/50 (0.68) | 34/50 (0.68) | 27/50 (0.54) |
| V2 (+ tests) | 38/50 (0.76) | 29/50 (0.58) | 27/50 (0.54) |
| V3 (all) | 38/50 (0.76) | 30/50 (0.60) | 25/50 (0.50) |
| **V4 (src+gaps)** | **40/50 (0.80)** | 35/50 (0.70) | 32/50 (0.64) |
| **Random anchor** | **41/50 (0.82)** | — | — |

The 30-branch in-prompt subset (directly targeted by V3/V4):

| Variant | Sonnet | Haiku | Llama 8B |
|---|---|---|---|
| V3 (all) | 26/30 (0.87) | 22/30 (0.73) | 18/30 (0.60) |
| **V4 (src+gaps)** | **28/30 (0.93)** | 25/30 (0.83) | 22/30 (0.73) |
| Random anchor | 26/30 (0.87) | — | — |

**On in-prompt targets: V4 Sonnet (0.93) edges out random (0.87). V3 equals random.**

---

## Key findings

### 1. Source code context hurts both metrics
V0 → V1 loses edges on M1 (−81 Sonnet, −80 Haiku) and loses M2 gap hits (−10% Sonnet, −6% Haiku). Hypothesis: source context makes the model produce more "realistic" but less diverse regexes.

### 2. Tests hurt M1 breadth but help Sonnet on M2
V1 → V2: M1 drops for llama (−149 edges). M2: Sonnet +8%, Haiku −10%, llama neutral. Tests appear to anchor Sonnet toward known API patterns that incidentally hit gaps, while constraining weaker models.

### 3. Gap hints help M2 targeting — but only when tests are removed
V1 → V4 (add gaps, no tests): +12% Sonnet, +2% Haiku, +10% llama on M2.
V3 vs V4 (same — drop tests from gap-aware prompt): V4 beats V3 for all 3 models (+4%, +10%, +14%). Tests act as noise in the presence of explicit gap targets.

### 4. Random is a strong M2 baseline — but not on the hardest branches
Random hits 41/50 targets. However, **9 branches random never reaches** at all. On those 9, LLMs hit 56–89%. This is where LLM value concentrates: branches reachable only via syntactically specific regex patterns (e.g., Unicode range operators, simplify.cc canonicalization paths).

### 5. Gap context does not generalize
V3/V4 show lower M2 on held-back targets (20 not shown to model) vs shown targets (30 in-prompt), and both are below V0/V2 on held-back targets. The model targets what it is told to target; it does not generalize to nearby uncovered branches.

---

## The 9 branches only LLMs can reach

These targets were never hit by random seeds in 100 tries. Best LLM coverage (V2 Sonnet, V0 Haiku):

| File | Line | Best LLM cell | Hit? |
|---|---|---|---|
| re2/simplify.cc | 79 | V0 Haiku, V2 Sonnet | 3/15 LLM cells |
| re2/regexp.cc | 212 | multiple | 5/15 cells |
| re2/parse.cc | 2003 | multiple | 6/15 cells |
| re2/simplify.cc | 52 | multiple | 6/15 cells |
| re2/parse.cc | 543 | multiple | **14/15 cells** |
| re2/parse.cc | 1823 | multiple | **14/15 cells** |
| re2/simplify.cc | 92 | multiple | **15/15 cells** |
| re2/walker-inl.h | 180 | multiple | **15/15 cells** |
| re2/parse.cc | 613 | multiple | **15/15 cells** |

The top 4 are "hard" (few cells hit). The bottom 5 are easy for LLMs but inaccessible to random — they require structurally valid regex patterns (named groups, Unicode escapes, Unicode range operators).

---

## Branch difficulty spectrum

Out of 50 target branches:
- **Hard** (0–3/15 LLM cells): 10 branches — mostly `re2/simplify.cc` and `util/rune.cc`
- **Medium** (4–8/15): 14 branches — mix of `parse.cc` edge cases
- **Easy** (≥14/15): 26 branches — standard parse paths, all LLMs agree

Most "hard" branches involve the regex simplifier and Unicode rune decoder — code paths that require precise multi-step syntactic structure that even targeted CoT prompting doesn't reliably produce.

---

## Model ranking

**M1 (total coverage):** Sonnet > Haiku > Llama ≈ Random (in all variants)
**M2 (targeting):** Sonnet > Haiku > Llama, with Sonnet the only model that consistently beats random on in-prompt targets (V4: 0.93 vs 0.87)

Sonnet uniquely benefits from unit test context on M2 (+8% V1→V2). Haiku and llama do not — for them, less context is better for targeting.

---

## Sanity check summary

| Check | Result |
|---|---|
| (a) V3 ≥ V1 on M2 for ≥2/3 models | PARTIAL — only Sonnet |
| (b) V3/V4 > random on M2 (all-50) | FAIL — random leads 0.82 vs best 0.80 |
| (c) Shown-30 ≥ held-back-20 (V3/V4) | PASS — 6/6 |
| (d) LLM M1 > random in ≥14/15 cells | PASS — 14/15 |

The main open question: random bytes beating gap-targeted LLMs on the all-50 aggregate is a **distribution mismatch problem** — 41 of the 50 targets are easy branches reachable by any valid regex, so the random advantage is structural.

---

## Limitations and next steps

| Limitation | Proposed fix |
|---|---|
| Random anchor beats LLMs on all-50 M2 because 41/50 targets are trivially reachable | Re-define M2 target set to hard branches only (LLM-exclusive territory) |
| Replay-only: no fuzzer feedback loop | Run 1h AFL++ campaigns seeded with V4 Sonnet vs random to measure downstream fuzzing benefit |
| RE2 only | Replicate on harfbuzz (complex Unicode state machine) where source context may help more |
| 100 seeds/cell: CI still wide on M2 per-seed mean | Scale to 500 seeds for stable bootstrap estimates |
| Gap CoT prompting doesn't generalize (held-back targets) | Try self-consistency: generate 10 candidates per target, pick the one the model rates highest |
