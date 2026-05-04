# Head-to-Head — LLM Seed Generation vs RL Mutation Policy at Frozen Fuzzer States

**Status:** Draft for refinement. Open decisions in §10. Implementation has not started.

This document is intentionally self-contained so it can be iterated on outside the
working directory. Background sections summarise both source projects; jump to §3
for the experimental design.

---

## 1. Background — the two source projects

This experiment sits at the intersection of two existing projects in
`/home/shreyasganesh/projects/`:

### 1A. `llm-fuzzing/` (this repo)

A research framework that prompts LLMs to synthesise fuzzing seeds and measures
them against random baselines. Pipeline:

1. Build coverage-instrumented target (RE2, harfbuzz) with clang-15 LLVM
   instrumentation.
2. Extract upstream unit tests; compute coverage gaps.
3. Render a Jinja2 prompt with one of 5 ablation-variant context bundles
   (`v0_none`, `v1_src`, `v2_src_tests`, `v3_all`, `v4_src_gaps`).
4. Synthesise 150 seeds via cached LLM calls (`core/llm_client.py`).
5. Replay seeds on a standalone `seed_replay` binary; measure:
   - **M1**: total union edges hit.
   - **M2**: fraction of frozen "hard" branches hit, where hard =
     `struct_hits >= 1 AND rand_hits == 0` (random baseline scores 0% by
     construction).

Key reference files:

| Purpose | Path |
|---|---|
| Target registry | `core/targets.py` (TARGETS dict, TargetSpec) |
| Variant registry | `core/variants.py` (STANDARD_VARIANTS) |
| LLM client + cache + pricing | `core/llm_client.py` |
| Per-model tuning | `core/config.py` (ModelDefaults) |
| Synthesis driver | `synthesis/scripts/generate_ablation_inputs.py` |
| Coverage measurement | `synthesis/scripts/measure_coverage.py` |
| M2 scoring | `analysis/scripts/measure_gap_coverage.py` |
| Frozen hard-branch sets | `dataset/fixtures/<target>/m2_target_branches.json` |
| Upstream union profiles | `dataset/fixtures/<target>/upstream_union_profile.json` |
| Stats helpers | `analysis/scripts/{mann_whitney,friedman_nemenyi,vargha_delaney}.py` |
| Cost audit / estimator | `analysis/scripts/{cost_audit,estimate_cost}.py` |
| Ablation orchestrator | `scripts/_ablation_base.py` (AblationRunner) |
| Cache | `.cache/llm/` (sha256 of model + messages + temperature + top_p) |

Seven models are wired up: claude-sonnet-4-6, claude-haiku-4-5,
llama-3.1-{8b,70b}-instruct, codestral-22b, plus nemotron and gpt-oss variants
via the UF LiteLLM proxy at `https://api.ai.it.ufl.edu`.

### 1B. `../rl-fuzzer/` (sibling project)

An AFL++-integrated DQN fuzzer that learns which of 47 mutation primitives to
apply at each fuzzing step. Two cooperating processes share state via a memory-
mapped file at `/tmp/rl_shm_<model_id>`:

- **C side** (`src/mutator_m*.c`): AFL++ custom mutator. Computes execution
  state (coverage, edges, crashes, plus model-specific features), writes to
  SHM with `__ATOMIC_RELEASE`, spin-waits on `action_seq` with
  `__ATOMIC_ACQUIRE`, applies the chosen mutation primitive, returns mutated
  buffer.
- **Python side** (`scripts/rl_server.py`): polls SHM for new state, runs DQN
  forward pass, writes back action ID, accumulates `(s, a, r, s′)` tuples in a
  replay buffer, runs Double-DQN training with target-network sync every 1000
  train steps.

Reward shaping:
`reward = (cov_delta) + log1p(crash_delta) * 1000`, no per-step cost.

Four model variants differ only in state representation (`scripts/models/`):
- M0_0 — 3-dim `[coverage_n, new_edges_n, crashes_n]`.
- M1_0 — 12-dim edge stability over all 65536 edges.
- M1_1 — 13-dim visited-edge stability + visit count.
- M2 — 97-dim per-mutator trace-bit magnitudes (best on jsoncpp at 668 edges
  vs M0_0's 502 over 500K steps).

Plateau detection (`scripts/models/common.py:355-372`): rolling 10K-step
coverage range ≤ 1 edge AND ε ≤ 0.06 AND step > 70% of budget. Sticky once
triggered.

Key reference files:

| Purpose | Path |
|---|---|
| RL server (training/eval loop) | `scripts/rl_server.py` |
| Shared DQN/buffer/plateau logic | `scripts/models/common.py` |
| M2 state builder (richest) | `scripts/models/m2.py` |
| M2 mutator (47-arm switch) | `src/mutator_m2.c` |
| Multi-run comparison harness | `scripts/compare_metrics.py` |
| Targets shared with this repo | `benchmarks/harfbuzz/`, `benchmarks/re2/` |

The 47 actions partition into deterministic stages (16: bit/byte flips,
arithmetic), interesting-value insertions (5), havoc mutations (18),
dictionary ops (4), and meta (2: custom mutator, full havoc).

---

## 2. Why this experiment is interesting

Existing literature treats LLM seed synthesis and RL mutation policy as
*orthogonal* approaches to the same ultimate goal (cover more code). They are
never compared at the same decision point because their interfaces don't
match — RL operates on `(state, action) → next_input`; LLM operates on
`(context) → seeds`.

This experiment forces them into a head-to-head: at a frozen AFL++ state,
both methods must produce a candidate input, and we score them on the same
coverage bitmap. Outcomes:

- **LLM wins consistently** → LLM-as-mutator is a viable replacement for RL,
  worth the wall-clock and dollar cost.
- **LLM wins only late** (after RL plateaus) → LLM-as-rescue strategy is
  viable; deploy as a fallback once plateau detection fires.
- **LLM never wins** → batch synthesis (the existing framework) is the right
  scope; online tool-use is unlikely to help.

Each outcome is publishable. RQ2 (when does LLM win) is the most novel — no
prior work characterises the *coverage-trajectory phase* in which LLM seeds
become competitive.

---

## 3. Research questions

| ID | Question |
|---|---|
| RQ1 | At a frozen AFL++ state $S_t$, does an LLM-generated seed cover more new edges than a seed produced by applying the RL agent's argmax action to the queue's current parent input? |
| RQ2 | Does the LLM advantage depend on freeze-time fuzzing maturity? Specifically, is the LLM-vs-RL gap larger late in fuzzing (after RL plateaus) than early? |
| RQ3 | When restricted to upstream-reachable but currently-uncovered branches, does either method preferentially hit the *harder* ones? |
| RQ4 | Per dollar of compute (LLM API cost vs RL inference + AFL++ exec time × node rate), which method finds more new coverage at $S_t$? |

---

## 4. Setup overview

```
   ┌─── Phase A: RL+AFL++ Run (in rl-fuzzer) ────────────────────┐
   │   train+eval on harfbuzz, snapshot every K steps:           │
   │      snap_t = (queue_dir, virgin_bits, dqn_state, parent)   │
   └──────────────────────────────────────────────────────────────┘
                              │  N snapshots × R training seeds
                              ▼
   ┌─── Phase B: Head-to-Head at Each Snapshot (in this repo) ───┐
   │   for snap_t:                                                │
   │      LLM branch:  generate K seeds given (parent,            │
   │                   uncovered-edge context, source)            │
   │      RL  branch:  sample K mutations from DQN policy,        │
   │                   apply to parent → K candidates             │
   │      AFL++ baseline: K vanilla havoc mutations of parent     │
   │      replay all 3K seeds against virgin_bits → edge deltas  │
   └──────────────────────────────────────────────────────────────┘
                              │
                              ▼
   ┌─── Phase C: Analysis ───────────────────────────────────────┐
   │   per-snapshot, per-method distribution of edge deltas;     │
   │   pairwise tests (Mann-Whitney) at each snapshot;           │
   │   Friedman across snapshots; effect sizes;                  │
   │   trajectory plots: edge gain vs freeze-time step           │
   └──────────────────────────────────────────────────────────────┘
```

---

## 5. Conditions (3×3 cell grid + baseline)

| | RL-greedy (argmax) | RL-εgreedy (training ε) | AFL++ havoc (no policy) |
|---|---|---|---|
| **LLM-state** (one-shot, source+gaps+parent) | A1 | A2 | A3 |
| **LLM-online** (tool-use loop, T turns) | B1 | B2 | B3 |
| **LLM-blind** (target name only, no state) | C1 | C2 | C3 |

**v1 scope:** A1 + LLM-state vs RL-greedy vs AFL-havoc — three-way comparison,
cleanest signal. Other cells are extension experiments once the harness exists.

---

## 6. Target

**harfbuzz only** in v1. Rationale:

- Unique target present in *both* source projects
  (`rl-fuzzer/benchmarks/harfbuzz/` and `llm-fuzzing/dataset/targets/harfbuzz.yaml`).
- Already has a coverage-instrumented `seed_replay` binary in
  `dataset/targets/src/harfbuzz/build/coverage/seed_replay`.
- Already has a frozen upstream union profile and frozen M2 hard-branch set.
- Binary input format → simpler than RE2's text/regex format for AFL++ havoc.

jsoncpp is a stretch goal once the harness is proven (rl-fuzzer's most mature
target but not yet wired into this repo's coverage measurement).

---

## 7. Snapshot protocol (Phase A)

Modify `rl-fuzzer/scripts/rl_server.py` to dump snapshots at log-spaced steps.

| Field | Source | Purpose |
|---|---|---|
| `step` | RL server step counter | freeze-time index |
| `queue_dir/` | copy AFL++'s `queue/` | inputs available to mutate |
| `virgin_bits.bin` | dump from `afl->virgin_bits` via mutator → SHM | cumulative coverage state |
| `current_parent.bin` | the `buf` AFL++ passed to `afl_custom_fuzz()` at freeze | the input both methods will mutate |
| `dqn_state.npy` | the M2 97-dim state vector at freeze | RL inference replay |
| `dqn_checkpoint.pt` | symlink to `bin/rl_m2.pt` at this step | reproducibility |
| `meta.json` | step, wall-clock, current edge count, ε | cost normalisation |

**Schedule.** Log-spaced over the run: steps 1k, 5k, 25k, 100k, 250k, 500k.
Six snapshots × five RL training seeds = 30 freeze points. Log-spacing is
specifically chosen to answer RQ2 (early vs late dynamics).

**Pause mechanism.** *Don't pause* — run AFL++ to completion, dump snapshots
throughout, do head-to-head off-line in Phase B. This keeps Phase A fully
reproducible (no race between snapshot and online comparison) and lets us
re-run Phase B with new conditions without retraining.

---

## 8. Head-to-head at one snapshot (Phase B)

For snapshot $S_t$:

1. **Hard-branch set $H_t$** = `upstream_union_profile ∩ ¬virgin_bits` —
   branches reachable per the upstream union profile but not yet hit by AFL++
   at step $t$. This is a per-snapshot M2 analog (vs the global frozen
   hard-branch set used in the main ablation).

2. **LLM branch (K=10 generations).**
   - Render a head-to-head prompt template (new file:
     `comparison/prompts/head_to_head_binary.j2`) — derived from
     `synthesis/prompts/ablation_synthesis_binary.j2` with `v3_all` context
     plus: (a) the parent input as a base64 hex hint, (b) up to 5 gap
     descriptions sampled from $H_t$.
   - Call LLM K times with distinct cache salts
     (`sample=0..9,snapshot=t,run=r`). One-shot per call (no tool use in v1).
   - Each call → 1 seed → replay against $S_t$'s virgin_bits → record
     `edges_new`, `hard_hits = |new_edges ∩ H_t|`.

3. **RL branch (K=10 mutation samples).**
   - Load `dqn_m2.pt` checkpoint. Set policy net to eval. Feed
     `dqn_state.npy` → forward → 47-action Q-vector.
   - Sample K actions: K=1 deterministic argmax + (K-1)=9 stochastic from
     `softmax(Q/τ)` with τ=1.
   - Apply each action to `current_parent.bin` via the standalone
     mutator (M2 below).
   - Each candidate → replay → record same fields.

4. **AFL-havoc baseline (K=10 random havocs of the same parent, deterministic
   seed=42 per snapshot for reproducibility).**

5. **Output schema.** Per snapshot:
   `comparison/results/snap_<run>_<step>/{llm,rl,havoc}/k_<i>.json` with
   `{seed_b64, edges_new, hard_hits, latency_ms, cost_usd}`.

---

## 9. Metrics & statistical analysis

| Metric | Definition | RQ |
|---|---|---|
| `mean_edges_new` | mean over K samples per (snapshot, method) | RQ1 |
| `prob_any_new` | P(at least 1 of K samples gains edges) | RQ1 |
| `hard_hit_rate` | mean `hard_hits / |H_t|` | RQ3 |
| `early_vs_late_delta` | (mean LLM−RL gap @ steps ≥ 100k) − (gap @ steps ≤ 25k) | RQ2 |
| `edges_per_dollar` | `mean_edges_new / cost_usd` (RL = wall-clock × node-rate) | RQ4 |

**Tests.**

- Per-snapshot pairwise: **Mann-Whitney U** on the K=10 distributions per
  method, with Holm correction across snapshots.
- Cross-snapshot: **Friedman test** treating each snapshot as a block,
  methods as treatments — answers "is one method consistently better across
  the whole fuzzing trajectory."
- Effect size: **Vargha–Delaney $\hat{A}_{12}$** per snapshot.

All three tests already have helpers in
`analysis/scripts/{mann_whitney,friedman_nemenyi,vargha_delaney}.py`.

**Reporting.** Coverage trajectory plot (x = AFL++ step, y = mean
`edges_new`, one line per method, error bands = ±std across the 5 training
seeds). Per-snapshot bar chart with significance markers.

---

## 10. Open decisions

These are real decision points, not rhetorical. Each affects scope/cost
materially.

| # | Decision | Default | Alt |
|---|---|---|---|
| 1 | K (samples per method per snapshot) | 10 | 20 (lower variance, 2× LLM cost) |
| 2 | LLM model(s) | claude-sonnet-4-6 only | + codestral-22b (free, weaker) |
| 3 | RL variants | M2 only (best) | All four (m0_0, m1_0, m1_1, m2) |
| 4 | Snapshot schedule | Fixed log-spaced (1k, 5k, 25k, 100k, 250k, 500k) | Plateau-triggered (snapshot whenever AFL++ goes 10k steps without new edges) — more research-relevant for RQ2 but requires online detection |
| 5 | AFL++ baseline | Per-snapshot K havocs | Single fresh AFL++ run as floor |
| 6 | LLM input — does it see `current_parent`? | Yes, base64-prefixed | No (LLM does from-scratch generation, RL mutates → the LLM-RL gap *is* the question) |
| 7 | Online tool-use condition (cell B1) | Defer to v2 | Run as a third condition in v1 |
| 8 | Snapshot count per run | 6 | 10 (denser RQ2 trajectory) |
| 9 | Independent RL training runs | 5 | 3 (cheaper) or 10 (tighter CIs) |
| 10 | Anti-contamination check | Run `dataset/scripts/contamination_probe.py` on each LLM-generated seed | Skip in v1, audit later |

**Most important decisions to settle before implementation:** #4 (snapshot
schedule), #6 (does LLM see parent), #7 (include online tool-use cell?).

---

## 11. Code layout

**Phase A** modifies `rl-fuzzer/scripts/rl_server.py` — add
`--snapshot-steps` flag, write snapshot dirs.

**Phase B + C** is new code in `llm-fuzzing/`:

```
comparison/
  scripts/
    extract_snapshot.py       # rl-fuzzer dump -> normalized snapshot dir
    run_llm_branch.py         # K LLM generations on a snapshot
    run_rl_branch.py          # K mutation-action samples on a snapshot
    run_havoc_branch.py       # K AFL-havoc samples (calls standalone C harness)
    apply_mutation.py         # Python wrapper around the 47-arm mutator
    replay_and_score.py       # replay seed against snapshot's virgin_bits
    run_comparison.py         # orchestrator: for each snapshot, all 3 branches
    aggregate.py              # cross-snapshot stats, plots
  prompts/
    head_to_head_binary.j2    # variant of v3_all augmented with parent + H_t
  fixtures/
    snapshots/<run_id>/<step>/   # checked-in for reproducibility (small)
  results/
    runs/<run_id>/<step>/{llm,rl,havoc}/k_<i>.json
    summary.json
docs/
  head_to_head_plan.md       # this doc
  head_to_head_results.md    # final report
```

The 47-arm mutator switch needs to be exposed as a callable. Cleanest:
`rl-fuzzer/src/mutator_oneshot.c` — same 47 cases, no AFL++ dependency, takes
`(buf, len, action_id, rng_seed)` on stdin, emits mutated buf on stdout.
Python wrapper invokes it via subprocess.

---

## 12. Implementation milestones

| # | Milestone | Acceptance |
|---|---|---|
| M0 | Design freeze | This doc + sign-off on §10 decisions |
| M1 | Snapshot dumper (rl-fuzzer) | A single snapshot round-trips: load → inspect virgin_bits → confirm coverage count matches the C side |
| M2 | Standalone mutator (rl-fuzzer/src/mutator_oneshot.c) | 1000 random `(parent, action)` pairs produce identical bytes via AFL++ path and standalone path |
| M3 | Replay + scoring (comparison/scripts/replay_and_score.py) | Given a snapshot and a seed, returns `(edges_new, hard_hits)`; validated against known seeds |
| M4 | Single-snapshot smoke test | 1 snapshot, K=2 each branch, all three methods produce seeds, scoring works |
| M5 | Full pilot | 1 RL training run, 6 snapshots, K=10, all three methods. Distributions sane |
| M6 | Full experiment | 5 RL runs, 30 snapshots, K=10. Aggregate; statistical tests pass |
| M7 | Write-up | `docs/head_to_head_results.md`; plots in `comparison/results/plots/` |

**Estimated cost.**
- Phase A: 5 RL training runs × ~10h each = ~50h compute.
- Phase B: 30 snapshots × 10 LLM calls × ~$0.05 ≈ ~$15 (claude-sonnet-4-6).
- Phase C: ~2h replay (negligible).

---

## 13. Risks & failure modes

| Risk | Mitigation |
|---|---|
| **AFL++ snapshot fidelity.** Restoring `virgin_bits` from a dump and from re-running the queue corpus may diverge if AFL++ has internal nondeterminism (scheduler, timing). | Prefer the dumped bitmap as ground truth. Verify by re-replaying queue corpus and confirming bitmap match within tolerance |
| **LLM contamination.** If the LLM has seen harfbuzz seeds in pretraining, "blind" condition is overstated. | Use existing `dataset/scripts/contamination_probe.py` to bound BLEU vs upstream verbatim seeds. Decision #10 |
| **Mutator extraction parity.** The 47-arm switch references AFL++ globals (extras dictionary, internal state). Standalone build must stub these or copy them. | Bit-exact parity check is M2 acceptance |
| **Undertrained RL.** If the DQN policy is barely better than havoc, the head-to-head is uninteresting. | Run a quick "trained-vs-vanilla" check first; if RL ≈ havoc, train longer or pick a target where RL clearly wins (jsoncpp from rl-fuzzer's own results) |
| **Per-attempt vs per-second framing.** LLM does seconds of work per attempt; RL does microseconds. Per-attempt comparison flatters LLM; per-second flatters RL. | Report both. Per-attempt addresses "strategy quality"; per-second addresses "practical impact" |
| **Hard-branch set $H_t$ may be tiny at high steps.** If AFL++ has covered most reachable branches by step 500k, $H_t$ is small and `hard_hit_rate` is noisy. | Plot $|H_t|$ vs step; if late snapshots have $|H_t| < 50$, drop them or add lower-step snapshots |

---

## 14. Definition of done

The experiment is complete when:

1. All 30 snapshots have run all three branches with K=10 samples each.
2. Per-snapshot, per-method distributions are saved as JSON.
3. Mann-Whitney + Friedman + Vargha–Delaney are computed and reported.
4. Coverage trajectory plot exists (x = AFL++ step, y = mean edges_new,
   one line per method, error bands).
5. `docs/head_to_head_results.md` answers RQ1–RQ4 with effect sizes and
   significance markers.
6. The whole pipeline is reproducible from `comparison/scripts/run_comparison.py`
   given the snapshot dirs.

---

## 15. Reference paths (cheat sheet for refinement)

When iterating on this plan, these are the files most worth pasting in for
context:

- `core/llm_client.py` — what the LLM client supports (tools, streaming,
  caching, pricing).
- `core/targets.py` + `core/variants.py` — TargetSpec and VariantSpec data
  classes.
- `synthesis/prompts/ablation_synthesis_binary.j2` — current binary-target
  prompt; `head_to_head_binary.j2` will derive from it.
- `analysis/scripts/measure_gap_coverage.py` — current M2 scoring; logic for
  intersecting seeds against a hard-branch set.
- `../rl-fuzzer/scripts/models/m2.py` + `../rl-fuzzer/src/mutator_m2.c` —
  the M2 RL agent and its 47-arm switch.
- `../rl-fuzzer/scripts/rl_server.py` — RL training/eval loop (for the
  snapshot dumper modification in M1).
- `../rl-fuzzer/scripts/compare_metrics.py` — multi-run comparison framework
  (Phase C plotting can adapt this).
