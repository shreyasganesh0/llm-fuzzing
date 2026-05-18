# experiment6 — FOLLOW-UP C: mechanism check (NO new LLM calls)

Lives in `docs/experiment6/` because it is a follow-up *to* experiment6,
reusing its on-disk over-generated pools. Append-only deviation log at
the bottom. Pre-registration FROZEN (user delegated autonomous
execution, 2026-05-18 — this commit is the freeze; precedes any
computation).

## 1. Question

Verify or falsify the experiment6 mechanism: *"low-entropy seeds carry
M2 because they pass early parsing and reach deep code."* experiment6
found, against its pre-registered prediction, that the **low**-entropy
subsample carried the M2 union (harfbuzz v1_src S_low 0.46, v3_all S_low
0.50, vs random/high 0.26). That established a *union-level* effect; the
*per-seed* mechanism is untested. Use existing data only.

## 2. Inputs (verified on disk; NO regeneration)

- Per-seed mean payload entropy: `results/experiment6/{v1_src,v3_all}/entropies.json` (present).
- Per-seed × 50-branch hit matrix: `results/experiment6/{v1_src,v3_all}/{v1_src,v3_all}/pool/gap_hits.jsonl` (present) → deep-reach is computable for free.
- Logprob sidecars: `synthesis/results/experiment6/logprobs/harfbuzz/ablation/{v1_src,v3_all}/codestral-22b/*.json` (456/461) → **prefix entropy** (first 8 payload tokens) recomputable offline via the experiment6_entropy masking logic, no LLM.
- **Per-seed M1 is NOT on disk.** experiment6 only computed *union*
  M2/M1. Per-seed M1 (edges from replaying ONE seed) requires a new
  per-seed `seed_replay`+llvm-cov pass over the ~450 pool seeds × 2
  pools. **Zero LLM cost**, but real CPU/LLVM compute — this corrects
  the web-instance plan's "already on disk". Done with the unmodified
  coverage pipeline (no invariant touched).

## 3. Frozen deep/early surrogate (the harfbuzz frozen set has NO explicit parse-stage annotation — surrogate fixed BEFORE any computation)

From the 50 frozen branches' files (verified histogram):
`src/hb-blob.cc`×2, `src/hb-open*`×11, `src/hb-ot*`×36, `src/hb-shape*`×1.

- **EARLY** (structural/blob/table-directory parse, pre-shaping) =
  files matching `hb-blob` OR `hb-open` → **13 branches**.
- **DEEP** (OpenType layout / shaping, past the structural parse) =
  files matching `hb-ot` OR `hb-shape` → **37 branches**.

This mapping is frozen here and will not be re-drawn after seeing
results (anti-gerrymander). Rationale: hb-blob = blob lifetime,
hb-open-type/hb-open-file = sfnt/table-directory reading (the first
validity gate); hb-ot-* / hb-shape = layout & shaping that only runs if
the font parsed. Recorded as a surrogate, with this rationale, per the
plan.

## 4. Computation

For each pool (v1_src, v3_all):
1. Assign every pooled seed an entropy **quartile** (Q1=lowest …
   Q4=highest mean payload entropy).
2. Per quartile: **deep-reach rate** = fraction of seeds hitting ≥1
   DEEP branch (from gap_hits.jsonl), and **mean per-seed M1** (from
   the new per-seed replay pass).
3. Recompute (1)–(2) using **prefix entropy** (mean entropy over the
   first 8 payload tokens only) instead of full mean payload entropy.
4. Tables: 4 rows (quartiles) × 2 cols (deep-reach rate, mean per-seed
   M1), one per pool, for BOTH entropy metrics.

## 5. Pre-registered prediction + 3-way interpretation (FROZEN)

**Predicted (blind, recommended call):** consistent with experiment6's
inverse finding, **the lowest-entropy quartile (Q1) has the highest
deep-reach rate**, monotone-ish decreasing toward Q4; prefix entropy
separates deep-reach **at least as well** as full entropy (the "did the
seed start a valid font header" intuition is concentrated in the prefix).
Minimum meaningful gap in deep-reach rate between the extreme quartiles
(Q1 − Q4): **0.10** (10 percentage points).

**Interpretation rule (pre-committed):**
- Q1 dominates deep-reach (Q1−Q4 ≥ +0.10) → **mechanism SUPPORTED**
  (low-entropy seeds are more valid → reach deep code).
- Quartiles similar on deep-reach (|Q1−Q4| < 0.10) but differ on mean
  per-seed M1 *conditional on reaching deep* → **mechanism DIFFERENT**
  (low-entropy seeds aren't more valid, they're differently
  exploratory).
- Quartiles do not differ on either → **original mechanism WRONG**; the
  M2 effect is union-level structure (low-entropy seeds cover
  non-overlapping branches), not per-seed reach.

## 6. Stopping

Complete when this doc's RESULTS section has: the two tables per pool
(mean entropy + prefix entropy), and the one-paragraph interpretation
adjudicated against §5.

## RESULTS — COMPLETE (no LLM; per-seed replay over both ~450-seed pools)

Surrogate verified live: EARLY=13, DEEP=37, UNMAPPED=0 (both pools).
Per-seed M1 = replayed (450/450 each pool); prefix entropy n_ok=456/461,
0 drops. Quartiles ≈113/113/112/112.

**Pool v1_src** — deep_reach_rate by entropy quartile (Q1=lowest):

| metric | Q1 | Q2 | Q3 | Q4 | Q1−Q4 | mean per-seed M1 (Q1..Q4) |
|---|---|---|---|---|---|---|
| full payload entropy | 0.142 | 0.080 | 0.063 | 0.134 | **+0.008** | 549 / 541 / 539 / 541 |
| prefix entropy (8 tok) | 0.124 | 0.088 | 0.107 | 0.098 | **+0.026** | 548 / 541 / 540 / 539 |

**Pool v3_all**:

| metric | Q1 | Q2 | Q3 | Q4 | Q1−Q4 | mean per-seed M1 (Q1..Q4) |
|---|---|---|---|---|---|---|
| full payload entropy | 0.142 | 0.080 | 0.054 | 0.080 | **+0.061** | 551 / 540 / 535 / 537 |
| prefix entropy (8 tok) | 0.133 | 0.071 | 0.080 | 0.071 | **+0.061** | 548 / 538 / 539 / 537 |

Conditional-on-deep-reach mean per-seed M1: v1_src Q1 574.6 vs Q4 574.3;
v3_all Q1 595.3 vs Q4 577.7 — `differs = False` both pools. Per-seed M1
is essentially flat across all entropy quartiles (~535–551 edges).

### Interpretation (FOLLOWUP.md §5 pre-committed rule — fired cleanly)

**Verdict for BOTH pools: "mechanism WRONG / union-level".** Every
Q1−Q4 deep-reach gap (full and prefix, both pools) is well below the
pre-registered ±0.10 threshold (max 0.061), is **non-monotone** (e.g.
v1_src full is U-shaped: Q1 0.142, Q3 0.063, Q4 0.134), and per-seed M1
does not differ by quartile (conditional or unconditional). So:

- experiment6's headline (the **low-entropy 150-seed subsample carries
  the M2 union**, S_low 0.46/0.50 vs random/high 0.26) is **real and
  unchanged** — but the proposed *per-seed* mechanism ("low-entropy
  seeds are individually more valid → individually reach deep code more
  often") is **FALSIFIED**. Low-entropy seeds are *not* individually
  more deep-reaching, and contribute the same ~540 edges each.
- Therefore the M2(S_low) advantage is a **union-level / complementarity
  effect**: a low-entropy 150-seed set collectively covers a *less
  redundant, more complementary* spread of hard branches, even though no
  individual low-entropy seed reaches deep code more than a high-entropy
  one. prefix entropy separated marginally better than full (still ≪
  threshold), consistent with "the effect is not concentrated in the
  payload prefix / per-seed validity".

## DEVIATION LOG (append-only)

- 2026-05-18 — Surrogate frozen as §3 (the web plan guessed
  `hb-cff-*`/`hb-buffer-*`; the actual frozen-set files are
  blob/open/ot/shape — mapping corrected and frozen before computation).
  Corrected the plan's "per-seed coverage already on disk": per-seed M1
  required a new (zero-LLM) per-seed replay pass (450×2, cached to
  `results/experiment6/followup/per_seed_m1_*.json`).
- 2026-05-18 — §5's "mechanism DIFFERENT" arm pinned only the +0.10
  deep-reach gap, not a threshold for "per-seed M1 differs conditional
  on deep-reach". Resolved with a pre-committed-style mechanical rule:
  differ iff `|Q1−Q4| / max(|Q1|,|Q4|) ≥ 0.10` over deep-reaching seeds
  only (relative 10%). Surfaced (not silently chosen); did not affect
  the verdict (the deep-reach gap alone already selected the
  "WRONG / union-level" branch for both pools).

## DEVIATION LOG (append-only)

- 2026-05-18 — Surrogate frozen as §3 (the web plan guessed
  `hb-cff-*`/`hb-buffer-*`; the actual frozen-set files are
  blob/open/ot/shape — mapping corrected and frozen before computation).
  Corrected the plan's "per-seed coverage already on disk": per-seed M1
  requires a new (zero-LLM) per-seed replay pass.
