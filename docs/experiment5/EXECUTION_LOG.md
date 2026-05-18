# experiment5 — EXECUTION LOG (append-only)

Every command, every deviation (with justification), the cost-gate
record, and the mandatory PRE-REGISTRATION (appended before any M2/M1
scoring, never after seeing results). Prior entries are never rewritten.

---

## 2026-05-18 — Setup & scope

- Branch `experiment5` cut from `7627ad8` (experiment4 HEAD, so all
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
- Wrote `docs/experiment5/{MANIFEST.json,METHODS.md,EXECUTION_LOG.md}`
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
(branch `experiment5`)
**Status:** written before stage 1 is launched — no experiment5 cell has
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
  experiment5 cell scored prior).
- Cmd: `UTCF_LLM_RPM=12 nohup .venv/bin/python
  scripts/run_ablation_re2.py --phase all --skip-existing
  --variants v0_none,v1_src,v2_src_tests,v3_all,v4_src_gaps
  --strategy cot_strict,few_shot --only-models codestral-22b
  --attempt-offset 700000 >> /tmp/exp7_re2_s1.log 2>&1 &`
- Unmodified `AblationRunner` / `run_ablation_re2.py`; non-default
  cells write under the `<strategy>/` path + `,strategy=<name>` salt
  (invariant 9) — `experiment2_1` default cells read-only via
  `--skip-existing`, never touched. No logprobs requested (cache key
  byte-identical to pre-experiment4 for every cell).
- Health monitor: **0 `400 Budget exceeded`** lines; seeds accumulating
  (proxy accepting traffic). Stage 2 (`self_critique`, offset 705000)
  and Stage 3 (`prompt_chain`, offset 710000) gated on this staying
  clean + a `cost_audit.py` headroom check between stages.
- Aggregator `analysis/scripts/experiment5_rank.py` (+tests) under
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
- Aggregator `analysis/scripts/experiment5_rank.py` (+15 passing tests,
  ruff clean) completed and is committed; it already classifies sub-150
  cells as CENSORED (not padded — invariant 4), so a "cot_strict
  collapses" outcome is representable in the ranking as a disclosed
  negative result.

## 2026-05-18 — cot_strict collapse DIAGNOSED (free; mechanism corrected)

Zero-proxy-cost analysis of the 492 cached `cot_strict` codestral-RE2
responses from stage 1:

- **It is NOT a parse failure.** `parse_regex_response` succeeds on
  **462/492 (94%)**; content median 1080 chars (well under the 2048
  LiteLLM cap); `is_degenerate_loop` trips 0/492. The runner's
  "synthesis capped: too many parse failures" is its *generic* no-new-
  seeds label, not the actual cause.
- **True mechanism = generation-diversity collapse.** The 462 OK
  responses yield **1384 parsed seed-instances but only 142 UNIQUE
  `content_b64`** (diversity ratio **0.103**). Five regexes dominate:
  `(?P<x>a+)` ×264, `(a*)*` ×192, `\p{Greek}+` ×162, `a{1000,}` ×125,
  `[^a-zA-Z0-9]` ×108. The rigid 4-step CoT scaffold suppresses
  codestral's sampling diversity on RE2.
- **Why cells censor at 14–32:** seeds are content-addressed
  (`seed_{sha256(target|sample|idx|content_b64)}.bin`), so a repeated
  regex overwrites the same file and `_count_seeds` does not grow → the
  20-attempt no-gain early-exit fires. Correct, invariant-consistent
  behaviour (dedup + 150-floor + no padding, invariant 4).
- **Intrinsic & unfixable by more generation:** only 142 distinct
  seeds exist across ALL cot_strict generations — < 150. cot_strict is
  therefore **CENSORED for codestral-22b/RE2**, with the precise cause
  being diversity collapse, not parsing. This is a clean negative
  result and is *sharper* than the pre-registered prediction (which
  expected cot_strict ≈ default at high-context, not a cross-variant
  diversity collapse). The pre-registration (`5b96f61`) declared all
  outcomes admissible and is unchanged.
- TODO at finalization (zero proxy cost): run `--phase m1,m2
  --skip-existing --strategy cot_strict` on the existing partial
  cot_strict seed dirs so each gets a `summary.json` with `n_seeds`<150
  → the aggregator marks them CENSORED (not MISSING) with the true
  count.

## 2026-05-18 — Stage continues: few_shot (offset 715000)

- Per the user-selected path (diagnose → continue staged, prompt_chain
  gated). cot_strict NOT re-run (write-off). Launch few_shot only,
  `--skip-existing` (default cells free/cached; cot_strict partial dirs
  untouched), `--attempt-offset 715000` (≥5000 bump, invariant 5;
  distinct from the 705000/710000 earmarked for self_critique/
  prompt_chain). Monitor specifically for the diversity-saturation
  signature (seeds plateau while attempts climb; unique/total ratio),
  not just the runner's parse-failure label.

## 2026-05-18 — Cost fail-safe hardened (user-approved, opt-in)

- Verified the EXISTING fail-safe works: `scripts/_ablation_base.py`
  20-attempt no-gain sliding window + MAX_ATTEMPTS=300; it DID fire for
  cot_strict (aborted v0_none@63, v1_src@44, v2_src_tests@132 — not
  300). Gap: slow (20-window, reset by trickle uniques) + no persisted
  failure artifact.
- Implemented (additive, default byte-identical — user-approved option
  "opt-in tighter+rate, default unchanged + always-on stats"):
  `abandon_policy()` reads `UTCF_ABANDON_NOGAIN` (unset/blank/invalid/<1
  → legacy `(20, False, None)`; valid int → tighter no-gain window +
  yield-ceiling guard; `UTCF_ABANDON_WARMUP` default 30). `_run_cell`
  now: (a) ALWAYS writes behaviour-neutral `_synthesis_stats.json` into
  seeds_dir (invisible to all `*.bin` consumers — verified by test);
  (b) the yield-ceiling guard abandons (reason `yield_ceiling`) when,
  after warmup, realised unique-seed rate projects > MAX_ATTEMPTS to
  reach 150 — the true cot_strict-style "lost cause" signal. Dollars
  NOT computed in-runner (invariant 7); orchestration facts persisted
  so $ is derivable from the single pricing source.
- Guard test `scripts/tests/test_abandon_policy.py` (11 passed) pins
  default==legacy byte-identity + stats-artifact invisibility. Full
  fast suite **416 passed, 1 skipped**; ruff clean.
- Post-hoc lost-cause analyzer `analysis/scripts/seed_yield_audit.py`
  under construction in parallel (offline; flags LOST_CAUSE +
  est wasted $).

## 2026-05-18 — few_shot stage = VIABLE

- `few_shot` filled 150 on v0_none/v1_src/v2_src_tests/v3_all
  (v4_src_gaps in progress) with NO early-exit/cap — diversity ratio
  ~0.80 (vs cot_strict 0.10). Confirms: the exemplar scaffold preserves
  codestral diversity; the rigid 4-step CoT scaffold is what collapses
  it. Stage ran under legacy abort behaviour (process predates the
  abandon-policy edit; legacy == new for unset env anyway).
- Next: when few_shot completes → stage 2 `self_critique` launched WITH
  `UTCF_ABANDON_NOGAIN=5` (aggressive opt-in) so a cot_strict-style
  collapse is abandoned within ~5 no-gain attempts / at the
  yield-ceiling instead of bleeding ~$1-2. Then the gated cheap
  prompt_chain probe.

## 2026-05-18 — few_shot COMPLETE (viable) + analyzer landed

- `few_shot` filled **150/150 on all 5 variants**, scored, no budget
  error. M2 (slices.all.union_frac_targets_hit): v0_none 0.467,
  v1_src 0.333, v2_src_tests 0.400, v3_all 0.467, **v4_src_gaps 0.733**.
- `analysis/scripts/seed_yield_audit.py` (+10 tests, ruff clean) built.
  Real smoke: `cot_strict` LOST_CAUSE on all 5 variants (parse_ok 94%,
  diversity 0.105, distinct ceiling 124<150), est wasted **$0.49**
  (cost basis cited: cost_audit mean tokens × PRICING_USD_PER_MTOK);
  `few_shot` VIABLE (realized 150/150). Subagent surfaced that the
  regex-level re-parse diversity differs from the content_b64-level
  probe (position-dependent sha256 flag bytes); it added a
  realized-fill VIABLE override so verdicts match the runner's true
  fill semantics (few_shot 150→VIABLE; cot_strict <150→LOST_CAUSE).
  Sound, test-pinned; kept.
- Next: stage 2 `self_critique` (2× calls; draft round reuses the
  default base template so it should keep diversity) launched WITH
  `UTCF_ABANDON_NOGAIN=5` — first live use of the aggressive opt-in
  abandon, so a cot_strict-style collapse is killed in ~5 no-gain
  attempts / at the yield-ceiling. Offset 705000 (pre-registered).

## 2026-05-18 — self_critique partial collapse; aggressive fail-safe WORKED

- `self_critique` (2× calls): only **v2_src_tests filled 150**
  (M2=0.467). The other 4 cells censored by the aggressive opt-in
  abandon (first live use, `UTCF_ABANDON_NOGAIN=5`):
  - v0_none 121 seeds, reason `nogain_window`, 185 attempts (M2 0.40)
  - v1_src 95, `nogain_window`, 151 attempts (M2 0.40)
  - v3_all 54, `nogain_window`, 72 attempts (M2 0.533)
  - **v4_src_gaps 59, reason `yield_ceiling`** — log: "projected 303
    attempts to reach 150 (> max 300) at rate 0.496"; abandoned at 117
    attempts (M2 0.667). The new yield-ceiling guard fired exactly as
    designed (the true "practically-guaranteed-failure" stop).
  - `_synthesis_stats.json` written for every cell (final_seeds,
    reason, no_gain_rate, strategy_calls_per_seed) — always-on artifact
    confirmed in production.
- Cost (cost_audit): litellm $15.16 → **$17.74** (+$2.58; grand total
  $104.00 / 17,272 entries). Headroom to $25 cap ≈ $7.3. 0 budget
  errors. The fail-safe demonstrably saved spend (censored cells cut at
  72–185 attempts, not ground to 300).
- `prompt_chain` (3× calls) is the last stage and shares cot_strict's
  rigid-multi-call collapse risk near a shrinking cap → run a single
  fail-safe-BOUNDED probe cell (v2_src_tests, the variant that filled
  for self_critique) under `UTCF_ABANDON_NOGAIN=5`, offset 710000;
  worst-case cost is bounded by the 5-window/yield-ceiling (~$0.2). Then
  surface the probe result + a full-sweep go/no-go before any larger
  prompt_chain spend.

## 2026-05-18 — prompt_chain probe = VIABLE (falsifies my prediction)

- `prompt_chain` v2_src_tests probe (3× calls, aggressive abandon
  armed): **FILLED 150** (reason `filled`, 190 attempts dispatched,
  no_gain_rate 0.246), **M2=0.467**. It did NOT collapse like
  cot_strict — the rigid 3-call scaffold still produced enough seed
  diversity for codestral on this variant. This **falsifies my
  pre-registered claim 2** ("prompt_chain UNDERperforms / collapses at
  high-context"); honest negative on my prediction, recorded as-is
  (the pre-registration declared all outcomes admissible).
- Cost: litellm $17.74 → **$18.37** (+$0.63 for the single 3× cell).
  Headroom to $25 ≈ **$6.6** (re-price; proxy internal counter opaque).
  0 budget errors. The fail-safe was armed but not needed (cell filled).
- Decision surfaced to the user (gated): the remaining 4 prompt_chain
  cells ≈ +$2.5–$4 (3×; some may fill, some may abandon under the
  fail-safe), landing ~$21–22 re-price — within ~$3 of a hard,
  opaque, mid-run-killing cap. The headline finding (strategy
  *reliability* dominates: default/few_shot viable; cot_strict
  diversity-collapses; self_critique mostly collapses) is already in
  hand; the per-cell pre-registration + CENSORED handling make a
  partial-but-honest prompt_chain result fully valid. Awaiting the
  user's spend call before any further prompt_chain generation.

## 2026-05-18 — experiment5 phase 1 COMPLETE (results observed)

- prompt_chain 2-cell subset: v0_none 146 (nogain_window @196, M2 0.533),
  **v3_all 6 seeds (nogain_window @14 — collapsed instantly, +$0.26 for
  both)**, v2_src_tests 150 (filled, M2 0.467). cot_strict partial dirs
  scored zero-proxy → CENSORED with true n (18/14/32/124; v4 n=0
  MISSING). self_critique CENSORED cells already had partial-corpus M2.
- `experiment5_rank` (25 cells, 12 rankable) + `seed_yield_audit` run.
  **Headline: no strategy beats `default` at any variant** (no
  Holm-significant contrast; Friedman n.s. @v2 p=0.147; not computable
  elsewhere because most non-default cells censored). Dominant effect =
  strategy *reliability*: default/few_shot fill 150 everywhere;
  cot_strict collapses everywhere; self_critique fills 1/5; prompt_chain
  fills 2/5 (collapses v3_all). Full table + adjudication vs `5b96f61`
  in RESULTS.md (claim "some strategy beats default" FALSIFIED;
  strategy×variant interaction SUPPORTED but via fillability).
- Cost (cost_audit): litellm $14.39 → **$18.63** (≈$4.2 total exp5);
  **$25 cap never hit**, 0 budget errors; the opt-in fail-safe
  demonstrably saved spend (prompt_chain@v3_all cut at 14 attempts;
  self_critique@v4 stopped by yield-ceiling). seed_yield_audit est.
  wasted ≈ $0.27.
- Phase-2 (harfbuzz) trigger evaluated: RE2 yields a clear negative/
  characterization result → recommendation NOT to spend on a harfbuzz
  strategy sweep on this evidence (separate pre-registration if ever
  pursued). experiment5 phase 1 COMPLETE; stopping criterion satisfied.

---

## 2026-05-18 — RENUMBERING (label-only; git provenance immutable)

Per user request the new-iteration experiments were renumbered to be
contiguous after the historical experiment1–3:
`experiment6→4 (ESSS)`, `7→5 (SVA)`, `8→6 (Follow-up A)`,
`9→7 (Follow-up B)`; the experiment-6 Follow-up C doc moved to
`docs/experiment4/FOLLOWUP.md`. **Only labels/identifiers changed**
(doc dirs, module names, output-data dirs, prose, cross-references).
The pre-registered predictions, effect sizes, falsifiers, **commit
hashes and timestamps are byte-unchanged** — this is not a content
edit to the frozen pre-registration. Git history was NOT rewritten
(safety): the original pre-registration/run commits and the branches
pushed to origin remain named `experiment6`, `experiment7`,
`experiment-followups`. Therefore any `branch`/`base_branch` field
above that now reads a renumbered label refers to the *experiment
identity*; the authoritative immutable anchors are the **commit
hashes** (hex, unchanged). Old↔new map is mirrored in
`docs/EXPERIMENTS.md` and `docs/STATUS.md` (Naming).
