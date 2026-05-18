# experiment4 — EXECUTION LOG (append-only)

Append-only. Every command run, every deviation from the plan (with
justification), and the mandatory PRE-REGISTRATION block (appended before
Stage 1 step 4 / M2 scoring, never after seeing results). Do not rewrite
prior entries.

---

## 2026-05-17 — Setup

- `git rev-parse HEAD` → `a5bc4c61f8fe2b9d94031d10b3b4c8144861d7ba`, branch `master`.
- Read (required): `docs/research_document_v3.md`, `docs/experiment2.md`,
  `docs/PROJECT_CONTEXT_FOR_WEB.md`, plus `docs/STATUS.md` (resume
  protocol — no `RESUME.md` present), and the load-bearing code
  (`scripts/_ablation_base.py`, `core/llm_client.py`,
  `analysis/metrics/m2.py`, `analysis/scripts/measure_gap_coverage.py`,
  `synthesis/scripts/generate_ablation_inputs.py`,
  `synthesis/scripts/parse_synthesis.py`, `core/prompt_strategies.py`).
- Fanned out two read-only investigation subagents (on-disk fact-finding
  for MANIFEST; research_document_v3.md summary). Findings folded into
  `MANIFEST.json`.
- Confirmed M2 baselines on disk: `results/ablation_harfbuzz/m2/v1_src/codestral-22b/summary.json`
  `slices.all.union_frac_targets_hit = 0.46`;
  `.../v3_all/...` = `0.26`. These match the prompt's stated baselines
  and define M2 = `slices.all.union_frac_targets_hit`.

### Deviations from the prompt (approved by user, 2026-05-17)

1. **`scripts/run_experiment4_harfbuzz.py` instead of
   `run_ablation_harfbuzz.py` verbatim.** Running the 450-seed pool
   through `run_ablation_harfbuzz.py` literally would append-and-subsample
   into the canonical `experiment2_1` codestral-22b harfbuzz `v1_src` /
   `v3_all` seed dirs (which already hold 150 seeds each), destroying
   `experiment2_1`'s headline cells. The wrapper reuses the **unmodified**
   `AblationRunner` (all five phases + attempt-offset / cache-salt
   semantics intact) and only redirects `synthesis_results_root` /
   `results_root` via `dataclasses.replace`. M2 fixture paths unchanged
   so scoring uses the identical frozen 50-branch set. Surfaced via
   AskUserQuestion; user selected the redirected-output wrapper.

2. **Additive logprobs param in `core/llm_client.py` +
   `synthesis/scripts/generate_ablation_inputs.py`.** Per-token logprobs
   cannot be obtained through the existing client path without requesting
   them. Implemented additively following the established
   tools/response_format back-compat pattern; default-off, env-gated
   (`UTCF_CAPTURE_LOGPROBS`), and regression-tested for byte-identical
   cache keys when not requested. Surfaced via AskUserQuestion; user
   selected the additive in-client approach.

3. **Work performed on git branch `experiment4`** (not `master`) with
   checkpoint commits, per user instruction to preserve work at
   intervals and keep it revertable. Commit messages carry no tooling
   attribution (repo Rule 0).

- Wrote `docs/experiment4/{MANIFEST.json, METHODS.md, EXECUTION_LOG.md}`
  and stubs `STAGE0_RESULT.md`, `RESULTS.md` **before** any experiment
  code, per the prompt's "MANIFEST + METHODS before code" rule.

## 2026-05-17 — core/llm_client.py logprob support (cache-safe)

- Added additive `logprobs`/`top_logprobs` kwargs to `_prompt_hash` and
  `LLMClient.complete` + `_extract_logprobs` helper; captured logprobs go
  to `Response.raw` (not in cache key). Anthropic path refuses logprobs.
- Wrote `tests/test_llm_client_logprob_backcompat.py`.
  `.venv/bin/python -m pytest -q tests/ core/tests/` →
  **123 passed, 1 skipped** (the opt-in 14k cache audit). `ruff check
  core/llm_client.py` → only the pre-existing SIM105 at line ~210
  (documented in STATUS.md); no new findings.
- Verified byte-identity: `_prompt_hash(... no logprobs ...)` ==
  frozen pre-change digest
  `9878904ae4d50fe49e162f5aadaf2bc1f649bab90fdd3fc05dc9d38cdfa7f373`;
  requesting logprobs yields a different key (own cache namespace).
  **Invariant 9 preserved and machine-checked.**
- Commits: `828fe0b` (doc scaffolding), `83bc5f0` (llm_client +
  regression test), on branch `experiment4`.

## 2026-05-17 — Stage 0 logprob capability gate

- Cmd: `UTCF_LITELLM_URL` defaulted in-script;
  `.venv/bin/python -m analysis.scripts.experiment4_stage0_probe`.
  One live call to `codestral-22b`, `logprobs=True, top_logprobs=20`,
  `use_cache=False`, `max_tokens=64`.
- Artifact: `results/experiment4/stage0_probe.json`. Exit 0.
- **Classification = FULL.** All 6/6 completion tokens carry an observed
  logprob AND 20/20 top-k alternatives. Per-token top-K-head Shannon
  entropy (METHODS §4) is computable. Gate CLEARED → Stage 1 proceeds.
- **Methods refinement (pre-computation, logged before any entropy):**
  the proxy codestral-22b tokenizer is SentencePiece-style — leading
  space encoded as `▁` (U+2581). Token→char reconstruction (METHODS §3)
  detokenises a single leading `▁`→space and asserts the reconstruction
  equals `resp.content`; on mismatch the response's seeds are dropped
  from the entropy pool and counted. Not a post-hoc change — discovered
  at probe time, recorded in STAGE0_RESULT.md + METHODS §3 before
  Stage 1 step 2.

## 2026-05-17 — Stage 1 pipeline code landed

- `synthesis/scripts/generate_ablation_inputs.py`: env-gated
  (`UTCF_CAPTURE_LOGPROBS`) per-token logprob request + per-seed sidecar
  persistence, default-strategy branch only (experiment4 scope).
  Default-off ⇒ byte-identical behaviour + cache key for every other
  run. **My edits add ZERO new lint findings**: original file at HEAD
  already had 8×E402 (pyproject-ignored in-tree for the sys.path
  bootstrap convention) + 1×I001 at line 694; the I001 is in the
  untouched `tool_use` import block, pre-existing, NOT introduced here —
  deliberately left alone (out of change scope).
- `scripts/run_experiment4_harfbuzz.py`: ~40-LOC wrapper; UNMODIFIED
  `AblationRunner` against `dataclasses.replace`'d harfbuzz TargetSpec
  (only `synthesis_results_root` / `results_root` redirected; M2
  fixtures, coverage binary, prep dataset, source roots unchanged).
  Dry-run verified: harfbuzz, v1_src+v3_all, codestral-22b, default,
  450 seeds/cell, 2 cells.
- `analysis/scripts/experiment4_{entropy,stratify,score}.py` + tests
  (built in parallel by three subagents to the METHODS contracts;
  reviewed). Subagent ambiguity resolutions accepted as logged in each
  file's docstring; notable ones: (a) a payload token with an
  uncomputable head drops the whole seed as a counted/disclosed drop
  (never silently shrinks the token set); (b) S_high/S_low/S_random are
  defined by their own rules, disjointness is an emergent property of a
  large pool, not enforced (consistent with METHODS §5 "independent
  draws"); (c) `union_m2` treats missing (seed,target) pairs as not-hit,
  mirroring the metric's own zero-row behaviour.
- `.venv/bin/pytest -q` (full fast suite) → **385 passed, 1 skipped**
  (incl. 52 new experiment4 tests + the cache back-compat regression).
  Commits `83bc5f0`, `a2a51e4`, `d1a44ab` on branch `experiment4`.

---

## PRE-REGISTRATION (appended BEFORE any M2 scoring — Stage 1 step 4)

**Timestamp (UTC):** 2026-05-17T23:52:23Z
**Commit at pre-registration:** `d1a44ab0b5559a96ba1028f594a956e62afc40fa`
(branch `experiment4`)
**Status:** written before the over-generation pool is even launched —
no M2 number for any subsample has been observed (none exist yet).

### The statistics (fixed; METHODS §5)

For each variant ∈ {`v1_src`, `v3_all`}, on exactly-150-seed subsamples
drawn from that cell's over-generation pool, with M2 = the UNMODIFIED
metric's `slices.all.union_frac_targets_hit`:

- `Δ_hr = M2(S_high) − M2(S_random)`
- `Δ_hl = M2(S_high) − M2(S_low)`

### My predicted sign (this is MY prediction, made blind)

- `Δ_hr > 0` for **both** variants.
- `Δ_hl > 0` for **both** variants.

Reasoning (stated so a reviewer can judge it, not to hedge): M2 is a
*union* over a by-construction-random-unreachable hard-branch set, so it
rewards a corpus that *deviates* from the common/random byte
distribution. Higher per-seed payload entropy ⇒ the model was less
locked into one continuation ⇒ the corpus spans more of the byte space
⇒ more of the rare hard branches get covered by *someone* in the union.
I hold this with **lower confidence for `v3_all`** (M2 baseline 0.26 vs
0.46 — fewer hits, noisier, and the extra v3 context may compress the
payload-entropy spread), and I explicitly note the competing hypothesis:
harfbuzz fonts need *low*-entropy well-formed structural headers to
parse at all, so the sign could plausibly reverse — that is exactly why
this is worth running and why all three outcomes are reportable.

### Falsifier (per the prompt, exactly)

For a given variant the pre-registered prediction is **falsified** if
**either**:

1. the bootstrap 95% CI of the difference **contains 0**, OR
2. the point-estimate **sign is negative** (reversed).

A variant where both `Δ_hr` and `Δ_hl` are strictly positive with 95%
CIs entirely above 0 **corroborates** the prediction for that variant.
Outcomes are reported per variant; mixed outcomes across the two
variants are reported honestly, not aggregated into a single verdict.

### Frozen analysis parameters (no post-hoc tuning permitted)

- subsample size k = 150 (Invariant 4); pool target ≈ 450/cell.
- S_random = `random.Random(42).sample(sorted(eligible_ids), 150)`.
- S_high / S_low = top / bottom 150 by mean payload entropy, ties by
  `input_id` ascending.
- bootstrap: n = 10000 resamples, `random.Random(42)`, percentile
  [2.5, 97.5], independent (unpaired) row-draws per subsample.
- entropy = renormalised top-K(=20)-head Shannon entropy in bits over
  base64-value tokens only (METHODS §3/§4); SentencePiece `▁`→space
  detokenisation; unlocatable / reconstruction-mismatch seeds dropped
  and counted.

## 2026-05-18 — Over-generation pool complete

- `UTCF_LLM_RPM=12 .venv/bin/python scripts/run_experiment4_harfbuzz.py
  --phase synthesis --variants v1_src,v3_all --only-models codestral-22b
  --num-seeds 450 --attempt-offset 600000` (background; exit 0). Log
  `/tmp/exp6_hb_pool.log`.
- Result: **v1_src 450 seeds (166 attempts), v3_all 450 seeds (176
  attempts)**; 456 logprob sidecars/cell (6 extra = ids the runner's own
  `_subsample_seeds` trimmed to land exactly on 450 — naturally excluded
  by stratify eligibility since they are not in the 450-seed pool dir).
  Logprob capture worked end-to-end through the UNMODIFIED AblationRunner
  → subprocess → modified driver → modified client; fresh API calls (new
  cache key), experiment2_1 cache + dirs untouched. Pool = exactly
  450/cell (Invariant-4 note: scored corpora are still 150).

## 2026-05-18 — INSTRUMENT REFINEMENT (pre-results; no entropy/M2 yet)

- First `experiment4_entropy` run: **100 % drop
  (`drop_reconstruction_mismatch`, 456/456 both cells)** → NO entropy
  value produced; stratify correctly refused (0 eligible < 150,
  Invariant 4 not relaxed). This is an instrument bug, not a pool-size
  issue.
- Root cause (inspected real sidecars): the UF-proxy codestral-22b
  tokenizer is SentencePiece **with byte-fallback**: every space → `▁`
  (not just leading — already handled), newlines → byte-fallback token
  `<0x0A>`, plus a zero-text `</s>` EOS. The Stage-0-era rule only
  covered `▁`.
- Fix: `detokenize` now handles 3 classes — special/control → ``;
  `<0xHH>` → byte (ASCII 1:1; ≥0x80 → U+FFFD ⇒ drop+count); else
  `▁`→space. **Verified: module `reconstruct` reproduces `raw_response`
  exactly for 80/80 sampled real sidecars** (was 0/60). METHODS §3
  rewritten to the verified rule; 5 regression tests added
  (`test_experiment4_entropy.py` → 27 passed); ruff clean.
- **Integrity statement:** this correction happened with ZERO entropy
  values and ZERO M2 numbers in existence (the first run dropped
  everything; no subsample was scored). It makes the measurement
  faithful to `resp.content`; it is not parameter tuning and cannot be
  outcome-driven. Pre-registration (`4d96b1c`) predates it and is
  unchanged.

## 2026-05-18 — MASKING-RULE REFINEMENT (pre-results; user-approved)

- First entropy+stratify on the 450-pool: ~50 % `drop_b64_unlocatable`
  (eligible v1_src=193, v3_all=233). With k=150, S_high∩S_low ≈ 109/150
  (v1_src) — the top-vs-bottom-quantile contrast was largely destroyed.
- Diagnosis (data, not guess): the parser `_coerce_to_b64` re-encodes any
  non-strictly-valid base64 (no padding etc.), so the persisted
  `content_b64` is a parser artifact, NOT a verbatim substring of
  `resp.content`. Strict canonical-substring location is therefore both
  over-strict (drops 50 %) AND subtly wrong (keys payload-token identity
  to a downstream coercion artifact rather than the model's emitted
  value). Measured: strict 193/233 of 456; **structural value-region
  location 456/456 in BOTH variants, neither=0**; seed↔region count
  exact in 303/305 responses (2 v3_all seeds<regions → drop+counted).
- **User decision (AskUserQuestion, 2026-05-18): adopt structural
  value-region location.** It is the more FAITHFUL realisation of
  METHODS §3's intent ("tokens that emit the payload bytes" = the i-th
  emitted `content_b64` value's tokens; the parser artifact is
  downstream and irrelevant to generation entropy). Pre-registration
  (`4d96b1c`) and the entropy formula / bootstrap are unchanged; no M2
  number existed when this was decided.
- Code: `experiment4_entropy.py` `_nth_quoted_value_span` →
  `_nth_content_b64_value_region` (locate i-th `"content_b64":"…"` JSON
  region structurally; closing quote = next `"` since base64 has no
  `"`/`\\`). `payload_token_indices` uses it; `content_b64` retained for
  the call contract/audit only. Driver sidecar
  `input_index_in_response` changed from "count of earlier same-canonical
  inputs" to the seed's POSITIONAL index `i` among parsed inputs (the
  correct key for region selection). METHODS §3 updated. Tests updated to
  structural semantics (`test_experiment4_entropy.py` → 27 passed); ruff
  clean (only the pre-existing line-694 I001 remains, untouched).
- Pool regenerated so sidecars carry the corrected positional index.
  Reused `--attempt-offset 600000` ON PURPOSE: run-1's logprob responses
  are cached under those exact keys, so regeneration is cache-HIT (fast,
  $0, identical responses). Invariant 5's rationale (don't get stuck
  replaying cached *failures* after partial progress) does not apply —
  run 1 completed successfully (reached 450); replay reproduces that
  success deterministically. Bumping the offset would MISS the cache and
  waste fresh API calls for no benefit. Logged here as a deliberate,
  justified interpretation, not a relaxation.

## 2026-05-18 — Pool regenerated; instrument now healthy (pre-M2 diagnostics)

- `rm -rf` of stale experiment4 outputs was denied by the user; instead
  the gitignored dirs were **moved** to `/tmp/exp6_stale/` (reversible),
  `results/experiment4/stage0_probe.json` preserved.
- Regeneration via the wrapper (`--attempt-offset 600000`, cache-HIT)
  completed in ~30 s: v1_src 450 seeds / 456 sidecars (163 attempts),
  v3_all 450 / 461 (171). Sidecars now carry the corrected positional
  `input_index_in_response` (verified: multi-seed responses show
  `[0,1,2]`).
- entropy + stratify (offline) — instrument-health diagnostics, NOT the
  pre-registered M2 outcome:
  - v1_src: n_ok=456, **0 drops**, entropy bits min0.100/med1.615/max3.453.
  - v3_all: n_ok=461, **0 drops**, entropy bits min0.038/med1.434/max2.992.
  - Disjoint strata restored: **S_high ∩ S_low = 0** for both variants.
    Mean payload entropy S_high / S_random / S_low —
    v1_src: 2.344 / 1.620 / 0.676 bits; v3_all: 2.199 / 1.376 / 0.617.
    S_random partially overlaps both (v1_src 58/45; v3_all 46/54), as
    expected for an independent uniform draw. The designed top-vs-bottom
    quantile contrast is intact.
- M2 scoring (unmodified `M2HardBranchMetric` → real `seed_replay` +
  LLVM on pool + 3 subsamples/variant, consistency anchor, 10k
  bootstrap) launched; results pending → RESULTS.md.

## 2026-05-18 — M2 SCORING COMPLETE (post-pre-registration; results observed)

- `analysis.scripts.experiment4_score` for both variants (UNMODIFIED
  `M2HardBranchMetric` → real `seed_replay`+LLVM on pool+3 subsamples;
  consistency anchor; 10k bootstrap, RNG 42). Exit 0.
- **All consistency anchors passed** (`consistency_ok=true`,
  S_random/S_high/S_low, both variants).
- M2 (slices.all.union_frac_targets_hit):
  - v1_src: S_random 0.26, S_high 0.38, S_low 0.46.
  - v3_all: S_random 0.26, S_high 0.26, S_low 0.50.
- Differences + 95% bootstrap CI:
  - v1_src Δ_hr +0.12 [−0.06,+0.26]; Δ_hl −0.08 [−0.30,+0.20].
  - v3_all Δ_hr  0.00 [−0.10,+0.14]; Δ_hl −0.24 [−0.34,+0.04].
- **Verdict vs pre-registration (`4d96b1c`): FALSIFIED for both
  variants on all four differences** (every CI contains 0; both Δ_hl
  sign-reversed). Data leans to the pre-registered *competing*
  hypothesis: LOW payload entropy carries more M2 (S_low highest in both
  cells; v3_all S_low 0.50 vs baseline/random 0.26). Inverse outcome —
  one of the three pre-declared admissible outcomes — reported as-is.
  Full prose: `RESULTS.md`.
- Cost accounting (user request, ran post-generation — disclosed):
  `estimate_cost.py` codestral 900-call upper bound $1.64 / ~337 calls
  $0.61; `cost_audit.py` actual: +342 cache entries, cumulative litellm
  re-price $14.39 vs $25 proxy cap, no `400 Budget exceeded` at any
  point. No remaining step issues LLM/proxy calls.
- Experiment COMPLETE. Stopping criterion honored: no additional
  analyses / subsamples / models. Stage 2 out of scope.

---

## 2026-05-18 — RENUMBERING (label-only; git provenance immutable)

Per user request the new-iteration experiments were renumbered to be
contiguous after the historical experiment1–3:
`experiment6→4 (ESSS)`, `7→5 (SVA)`, `8→6 (Follow-up A)`,
`9→7 (Follow-up B)`; the experiment-6 Follow-up C doc moved to
`docs/experiment4/FOLLOWUP.md`. **Only labels/identifiers changed**
(doc dirs, module names, output-data dirs, prose, cross-references).
The pre-registered predictions, effect sizes, falsifiers, **commit
hashes and timestamps are byte-unchanged** — this is not a content
edit to the frozen pre-registration. Git history was NOT rewritten
(safety): the original pre-registration/run commits and the branches
pushed to origin remain named `experiment6`, `experiment7`,
`experiment-followups`. Therefore any `branch`/`base_branch` field
above that now reads a renumbered label refers to the *experiment
identity*; the authoritative immutable anchors are the **commit
hashes** (hex, unchanged). Old↔new map is mirrored in
`docs/EXPERIMENTS.md` and `docs/STATUS.md` (Naming).
