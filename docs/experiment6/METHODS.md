# experiment6 — METHODS (Follow-up A: cot_strict mechanism isolation)

DRAFT for user review. No strategies/templates/runs created yet.

## 1. Question

`cot_strict` collapses on RE2/codestral (EXPERIMENT_DEEP_DIVE §6.1:
462/492 parse OK, 142 unique `content_b64` across all responses, top-5
regexes byte-identical to the in-template "Examples …" list, → CENSORED
at every variant). Two candidate causes: (i) the example list anchors
the model; (ii) the rigid 4-step labels (`Step 1 (Quote): … Step 4
(Regex):`) force terse template-completion. Isolate them.

## 2. What existing data already tells us (basis for the predictions)

`default` and `cot_strict` render the **byte-identical** example block
(base template lines 69–75 ≡ cot template lines 77–83). At the *same*
cell `re2/v3_all/codestral`: `default` **filled 150, M2 0.800**;
`cot_strict` **CENSORED n=124**. The *only* difference is the 4-step
labels. ⇒ "the example list alone causes the collapse" is already
falsified by existing data; the rigid labels are the prime suspect.
This drives the asymmetric predictions in §6.

## 3. Three new strategies (registry + template + dispatch only — NOT yet created)

Each is a minimal edit of `synthesis/prompts/ablation_synthesis_regex_cot.j2`,
registered in `core/prompt_strategies.py` + `_TEMPLATE_SUFFIX_BY_STRATEGY`
in `generate_ablation_inputs.py`, with `,strategy=<name>` cache salt and
a `<name>/` path segment (invariant 9; phase9 test must still pass).

- **cot_strict_no_examples** — cot template with the entire "Examples of
  the kind of patterns that stress the parser/compiler:" block
  (`(?P<x>a+)`, `(a*)*`, `\p{Greek}+`, `(?=abc)` rejected, `a{1000,}`,
  `[^a-zA-Z0-9]`) removed. Labels kept. Isolates: do the labels collapse
  diversity *without* the example crutch (onto a different set)?
- **cot_strict_rotated_examples** — pool of 8 = the existing 5 + 3 new
  RE2-**rejected** patterns (RE2 is a finite-automaton engine; rejected
  syntax exercises the parser's *error* paths — consistent with the
  existing `Lookarounds (rejected): (?=abc)` convention):
  `backreference (a)\1`, `possessive quantifier a++`,
  `class intersection [\w&&[^a-c]]`. Pool persisted at
  `dataset/fixtures/cot_examples_pool.json`. Per call, deterministically
  pick 3 of 8 by `hash(attempt_index) % C(8,3)` so the same attempt
  index always sees the same 3 (reproducible) but successive attempts
  rotate. (Most invasive of the three: the rotation seed must be
  threaded from the synthesis driver's `run_id` into prompt build.)
  Isolates: does *varying* the anchor restore diversity (anchor-content
  vs anchor-presence)?
- **cot_strict_no_labels** — cot template with the rigid 4 labels
  replaced by a single "Explain your reasoning briefly, then emit the
  regex." Example block kept. This is ≈ the base/`default` template +
  a soft rationale ask → it is the *midpoint of a rigidity gradient*
  (none=default → free-form=this → rigid-4-step=cot_strict).

## 4. Runs

`RE2 × v3_all × codestral-22b × {the 3 new strategies}` = **3 cells**.
Same 300-attempt cap + `UTCF_ABANDON_NOGAIN=5` yield-ceiling +
150-normalization as experiment5. CENSORED if it cannot fill. Score with
the **unmodified** M1+M2 pipeline. `--attempt-offset` ≥ 5000 above all
prior (propose 800000). estimate_cost first; abort if >$5 projected.

## 5. Control baseline (no spend) — REQUIRED

Compute the `default @ re2/v3_all/codestral` regex-frequency histogram
from the **already-cached** responses (no new calls) as the diversity
reference. "Diversified vs collapsed" is meaningless without it.

## 6. Pre-registered predictions (see EXECUTION_LOG for the frozen block)

| strategy | fills 150? | diversity-recovery (ratio ≥0.5)? | M2 vs cot_strict CENSORED ref | dominant-regex set |
|---|---|---|---|---|
| cot_strict_no_labels | **Y** (≈default) | **Y** | ≈ default region (≫ +0.05) | resembles default, not the 5 |
| cot_strict_no_examples | **N (still CENSORED)** | N | not meaningfully > cot_strict | collapses onto a *different* small set (not the original 5) |
| cot_strict_rotated_examples | **N (still CENSORED)** | N | not meaningfully > cot_strict | rotates but still low-diversity |

Mechanism reading rule: if `no_labels` fills & `no_examples` /
`rotated_examples` stay CENSORED → **the rigid labels are the cause**,
the example list only determines *what* it collapses onto. If
`no_examples` *also* fills → examples are (jointly) necessary. If
`rotated_examples` fills but `no_examples` doesn't → anchor *fixity*,
not presence, is the lever. Minimum meaningful M2 effect = **0.05**.

## 7. Stopping

experiment6 complete when RESULTS.md has the 3-row M2 + diversity-ratio
(or CENSORED) table, the per-cell top-5 regex histograms vs the default
control histogram, and the one-paragraph mechanism reading adjudicated
against §6. No extra variants/models/targets.
