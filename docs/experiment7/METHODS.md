# experiment7 — METHODS (Follow-up B: cross-target reliability)

## 1. Question

Is the `cot_strict` collapse RE2-specific (anchored on the RE2 regex
example list) or general to codestral structured-target prompting under
a rigid labeled scaffold? Test on **harfbuzz (binary)**.

## 2. Repo-reality (drives the design)

- `default @ harfbuzz/v3_all/codestral-22b` **already exists**
  (experiment2_1, n=150, M2=**0.260**). It is the comparison anchor and
  is **NOT regenerated** (no-regenerate rule).
- ⇒ **B = exactly 1 new LLM cell**: `cot_strict @ harfbuzz/v3_all/
  codestral-22b`, binary format, using the existing
  `ablation_synthesis_binary_cot.j2` — **no template changes**.
- `ablation_synthesis_binary_cot.j2` carries the same rigid 4-step
  labels (`Step 1 … Step 4 (Bytes)`) but **no RE2-style "Examples of
  the kind of patterns" stress list** (that list is regex-specific).
  Therefore B tests whether the **label-rigidity** mechanism
  generalizes across targets; it does **not** re-test the RE2
  example-anchor. **Read jointly with experiment6** (which isolates
  labels vs examples on RE2).

## 3. Run

`harfbuzz × v3_all × codestral-22b × cot_strict` = 1 cell. Existing
binary pipeline, 150-normalization (CENSORED if it cannot fill),
`UTCF_ABANDON_NOGAIN=5` yield-ceiling, `--attempt-offset 810000`
(≥5000 above all prior). Score with the unmodified M1+M2 pipeline
against the frozen harfbuzz 50-branch set. estimate_cost first.

## 4. Pre-registered prediction + interpretation rule (frozen — see EXECUTION_LOG)

**Predicted (my recommended call, made blind):** consistent with
experiment6's reasoning (the rigid *labels*, not the example list, drive
the collapse) and with the convergent exp4/7 finding that codestral's
binding constraint on structured targets is producing a *valid, diverse*
corpus — **`cot_strict` will be CENSORED on harfbuzz too** (does not
fill 150), and where it produces a partial corpus its M2 will be **≤**
the existing `default` 0.260 (not meaningfully above by ≥0.05).

**Interpretation rule (pre-committed, both outcomes informative):**
- `cot_strict` fills 150 AND M2 ≥ default 0.260 (within/above, Δ ≥ −0.05)
  → the collapse is **RE2-specific**, anchored on the RE2 example list
  (label-rigidity does NOT generalize to the binary target).
- `cot_strict` CENSORED, OR fills but M2 strictly below default by
  > 0.05 → the collapse is **general** to rigid-label structured-target
  prompting under codestral, not a quirk of the RE2 examples
  (strengthens the convergent "reliability/validity dominates" thesis).

Minimum meaningful M2 effect = **0.05**.

## 5. Stopping

Complete when RESULTS.md states: filled? / n_seeds / M2 (or CENSORED)
for the new `cot_strict` cell, the Δ vs the existing default 0.260,
which interpretation-rule branch fired, actual cost, deviations.
