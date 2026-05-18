# experiment9 — RESULTS (Follow-up B: cross-target reliability)

**Status: COMPLETE.** Pre-registered (frozen, `44d8104`) before the run.
1 new LLM cell; `default@harfbuzz/v3_all/codestral` (M2 0.260) reused,
NOT regenerated.

| cell | seeds | M2 | Δ vs default 0.260 |
|---|---|---|---|
| `cot_strict @ harfbuzz/v3_all/codestral` | **150 (FILLED)** | **0.120** | **−0.140** |
| `default @ harfbuzz/v3_all/codestral` (existing) | 150 | 0.260 | — |

**Adjudication.** Pre-registered rule "CENSORED or fills-but-M2<default−0.05
→ GENERAL collapse" → the fills-but-low-M2 branch fired. But read with
experiment8 A this is sharper: cot_strict **did not collapse/censor** on
harfbuzz (filled 150 fast) because the binary cot template has the rigid
labels but **no RE2 example list** — corroborating A that the *static
example list* (not the labels) causes the RE2 fill collapse. Separately,
the rigid-label scaffold still **hurt seed quality** (M2 0.120 vs 0.260,
−0.140) with no collapse — a *general* label-scaffold M2 penalty
distinct from the example-anchor fill collapse. Conclusion: fill/diversity
collapse = RE2-example-list-specific; rigid-label M2 cost = general to
structured-target prompting under codestral. Cost: litellm $19.14→$19.26
(+$0.12), $25 cap not hit, 0 budget errors.
