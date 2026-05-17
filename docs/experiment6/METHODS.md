# experiment6 — METHODS

Every methodological choice, with its justification. This file is written
*before* the code that depends on it (the payload-masking rule in §3 is
load-bearing for interpretability and is fixed here before any entropy is
computed). If a later choice contradicts something here, the contradiction
is logged in `EXECUTION_LOG.md` with a justification — this file is not
silently rewritten after seeing results.

---

## 1. Scientific claim under test

Within one `(target=harfbuzz, variant, model=codestral-22b, strategy=default)`
cell, the 150 seeds that determine the M2 union score are **not**
interchangeable. Per-seed *mean payload entropy* of the generation predicts
which seeds carry M2. Operationally: a 150-seed subsample chosen as the
**top** quantile by mean payload entropy (`S_high`) hits more M2 hard
branches than a **bottom**-quantile subsample (`S_low`), with a
uniformly-random subsample (`S_random`) in between.

This is a directional, falsifiable hypothesis. The three outcomes
(predicted direction holds / no relationship / inverse) are all
scientifically reportable. The pre-registered prediction and its falsifier
are recorded in `EXECUTION_LOG.md` **before** M2 scoring (Stage 1 step 4),
not here.

## 2. M2 metric — exactly as the existing pipeline defines it

We do not redefine M2. M2 for a corpus `S` is

    M2(S) = (# of the 50 frozen hard branches hit by the UNION of seeds in S) / 50

read directly as `slices.all.union_frac_targets_hit` from the `summary.json`
that the **unmodified** `analysis/scripts/measure_gap_coverage.py` writes
(invoked through `analysis.metrics.M2HardBranchMetric`, also unmodified).

- The frozen hard-branch set is `dataset/fixtures/harfbuzz_ab/harfbuzz/m2_target_branches.json`
  (50 branches: 30 `shown` + 20 `held_back`), produced by
  `freeze_target_branches.py` under the filter
  `struct_hits >= 1 AND rand_hits == 0`. **Invariant 2.** This experiment
  neither re-freezes nor relaxes that filter. It scores against the
  identical frozen file used by `experiment2_1`, so its numbers are
  directly comparable to the published `v1_src=0.46` / `v3_all=0.26`
  baselines.
- M2 is a *set/union* function of the corpus, not a per-seed mean. The
  headline number for each subsample is the union fraction over the
  `all` slice. We also retain `shown` / `held_back` slice numbers for
  audit but the pre-registered statistic is the `all` slice.

### 2.1 Why M2 of a subsample is computable without re-replaying

`measure_gap_coverage.py` writes `gap_hits.jsonl`: one line per
`(seed_id, target_idx)` with a boolean `hit`. That is exactly the
per-seed × 50-branch hit matrix. Two consequences:

1. We run the **unmodified** metric once per *materialised subsample
   directory* (S_random/S_high/S_low) to obtain its canonical
   `summary.json` → `union_frac_targets_hit` is M2(S). This is the
   "score M2 on each subsample using the existing M2 metric" step,
   taken literally.
2. The same run's `gap_hits.jsonl` is the resampling substrate for the
   bootstrap (§5). The bootstrap therefore reuses the metric's own
   output; it does not re-implement the hit logic or the filter.

We additionally run the metric once over the **full pool** as a
consistency anchor: the union over a subsample's rows in the pool matrix
must equal that subsample's standalone `union_frac_targets_hit`. A
mismatch aborts the experiment (it would indicate a seed-identity bug).

## 3. Payload-vs-structural masking rule (LOAD-BEARING — fixed before any entropy is computed)

codestral-22b on harfbuzz emits the binary-format response parsed by
`synthesis/scripts/parse_synthesis.py::parse_synthesis_response`. The wire
shape is JSON:

```
{"inputs": [
   {"content_b64": "<BASE64>", "target_gaps": ["file:line", ...], "reasoning": "..."},
   ... up to num_inputs ...
]}
```

The **payload** of a seed is the harfbuzz font blob = the bytes obtained by
`base64.b64decode(content_b64)` and written verbatim to `seed_*.bin`.
"Mean payload entropy" is the mean per-token Shannon entropy over **only
the tokens that emit the base64 characters of that seed's `content_b64`
string value** — and nothing else.

Concretely, a generation token is a **payload token for seed *j*** iff:

- it falls strictly inside the character span of the *value* of the
  `content_b64` key for the *j*-th parsed input — i.e. between (but not
  including) the opening and closing `"` that delimit that base64 string;
  and
- it lies in the half-open char range `(open_quote, close_quote)` of that
  specific value occurrence (the *j*-th, matched positionally to the *j*-th
  parsed `GeneratedInput`).

A token is **structural / excluded** if it emits any of: JSON punctuation
or whitespace (`{ } [ ] : ,` and inter-field whitespace/newlines), the key
names (`"inputs"`, `"content_b64"`, `"target_gaps"`, `"reasoning"`), the
surrounding double-quotes themselves, the `target_gaps` array, the
`reasoning` string, any markdown code fence (```` ```json ````), or any
prose the model emits around the JSON.

Token→char mapping: logprob responses return `choices[0].logprobs.content`
as an ordered list of token records. **Refinement recorded after the
Stage 0 probe, before any entropy is computed** (see STAGE0_RESULT.md,
EXECUTION_LOG.md): the UF-proxy codestral-22b tokenizer is SentencePiece-
style — a leading space is encoded as `▁` (U+2581) on the token, so the
raw `.token` strings do *not* concatenate to the literal completion. The
reconstruction therefore detokenises each `.token` by replacing a single
leading `▁` with a space before accumulating char offsets, and the
reconstructed string is asserted equal to `resp.content` at runtime. On
mismatch the whole response's seeds are dropped from the entropy pool and
counted (same disclosure rule as below) rather than guessing an
alignment. Cumulative detokenised lengths give each token a half-open
`[start, end)` char span; the `content_b64` value spans are located on
the reconstructed text with the same lenient JSON view the parser uses
and matched positionally to the parsed inputs.

Boundary tokens (a token that straddles the closing `"` of a base64 value,
common because models tokenise `...XYZ"` as one piece) are **excluded**:
only tokens whose entire span lies within `(open_quote, close_quote)`
count. This biases the payload-token set toward *pure* base64 tokens,
which is the conservative choice for interpretability — we measure the
entropy of the bytes-emitting tokens, not the entropy of "deciding to stop
the string."

Seed exclusion: if a parsed seed's `content_b64` cannot be located as a
verbatim substring of the raw completion (e.g. the parser's
`_coerce_to_b64` re-encoded hex/text/non-b64 input, or the JSON-salvage
path reconstructed it), that seed **cannot have a faithful payload-token
set** and is dropped from the entropy-stratified pool. The drop is counted
and reported in `RESULTS.md`; it does not silently shrink a stratum
without disclosure. (We expect this to be rare for codestral, which emits
clean base64, but it must be handled, not assumed away.)

Rationale for masking exactly here: the hypothesis is about the *payload*
the fuzzer ingests. Structural-token entropy is dominated by the model's
JSON-formatting certainty and `reasoning` prose — confounds with nothing
to do with whether the resulting font blob reaches a hard branch. Masking
to base64-value tokens isolates "how uncertain was the model about the
bytes it shipped."

## 4. Per-token entropy formula

For a payload token *t*, the API returns the chosen token's logprob plus
the top-`K` alternatives' logprobs (`K = top_logprobs = 20`). Let the
returned set of (token, logprob) pairs for position *t* be
`{(w_i, ℓ_i)}`. Convert to probabilities `p_i = exp(ℓ_i)`. These do **not**
sum to 1 (they are a truncated head of the full vocab distribution). We
compute the **Shannon entropy of the observed top-K head, renormalised**:

    q_i = p_i / Σ_j p_j
    H(t) = − Σ_i q_i * log2(q_i)      [bits]

This is an entropy *estimate over the model's top-K head*, not the full
softmax — stated explicitly because it is a known, bounded approximation:
H(t) ∈ [0, log2(K)] = [0, ≈4.32] bits. It is monotone in the model's
local uncertainty over its most probable continuations, which is the
quantity the hypothesis is about. The renormalisation choice and the
top-K-head caveat are recorded here so the result is interpreted as
"top-20-head entropy," not "true predictive entropy."

If Stage 0 returns **PARTIAL** (observed logprob only, no top-K), H(t) is
not computable and the experiment STOPS and asks the user (surrogate:
surprisal stratification) — it does not silently substitute surprisal.

Per-seed statistic: `mean_payload_entropy(j) = mean over payload tokens of
seed j of H(t)`. Seeds with zero payload tokens after masking are dropped
(counted/reported), same disclosure rule as §3.

## 5. Bootstrap procedure

For each variant we report two differences with 95% bootstrap CIs:

    Δ_hr = M2(S_high) − M2(S_random)
    Δ_hl = M2(S_high) − M2(S_low)

M2 is a union (set) statistic, so the resampling unit is the **seed**.
From the unmodified metric's `gap_hits.jsonl` for a 150-seed subsample we
have a 150×50 boolean matrix `B`. One bootstrap replicate of `M2(S)`:

1. draw 150 row indices uniformly with replacement from `0..149`;
2. `M2* = (# columns c where any drawn row has B[row, c] == True) / 50`.

`S_high`, `S_random`, `S_low` are disjoint seed sets, so their replicates
are drawn **independently** (no pairing across subsamples). For
`b = 1..10000`: compute `M2*_high(b)`, `M2*_random(b)`, `M2*_low(b)` from
independent row-draws, then
`Δ_hr*(b) = M2*_high(b) − M2*_random(b)` and likewise `Δ_hl*`. The 95% CI
is the [2.5, 97.5] percentile of `{Δ*(b)}`. `n_resamples = 10000`. The
bootstrap RNG is `random.Random(42)`, instantiated once at the bootstrap
call site (a different, independently-seeded `random.Random(42)` instance
than the one used for `S_random` selection — same seed value, distinct
streams at distinct call sites; both fixed for reproducibility per
research_document_v3.md §6.2 "fixed random seeds recorded for every
trial").

Falsifier (the literal pre-registered condition is in `EXECUTION_LOG.md`):
the prediction is falsified for a variant if either CI contains 0, or if
the point estimate sign is opposite to the pre-registered sign.

## 6. Determinism of subsample selection

- `S_random`: `sorted(pool, key=seed_id)` then `random.Random(42).sample(.,150)`.
  Mirrors the existing protocol (`scripts/_ablation_base.py::_subsample_seeds`
  uses `random.Random(42).sample`). **Invariant 3.**
- `S_high`: pool sorted by `mean_payload_entropy` descending; ties broken
  by `seed_id` ascending (total order → fully deterministic); take first 150.
- `S_low`: same key ascending; take first 150.
- All three seed-id lists are persisted under
  `results/experiment6/<variant>/subsamples/{S_random,S_high,S_low}.json`
  before scoring.
- Each subsample is materialised as a directory of the actual `seed_*.bin`
  files (copied, not symlinked, so the metric's `iterdir()` is stable)
  so the **unmodified** `M2HardBranchMetric.compute_cell` runs against it
  with no metric-code fork. **Invariant 4** (every scored dir = 150).

## 7. Over-generation, isolation, cache safety

- Orchestration is the **unmodified** `scripts._ablation_base.AblationRunner`
  driven by a ~40-LOC wrapper `scripts/run_experiment6_harfbuzz.py` that
  passes `dataclasses.replace(TARGETS['harfbuzz'], synthesis_results_root=
  synthesis/results/experiment6, results_root=results/experiment6)`. M2
  fixture paths are left untouched so scoring uses the identical frozen
  set. The canonical `experiment2_1` dirs are never written.
- Pool size 450/cell via `--num-seeds 450`. The non-default `--num-seeds`
  warning is expected and acknowledged: 450 is the *pool*; every *scored*
  corpus is exactly 150 (invariant 4 intact). If a single
  attempt-capped pass yields < ~450, additional passes with the
  `--attempt-offset` bumped by ≥5000 each (invariant 5) accumulate into
  the same pool dir; every deviation logged in `EXECUTION_LOG.md`.
- `--attempt-offset 600000` (≥5000 bump; clear of all prior offsets).
- Logprob capture is env-gated (`UTCF_CAPTURE_LOGPROBS`, set only by the
  experiment6 wrapper). `LLMClient.complete(logprobs=..., top_logprobs=...)`
  and `_prompt_hash` add these to the cache-key payload **only when
  non-None**; default `None` ⇒ byte-identical key. The 14,268 pre-existing
  `.cache/llm/` entries and every non-logprob caller are provably
  unaffected — guarded by `tests/test_llm_client_logprob_backcompat.py`,
  which asserts `_prompt_hash(...)` with `logprobs=None` equals a frozen
  pre-change digest. **Invariant 9.**

## 8. Threats to validity carried from the spec

- harfbuzz is a Tier-1, high-contamination, mixed binary/text target
  (research_document_v3.md §4.2, §9 TV1). Payload entropy on a partly
  base64-of-binary format is being measured at the *base64-character*
  token level, not raw-byte level — the entropy is of the model's
  base64-emitting decisions. This is the honest readout of "model
  uncertainty about the bytes it shipped" and is stated as such; we do
  not claim it equals the Shannon entropy of the decoded font bytes.
- Top-K-head truncation (§4) bounds H ≤ log2(20). Differences between
  strata are still informative because the truncation is identical across
  all seeds.
- M2 is a union statistic; a single extreme seed can move it. The
  bootstrap over seed composition (§5) is exactly the right uncertainty
  model for that, which is why we bootstrap the set function rather than a
  per-seed mean.
