# experiment9 — EXECUTION LOG (append-only)

## 2026-05-18 — Setup

- Branch `experiment-followups` (from `experiment7` `c519f46`).
- Verified read-only: `harfbuzz/v3_all/default/codestral-22b` exists,
  n=150, M2=0.260 (experiment2_1) — comparison anchor, NOT regenerated.
- `ablation_synthesis_binary_cot.j2` confirmed: rigid 4-step labels,
  no RE2 example list. No template changes for B.

---

## PRE-REGISTRATION — FROZEN

**Status.** User delegated autonomous execution (message 2026-05-18:
"execute it on ur own dont ask for any options ... choose your
recommended one and proceed"). There is therefore no separate human
review/edit step; **this commit is the freeze point** and precedes the
single LLM cell. Predictions/effect size below are not edited post-hoc;
any change is a dated deviation entry.

**Timestamp:** 2026-05-18T03:19:51Z
**Commit at freeze:** `c519f46427187fe193e87d5756f14364932173af`
(draft); the freeze/authorization commit hash is recorded in the next
entry when this file is committed.

### Frozen statistics

- New cell: `cot_strict @ harfbuzz/v3_all/codestral-22b`. M2 =
  `slices.all.union_frac_targets_hit`. Compare to existing
  `default @ harfbuzz/v3_all/codestral` M2 = 0.260 (not regenerated).
- Minimum meaningful M2 effect = **0.05**.

### Predicted sign (blind; recommended call)

`cot_strict` on harfbuzz → **CENSORED** (does not fill 150); where a
partial corpus exists, M2 not meaningfully above default 0.260
(Δ ≤ +0.05). I.e. the collapse is predicted **general** to rigid-label
structured-target prompting under codestral, not RE2-example-specific.

### Falsifier / interpretation (pre-committed)

- Falsified (→ "collapse is RE2-specific"): `cot_strict` FILLS 150 and
  M2 ≥ 0.260 − 0.05.
- Corroborated (→ "collapse is general"): `cot_strict` CENSORED, OR
  fills but M2 < 0.260 − 0.05.
Both outcomes are reportable; neither is optimized for. Must be read
jointly with experiment8 (labels-vs-examples isolation).

<!-- approval/freeze commit, run, deviations appended below -->

---

## 2026-05-18 — B RESULT (read JOINTLY with experiment8 A)

Run: harfbuzz×v3_all×codestral×cot_strict, binary template (no changes),
`UTCF_ABANDON_NOGAIN=5`, offset 810000, `--phase all --skip-existing`
(default@harfbuzz/v3_all NOT regenerated). 0 budget errors.

- `cot_strict@harfbuzz/v3_all`: **FILLED 150** (reason `filled`, 56
  attempts, no_gain 0.08 — fast, NO diversity collapse), **M2 = 0.120**.
- `default@harfbuzz/v3_all` (existing, M2 **0.260**, not regenerated).
- Δ = M2(cot_strict) − M2(default) = **−0.140** (≫ the 0.05 minimum).

**Pre-registered interpretation rule:** "cot_strict CENSORED, OR fills
but M2 < default − 0.05 → collapse is GENERAL". The *fills-but-M2-below*
branch fired → mechanically **GENERAL**.

**Honest joint reading with experiment8 A (the precise picture):** the
two results SPLIT what the pre-reg lumped together:
- **Diversity/fill collapse is RE2-example-list-specific.** The binary
  cot template has the rigid 4-step labels but NO RE2 example list;
  cot_strict FILLED 150 fast on harfbuzz with no collapse. This
  CORROBORATES experiment8 A (the static example list, not the labels,
  drives the RE2 fill collapse).
- **The rigid-label scaffold independently DEGRADES seed quality on
  structured targets even without a collapse.** Filled, but M2 0.120 ≪
  default 0.260 (−0.140). So the label scaffold has a real, general M2
  cost distinct from the example-anchor fill collapse.
Net: the pre-reg's binary "RE2-specific vs general" framing was too
coarse; the data resolves it as *fill-collapse = example-specific
(A+B agree); label-scaffold M2 penalty = general*. Both outcomes were
pre-declared reportable; reported as-is.
