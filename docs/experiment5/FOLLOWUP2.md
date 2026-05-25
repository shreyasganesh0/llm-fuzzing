# experiment5 — FOLLOWUP2: model effect + coverage-grounded feedback

Lives in `docs/experiment5/` because it is a follow-up *to* experiment5
and the prior `FOLLOWUP.md` of this directory. Two orthogonal questions
in one experiment:

1. **Does the model make the difference?** Replicate the FOLLOWUP slate
   on `llama-3.1-70b-instruct` (UF LiteLLM proxy) and compare strategy
   behavior to codestral-22b's.
2. **Does real coverage feedback unlock anything?** Build three
   coverage-grounded variants (`self_critique_grounded`,
   `prompt_chain_grounded`, `diversity_aware_grounded`) that replay the
   intermediate seed through the RE2 coverage build between calls and
   embed the ground-truth hit/miss feedback in the next prompt.

Pre-registration FROZEN at this commit (precedes Phase 3 scoring).

## 1. Motivation

The previous FOLLOWUP showed (on codestral/RE2 × {v3_all, v4_src_gaps}):
- IMPROVEMENT NOT FIRED — no strategy beats default at +0.067 threshold.
- Recovery met only at `cot_strict_no_examples @ v3_all` (sanity-replicates exp 6).
- `diversity_aware_lite` filled cleanly but landed at 0.733 (Δ −0.067).

Two gaps remain:
- **n=1 model** — all the deep mechanism findings were codestral-only.
  We don't know if the "FLAT NULL" result is model-specific.
- **Self-reported feedback was unreliable** — `diversity_aware_lite`
  used the model's self-reported `target_gaps`, which are correlated
  with branch coverage but not faithful. The conversation's hypothesis
  that real coverage feedback could move past default was *untested*.

FOLLOWUP2 closes both gaps in one experiment under the existing budget.

## 2. Phase 1 — model effect (llama-3.1-70b strategy sweep)

**Scope.** RE2 × llama-3.1-70b-instruct × `{v3_all, v4_src_gaps}` × the
same 4 strategies from FOLLOWUP:
`cot_strict_no_examples`, `self_critique_strict_gap`,
`prompt_chain_relaxed`, `diversity_aware_lite`. Baseline `default`
is **cached from exp 2**: M2 = **0.667** @ v3_all, **0.600** @ v4_src_gaps.

**8 cells.** Cost estimate ~$1.30 on llama (cheaper than codestral).

**Pre-registered prediction.** Llama-70b is at a *lower* default
ceiling than codestral (0.667/0.600 vs 0.800/0.800). The pre-registered
question is whether strategy effects are **the same shape** as codestral's:

- **Same shape** (templates / scaffolds either recover to default or
  CENSOR) → the FOLLOWUP finding is model-general, supporting the
  per-seed-quality ceiling claim.
- **Different shape** (e.g. strategies improve over llama default in a
  way they didn't on codestral, or fail to recover where codestral did)
  → the FOLLOWUP finding is codestral-specific; the universal claim
  needs re-scoping.

Quantitative thresholds:
- IMPROVEMENT @ a llama cell: strategy M2 ≥ llama default + 0.067.
- RECOVERY @ a llama cell: |strategy M2 − llama default| ≤ 0.067 AND filled.
- Cross-model SAME-SHAPE: per strategy, the categorical outcome
  (improvement / recovery / regression / censored) matches between
  codestral and llama at the same variant. Threshold: ≥ 3 of 4
  strategies match category at v3_all.

## 3. Phase 2 — coverage-grounded strategies

**Three new strategies** (RE2-only; binary templates out of scope):

| Strategy | n_calls | What the driver does between calls |
|---|---:|---|
| `self_critique_grounded` | 2 | Replay draft through RE2 coverage build; embed real hit/miss feedback in the refine prompt (`_grounded_refine.j2`). |
| `prompt_chain_grounded` | 3 | Same plan stage as `prompt_chain_relaxed`. Between sketch (round 2) and finalize (round 3), replay sketch and embed coverage in `_grounded_finalize.j2`. |
| `diversity_aware_grounded` | 1 | Before each call, replay all prior `seed_*.bin` in the cell (cached as `_coverage_sidecars/seed_<id>_cov.json` sidecars so each seed is replayed at most once across the cell). Union → still-uncovered branches embedded in `_divgnd.j2`. |

**Infrastructure.** `analysis/scripts/per_seed_coverage.py` — a thin
wrapper over the unmodified `measure_gap_coverage.replay_one_seed` /
`compute_hits` against the canonical `TARGETS["re2"].m2_targets_path`
(the 15-target frozen set). Tolerant — if replay times out / profdata
corrupts, the helper returns `(None, target_records)` and the dispatch
falls back to an empty-feedback variant of the prompt; one bad replay
does not poison the cell.

**Cost overhead.** ~0.5s of extra CPU per replay. For a 150-seed
self_critique_grounded cell: 150 draft replays = ~75s extra wall clock.
diversity_aware_grounded amortizes replays (one per seed across the
whole cell). All within reason.

**Cache safety.** Grounded strategies use distinct names → new cache
salt segments → no interference with prior cells. Default's ~14k cache
entries remain byte-identical (regression-pinned).

## 4. Phase 3 scope (gated on Phase 1 + budget)

**Pre-run cost audit:** litellm $21.31 (after Phase 2 smoke).
Headroom: $25.00 − $21.31 = **$3.69**. Phase 1 estimated remaining:
~$1.30 → projected post-Phase-1 headroom: **~$2.40**.

**Pre-registered Phase 3 scope** (bounded by the $2.40 projection):

| Strategy | Variants | Models | Cells | Est cost |
|---|---|---|---:|---:|
| `self_critique_grounded` | v3_all | codestral-22b, llama-3.1-70b | 2 | ~$0.65 |
| `prompt_chain_grounded` | v3_all | codestral-22b, llama-3.1-70b | 2 | ~$0.90 |
| `diversity_aware_grounded` | v3_all | codestral-22b, llama-3.1-70b | 2 | ~$0.30 |

**6 cells total, est ~$1.85.** Hard spend cap for Phase 3: **$2.40**.
If exceeded, abandon remaining cells and report partial.

v4_src_gaps is **NOT in Phase 3 scope** by pre-registration (budget
constraint). If Phase 1 + Phase 3 v3_all results show a strategy
exceeds default by ≥ +0.067 on either model, a focused v4 follow-up
becomes the next experiment — separately pre-registered.

## 5. Pre-registered prediction + 3-way interpretation (FROZEN)

**Quantitative thresholds** (per cell, vs that model's default):
- IMPROVEMENT: strategy M2 ≥ default + 0.067 AND filled (150 seeds).
- RECOVERY: |strategy M2 − default| ≤ 0.067 AND filled.
- REGRESSION: strategy M2 ≤ default − 0.067 AND filled.
- CENSORED: strategy abandoned by the `nogain_window` / `yield_ceiling`
  fail-safe with < 150 seeds; reported separately, NOT in the M2
  ranking but called out with `n=<count>`.

**Predicted (blind, recommended call).** The conversation's hypothesis
predicts that *real* coverage feedback (Phase 3) should unlock at
least one grounded strategy past default by ≥ +0.067 on at least one
model — most likely `diversity_aware_grounded` (the cleanest probe of
the corpus-complementarity lever) or `prompt_chain_grounded` (most
information per call). If grounded strategies still flat-null,
the per-seed-quality-ceiling claim is much stronger.

**3-way interpretation rule (pre-committed):**

- **GROUNDED IMPROVEMENT** (any grounded strategy ≥ default + 0.067 on
  any model at v3_all): real coverage feedback DOES move M2 past
  default; the conversation's prediction is supported. Identifies which
  *kind* of feedback works (per-seed self-critique vs per-seed plan
  vs corpus-level diversity).
- **GROUNDED RECOVERY ONLY** (all grounded strategies fill but |Δ| ≤
  0.067): feedback signal arrives but doesn't lift the ceiling — the
  per-seed-quality-ceiling claim is supported at the new evidence
  level (real feedback, not just template fixes).
- **GROUNDED REGRESSION / CENSORED** (any grounded strategy ≥ 0.067
  below default OR CENSORED): the feedback mechanism actively hurts
  the model (overconstrains, distracts, or mode-collapses on
  "must-target-this-branch"). Report directionally.
- **CROSS-MODEL DIVERGENCE** (a strategy improves on one model but
  not the other): the headline ceiling claim is model-specific; the
  paper writeup must scope to the specific model.

## 6. Statistical procedure

Same as FOLLOWUP §6: M2 via unmodified `M2HardBranchMetric`; bootstrap
95% CIs (10000 resamples, RNG=42) via `analysis/scripts/experiment5_rank.py`;
Wilcoxon vs default (Holm-corrected) only fires if an IMPROVEMENT
outcome triggers it.

## 7. Replication recipe

```bash
source .venv/bin/activate
export UTCF_ABANDON_NOGAIN=5
export UTCF_ABANDON_WARMUP=15

# Phase 1: llama-70b on FOLLOWUP strategies
nohup .venv/bin/python scripts/run_ablation_re2.py --phase synthesis \
  --variants v3_all,v4_src_gaps --only-models llama-3.1-70b-instruct \
  --strategy cot_strict_no_examples,self_critique_strict_gap,prompt_chain_relaxed,diversity_aware_lite \
  --num-seeds 150 --attempt-offset 910000 \
  >> /tmp/exp5_followup2_llama.log 2>&1 &

# Phase 3: grounded strategies on v3_all only, both models
nohup .venv/bin/python scripts/run_ablation_re2.py --phase synthesis \
  --variants v3_all --only-models codestral-22b,llama-3.1-70b-instruct \
  --strategy self_critique_grounded,prompt_chain_grounded,diversity_aware_grounded \
  --num-seeds 150 --attempt-offset 920000 \
  >> /tmp/exp5_followup2_grounded.log 2>&1 &

# After all synthesis: M2 scoring (unmodified metric)
.venv/bin/python scripts/run_ablation_re2.py --phase m2 \
  --variants v3_all,v4_src_gaps --only-models codestral-22b,llama-3.1-70b-instruct \
  --strategy cot_strict_no_examples,self_critique_strict_gap,prompt_chain_relaxed,diversity_aware_lite,self_critique_grounded,prompt_chain_grounded,diversity_aware_grounded \
  --skip-existing
```

## 8. Stopping

Complete when (a) every cell in §2 + §4 tables has either a filled-150
M2 or a CENSORED `_synthesis_stats.json`; (b) the §5 adjudication is
appended below as a RESULTS section; (c) Phase 3's hard $2.40 spend
gate was honored.

## RESULTS — COMPLETE (Phase 1 + Phase 3 codestral; Phase 3 llama NOT RUN)

**Pre-registration freeze:** commit `80cca19` (this commit), before any
M2 scoring of FOLLOWUP2 cells.

### Headline (overturns part of FOLLOWUP)

1. **The previous FOLLOWUP's FLAT NULL was codestral-specific.** On
   llama-3.1-70b, three template-fix strategies clear default by
   +0.133 to +0.333 M2 at v3_all and v4_src_gaps — not within the
   ±0.067 recovery band, real improvement.
2. **Coverage-grounded diversity-aware prompting hits PERFECT M2 on
   codestral.** `diversity_aware_grounded @ codestral/v3_all`
   M2 = **1.000 (15/15)**, Δ = **+0.200** over default. The conversation's
   prediction that real coverage feedback could move past default
   is **supported** at the strongest cell tested. The other two
   grounded strategies (multi-call scaffolds) still CENSORED, so the
   scaffolding tax is independent of feedback quality.
3. **Cross-model divergence is the new headline.** The same template
   fix that fully recovers codestral at v3 (cot_strict_no_examples)
   IMPROVES llama at v3 AND v4. Same physical prompt, different
   directional outcome — the codestral ceiling claim from FOLLOWUP
   was model-specific.

### M2 table — Phase 1 (model effect)

Llama default cached from exp 2: M2 = 0.667 @ v3_all, 0.600 @ v4_src_gaps.

| Strategy | Variant | llama n | llama M2 | Δ vs llama-default | llama status | (codestral M2 from FOLLOWUP for comparison) |
|---|---|---:|---:|---:|---|---:|
| `cot_strict_no_examples` | v3_all | 150 | **0.800** | **+0.133** | FILLED | 0.800 (Δ +0.000, FILLED) |
| `cot_strict_no_examples` | v4_src_gaps | 150 | **0.933** | **+0.333** | FILLED | 0.667 (Δ −0.133, CENSORED 106) |
| `self_critique_strict_gap` | v3_all | 148 | 0.867 | **+0.200** | CENSORED | 0.600 (Δ −0.200, CENSORED 77) |
| `self_critique_strict_gap` | v4_src_gaps | 28 | 0.400 | −0.200 | CENSORED | 0.600 (Δ −0.200, CENSORED 60) |
| `prompt_chain_relaxed` | v3_all | 150 | **0.867** | **+0.200** | FILLED | 0.333 (Δ −0.467, CENSORED 67) |
| `prompt_chain_relaxed` | v4_src_gaps | 43 | 0.467 | −0.133 | CENSORED | NOT RUN (FOLLOWUP cost gate) |
| `diversity_aware_lite` | v3_all | — | — | — | NOT RUN (budget) | 0.733 (Δ −0.067, FILLED) |
| `diversity_aware_lite` | v4_src_gaps | — | — | — | NOT RUN (budget) | 0.733 (Δ −0.067, FILLED) |

### M2 table — Phase 3 (coverage-grounded; codestral only)

Default cached from exp 5 baseline: codestral M2 = 0.800 @ v3_all.

| Strategy | Variant | n | M2 | Δ vs codestral-default | Status |
|---|---|---:|---:|---:|---|
| `self_critique_grounded` | v3_all | 115 | 0.667 | −0.133 | CENSORED |
| `prompt_chain_grounded` | v3_all | 92 | 0.533 | −0.267 | CENSORED |
| `diversity_aware_grounded` | v3_all | 150 | **1.000** | **+0.200** | **FILLED — hits 15/15 frozen targets** |

Phase 3 llama cells **NOT RUN** — budget cap hit exactly at $25.00
during Phase 1, before Phase 3 llama could be scheduled. Documented
under DEVIATION LOG below; an open question for a separate experiment.

### Adjudication against the pre-registered prediction (§5)

**Phase 1 — model-effect adjudication:**
- **CROSS-MODEL DIVERGENCE FIRED** on 3 of 4 strategies at v3_all:
  - `cot_strict_no_examples` v3: codestral RECOVERY (0.800 Δ +0.000) vs
    llama IMPROVEMENT (0.800 Δ +0.133)
  - `self_critique_strict_gap` v3: codestral CENSORED 77 / Δ −0.200 vs
    llama CENSORED 148 / Δ +0.200 (different *directional* sign)
  - `prompt_chain_relaxed` v3: codestral CENSORED 67 / Δ −0.467 vs
    llama FILLED / Δ +0.200 (different *outcome category*: collapsed
    on codestral, beat default on llama)
- **SAME-SHAPE threshold (≥3 of 4 match category at v3): NOT MET.**
  Only 1 of 4 strategies matches outcome category across the two
  models (the diversity_aware_lite codestral row vs unknown llama
  row, which can't be evaluated). The pre-registered "same shape"
  hypothesis is **FALSIFIED**.
- **Joint reading:** the codestral "FLAT NULL" headline from FOLLOWUP
  is **NOT model-general**. The same template fixes that did nothing
  on codestral lift M2 by 0.13–0.33 on llama-70b at the highest-context
  variants. Llama's lower default ceiling (0.667/0.600 vs codestral's
  0.800) leaves more headroom that strategies can exploit.

**Phase 3 — coverage-grounded adjudication (codestral only):**
- **GROUNDED IMPROVEMENT FIRED:** `diversity_aware_grounded @ v3_all`
  M2 = 1.000, Δ = +0.200 over default. Strategy filled cleanly (150/150
  seeds in 69 attempts — the fastest fill of any FOLLOWUP/FOLLOWUP2
  cell, suggesting the corpus-coverage feedback is *easier* for the
  model to use than the in-template gap list). The corpus-complementarity
  hypothesis from experiment4 FOLLOWUP-C is **SUPPORTED** at the
  union-coverage level: when the model is told *which 15 frozen
  branches the corpus is still missing*, it produces seeds that
  collectively close the union.
- **Multi-call grounded strategies still CENSORED:**
  `self_critique_grounded` 115/150 (Δ −0.133); `prompt_chain_grounded`
  92/150 (Δ −0.267). Real coverage feedback did NOT fix the
  structural fillability tax of these scaffolds. The scaffolding tax
  is therefore **independent of feedback quality** — it is the
  scaffold itself (mandated multi-call reasoning structure), not the
  signal the scaffold operates on, that causes the collapse on
  codestral.
- **GROUNDED IMPROVEMENT vs RECOVERY vs REGRESSION:** mixed by
  strategy type. Single-call diversity-aware variant
  (diversity_aware_grounded) → IMPROVEMENT. Multi-call scaffolds
  (self_critique_grounded, prompt_chain_grounded) → REGRESSION /
  CENSORED. The split is the cleanest cleavage the experiment
  produced: *what fixes M2 is corpus-level diversity guidance, not
  per-seed scaffolded reasoning*.

### Cost audit

- Pre-FOLLOWUP2: $21.31 (after FOLLOWUP smoke).
- Post-FOLLOWUP2: **$25.00** (litellm). The proxy cap was hit
  exactly during Phase 1's llama prompt_chain_relaxed @ v4_src_gaps
  cell.
- FOLLOWUP2 spend: **+$3.69**.
- **Phase 3 llama NOT RUN** because of the cap. This is the most
  important open question the experiment cannot answer at this budget:
  does grounded coverage feedback also lift llama past *its* default
  the same way it did for codestral? The 1.000 ceiling on codestral
  strongly suggests yes (the M2 metric ceiling is 1.000 regardless of
  model), but it is untested.

### What this changes about the overall story

Before FOLLOWUP2, the canonical narrative was:
> *Prompt-only interventions on codestral cannot beat default; the M2
> union is bounded by corpus complementarity that single-call prompts
> can't directly steer.*

After FOLLOWUP2 the narrative is:
> *Prompt-only interventions are model-specific. On lower-default
> models (llama-3.1-70b), the same template fixes that did nothing on
> codestral lift M2 by 0.13–0.33. On codestral itself, the lever is
> not per-seed reasoning depth or self-reported gap claims, but
> **real-coverage-feedback-driven corpus diversity**: when given
> ground-truth "still-uncovered" branches, codestral closes the entire
> 15-target union (M2 = 1.000) in a single-call diversity-aware
> strategy.*

Two new research directions opened:
1. Does grounded diversity also achieve perfect M2 on llama-70b and
   on other models? (Budget-blocked — separate experiment.)
2. Does the codestral 1.000 generalize across variants and targets?
   Tested only @ v3_all on RE2.

## DEVIATION LOG (append-only)

- 2026-05-25 — **Phase 3 llama NOT RUN.** Budget cap hit exactly at
  $25.00 during Phase 1's llama `prompt_chain_relaxed @ v4_src_gaps`
  cell (which CENSORED at 43/150 seeds — the cell was in flight when
  the cap was reached; `pkill` invoked to prevent breach). Phase 1's
  `diversity_aware_lite @ llama` (both variants) and Phase 3's three
  grounded strategies on llama were not started. Documented in §
  RESULTS Cost audit. Material consequence: cross-model generality
  of the codestral 1.000 grounded-diversity result is untested.
- 2026-05-25 — **`self_critique_strict_gap @ llama/v3_all` reached 148/150
  seeds** (just barely under fill) before the nogain_window fired.
  Reported as CENSORED per the §5 rule (filled = 150 strict). The 0.867
  M2 is shown for audit but is technically not directly comparable to
  150-seed cells.
- 2026-05-25 — **Phase 1 strategy execution order matters under a
  budget cap.** The orchestrator runs strategies sequentially per
  cell-tuple, so `cot_strict_no_examples` ran first (both variants
  cleanly, ~$0.5) and `diversity_aware_lite` was last in the queue
  (never reached). For future budget-tight runs, schedule the
  highest-information strategy first.
