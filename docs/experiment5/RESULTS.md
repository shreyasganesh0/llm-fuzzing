# experiment5 — RESULTS (Strategy-as-Variant Ablation, RE2 × codestral-22b)

**Status: COMPLETE (phase 1, RE2).** Pre-registered commit `5b96f61`
(2026-05-18T01:02:14Z) **before any scoring**. M2 scored by the
**unmodified** `M2HardBranchMetric` against the frozen RE2-v2 15-branch
set. Ranking/stats: `analysis/scripts/experiment5_rank.py`; lost-cause
audit: `analysis/scripts/seed_yield_audit.py`.

## Headline

**No prompting strategy beats `default` at any context variant for
codestral-22b on RE2.** No strategy-vs-`default` contrast is
Holm-significant (all p_Holm ≥ 0.55); Friedman is n.s. where computable
(v2_src_tests χ²=5.36, p=0.147). The dominant effect is **strategy
*reliability*, not seed quality**: `default` and `few_shot` reliably fill
150-seed corpora across all 5 variants, whereas `cot_strict`,
`self_critique`, and `prompt_chain` frequently **cannot produce a usable
150-seed corpus at all** for this model. This is the "global-null +
reliability-dominates" outcome — one of the three pre-declared
admissible outcomes.

## M2 table — variant × strategy (point [boot 95% CI]; 150-seed cells only valid)

| variant | default | cot_strict | few_shot | self_critique | prompt_chain |
|---|---|---|---|---|---|
| v0_none | 0.333 [0.27,0.33] | CENSORED n=18 | **0.467** [0.33,0.47] | CENSORED n=121 | CENSORED n=146 |
| v1_src | 0.400 [0.27,0.40] | CENSORED n=14 | 0.333 [0.27,0.33] | CENSORED n=95 | not run |
| v2_src_tests | **0.733** [0.47,0.73] | CENSORED n=32 | 0.400 [0.27,0.40] | 0.467 [0.33,0.47] | 0.467 [0.27,0.47] |
| v3_all | **0.800** [0.67,0.80] | CENSORED n=124 | 0.467 [0.27,0.47] | CENSORED n=54 | CENSORED n=6 |
| v4_src_gaps | **0.800** [0.60,0.80] | not run (n=0) | 0.733 [0.47,0.73] | CENSORED n=59 | not run |

**CENSORED = the cell could not fill 150 seeds** (synthesis abandoned by
the fail-safe). Its M2 is shown for audit but is NOT comparable to a
filled cell — M2 is a union over the corpus, so a smaller corpus
mechanically yields a smaller union. CENSORED cells are excluded from
the ranking and the contrasts (invariant 4: only 150-seed corpora are
comparable). M1 table is in `results/experiment5/experiment5_re2_rank.md`.

## Ranking (filled cells only — M2 primary, M1 tiebreak)

1. `v3_all` / **default** — M2 0.800 (M1 1366)
2. `v4_src_gaps` / **default** — 0.800 (1321)
3. `v2_src_tests` / **default** — 0.733 (1495)
4. `v4_src_gaps` / few_shot — 0.733 (1281)
5. `v0_none` / few_shot — 0.467 … (full list in the rank artifact)

`default` holds ranks 1–3; `few_shot` is the only non-default strategy
in the top 5, and never above `default` at the same variant.

## Statistics

- **Strategy-vs-default (Wilcoxon signed-rank over the 15 frozen-target
  union indicators, Holm-corrected, family=16):** *no* contrast
  significant. Smallest raw p = 0.046 (`cot_strict` vs default @
  v1_src, Δ=−0.267) → p_Holm = 0.55. Every other p_Holm ≈ 0.75–1.0.
- **Friedman (per variant, block = frozen hard-branch target):** only
  v2_src_tests had ≥3 fillable strategies (default/few_shot/self_critique/
  prompt_chain) → χ²=5.36, p=0.147, **not significant**; mean ranks
  default 2.07 < prompt_chain 2.60 = self_critique 2.60 < few_shot 2.73.
  Other variants had <3 non-censored cells (cot_strict always censored;
  self_critique/prompt_chain mostly censored) so Friedman was not
  computable — itself the finding (most strategies don't yield a cell).

## Adjudication against the pre-registered prediction (`5b96f61`)

| Pre-registered claim | Outcome |
|---|---|
| 1. `default` & `cot_strict` top tier at high-context | **MIXED.** `default` is the top performer (0.800 @ v3_all/v4, 0.733 @ v2) — supported. `cot_strict` part **FALSIFIED**: it never fills any cell (diversity collapse — 5 regexes ≈60% of output; 142 unique < 150). |
| 2. `prompt_chain` underperforms at high-context (`v3_all`) | **SUPPORTED at v3_all** (collapsed to 6 seeds — total fill failure at the richest-context variant) but viable at v2_src_tests (150) and near-fill at v0_none (146). Directionally correct; mechanism was fill-collapse, not low per-seed M2. |
| 3. Strategy × variant interaction is real | **SUPPORTED, via an unanticipated axis.** The interaction is dominated by *fillability*: cot_strict collapses at all variants; self_critique fills only v2; prompt_chain fills v0/v2 but collapses v3_all; default/few_shot fill everywhere. The best strategy is variant-dependent, but the load-bearing interaction is reliability, not M2-of-filled. |
| 4. Best cell = cot_strict@v3_all or default@v3_all | **`default @ v3_all` (0.800).** default part correct; cot_strict part falsified. |
| Global null (no strategy beats default + …) | **HOLDS.** No Holm-significant strategy gain over default anywhere; Friedman n.s. where computable. Pre-declared admissible; reported as-is. |

Net: the "some strategy beats default" hypothesis is **falsified** for
codestral-22b/RE2; the strategy×variant *interaction* is **supported**
but its dominant component is **strategy reliability** (can the scaffold
even produce 150 diverse seeds?), not the M2 of the cells that do fill.
A clean characterization result.

## Lost-cause audit & cost (the money question)

`seed_yield_audit` flags `cot_strict` LOST_CAUSE ×4 (+ v4 n=0),
`prompt_chain` LOST_CAUSE @ v1_src/v3_all/v4 (collapsed/not-run),
`self_critique` MARGINAL ×3 — total est. wasted ≈ **$0.27** on
collapsed cells. The opt-in aggressive fail-safe
(`UTCF_ABANDON_NOGAIN=5` + yield-ceiling) **demonstrably saved spend**:
e.g. `prompt_chain@v3_all` was killed at **14 attempts** (vs grinding to
~300×3 calls under the legacy 20-window); `self_critique@v4_src_gaps`
was stopped by the **yield-ceiling guard** ("projected 303 > max 300 at
rate 0.496"). Total experiment5 spend ≈ **$4.2** (cost_audit: litellm
$14.39 → $18.63); the **$25 proxy cap was never hit** (0 budget
errors); cumulative headroom ≈ $6.4.

## Deviations & notes (every one)

1. **Staged cheapest-first, user-gated** (cot_strict+few_shot →
   self_critique → prompt_chain probe → 2-cell subset). cot_strict
   re-runs avoided after collapse; prompt_chain capped at 3 of 5
   variants (v0_none, v2_src_tests, v3_all) by user spend decision —
   v1_src/v4_src_gaps prompt_chain intentionally **not run** (reported
   as such, not as data).
2. **Opt-in aggressive abandon** (`UTCF_ABANDON_NOGAIN=5`,
   default-unchanged & regression-pinned) used for stages 2–4; the
   `default`/`few_shot` baseline cells were produced under legacy
   behaviour (env unset == byte-identical), so cross-strategy
   comparison is not confounded by the abort policy.
3. cot_strict/self_critique CENSORED cells were M2-scored on their
   partial (<150) corpora at **zero proxy cost** purely so the
   aggregator marks them CENSORED (not MISSING) with the true seed
   count; those M2 values are explicitly non-comparable (see table
   note) and excluded from ranking/stats.
4. `seed_yield_audit` per-strategy diversity is reported as a
   non-cot_strict **aggregate** (cache stores no prompt → only
   cot_strict is attributable via its mandated reasoning marker); the
   realized-fill override makes the VIABLE/LOST_CAUSE verdict match the
   runner's true fill, which is the load-bearing signal.
5. Phase 2 (harfbuzz) — the pre-registered "only if RE2 shows signal"
   trigger: RE2 shows a clear **negative/characterization** result (no
   strategy beats default; reliability dominates). Recommendation:
   **do not** spend on a harfbuzz strategy sweep on this evidence
   unless specifically targeting the reliability question on the
   binary format; if pursued it is a separate, separately-pre-
   registered experiment.

## Stopping

Per the stopping criterion: phase 1 is complete (full M2/M1 tables,
per-cell CIs, Friedman/Nemenyi, Holm contrasts, adjudication vs the
pre-registration). No further strategies/models/targets run.
