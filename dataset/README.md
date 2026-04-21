# Phase 1 — Dataset Construction

Extract real upstream unit tests from FuzzBench targets, measure per-test
coverage under LLVM source-based instrumentation, and compute coverage gaps.

## Pipeline

```bash
# 1. Clone upstream at pinned commit + fetch harness/dict.
./dataset/scripts/fetch_target.sh dataset/targets/re2.yaml

# 2. Build three variants (coverage / sanitizer / fuzzer).
./dataset/scripts/build_instrumented.sh dataset/targets/re2.yaml

# 3. Extract tests, measure per-test coverage, compute gaps, audit provenance.
python dataset/scripts/build_dataset.py --target re2

# 4. Probe for training-data contamination per (target, model).
python dataset/scripts/contamination_probe.py --target re2 --model gpt-4o-2024-08-06
```

## Output

`dataset/<target>/` with per-test `test_code.cc`, `coverage.json`, and
`upstream_location.json`; `coverage_gaps.json` and `dataset_stats.json`
at the target root; `contamination_report.json` per (target, model) pair.

## Invariants (enforced by build_dataset.py)

- Every test traces to `upstream_repo:commit:file:line` and the first three
  non-empty lines match the upstream source (±2 line window).
- `test_code` is unmodified from upstream (never synthesised).
- `coverage_gaps.json` contains `total_upstream_tests` and
  `union_coverage_pct` (plan §1.6 audit naming).

See `docs/plan_v3.md` §1 for the full specification.
