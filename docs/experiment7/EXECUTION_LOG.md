# experiment7 — EXECUTION LOG (append-only)

Every command, every deviation (with justification), the cost-gate
record, and the mandatory PRE-REGISTRATION (appended before any M2/M1
scoring, never after seeing results). Prior entries are never rewritten.

---

## 2026-05-18 — Setup & scope

- Branch `experiment7` cut from `7627ad8` (experiment6 HEAD, so all
  prior experiment docs co-exist for the unified blind-reader index).
- Scope locked with the user (AskUserQuestion, 2026-05-18): RE2 first
  (harfbuzz only if signal — separate later pre-registration);
  codestral-22b only; strategies {default,cot_strict,few_shot,
  self_critique,prompt_chain}; single 150-seed draw + bootstrap +
  Friedman/Nemenyi.
- Verified the 5 `default × v0..v4` RE2 codestral M2 cells already
  exist (`results/ablation_re2_v2/m2/<v>/codestral-22b/summary.json`,
  experiment2_1) → read-only baseline, `--skip-existing`.
- No isolation wrapper needed: non-default strategy cells write under a
  `<strategy>/` path segment + `,strategy=<name>` cache salt (invariant
  9) — disjoint from the default experiment2_1 cells. No new
  orchestrator; the unmodified `scripts/run_ablation_re2.py` runs it.
- Wrote `docs/experiment7/{MANIFEST.json,METHODS.md,EXECUTION_LOG.md}`
  and a `RESULTS.md` stub BEFORE any code/run.

## 2026-05-18 — COST GATE (recorded before any generation)

- `estimate_cost.py codestral-22b`: 20 non-default cells ≈ **$8.61**
  typical (~4,900 calls) / **$15.81** conservative (~9,000 calls);
  per-call $0.0018; `default` cells = $0 (cache hits).
- `cost_audit.py`: cumulative **litellm $14.39** vs the **$25** proxy
  cap that blocked experiment3_1 at $25.01. Apparent headroom ≈ $10.6;
  proxy internal counter opaque.
- User decision (AskUserQuestion, 2026-05-18): **staged cheapest-first
  with live budget monitoring** — stage 1 `cot_strict,few_shot`
  (offset 700000), stage 2 `self_critique` (705000), stage 3
  `prompt_chain` (710000); `--skip-existing`; abort on
  `400 Budget exceeded`; resumable.

---

## PRE-REGISTRATION (appended BEFORE any M1/M2 scoring)

**Timestamp (UTC):** 2026-05-18T01:02:14Z
**Commit at pre-registration:** `7627ad81e3f6ff73e1b6d98b01f16daf7b196eef`
(branch `experiment7`)
**Status:** written before stage 1 is launched — no experiment7 cell has
been generated or scored; the only RE2 codestral M2 numbers in existence
are the `experiment2_1` `default` baselines (public:
v0 0.333 → v3/v4 0.800 region; the 5 baseline summaries on disk).

### Statistics (frozen; METHODS §2, §4)

For each `(variant, strategy)` cell: M2 =
`slices.all.union_frac_targets_hit` (primary), M1 = union edges
(secondary). Per-cell bootstrap 95% CI (n=10000, `random.Random(42)`).
Across cells: Friedman + Nemenyi (+ CD diagram). Per variant, the four
non-`default` strategies are each contrasted vs `default` with Holm
correction.

### My predicted ranking & interaction (blind; this is MY prediction)

1. **`default` and `cot_strict` are the top tier at the high-context
   variants** (`v2_src_tests`, `v3_all`). RE2 regex strings are short, so
   in-band reasoning is cheap and `cot_strict` should match or slightly
   beat `default`; the experiment2_1 RE2 ceiling (codestral M2 ≈ 0.800
   at v3/v4, `default`) is near the achievable maximum for this model.
2. **`prompt_chain` UNDERperforms at high-context variants.** Three
   sub-calls under the UF LiteLLM 2048-char response cap fragment the
   already-context-heavy v3_all prompt → more parse failures, lower seed
   quality. I predict `prompt_chain @ v3_all < default @ v3_all`.
3. **Strategy × variant interaction is REAL (non-zero).** The best
   strategy is variant-dependent: decomposition/exemplar strategies
   (`prompt_chain`, `few_shot`) are *relatively* stronger at the
   low-context end (`v0_none`, where they compensate for missing
   context) than at `v3_all`. I predict the strategy ranking at
   `v0_none` differs from the ranking at `v3_all`.
4. **Overall best cell:** a high-context × {`default` or `cot_strict`}
   cell (point prediction: `cot_strict @ v3_all` or `default @ v3_all`).

### Falsifier (per-claim)

- Claim 2 falsified if `prompt_chain @ v3_all` ≥ `default @ v3_all`
  (Δ ≥ 0) at the point estimate, or its CI sits above 0.
- Claim 3 (interaction) falsified if the strategy ordering is
  statistically indistinguishable across variants — i.e. Friedman shows
  no significant strategy effect, OR the top strategy is the same at
  every variant with overlapping CIs (no reordering).
- Claim 1/4 falsified if the top-M2 cell after Holm is a non-
  `default`/`cot_strict` strategy or a low-context variant.
- Global null: if **no** non-`default` strategy beats `default` at any
  variant after Holm AND there is no interaction, the "strategy as a
  variant" framing adds nothing for RE2/codestral — a publishable
  negative result, reported as such.

All three outcome classes (a non-default strategy wins / `default` is
unbeaten / variant-dependent winners) are declared admissible in
advance; none is optimised for.

### Frozen analysis params (no post-hoc tuning)

k=150 seeds/cell (invariant 4); subsample `random.Random(42)`
(invariant 3); bootstrap n=10000 `random.Random(42)`, percentile
[2.5,97.5]; Friedman α=0.05, Nemenyi/Holm for post-hoc; M2 filter +
frozen RE2-v2 set unchanged (invariant 2); attempt-offset 700000/
705000/710000 per stage (invariant 5).

## 2026-05-18 — Stage 1 launched (cot_strict, few_shot)

- Pre-registration committed `5b96f61` BEFORE this launch (no
  experiment7 cell scored prior).
- Cmd: `UTCF_LLM_RPM=12 nohup .venv/bin/python
  scripts/run_ablation_re2.py --phase all --skip-existing
  --variants v0_none,v1_src,v2_src_tests,v3_all,v4_src_gaps
  --strategy cot_strict,few_shot --only-models codestral-22b
  --attempt-offset 700000 >> /tmp/exp7_re2_s1.log 2>&1 &`
- Unmodified `AblationRunner` / `run_ablation_re2.py`; non-default
  cells write under the `<strategy>/` path + `,strategy=<name>` salt
  (invariant 9) — `experiment2_1` default cells read-only via
  `--skip-existing`, never touched. No logprobs requested (cache key
  byte-identical to pre-experiment6 for every cell).
- Health monitor: **0 `400 Budget exceeded`** lines; seeds accumulating
  (proxy accepting traffic). Stage 2 (`self_critique`, offset 705000)
  and Stage 3 (`prompt_chain`, offset 710000) gated on this staying
  clean + a `cost_audit.py` headroom check between stages.
- Aggregator `analysis/scripts/experiment7_rank.py` (+tests) under
  construction in parallel (offline; no proxy/LLM).

## 2026-05-18 — Stage 1 STOPPED (cot_strict collapse; budget NOT breached)

- **Honest correction:** an interim monitoring grep (`400|budget`,
  case-insensitive) reported "2 budget-error lines" and was treated as a
  possible stop-signal. Direct inspection disproved it: a precise scan
  (`Budget has been exceeded|BudgetExceeded|status_code 400`) returns
  **0**. The proxy cap was NOT hit. The earlier count was a false
  positive (the substring "400"/"budget" in benign attempt/seed-count
  fields). Recorded so the log is not misleading.
- **Real finding — `cot_strict` is non-viable for codestral-22b on
  RE2.** 3 cells censored by the parse-failure cap
  ("synthesis capped: cell skipped (too many parse failures)"):
  `v0_none`=18, `v1_src`=14, `v2_src_tests`=32 seeds (<150). `v3_all`
  reached 124 and was still short when the run was stopped;
  `v4_src_gaps` and all `few_shot` cells were never reached (the
  strategy loop runs `cot_strict` across all variants first). The
  4-step labelled-CoT template, with codestral on the regex-JSON
  format, parses far below the rate needed to fill 150 — the same
  "format-compliance separates usable from unusable" failure mode seen
  in experiment2_1 (llama-3.3-70b) and experiment1_4 (exp1_gaps_only).
- **Cost (cost_audit, ground truth):** litellm $14.39 → **$15.16**
  (+$0.77; +492 cache entries). Cap ($25) never threatened; cost is
  NOT the blocker — the blocker is censored-cell unusability.
- **Action:** the run was intentionally `pkill`-ed (the
  background-task "failed exit 144" notice is that deliberate stop, not
  an experiment error) to avoid spending more proxy budget grinding
  `cot_strict`/`prompt_chain` toward the 300-attempt cap on cells that
  cannot reach 150. Stages 2/3 NOT launched. Per the staged plan + the
  user's "stop and surface rather than push through" instruction, this
  is surfaced for a scope decision before any further generation.
- Aggregator `analysis/scripts/experiment7_rank.py` (+15 passing tests,
  ruff clean) completed and is committed; it already classifies sub-150
  cells as CENSORED (not padded — invariant 4), so a "cot_strict
  collapses" outcome is representable in the ranking as a disclosed
  negative result.

<!-- subsequent entries appended below as work proceeds -->
