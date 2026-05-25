# experiment5 — FOLLOWUP: implementation-fix probes for the strategy axis

Lives in `docs/experiment5/` because it is a follow-up *to* experiment5,
reusing the same scoring metric, RE2-v2 frozen 15-branch set, and
codestral-22b model. Pre-registration FROZEN at this commit
(precedes any M2 scoring of the cells listed below).

## 1. Motivation (one paragraph)

experiment5 found that no prompt-optimization strategy
(`cot_strict`, `few_shot`, `self_critique`, `prompt_chain`) beats
`default` for codestral-22b on RE2; the dominant effect is *fillability*
(three of four strategies CENSORED at most variants) rather than seed
quality. Experiments 6 and 7 isolated the mechanism for the cot_strict
collapse: the **static in-template example list** drives the diversity
collapse, not the rigid CoT labels (exp 6 RE2); and the rigid label
scaffold *separately* imposes a per-seed M2 penalty even when fill works
(exp 7 harfbuzz). experiment4's FOLLOWUP-C established that for
codestral on these targets, **corpus-level branch complementarity** —
not per-seed reasoning depth or per-seed validity — is the dominant
lever for union-M2. This FOLLOWUP tests two distinct hypotheses with
that mechanism in hand: (a) can a *template-level fix* to the
underperforming strategies recover them to default, and (b) can a
*diversity-aware* prompt that includes prior-seed gap claims **beat**
default by directly targeting corpus complementarity.

## 2. Strategies under test (4)

All are RE2-only (the exp5 phase-1 scope; phase-2 harfbuzz is a
separately-pre-registered question and explicitly out of scope here).
Templates live in `synthesis/prompts/`; dispatch in
`synthesis/scripts/generate_ablation_inputs.py`.

| Strategy | Tier | n_calls/seed | What it changes vs the exp5 baseline |
|---|---|---:|---|
| `cot_strict_no_examples` | template-only | 1 | Already implemented & validated in exp 6 (`docs/experiment6/RESULTS.md`); included here for the cross-variant comparison table at `v4_src_gaps` (exp 6 only ran `v3_all`). |
| `self_critique_strict_gap` | template-only | 2 | Round-2 refine prompt explicitly enumerates the draft's claimed `target_gaps` AND a sample of up-to-8 unclaimed gaps from the cell's gap list; instructs the model to pivot to an unclaimed gap. Same draft (round 1) as `default`, distinct cache salt. |
| `prompt_chain_relaxed` | template-only | 3 | Plan-stage `target_gap` is allowed to be `unspecified`; sketch/finalize stages may pivot to a different gap than the plan committed to. Targets the v3_all collapse where the original prompt_chain produced 6/150 seeds. |
| `diversity_aware_lite` | driver-thread | 1 | Single-call. Driver reads existing `sample_*.json` sidecars in the cell's synthesis_dir, dedupes & count-ranks prior seeds' self-reported `target_gaps`, and embeds a "COVERAGE FROM OTHER SEEDS IN THIS BATCH" block in the prompt. Caveat: self-reported `target_gaps` are an unreliable signal — a full coverage-grounded diversity strategy would replay each seed through llvm-cov, which is a separately-scoped engineering project. The "lite" suffix marks this. |

**Deferred: `self_critique_grounded`** — would require an in-loop
coverage hook (~200-400 LOC, sparse per-seed signal on a 15-target set).
Documented here as out of scope; potential Phase 2 if `self_critique_strict_gap`
shows directional evidence.

## 3. Scope (locked)

- **Target:** RE2 only (RE2-v2 coverage build, frozen 15-branch set).
- **Model:** codestral-22b only.
- **Variants:** `v3_all` (where `default` reaches its 0.800 ceiling),
  `v4_src_gaps` (the other 0.800 default cell). Both are the
  highest-context cells in the exp 5 grid; if a strategy is going to
  beat default anywhere, it has to do it here.
- **Strategy × variant matrix** (5 new cells; cot_strict_no_examples @
  v3_all is already done in exp 6 and pulled in from cache):

  | Strategy | v3_all | v4_src_gaps |
  |---|:-:|:-:|
  | `cot_strict_no_examples` | (cache, exp 6) | **NEW** |
  | `self_critique_strict_gap` | **NEW** | **NEW** |
  | `prompt_chain_relaxed` | **NEW** | (skip — exp5 already ran prompt_chain @ v4 as `not run`, and 3 calls/seed × 2 variants is the highest-cost cell of the slate) |
  | `diversity_aware_lite` | **NEW** | **NEW** |

- **Seeds/cell target:** 150 (load-bearing; subsampled deterministically).
  If a cell cannot fill 150 (abandon by `nogain_window` or
  `yield_ceiling`), it is reported as CENSORED with the actual seed count
  and the M2 of the partial corpus, mirroring exp 5's reporting pattern.

## 4. Cost gate

- **Pre-run litellm headroom:** $25 cap − $19.26 cumulative (per
  `analysis/scripts/cost_audit.py` at 2026-05-25T03:00 UTC) = **$5.74**.
- **Estimate per cell** (from exp 5 actuals, codestral-22b on RE2):
  - 1 call/seed cell: ~$0.21–0.30 (longer prompts → upper end for
    `diversity_aware_lite`).
  - 2 calls/seed cell: ~$0.43 (`self_critique_strict_gap`).
  - 3 calls/seed cell: ~$0.64 (`prompt_chain_relaxed`).
- **Slate total estimate:** ~$2.10 across 5 new cells.
- **Hard spend cap for this FOLLOWUP:** **$4.00**. If
  `analysis/scripts/cost_audit.py` shows cumulative litellm > $23.26 at
  any inter-cell checkpoint, remaining cells are abandoned and reported
  as NOT RUN.
- **Fail-safe:** every cell runs under
  `UTCF_ABANDON_NOGAIN=5 UTCF_ABANDON_WARMUP=15` (exp 5's aggressive
  policy) to short-circuit collapsed cells. Yield-ceiling guard ON.
- **Cache safety:** new strategies' cache salts append the strategy name
  per `make_cache_salt`; `default`'s ~14k cache entries are byte-identical
  (regression-pinned by `tests/test_llm_client_logprob_backcompat.py`).

## 5. Pre-registered prediction + 3-way interpretation (FROZEN)

**Primary point estimates (M2; `slices.all.union_frac_targets_hit`):**

`default @ v3_all` = **0.800**, `default @ v4_src_gaps` = **0.800**
(from `docs/experiment5/RESULTS.md`, frozen in exp5 pre-reg `5b96f61`).

**Minimum meaningfully-distinguishable change in M2:**
**1/15 = 0.067** (one frozen target). Below this, point-estimate
differences are within metric quantum and not interpreted as signal.

**Predicted (blind, recommended call):** the *implementation-fix* tier
(cot_strict_no_examples, self_critique_strict_gap, prompt_chain_relaxed)
**recovers to within ±0.067 of default** — they will neither beat
default nor underperform it materially. `diversity_aware_lite` **beats
default by ≥ +0.067** at ≥ 1 variant (corpus complementarity is the
right lever, and self-reported `target_gaps` are at least correlated
with the right thing — branch coverage). All three scientifically
informative outcomes (improvement / recovery / null) are admissible.

**3-way interpretation rule (pre-committed, applies per variant):**

- **IMPROVEMENT** (any strategy ≥ default + 0.067 at any variant) →
  *the prompt fix unlocks new M2*. If only `diversity_aware_lite`
  improves → **corpus-complementarity hypothesis SUPPORTED**. If only
  a template-fix tier-2 strategy improves → **the original
  implementation suppressed real headroom; recovery overshot**.
- **RECOVERY ONLY** (no strategy ≥ default + 0.067; at least 2 of the 3
  template-fix strategies are within ±0.067 of default) → the original
  strategies underperformed *because of fixable implementation choices*
  (not because the strategy class is fundamentally weaker than
  `default`). Consistent with the implementation-fix hypothesis from the
  conversation that motivated this experiment.
- **FLAT NULL** (all strategies stay within ±0.067 of default; none
  improve, but at least one of the original strategies didn't recover
  even with the fix) → there is a real per-seed-quality ceiling and
  template fixes are insufficient. Consistent with the per-seed-quality
  ceiling implied by exp 7 (cot_strict @ harfbuzz/v3_all: filled, M2
  0.120 vs default 0.260 — fix wasn't enough).
- **REGRESSION** (any fix-tier strategy ≥ 0.067 *below* default) →
  the fix introduced a new failure mode; report directionally.

**Reliability secondary outcome (auxiliary, not the headline):** every
cell additionally reports its `_synthesis_stats.json` (fill count,
no-gain rate, calls/seed actual). A CENSORED cell with fill < 150
counts as a "fix did not solve the fillability problem either" for the
strategy in question; it is excluded from the M2 ranking but called
out in the results table with `n=<count>` per exp 5's convention.

## 6. Statistical procedure

- **Per-cell point estimate:** M2 from the **unmodified**
  `analysis.metrics.M2HardBranchMetric` scoring exactly 150 seeds.
- **Bootstrap 95% CI** on M2 via 10000 resamples over the seed dimension
  using `analysis/scripts/experiment5_rank.py` (unchanged; the same
  script exp 5 used). RNG = `random.Random(42)`.
- **Strategy-vs-default contrast** (per variant): Wilcoxon signed-rank
  over the 15 frozen-target union indicators (paired by target).
  Holm-corrected across the strategy × variant comparison family
  (≤ 8 contrasts: 4 strategies × 2 variants).
- **Friedman test** across strategies per variant where ≥ 3 strategies
  fill 150 seeds.
- **Falsifier:** the IMPROVEMENT outcome requires the point-estimate
  delta ≥ +0.067 AND the bootstrap CI lower bound > 0. The RECOVERY
  outcome requires |delta| ≤ 0.067 AND the strategy filled 150. The
  FLAT NULL outcome requires either no improvement OR a non-recovering
  strategy.

## 7. Replication recipe

```bash
# Pre-flight: cost audit, env, smoke tests already verified at this commit.
source .venv/bin/activate

# Per cell, the runner is the unmodified scripts/run_ablation_re2.py
# wrapper. Output paths under results/ablation_re2_v2/<strategy>/m2/...
# are disjoint from the canonical default cells.

export UTCF_ABANDON_NOGAIN=5
export UTCF_ABANDON_WARMUP=15

# Cheapest first: diversity_aware_lite (1 call/seed) on both variants
nohup .venv/bin/python scripts/run_ablation_re2.py --phase synthesis \
  --variants v3_all,v4_src_gaps --only-models codestral-22b \
  --strategy diversity_aware_lite \
  --num-seeds 150 --attempt-offset 900000 \
  >> /tmp/exp5_followup_div.log 2>&1 &

# Then self_critique_strict_gap (2 calls/seed) on both variants
nohup .venv/bin/python scripts/run_ablation_re2.py --phase synthesis \
  --variants v3_all,v4_src_gaps --only-models codestral-22b \
  --strategy self_critique_strict_gap \
  --num-seeds 150 --attempt-offset 900000 \
  >> /tmp/exp5_followup_scgap.log 2>&1 &

# Then cot_strict_no_examples (1 call/seed) on v4 only (v3_all in exp6 cache)
nohup .venv/bin/python scripts/run_ablation_re2.py --phase synthesis \
  --variants v4_src_gaps --only-models codestral-22b \
  --strategy cot_strict_no_examples \
  --num-seeds 150 --attempt-offset 900000 \
  >> /tmp/exp5_followup_cotnox.log 2>&1 &

# Then prompt_chain_relaxed (3 calls/seed) on v3_all only — gate on
# inter-cell cost audit before launching
nohup .venv/bin/python scripts/run_ablation_re2.py --phase synthesis \
  --variants v3_all --only-models codestral-22b \
  --strategy prompt_chain_relaxed \
  --num-seeds 150 --attempt-offset 900000 \
  >> /tmp/exp5_followup_pcrlx.log 2>&1 &

# After all synthesis: M2 scoring (unmodified metric)
.venv/bin/python scripts/run_ablation_re2.py --phase m2 \
  --variants v3_all,v4_src_gaps --only-models codestral-22b \
  --strategy diversity_aware_lite,self_critique_strict_gap,cot_strict_no_examples,prompt_chain_relaxed \
  --skip-existing
```

## 8. Stopping

Complete when (a) every cell in the table has either a filled-150 M2 or
a CENSORED `_synthesis_stats.json` with `final_seeds` and abandon
reason; (b) the inter-cell cumulative-cost gate was either honored or
recorded; (c) the §5 adjudication is filled into a RESULTS section
appended below this pre-registration.

## DEVIATION LOG (append-only)
