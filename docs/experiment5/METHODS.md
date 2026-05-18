# experiment5 — METHODS (replication-grade; written before any scoring)

A blind reader with only this repo should be able to reproduce every
number. Every methodological choice is justified here before the code
that depends on it. Contradictions are logged in `EXECUTION_LOG.md`, not
silently edited.

## 1. Question & hypotheses

Treat the prompting **strategy** as an ablation axis next to the context
**variant**. On `RE2 × codestral-22b`, for the 5×5 grid
`variant ∈ {v0_none,v1_src,v2_src_tests,v3_all,v4_src_gaps} ×
strategy ∈ {default,cot_strict,few_shot,self_critique,prompt_chain}`:

1. **Ranking:** which `(variant, strategy)` cell maximizes M2 (primary)
   and M1 (secondary)?
2. **Strategy main effect:** does any non-`default` strategy beat
   `default` at the same variant, after multiple-comparison correction?
3. **Interaction:** is the best strategy the *same* across variants
   (no interaction) or variant-dependent (interaction)? (PROJECT_CONTEXT
   §9 open-question 5.)

This closes PROJECT_CONTEXT §8 gap 6 ("strategy sweep not run
end-to-end"). The `experiment3` framework already supports the
`variant × strategy × model` matrix; experiment5 is the first end-to-end
*scored* run of it plus a principled ranking analysis.

## 2. Metric — exactly the existing pipeline

M2(cell) = `slices.all.union_frac_targets_hit` from the **unmodified**
`analysis.metrics.M2HardBranchMetric` (→ `measure_gap_coverage.py`),
scored against the frozen RE2-v2 set
`dataset/fixtures/re2_ab_v2/re2/m2_target_branches.json` (15 hard
branches; filter `struct_hits ≥ 1 AND rand_hits == 0`, **invariant 2**,
not re-frozen). M1 = total union edges from `M1EdgesMetric`. Random
anchor M2 = 0.000 by construction; every cell reported absolute **and**
vs-random per `analysis/CLAUDE.md`.

The `default × v0..v4` RE2 codestral cells already exist
(`results/ablation_re2_v2/m2/<variant>/codestral-22b/summary.json`,
`experiment2_1`); they are the read-only baseline column and are NOT
regenerated (`--skip-existing`). Non-`default` strategy cells write to
`results/ablation_re2_v2/<strategy>/m{1,2}/<variant>/codestral-22b/` —
disjoint from the default path by construction (invariant 9), so the
canonical `experiment2_1` cells are never touched.

## 3. Orchestration (reuse; no new orchestrator)

The **unmodified** `scripts.run_ablation_re2.py` → `AblationRunner`
already runs the matrix. Phases per cell: prep → synthesis → random →
m1 → m2 (the 150-seed retry/normalize loop, attempt-offset, and
strategy cache-salt are all AblationRunner's, unchanged). Exact
commands are in §7. `--attempt-offset 700000` (≥5000 bump, invariant 5;
clear of experiment4's 600000). No logprobs requested → cache key
byte-identical to pre-experiment4 for every cell (invariant 9).

## 4. Statistics & confound control

- **Per-cell uncertainty:** the existing `measure_gap_coverage` emits
  per-seed hit data + bootstrap CIs in each cell's `summary.json`. For
  between-cell comparisons we reuse the experiment4 seed-bootstrap
  pattern (resample the 150 seeds with replacement, recompute the union
  M2, n=10000, `random.Random(42)`, percentile [2.5,97.5]). The
  bootstrap RNG is a distinct, documented stream from the RNG-42 seed
  subsampling (same value, different call site) — both fixed for
  reproducibility.
- **Across-cell ranking:** Friedman test over the per-seed M2 profiles
  of the cells, Nemenyi post-hoc, and a critical-difference diagram
  (repo provides `analysis/scripts/{friedman_nemenyi,mann_whitney,
  vargha_delaney}.py`), with Holm correction for pairwise strategy-vs-
  `default` contrasts within each variant. Mandated by `analysis/CLAUDE.md`
  for multi-cell ablations.
- **Single-draw noise is acknowledged:** experiment4 demonstrated
  within-cell seed composition can swing M2 by ≥0.24. A bare "highest M2
  wins" is therefore NOT claimed; the ranking is reported *with* CIs and
  the Friedman/Nemenyi separation, and any "best cell" claim requires
  its CI to clear `default` at the same variant after Holm.
- **No model/target confound:** single model (codestral-22b), single
  target (RE2 phase 1). Strategy is the only axis crossed with variant.
- **Parse-rate confound:** non-`default` templates (esp. `prompt_chain`,
  3 sub-calls) have different parse rates → different attempt counts.
  The 150-seed floor + 20-consecutive-fail early-exit (AblationRunner)
  bound this; a cell that cannot reach 150 is reported as a censored
  result (its seed count + the abort), never silently padded.

## 5. Cost gate (hard pre-execution gate; invariant 7)

UF invoices $0 but the proxy enforces a **$25 cap** (blocked
`experiment3_1` at its reported $25.01). `estimate_cost.py`
(2026-05-18): 20 non-default cells ≈ **$8.6 typical / $15.8
conservative**; `default` cells = $0 (cache hits). Cumulative litellm
re-price (`cost_audit.py`) = $14.39 → apparent headroom ≈ $10.6, but the
proxy's internal counter is opaque. **Mitigation = staged cheapest-first
with live monitoring** (MANIFEST `staged_execution_plan`): stage 1
`cot_strict,few_shot` (1×), stage 2 `self_critique` (2×), stage 3
`prompt_chain` (3×); `--skip-existing`; abort on `400 Budget exceeded`;
each stage resumable with a bumped offset (invariant 5). Every stage's
actual cost is recorded in `EXECUTION_LOG.md` from `cost_audit.py`.

## 6. Determinism

Seed subsampling `random.Random(42)` (invariant 3); RE2 flag bytes
sha256-derived; bootstrap `random.Random(42)` at its own call site;
strategy cache salts deterministic (`make_cache_salt`). Re-running with
the same offset replays cached successes; a *restart* after partial
progress bumps the offset by ≥5000 (logged).

## 7. Exact replication commands

```bash
# 0. Environment (pre-populated venv; LLVM-15 toolchain; UF proxy).
source .venv/bin/activate

# 1. Stage 1 (cheapest, ~$2.5): cot_strict + few_shot, 5 variants.
UTCF_LLM_RPM=12 nohup .venv/bin/python scripts/run_ablation_re2.py \
  --phase all --skip-existing \
  --variants v0_none,v1_src,v2_src_tests,v3_all,v4_src_gaps \
  --strategy cot_strict,few_shot --only-models codestral-22b \
  --attempt-offset 700000 >> /tmp/exp7_re2_s1.log 2>&1 &

# 2. Cost check, then Stage 2 (self_critique) — only if stage 1 clean:
.venv/bin/python -m analysis.scripts.cost_audit
UTCF_LLM_RPM=12 nohup .venv/bin/python scripts/run_ablation_re2.py \
  --phase all --skip-existing --variants v0_none,v1_src,v2_src_tests,v3_all,v4_src_gaps \
  --strategy self_critique --only-models codestral-22b \
  --attempt-offset 705000 >> /tmp/exp7_re2_s2.log 2>&1 &

# 3. Stage 3 (prompt_chain) — only if 1-2 clean + headroom:
UTCF_LLM_RPM=12 nohup .venv/bin/python scripts/run_ablation_re2.py \
  --phase all --skip-existing --variants v0_none,v1_src,v2_src_tests,v3_all,v4_src_gaps \
  --strategy prompt_chain --only-models codestral-22b \
  --attempt-offset 710000 >> /tmp/exp7_re2_s3.log 2>&1 &

# 4. Rank + stats (after cells exist):
.venv/bin/python -m analysis.scripts.experiment5_rank \
  --target re2 --model codestral-22b \
  --variants v0_none,v1_src,v2_src_tests,v3_all,v4_src_gaps \
  --strategies default,cot_strict,few_shot,self_critique,prompt_chain \
  --out-dir results/experiment5
```

Per-cell outputs land at
`results/ablation_re2_v2[/<strategy>]/m{1,2}/<variant>/codestral-22b/summary.json`
(gitignored). The ranked table + CD diagram + CIs land under
`results/experiment5/` and are summarised in `RESULTS.md`.

## 8. Stopping criterion

experiment5 phase 1 is complete when `RESULTS.md` contains: the 25-cell
M2 (and M1) table with per-cell bootstrap CIs, the Friedman result +
Nemenyi/CD diagram, the strategy-vs-`default`-within-variant Holm
contrasts, and prose adjudicated against the pre-registered ranking +
interaction prediction. No further strategies/models/targets are run in
phase 1. harfbuzz (phase 2) is a separate, separately-pre-registered
decision taken only if RE2 shows signal.
