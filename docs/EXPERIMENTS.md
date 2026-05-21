# EXPERIMENTS — unified, replication-grade index

**Audience.** (a) A *blind reader* with no filesystem access who must
understand what every experiment asked, found, and changed; (b) a
*replicator* with the repo who must reproduce any result exactly. Every
experiment has a one-line question, an exact scope, a copy-paste
replication recipe, frozen headline numbers, a pointer to its detailed
docs, and a precise **Built / Added / Removed** provenance.

This file is an *index and provenance ledger*. The authoritative detail
for each experiment lives in the per-experiment docs named below; where
this file and a per-experiment doc disagree, the per-experiment doc
(and, for numbers, the on-disk `summary.json` it cites) wins.

## Numbering & git-provenance note (renumbered 2026-05-18)

The iteration experiments were renumbered to run contiguously after the
historical `experiment1–3`. **All docs / code modules / output-data
dirs use the NEW numbers.** Map:

| New | Old | What it is |
|---|---|---|
| `experiment4` | `experiment6` | ESSS — entropy-stratified seed selection |
| `experiment5` | `experiment7` | SVA — strategy-as-variant ablation |
| `experiment6` | `experiment8` | Follow-up A — cot_strict mechanism isolation |
| `experiment7` | `experiment9` | Follow-up B — cross-target reliability |
| `experiment4/FOLLOWUP.md` | `experiment6/FOLLOWUP.md` | Follow-up C — exp4 mechanism check |

**Immutable git provenance (history NOT rewritten — safety):** the
original pre-registration/run commits, and the branches pushed to
`origin`, retain the OLD labels — branches `experiment6`, `experiment7`,
`experiment-followups`. Frozen pre-registration **commit hashes and
timestamps are unchanged** (the authoritative anchors; hex hashes were
never touched). Only experiment *identity* labels were renumbered; any
`branch`/`base_branch` field inside a frozen pre-reg doc that now shows
a renumbered label refers to the experiment identity — resolve the
actual VCS branch via the map above + the commit hash.

**External working-doc references.** A few frozen experiment artifacts —
pre-registration docs (`experiment*/MANIFEST.json`,
`experiment*/METHODS.md`), append-only execution logs
(`experiment*/EXECUTION_LOG.md`), and the frozen
`dataset/fixtures/cot_examples_pool.json` — cite external review briefs
by bare filename (`EXPERIMENT_DEEP_DIVE.md`,
`PROJECT_CONTEXT_FOR_WEB.md`). Those were idea- and
report-generation working aids, never part of the tracked repo, and are
deliberately kept out of git history (brainstorming artifacts are not
committed); on the author's machine they live under the gitignored
`scratch/` dir. The frozen artifacts are left byte-for-byte intact. Every
pre-registered hypothesis they informed is restated self-containedly in
the relevant `experiment*/RESULTS.md` and in
`docs/experiment_iteration_summary.md`, so no tracked result depends on
them.

---

## 0. Global prerequisites (replication, once)

| Need | Where / how |
|---|---|
| Python env | `.venv` is pre-populated; `source .venv/bin/activate` (do not reinstall). |
| Fast tests (no net/LLM/LLVM) | `.venv/bin/pytest -q` (expect all green; one opt-in cache-audit skips). |
| LLVM toolchain | `clang-15`, `llvm-cov-15`, `llvm-profdata-15` (coverage builds, M1/M2 replay). |
| LLM keys | `secrets/claude_key` (Anthropic). UF LiteLLM proxy `https://api.ai.it.ufl.edu` for llama/codestral/etc.; **no key file** — set `UTCF_LITELLM_URL`. `secrets/` gitignored. |
| Build targets once | `dataset/scripts/fetch_target.sh` + `build_instrumented.sh` for `re2` / `harfbuzz`; freeze M2 once: `python -m analysis.scripts.freeze_target_branches --target {re2_v2,harfbuzz}` (DO NOT re-freeze — it invalidates prior runs). |
| Resume protocol | Read `docs/RESUME.md` if present, else `docs/STATUS.md`, before any stateful work. |
| Cost discipline | Dollar figures must cite a command: `analysis/scripts/cost_audit.py` (actual, walks `.cache/llm/`) or `estimate_cost.py` (projected). UF LiteLLM has a **proxy-side $25 budget cap** (blocked `experiment3_1` at $25.01) — estimate before generating. |

**Load-bearing invariants (all experiments).** (1) No fabricated tests —
extractors raise, never synthesize. (2) M2 hard-branch filter is
`struct_hits ≥ 1 AND rand_hits == 0`, frozen; random scores exactly
0.000 by construction. (3) RNG seed = 42 for all subsampling. (4)
150-seed normalization per scored cell. (5) Cache salt includes
`attempt+offset`; bump `--attempt-offset` ≥ 5000 on restart. (9)
`default`-strategy cache key is byte-identical to pre-strategy code;
non-default strategies append `,strategy=<name>`. Detail + the incident
behind each: `docs/EXPERIMENT_WALKTHROUGH.md §7` (invariants) and
`docs/experiment2.md §3` (the `experiment2_0` M2-filter incident).

---

## 1. experiment1 — RE2 single-model A/B (llama-3.1-8b)

- **Question.** Does adding an uncovered-branch list on top of source
  code help, and does the advantage generalize to held-out files?
- **Scope.** RE2 only, llama-3.1-8b-instruct, regex input format, 6
  sub-versions (`experiment1_0..1_5`).
- **Headline (frozen).** Regex-format A/B: `exp1` (gap-targeted) 1243
  edges vs `exp2` (source-only) 1133 vs random 980. The +110 in-dist
  lead **collapses to +3 on held-out files** (`experiment1_3`); the
  efficient recipe is `exp2_plus_gaps` (1250) — source + cheap gap
  annotations, no tests (`experiment1_4`).
- **Replicate.** `docs/STATUS.md §7` has the exact command block
  (generate_inputs / generate_source_inputs / measure_coverage /
  ab_coverage_diff). Cost ≈ $0.01, ~2 min.
- **Detail / numbers.** `docs/experiment1.md` (authoritative).
- **Provenance.** Historical (on `master`); built/added/removed is in
  `docs/experiment1.md` + `docs/STATUS.md §11` changelog + git history.

## 2. experiment2 — Multi-model context ablation + real-fuzzer campaigns

- **Question.** How does prompt-context (`v0_none`→`v4_src_gaps`)
  interact with model across two targets, by M1/M2 and by fuzzer
  campaign?
- **Scope.** RE2 + harfbuzz, 7 models, 5 variants, 150 seeds/cell.
  Sub-versions `experiment2_0` (INVALIDATED — weak M2 filter; the
  canonical worked example of how M2 can be wrong), `experiment2_1`
  (HEADLINE), `experiment2_2` (campaigns, concept-proof only).
- **Headline (frozen).** RE2 best M2 **0.867** (nemotron-120b @
  `v1_src`); harfbuzz best M2 **0.640** (sonnet @ `v0_none`).
  codestral-22b is the most reliable free model (RE2 M2 ≤ 0.800,
  harfbuzz ≤ 0.480; `v1_src`=0.46, `v3_all`=0.26 on harfbuzz — the
  baselines experiment4 builds on). Random M2 = 0.000.
- **Replicate.** `docs/experiment2.md §4.5`:
  `nohup .venv/bin/python scripts/run_ablation_{re2,harfbuzz}.py
  --phase all --skip-existing`.
- **Detail / numbers.** `docs/experiment2.md` (authoritative;
  `experiment2_0` post-mortem in §3); `PROJECT_CONTEXT §5.2`.
- **Provenance.** Historical (`master`); see `docs/experiment2.md` +
  `docs/STATUS.md §11`.

## 3. experiment3 — Prompt-strategy axis

- **Question.** Add *strategy* (how we prompt) orthogonal to *variant*
  (what context); does it help?
- **Scope.** Framework for `target × variant × model × strategy`
  (`experiment3_0`, code-only, commit `e64bf5e`); a 5-cell
  CoT×RAG×tools isolation on RE2/gpt-oss-20b (`experiment3_1`, BLOCKED
  on the UF proxy $25 cap at $25.01).
- **Headline (frozen).** 7 strategies registered
  (`default,cot_strict,few_shot,self_critique,prompt_chain,tool_use,
  tool_use_retrieval`); `default` cache byte-identical (14k entries
  preserved). **End-to-end scored strategy sweep never run** — that is
  the gap `experiment5` closes.
- **Replicate.** `docs/experiment3.md §3.5` (queued push-button, blocked
  on credit).
- **Detail.** `docs/experiment3.md`; `PROJECT_CONTEXT §5.3, §8 gap 6,
  §9 Q5`.
- **Provenance.** Historical (`master`); `docs/experiment3.md`.

## 6. experiment4 — Entropy-Stratified Seed Selection (ESSS) — COMPLETE

- **Question.** Within one `(harfbuzz × variant × codestral-22b ×
  default)` cell, does per-seed mean *payload entropy* predict which of
  the 150 seeds carry the M2 union score?
- **Scope.** harfbuzz, codestral-22b, `{v1_src, v3_all}`, default
  strategy, Stages 0–1 only. Branch `experiment4`.
- **Result (frozen; `docs/experiment4/RESULTS.md`).** Stage 0 logprob
  gate = **FULL**. Pre-registered (commit `4d96b1c`,
  2026-05-17T23:52:23Z, before scoring) prediction "high-entropy carries
  M2" is **FALSIFIED for both variants** (all four bootstrap CIs contain
  0; both Δ_hl sign-reversed). The data leans to the pre-registered
  *competing* hypothesis — **low**-entropy seeds carry more M2:
  | Variant | M2(S_random) | M2(S_high) | M2(S_low) |
  |---|---:|---:|---:|
  | v1_src | 0.26 | 0.38 | 0.46 |
  | v3_all | 0.26 | 0.26 | **0.50** |
  Δ_hr v1_src +0.12 [−0.06,+0.26], v3_all 0.00 [−0.10,+0.14]; Δ_hl
  v1_src −0.08 [−0.30,+0.20], v3_all −0.24 [−0.34,+0.04]. Inverse
  outcome, reported as one of the three pre-declared admissible outcomes.
- **Replicate.** `docs/experiment4/METHODS.md` §7 + the commands in
  `docs/experiment4/EXECUTION_LOG.md`; driver
  `scripts/run_experiment4_harfbuzz.py` (sandboxed output roots),
  analysis `analysis/scripts/experiment4_{stage0_probe,entropy,
  stratify,score}.py`. Logprob capture is env-gated
  (`UTCF_CAPTURE_LOGPROBS`); default-off ⇒ cache byte-identical.
- **Detail.** `docs/experiment4/{MANIFEST.json,METHODS.md,
  STAGE0_RESULT.md,EXECUTION_LOG.md,RESULTS.md}` (replication-grade).

### experiment4 — Built / Added / Removed (precise, from git, branch `experiment4`)

| Action | Path | Note |
|---|---|---|
| Added | `analysis/scripts/experiment4_stage0_probe.py` | Stage 0 logprob-capability probe |
| Added | `analysis/scripts/experiment4_entropy.py` | payload-masked top-K-head entropy (METHODS §3/§4) |
| Added | `analysis/scripts/experiment4_stratify.py` | deterministic S_random/S_high/S_low (inv 3/4) |
| Added | `analysis/scripts/experiment4_score.py` | M2 via UNMODIFIED metric + seed bootstrap |
| Added | `analysis/tests/test_experiment4_{entropy,stratify,score}.py` | unit suites (synthetic, offline) |
| Added | `scripts/run_experiment4_harfbuzz.py` | ~40-LOC AblationRunner wrapper, redirected output roots |
| Added | `tests/test_llm_client_logprob_backcompat.py` | freezes the pre-change `_prompt_hash` digest |
| Added | `docs/experiment4/{MANIFEST.json,METHODS.md,STAGE0_RESULT.md,EXECUTION_LOG.md,RESULTS.md}` | replication docs |
| Modified | `core/llm_client.py` | additive opt-in `logprobs/top_logprobs` (cache key byte-identical when unused; +`_extract_logprobs`) |
| Modified | `synthesis/scripts/generate_ablation_inputs.py` | env-gated default-strategy logprob request + per-seed sidecar persistence (no-op when env unset) |
| Modified | `docs/STATUS.md` | §11 changelog entry + "Last updated" |
| Added→Removed | `docs/RESUME.md` | created during the run (commit `1293882`), retired when complete (commit `7627ad8`) per the resume protocol |
| Untouched (by design) | `analysis/metrics/m2.py`, `analysis/scripts/{freeze_target_branches,measure_gap_coverage}.py`, `core/{variants,prompt_strategies}.py` | M2 filter / frozen sets / registries never modified |

Commit trail (branch `experiment4`, base `master`): `828fe0b` →
`83bc5f0` → `a2a51e4` → `d1a44ab` → `4d96b1c` (pre-registration) →
`1293882` → `10f2a88` → `0ae16a1` → `e37cdfe` → `7627ad8` (results).
Two pre-results instrument refinements (SentencePiece byte-fallback
detokeniser; strict→structural payload masking, user-approved) are
logged in `docs/experiment4/EXECUTION_LOG.md`; the pre-registration
predates and is unchanged by both.

## 7. experiment5 — Strategy-as-Variant Ablation (SVA) — COMPLETE (phase 1, RE2)

- **Question.** Treating prompting *strategy* as an axis next to
  *variant*: which `(variant, strategy)` cell maximizes M2/M1 on
  `RE2 × codestral-22b`, does any strategy beat `default`, and does
  strategy interact with variant? (Closes `PROJECT_CONTEXT §8 gap 6`,
  `§9 Q5`.)
- **Scope (locked w/ user 2026-05-18).** RE2 first (harfbuzz only if
  signal — separately pre-registered), codestral-22b only, 5×5 grid
  `{v0_none,v1_src,v2_src_tests,v3_all,v4_src_gaps} ×
  {default,cot_strict,few_shot,self_critique,prompt_chain}`, single
  150-seed draw + bootstrap + Friedman/Nemenyi. Branch `experiment5`.
- **Result (frozen; `docs/experiment5/RESULTS.md`).** Pre-registered
  `5b96f61` before scoring; staged cheapest-first under the $25-cap
  gate. **No strategy beats `default` at any variant** (no
  Holm-significant contrast; Friedman n.s. @v2 p=0.147, not computable
  elsewhere). Dominant effect = strategy **reliability**:
  `default`/`few_shot` fill 150 on all 5 variants; `cot_strict`
  mode-collapses (5 regexes ≈60% of output, 142 unique < 150) at every
  variant; `self_critique` fills 1/5; `prompt_chain` fills 2/5
  (collapses at high-context `v3_all`, 6 seeds). Best cell:
  `default @ v3_all` M2 **0.800**. Pre-registered "some strategy beats
  default" **FALSIFIED**; strategy×variant interaction **SUPPORTED** but
  via fillability, not M2-of-filled. Total spend ≈ $4.2; $25 cap never
  hit (the opt-in fail-safe cut collapsed cells early — e.g.
  `prompt_chain@v3_all` at 14 attempts).
- **Replicate.** Exact commands in `docs/experiment5/METHODS.md §7`.
  Uses the UNMODIFIED `scripts/run_ablation_re2.py`; non-default cells
  write under `results/ablation_re2_v2/<strategy>/m{1,2}/...` (disjoint
  from the default `experiment2_1` cells; nothing canonical touched).
- **Detail.** `docs/experiment5/{MANIFEST.json,METHODS.md,
  EXECUTION_LOG.md(+pre-registration),RESULTS.md}`.

### experiment5 — Built / Added / Removed (precise, from git, branch `experiment5`)

| Action | Path | Note |
|---|---|---|
| Added | `docs/experiment5/{MANIFEST.json,METHODS.md,EXECUTION_LOG.md,RESULTS.md}` | replication docs + pre-registration + cost-gate record |
| Added | `analysis/scripts/experiment5_rank.py` + `analysis/tests/test_experiment5_rank.py` | ranking aggregator (M2/M1 table, bootstrap CIs, Friedman/Nemenyi/CD, Holm Wilcoxon) — built because `ablation_summary.py` is pinned to the invalidated `experiment2_0` path |
| Added | `analysis/scripts/seed_yield_audit.py` + `analysis/tests/test_seed_yield_audit.py` | offline lost-cause auditor: per-cell VIABLE/MARGINAL/LOST_CAUSE + est. wasted $ + ABANDON recommendation |
| Added | `scripts/tests/test_abandon_policy.py` | pins default==legacy byte-identity + stats-artifact `*.bin`-invisibility |
| Modified | `scripts/_ablation_base.py` | **additive, default byte-identical**: `abandon_policy()` (opt-in `UTCF_ABANDON_NOGAIN`/`UTCF_ABANDON_WARMUP` → tighter no-gain window + yield-ceiling guard) + always-on behaviour-neutral `_synthesis_stats.json`. Env unset ⇒ legacy 20-window, no yield-ceiling (regression-pinned) |
| Modified | `docs/STATUS.md`, `docs/EXPERIMENTS.md` | index/changelog |
| Untouched (by design) | `scripts/run_ablation_re2.py`, `core/*`, `analysis/metrics/*`, `analysis/scripts/{measure_gap_coverage,freeze_target_branches}.py`, `synthesis/scripts/parse_synthesis.py` | reuses the orchestrator + metric unchanged; **no logprobs requested** ⇒ every cache key byte-identical; M2 filter/frozen set untouched |

Commit trail (branch `experiment5`, base `experiment4` HEAD `7627ad8`):
`5b96f61` (pre-registration) → … (appended as stages complete; see
`docs/experiment5/EXECUTION_LOG.md`).

---

## Branch / merge state (for the replicator)

`experiment4` and `experiment5` are **branches off `master`**, not
merged. `master` is the clean base; `experiment4` adds the ESSS work;
`experiment5` branches from `experiment4` (so all experiment docs
co-exist for this index) and adds SVA. Each branch is an independent,
revertable checkpoint trail with no tooling attribution in commit
messages. To reproduce a given experiment, check out its branch and
follow that experiment's `METHODS.md §7` (or `docs/STATUS.md §7` for
`experiment1`). Gitignored outputs (`results/`, `synthesis/results/`,
`.cache/llm/`, `secrets/`) are regenerated by the recipes, not
committed.

## Where the authoritative numbers live

Frozen headline numbers: `docs/experiment{1,2,3}.md`,
`docs/experiment4/RESULTS.md`, `docs/experiment5/RESULTS.md`.
Per-cell ground truth: the on-disk
`results/ablation_<target>[/<strategy>]/m{1,2}/<variant>/<model>/summary.json`
(gitignored — regenerate). Cost ground truth:
`analysis/scripts/cost_audit.py`. Cross-experiment narrative:
`docs/experiment_iteration_summary.md`. Load-bearing-invariant incident
history: `docs/EXPERIMENT_WALKTHROUGH.md §7`.
Living "what is running now": `docs/STATUS.md` (or `docs/RESUME.md`).

## 8/9 + experiment4-FOLLOWUP — iteration follow-ups (branch `experiment-followups`)

- **Question.** Mechanism-isolate the experiment5 `cot_strict` collapse
  (A), test its target-generality (B), and check experiment4's
  low-entropy→M2 per-seed mechanism (C). Pre-registered+frozen
  (`44d8104`) before any run; cost-gated; ≈$0.7 total.
- **Result (frozen).**
  - **experiment6 (A)** `docs/experiment6/RESULTS.md`: pre-reg
    FALSIFIED — the **static in-template example list** (not the rigid
    4-step labels) causes the `cot_strict` diversity collapse.
    `cot_strict_no_examples` FILLED 150 @ M2 **0.800** (= `default`);
    `cot_strict_rotated_examples` FILLED @ 0.667; `cot_strict_no_labels`
    CENSORED 50 (collapsed onto the example regexes).
  - **experiment7 (B)** `docs/experiment7/RESULTS.md`:
    `cot_strict@harfbuzz/v3_all` FILLED 150 (no collapse — binary cot
    has no RE2 example list; corroborates A) but M2 **0.120** vs
    `default` 0.260 → a *general* rigid-label M2 penalty distinct from
    the example-anchor fill collapse.
  - **experiment4 FOLLOWUP (C)** `docs/experiment4/FOLLOWUP.md`: no-LLM;
    deep-reach & per-seed M1 ~flat across entropy quartiles (Q1−Q4 ≤
    0.061 ≪ ±0.10) → experiment4's low-entropy→M2 is **union-level
    complementarity**, the per-seed "valid→reaches deep" mechanism is
    **FALSIFIED**.
  - Cross-experiment 1-pager: `docs/experiment_iteration_summary.md`.
    Supersedes the earlier default-vs-`cot_strict` inference that rigid
    CoT labels caused the collapse.
- **Built/Added/Removed (git, branch `experiment-followups`).** Added:
  `core/prompt_strategies.py` (+3 strategy classes & registry entries);
  `synthesis/prompts/ablation_synthesis_regex_cot_{noex,rot,nolbl}.j2`;
  `dataset/fixtures/cot_examples_pool.json`;
  `analysis/scripts/experiment4_followup.py` (+`analysis/tests/test_experiment4_followup.py`);
  `docs/experiment6/*`, `docs/experiment7/*`, `docs/experiment4/FOLLOWUP.md`,
  `docs/experiment_iteration_summary.md`. Modified (additive):
  `synthesis/scripts/generate_ablation_inputs.py` (3 dispatch entries +
  rotated-examples threading), `tests/test_phase9_integration.py` &
  `scripts/tests/test_ablation_base_cli.py` (drift-guards updated in
  lockstep), `docs/STATUS.md`, `docs/EXPERIMENTS.md`. Untouched
  (invariants): M2 filter / frozen sets / `measure_gap_coverage` /
  default cache layout (byte-identical, guarded — 426 tests pass).
