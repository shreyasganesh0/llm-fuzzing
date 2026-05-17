# experiment6 — RESULTS

**Status: PENDING.** Populated after Stage 1 (conditional on Stage 0 =
FULL). Will contain:

- The M2 table: variant × {S_random, S_high, S_low} (each = the unmodified
  metric's `slices.all.union_frac_targets_hit` over an exactly-150-seed
  subsample).
- Both bootstrap 95% CIs per variant: `M2(S_high) − M2(S_random)` and
  `M2(S_high) − M2(S_low)`.
- Plain-prose interpretation against the pre-registered prediction
  (`EXECUTION_LOG.md`), including whether the falsifier fired.
- Pool sizes actually achieved, seed-drop counts (entropy-unlocatable
  payloads, §3 of METHODS), and every deviation.

<!-- M2 table + CIs + interpretation appended after Stage 1 -->
