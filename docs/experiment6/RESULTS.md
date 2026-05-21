# experiment6 — RESULTS (Follow-up A: cot_strict mechanism isolation)

**Status: COMPLETE.** Pre-registered & frozen at commit `44d8104`
(2026-05-18) **before** the strategies/templates existed and before any
run. Impl `a3dc0cf`; run RE2×v3_all×codestral-22b×{3 new strategies},
`UTCF_ABANDON_NOGAIN=5`, offset 800000, unmodified M1/M2 pipeline,
0 budget errors.

## Result table (vs the two existing, non-regenerated controls)

| strategy | seeds | fill reason | M2 | uniq_regex / realized | diversity-recovery* |
|---|---|---|---:|---|---|
| `cot_strict` (exp5 ref) | 124 | CENSORED | 0.533† | — (142 unique total < 150) | ❌ |
| **`cot_strict_no_examples`** | **150** | filled (108 att) | **0.800** | 121/150 ≈ **0.81** | ✅ |
| **`cot_strict_rotated_examples`** | **150** | filled (95 att) | **0.667** | 119/150 ≈ **0.79** | ✅ |
| `cot_strict_no_labels` | 50 | CENSORED (nogain) | 0.400† | 39/50 | ❌ |
| `default` (control, existing) | 150 | — | 0.800 | 134/150 | ✅ |

\* pre-registered def: fills 150 AND unique-content ratio ≥ 0.50
(`cot_strict` reference = 0.103). † CENSORED M2 (n<150) is corpus-size-
dependent and NOT comparable to a 150 cell — shown for audit only.

## Top-5 realized-regex histograms (the key diversity diagnostic)

- `cot_strict` (exp5, reference): collapse onto `(?P<x>a+)` / `(a*)*` /
  `\p{Greek}+` / `a{1000,}` / `[^a-zA-Z0-9]` — **byte-identical to the
  in-template example list**.
- `cot_strict_no_examples`: `(a)`, `(?P<A>expr)`, `(a)(b)`, `a|b`,
  `(?P<name>` — each only ×3; **diversified off the original 5**.
- `cot_strict_rotated_examples`: `(a)`, `(a)*`, `(?P<name>a+)`,
  `((a*)*)` — each ×3; diversified.
- `cot_strict_no_labels`: `(?P<x>a+)`×3, `(a*)*`×3, `(a{1000,})`×2,
  `[^\p{Greek}]+`×2 — **still the original example-list patterns**.
- `default` control: `\p{Greek}+`, `((a*)*)`, `(?P<x>a+)` — each ≤3;
  diverse.

## Adjudication vs the frozen pre-registration — **ALL 3 PREDICTIONS FALSIFIED**

The pre-registered hypothesis was "the rigid 4-step labels, not the
example list, cause the collapse." Every directional prediction was
wrong:

| pre-registered prediction | outcome |
|---|---|
| `no_labels` FILLS ≈ default | **FALSE** — CENSORED at 50; collapsed onto the example regexes |
| `no_examples` STILL CENSORED | **FALSE** — FILLED 150, M2 0.800 (= default), diversity recovered |
| `rotated_examples` STILL CENSORED | **FALSE** — FILLED 150, M2 0.667, diversity recovered |

The pre-registered falsifier clause "(b) `cot_strict_no_examples` DOES
fill 150 and recovers diversity → contradicts 'labels are the cause'"
fired explicitly.

## Mechanism reading (data-driven; opposite of the prior hypothesis)

**The static, fixed in-template example list is the proximate cause of
the `cot_strict` diversity collapse — not the rigid 4-step labels.**
Removing the list (labels kept) fully recovers fill *and* M2 to
default's 0.800; rotating it recovers (0.667); removing the labels while
keeping the static list still collapses onto exactly those example
patterns. Labels are **neither necessary nor sufficient**.

Reconciles with all prior data as a **(static-example-anchor ×
mandated-reasoning-elaboration) interaction**: `default` shows the same
list but only informal numbered *hints* (no mandated multi-clause
reasoning field) and escapes; `cot_strict` and `cot_strict_no_labels`
pair the static list with a mandated rationale field → the model leans
on the anchor and mode-collapses; `no_examples` / `rotated` break the
anchor → diversity returns. This cleanly **falsifies the prior "labels
are the cause" inference** and directly **supports lever L2**
(remove/rotate the in-template example list) — and shows the cot_strict
*scaffold itself* is not harmful on RE2 once the anchor is removed
(`no_examples` ties `default` at M2 0.800).

## Deviations / notes

- Pre-registration falsified — reported as-is (the design declared all
  outcomes admissible; this is the high-information outcome). The prior
  rigid-labels inference (from default-vs-`cot_strict` alone) was wrong
  because it did not account for `default`'s lack of a mandated
  reasoning field.
- CENSORED M2 values are non-comparable (n<150) and excluded from the
  primary claims; the diversity-recovery flag + the realized-regex
  histograms are the comparable evidence.
- Cost: covered by the experiment6/9 cost gate (estimate $1.52 ≪ $5
  abort threshold); litellm cumulative ~$19.1 of the $25 cap; 0 budget
  errors; the yield-ceiling fail-safe aborted `no_labels` at 60 attempts.

## Stopping

experiment6 complete: 3-row table, histograms vs the default control,
and the mechanism reading adjudicated against the frozen pre-reg are all
recorded. No extra variants/models/targets run.
