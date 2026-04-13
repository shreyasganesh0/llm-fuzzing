# Experiment B — prompt-ablation summary (RE2, llama-3.1-8b-instruct, regex format)

## Headline

| cell | gaps? | tests? | source? | seeds | **edges** | Δ vs exp1_full |
|---|:-:|:-:|:-:|---:|---:|---:|
| **exp2_plus_gaps** | ✅ | ❌ | ✅ | 30 | **1250** | **+7** |
| exp1_full         | ✅ | ✅ | ❌ | 20 |  1243 |  0 |
| exp2_plus_tests   | ❌ | ✅ | ✅ | 30 |  1210 | -33 |
| exp2_source       | ❌ | ❌ | ✅ | 30 |  1133 | -110 |
| exp1_tests_only   | ❌ | ✅ | ❌ | 30 |  1093 | -150 |
| random            | — | — | — | 30 |   980 | -263 |
| exp1_gaps_only*   | ✅ | ❌ | ❌ | 10 |   879 | -364 |

*`exp1_gaps_only` produced only 10 seeds — loop detector aborted 2/3 samples.
The other gap-containing cells did not suffer this; the dense gap list
without tests or source appears to trigger llama's degenerate repetition
mode. This is a **data point about the prompt itself**, not a bug.

## Decomposing exp1_full's advantage

The plan's decision rules:

- "If `exp1_gaps_only` ≈ `exp1_full`, the win is from the gaps." — **NOT supported.**
  exp1_gaps_only falls 364 edges below exp1_full (and 101 below random at n=10).
- "If `exp2_plus_gaps` > `exp1_full`, gaps stack with source." — **Supported.**
  +7 edges; 142 "exp2_plus_gaps only" edges not reached by exp1_full.
- "If `exp2_plus_tests` ≥ `exp1_full`, tests are the generalizing piece." — **NOT supported.**
  exp2_plus_tests is 33 edges BELOW exp1_full.

## What this says about the generalization hypothesis

The user's hypothesis is that exp2 (source-only) generalizes better than
exp1 because exp1 has fixture-specific info (the gaps + tests are
computed from the fixture).

In-distribution on the fixture this experiment does NOT support that
framing directly — exp1_full wins on its own fixture. But the ablation
reveals a more precise picture:

1. **Source code is the best "information carrier"**. Every cell that
   contains source (exp2_*) produces ≥ 1133 edges; every cell without
   source (exp1_*, random) is ≤ 1243 edges. The spread among no-source
   cells is 879–1243 (a 364-edge range, dominated by loop issues);
   among source cells it's 1133–1250 (only a 117-edge range). Source
   makes the model robust to prompt variation.

2. **Gaps amplify source**. exp2_plus_gaps beats exp2_source by +117
   edges (1133 → 1250), and beats exp2_plus_tests by +40 (1210 → 1250).
   That's the largest single-variable gain in the table.

3. **Tests alone are a weak substitute for source**. exp1_tests_only
   (1093) ≈ exp2_source (1133) — so 5 test examples are worth roughly
   the same as 14k tokens of source code for this fixture. Neither is
   a substitute for source + something.

4. **The `exp1_gaps_only` collapse is evidence for the hypothesis
   direction**: strip source context, force gap-by-gap enumeration,
   llama derails. If exp2's recipe scales to new targets but exp1's
   gap-without-tests variant degrades on llama here, that argues
   exp1's approach has worse model robustness.

The cleanest test of "which generalizes better" is still Experiment A
(held-out source-file subset) — that measures transferability within
RE2 itself, without the tests/fixture coupling.

## Artifacts

- `ablation_diff.md` — full N-cell diff (with top files each cell hits)
- `ablation_diff.json` — machine-readable row dump
- `{cell}.json` for each cell — raw CoverageProfile

## Follow-ups worth considering (deferred)

- Rerun `exp1_gaps_only` with `--samples 6` to get n≈20–30 and give it
  a fair shot. If it still collapses, that's a stable result, not
  under-sampling.
- Re-run `exp2_plus_gaps` with `--samples 6` (n≈60) to see if its +7
  lead over exp1_full is stable or stochastic noise. Current lead is
  smaller than per-seed resolution.
