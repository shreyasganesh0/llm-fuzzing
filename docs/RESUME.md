# RESUME — active experiment: experiment6 (ESSS)

_Short-lived. Delete when experiment6 reaches RESULTS.md complete._

**What's running:** experiment6 — Entropy-Stratified Seed Selection on
harfbuzz / codestral-22b, variants `v1_src` + `v3_all`, default strategy.
Branch: `experiment6` (checkpoint commits; no tooling attribution).

**Authoritative state for this experiment:**
`docs/experiment6/EXECUTION_LOG.md` (append-only; pre-registration block
already committed at `4d96b1c`, 2026-05-17T23:52:23Z — BEFORE any M2
scoring), `docs/experiment6/MANIFEST.json`, `docs/experiment6/METHODS.md`.
Stage 0 = **FULL** (logprob gate cleared; `STAGE0_RESULT.md`).

**Stage 1 progress / next action:**

1. Over-generation pool (~450/cell) running in background:
   `UTCF_LLM_RPM=12 .venv/bin/python scripts/run_experiment6_harfbuzz.py
   --phase synthesis --variants v1_src,v3_all --only-models codestral-22b
   --num-seeds 450 --attempt-offset 600000` → log `/tmp/exp6_hb_pool.log`.
   Pool seeds: `synthesis/results/experiment6/seeds/harfbuzz/ablation/<v>/codestral-22b/`
   Logprob sidecars (1:1 with seeds):
   `synthesis/results/experiment6/logprobs/harfbuzz/ablation/<v>/codestral-22b/`
2. When pool ≥ ~450/cell (or attempt-capped): for each variant
   - `python -m analysis.scripts.experiment6_entropy --sidecar-dir <sidecars> --out results/experiment6/<v>/entropies.json`
   - `python -m analysis.scripts.experiment6_stratify --pool-dir <pool> --entropy-json results/experiment6/<v>/entropies.json --out-root results/experiment6/<v>/subsamples`
   - `python -m analysis.scripts.experiment6_score --variant <v> --pool-dir <pool> --s-random-dir .../S_random --s-high-dir .../S_high --s-low-dir .../S_low --out-root results/experiment6/<v>`
   (If a single pass yields < ~450, rerun step 1 with `--attempt-offset`
   bumped by ≥5000 — invariant 5 — accumulating into the same pool dir.)
3. Compose `docs/experiment6/RESULTS.md`: M2 table (variant ×
   {S_random,S_high,S_low}), both bootstrap CIs/variant, prose vs the
   pre-registered prediction, pool sizes + entropy-drop counts, every
   deviation. Then delete this RESUME.md and update `docs/STATUS.md`.

**Invariants in force:** M2 filter frozen (2), RNG 42 subsampling (3),
150-seed scored corpora (4), attempt-offset 600000 (5), default-strategy
cache byte-identical & regression-tested (9). Forbidden to modify:
`analysis/metrics/m2.py`, `freeze_target_branches.py`, core logic of
`measure_gap_coverage.py`.
