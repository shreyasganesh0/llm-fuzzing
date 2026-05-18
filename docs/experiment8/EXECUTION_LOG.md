# experiment8 — EXECUTION LOG (append-only)

Append-only. Every command, every deviation (with justification), and
the PRE-REGISTRATION block. Prior entries are never rewritten.

## 2026-05-18 — Draft setup

- Branch `experiment-followups` cut from `experiment7` HEAD
  `c519f46`. Doc structure follows the experiment6/7 directory +
  EXECUTION_LOG convention (reworked from the web-instance plan's flat
  `experiment8.md`).
- Verified, read-only, the baselines pinned in MANIFEST:
  `re2/v3_all/default` n=150 M2=0.800; `re2/v3_all/cot_strict` n=124
  M2=0.533 (CENSORED). These are NOT regenerated.
- No strategies, templates, fixtures, or runs created yet — this commit
  is the pre-registration draft for user review.

---

## PRE-REGISTRATION — **DRAFT (not frozen)**

**Status.** The predictions, effect sizes, and falsifier below are fully
specified and git-timestamped by this commit *before any run and before
the strategies/templates exist*. They are presented for user review.
Pre-registration **integrity rule**: the directional predictions and the
0.05 effect-size threshold below will **not** be edited after review;
the user-approval commit (which also authorizes implementation+runs) is
the freeze point and precedes any LLM generation. If the user changes a
prediction during review, that change is logged here as a dated
deviation with its rationale.

**Timestamp (draft):** 2026-05-18T03:19:51Z
**Commit at draft:** `c519f46427187fe193e87d5756f14364932173af`

### Statistics / definitions (frozen)

- Per cell: M2 = `slices.all.union_frac_targets_hit` (primary), M1
  (secondary), unique-content ratio over all attempted seeds.
- **Diversity recovery** = fills exactly 150 seeds AND unique-content
  ratio ≥ 0.50 (cot_strict reference = 0.103).
- **Minimum meaningful M2 effect = 0.05.**
- Reference cells (existing, not regenerated): `default@v3_all` M2=0.800
  (filled), `cot_strict@v3_all` CENSORED n=124.

### Predicted sign of each effect (this is the prediction, made blind to any new run)

Derived from existing data (default & cot_strict share the identical
example block; only labels differ; default fills, cot_strict collapses):

1. **cot_strict_no_labels → FILLS 150, diversity recovers (ratio ≥0.5),
   M2 lands near the default region (≫ +0.05 above the cot_strict
   CENSORED reference).** High confidence (it is ≈ default + a soft
   rationale).
2. **cot_strict_no_examples → STILL CENSORED (does not fill 150),
   diversity does NOT recover; but its dominant-regex set is DIFFERENT
   from the original 5** (labels cause collapse; examples only set its
   content).
3. **cot_strict_rotated_examples → STILL CENSORED**, diversity does not
   recover.

### Falsifier

The mechanism claim "the rigid 4-step labels (not the example list)
cause the collapse" is **falsified** if any of: (a) `cot_strict_no_labels`
does NOT fill / does not recover diversity; OR (b) `cot_strict_no_examples`
DOES fill 150 and recovers diversity (≥0.5) — that would mean removing
examples alone fixes it, contradicting "labels are the cause"; OR
(c) all three remain CENSORED with the *same* original 5-regex dominant
set (would implicate neither isolated feature). All outcomes are
reportable; none is optimized for.

<!-- subsequent entries (approval/freeze, runs, deviations) appended below -->
