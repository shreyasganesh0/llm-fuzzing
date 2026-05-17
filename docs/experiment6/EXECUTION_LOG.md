# experiment6 — EXECUTION LOG (append-only)

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

1. **`scripts/run_experiment6_harfbuzz.py` instead of
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

3. **Work performed on git branch `experiment6`** (not `master`) with
   checkpoint commits, per user instruction to preserve work at
   intervals and keep it revertable. Commit messages carry no tooling
   attribution (repo Rule 0).

- Wrote `docs/experiment6/{MANIFEST.json, METHODS.md, EXECUTION_LOG.md}`
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
  regression test), on branch `experiment6`.

## 2026-05-17 — Stage 0 logprob capability gate

- Cmd: `UTCF_LITELLM_URL` defaulted in-script;
  `.venv/bin/python -m analysis.scripts.experiment6_stage0_probe`.
  One live call to `codestral-22b`, `logprobs=True, top_logprobs=20`,
  `use_cache=False`, `max_tokens=64`.
- Artifact: `results/experiment6/stage0_probe.json`. Exit 0.
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

<!-- subsequent entries appended below as work proceeds -->
