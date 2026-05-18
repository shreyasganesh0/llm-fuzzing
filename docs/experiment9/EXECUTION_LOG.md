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
