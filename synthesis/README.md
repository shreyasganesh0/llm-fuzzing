# Phase 3 — Gap-Filling Input Synthesis & libFuzzer Campaigns

Not yet implemented. See `docs/plan_v3.md` §3 for the full spec:

- `generate_inputs.py` — temperature=0.7, top_p=0.95, 3 samples, condition on coverage_gaps.json
- `validate_inputs.py` — parse-rate check against upstream harness
- `run_fuzzing.py` — 20 trials × 23h × 6 configs (plan §3.4)
- `dedup_crashes.py`, `failure_analysis.py`, `compare_baselines.py`

This directory holds campaign configs and results; `.gitkeep` placeholders preserve structure.
