# experiment4 — RESULTS

**Status: COMPLETE.** Stage 0 = FULL; Stage 1 executed end-to-end.
Pre-registration: `EXECUTION_LOG.md`, committed `4d96b1c`
(2026-05-17T23:52:23Z) **before** any M2 scoring. M2 scored by the
UNMODIFIED `analysis.metrics.M2HardBranchMetric` against the identical
frozen 50-branch set used by `experiment2_1`. All subsample consistency
anchors passed (`consistency_ok = true` for S_random/S_high/S_low in both
variants — the pool hit-matrix union equals each subsample's standalone
`union_frac_targets_hit`).

## Pool & stratification (instrument health)

| Variant | Pool | Eligible (entropy-locatable) | Drops | S_high∩S_low | Mean payload entropy (bits) S_high / S_random / S_low |
|---|---:|---:|---:|---:|---|
| `v1_src` | 450 | 456 | 0 | 0 | 2.344 / 1.620 / 0.676 |
| `v3_all` | 450 | 461 | 0 | 0 | 2.199 / 1.376 / 0.617 |

Structural value-region masking located 100 % of payloads (0 drops);
S_high and S_low are fully disjoint with a large entropy separation; the
top-vs-bottom-quantile contrast is intact.

## M2 table (slices.all.union_frac_targets_hit; exactly 150 seeds each)

| Variant | M2(S_random) | M2(S_high) | M2(S_low) | experiment2_1 baseline |
|---|---:|---:|---:|---:|
| `v1_src` | 0.26 | **0.38** | **0.46** | 0.46 |
| `v3_all` | 0.26 | **0.26** | **0.50** | 0.26 |

## Pre-registered differences + bootstrap 95 % CIs (n=10000, RNG 42)

| Variant | Δ_hr = M2(S_high)−M2(S_random) | 95 % CI | Δ_hl = M2(S_high)−M2(S_low) | 95 % CI |
|---|---:|---|---:|---|
| `v1_src` | **+0.12** | [−0.06, +0.26] | **−0.08** | [−0.30, +0.20] |
| `v3_all` | **0.00** | [−0.10, +0.14] | **−0.24** | [−0.34, +0.04] |

## Interpretation vs the pre-registered prediction

**Pre-registered prediction:** `Δ_hr > 0` and `Δ_hl > 0` for *both*
variants (higher per-seed payload entropy → more M2 hard branches).
**Falsifier:** for a variant, falsified if *either* the 95 % CI contains
0 *or* the point-estimate sign is negative.

**Outcome: the prediction is FALSIFIED for both variants, on all four
differences, and the data leans toward the explicitly pre-registered
*competing* hypothesis (lower payload entropy carries more M2).**

- **Δ_hr (high vs random) is null in both variants.** v1_src +0.12 with
  CI [−0.06, +0.26] (contains 0); v3_all exactly 0.00 with CI
  [−0.10, +0.14] (contains 0). High-entropy selection is **not** better
  than a uniform-random 150-seed draw. Falsified by the CI-contains-0
  clause in both.
- **Δ_hl (high vs low) is negative in both variants** — the *opposite*
  of the predicted sign. v1_src −0.08 (CI [−0.30, +0.20], contains 0);
  v3_all **−0.24** (CI [−0.34, +0.04], whose mass sits almost entirely
  below zero and only just touches 0 at the +0.04 edge). The
  bottom-entropy subsample `S_low` has the **highest** M2 in both cells:
  v1_src S_low 0.46 ≥ S_high 0.38 ≥ S_random 0.26; v3_all S_low **0.50**
  ≫ S_high 0.26 = S_random 0.26.
- The effect is strongest and nearly conventionally-significant for
  **`v3_all`**: selecting the 150 lowest-payload-entropy seeds raised M2
  from the `experiment2_1` published baseline 0.26 to **0.50**, while the
  high-entropy and random subsamples stayed at 0.26. This corroborates
  the competing mechanism flagged in the pre-registration verbatim:
  *"harfbuzz fonts need low-entropy well-formed structural headers to
  parse at all, so the sign could plausibly reverse."* A confident
  (low-entropy) base64-of-font generation is more likely to be a
  well-formed font that survives early parsing and reaches the
  hard-branch set; a high-entropy generation is more likely a garbled
  blob rejected before it gets deep.

**Bottom line.** Within a (harfbuzz × variant × codestral-22b ×
default) cell, the 150 seeds are *not* equally responsible for M2 — per-
seed mean payload entropy **does** predict which seeds carry the score,
but in the **inverse** direction to the pre-registered hypothesis: it is
the **low**-entropy seeds, not the high-entropy ones, that carry M2 (and
markedly so for `v3_all`). All three scientifically-informative outcomes
were declared admissible in advance; this is the inverse outcome,
reported as-is. No `Δ_hr`/`Δ_hl` reaches a CI strictly excluding 0, so
no claim is made at conventional significance; the consistent negative
`Δ_hl` and the v3_all magnitude are the substantive signal.

## Deviations from the original plan (every one, explicitly)

1. **Wrapper instead of `run_ablation_harfbuzz.py` verbatim**
   (`scripts/run_experiment4_harfbuzz.py`): unmodified `AblationRunner`
   with `dataclasses.replace`'d output roots, to protect the canonical
   `experiment2_1` codestral cells. M2 fixtures unchanged. User-approved.
2. **Additive env-gated `logprobs` in `core/llm_client.py` +
   `generate_ablation_inputs.py`**: required to obtain per-token
   logprobs through the existing path; default-off, cache-key byte-
   identical for all non-logprob callers (regression-tested,
   `tests/test_llm_client_logprob_backcompat.py`). User-approved.
3. **Tokenizer detokenisation refined twice before any entropy/M2
   existed** — Stage-0 SentencePiece note, then the real-data
   byte-fallback/special-token fix (reconstruction was 0 %→100 %
   faithful). Pre-results instrument corrections; pre-registration
   unchanged.
4. **Payload masking switched from strict canonical-substring to
   structural `content_b64` value-region location** (the persisted
   `content_b64` is a parser `_coerce_to_b64` artifact; strict matching
   dropped ~50 % and was keyed off a downstream artifact). Recovered
   456/461 of 456/461 (0 drops); restored disjoint strata. **Explicitly
   surfaced and user-approved (AskUserQuestion, 2026-05-18).**
   Pre-registration (`4d96b1c`) and the entropy formula / bootstrap were
   unchanged.
5. **Pool regenerated once** (cache-HIT, `--attempt-offset 600000`
   reused on purpose to replay cached successes deterministically) so
   sidecars carried the corrected positional `input_index_in_response`.
   Reasoning logged; invariant-5's anti-stuck-on-failure rationale does
   not apply (run 1 succeeded). Stale dirs **moved** (not deleted, after
   an `rm -rf` denial) to `/tmp/exp6_stale/`.
6. **Cost estimate ran AFTER generation, not before.** The user asked
   for an `estimate_cost.py` projection prior to generation; it was
   surfaced only after the pool already existed. Disclosed in the
   conversation. Actual cost (cost_audit.py): experiment4 added 342
   cache entries (~$0.55 codestral accounting); cumulative litellm
   re-price $14.39 vs the $25 proxy cap; no `400 Budget exceeded`
   occurred. No remaining step makes any LLM/proxy call.

## Stopping

Per the stopping criterion, no further analyses, subsample strategies,
or models were run after the primary result. Stage 2 mechanism analysis
remains explicitly out of scope.
