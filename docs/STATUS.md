# Project Status — LLM-Guided Fuzzing Seed Corpora

_Living handoff doc. Rewritten whenever state changes. If you're a fresh
Claude instance: read this first, then `docs/AB_RE2_REPORT.md`; the
`docs/research_document_v3.md` and `docs/claude_code_plan_v3.md` are the
authoritative specs._

**Last updated:** 2026-04-13

---

## 1. TL;DR — where we are right now

We have an end-to-end pipeline that:
1. Builds a coverage-instrumented RE2, extracts upstream tests, computes
   baseline coverage + gaps.
2. Calls an LLM (llama-3.1-8b-instruct via UF LiteLLM proxy) to synthesise
   seed inputs in two variants — **exp1** (gap-targeted) and **exp2**
   (source-only).
3. Measures per-seed-corpus coverage on a standalone `seed_replay` binary
   and emits a set-wise diff.

The 2026-04-13 regex-format A/B shows exp1 wins by **+110 edges
in-distribution** on RE2 (1243 vs 1133). Follow-up generalization
experiments run the same day show this advantage is **largely
fixture-specific**:

- **Random baseline:** 980 edges. Both LLM cells clear the floor.
- **Experiment A — held-out source subset:** restricting exp1's gap list to
  parser files (set A) and measuring coverage on execution files (set B),
  `exp1_heldout` (581) *loses* to `exp2_source` (596) by 15 edges; the
  in-distribution +110 collapses to **+3** on held-out files. Hypothesis
  "exp2 generalizes better" supported.
  (`dataset/fixtures/re2_ab/ab_coverage/heldout_summary.md`)
- **Experiment B — prompt ablation (7 cells):** new leader is
  `exp2_plus_gaps` (1250 > `exp1_full`=1243). Source code is the
  load-bearing context; gaps amplify it; `exp1_gaps_only` collapsed
  (879, loop aborts). Hypothesis supported with nuance: exp2's recipe +
  cheap gap add is the efficient frontier.
  (`dataset/fixtures/re2_ab/ab_coverage/ablation_summary.md`)
- **Experiment C (1h × 3-trial libFuzzer campaigns):** deferred. Requires
  building `build/fuzzer/` variant + ~9 CPU-hours. Current A+B evidence
  was judged sufficient; revisit if campaign-time data is requested.

**Nothing is actively in-progress** at time of writing. Pipeline is green,
A+B artifacts on disk, slide deck built (still reflects 2-cell A/B — not
yet regenerated for the new experiments).

---

## 2. Planned next (ordered by payoff / cost)

1. **Write up A+B results** — update `docs/AB_RE2_REPORT.md` with
   held-out + ablation tables, regenerate slide deck. Zero LLM cost;
   ~1 hour edit.
2. **Experiment C (1h × 3-trial libFuzzer campaign)** — deferred
   2026-04-13 after A+B. Needs `build/fuzzer/` variant (empty dir right
   now; run `build_instrumented.sh`) + ~9 CPU-hours. Revisit if reviewers
   want campaign-time data.
3. **Follow-ups flagged in ablation_summary.md:**
   - rerun `exp1_gaps_only` with `--samples 6` to give it n≈20–30 past
     the loop-abort rate; confirm the 879-edge collapse is stable.
   - rerun `exp2_plus_gaps` with `--samples 6` (n≈60) to test whether
     its +7 lead over `exp1_full` is stochastic noise.
4. **Frontier-model smoke run** (GPT-4o or Claude Sonnet on regex
   format, exp1 + exp2 on RE2) — ~$5-10. Tells us whether llama's
   residual 1/6 loop rate is hiding an effect. Higher priority now that
   `exp1_gaps_only` collapsed on llama specifically.
5. **Second target A/B** — weeks of work. Requires pinned SHAs, a new
   fixture, a new target-specific prompt template.
6. **24h libFuzzer campaign per seed set** — blocked on 29,440 CPU-hour
   cluster allocation.

---

## 3. How the pipeline works (1-screen version)

```
Phase 1  ── build_instrumented.sh ─▶ coverage-instrumented RE2 +
           extract_tests.py        ──▶ tests.json
           run_test_coverage.py    ──▶ per_test + union CoverageProfiles
           compute_gaps.py         ──▶ coverage_gaps.json

Phase 2  ── (exp1 only) LLM predicts hard branches from test context

Phase 3  ── generate_inputs.py --experiment exp1 --input-format regex
              └─▶ renders input_synthesis_regex.j2 with gaps + tests
              └─▶ 3 LLM samples, T=0.7, cache-salted per sample
              └─▶ parse_regex_response prepends 2 sha256-seeded flag
                  bytes, clips to [3, 64] bytes, dedupes
              └─▶ seeds/re2/exp1/<model>/seed_<id>.bin

           generate_source_inputs.py --input-format regex
              └─▶ source-only variant; assert_no_tests guards the prompt
              └─▶ seeds/re2/source_only/<model>/seed_<id>.bin

Evaluate  ── seed_replay_main.cc + llvm-profdata + llvm-cov-15
              └─▶ per-cell CoverageProfile JSON
           ab_coverage_diff.py
              └─▶ set-wise line/edge diff, Jaccard, per-file tables
```

Key code entry points:
- `synthesis/scripts/generate_inputs.py` — exp1 driver
- `synthesis/scripts/generate_source_inputs.py` — exp2 driver
- `synthesis/scripts/parse_synthesis.py::parse_regex_response` —
  regex-text parser + flag-byte prepender
- `synthesis/scripts/measure_coverage.py` — per-corpus coverage
- `analysis/scripts/ab_coverage_diff.py` — diff exp1 vs exp2
- `dataset/targets/src/re2/harness/seed_replay_main.cc` —
  standalone driver; reads `argv[1]` as bytes, calls
  `LLVMFuzzerTestOneInput`.

---

## 4. Where to find things

| Thing | Path |
|---|---|
| A/B writeup (full numbers + reproducibility) | `docs/AB_RE2_REPORT.md` |
| Research design (authoritative) | `docs/research_document_v3.md` |
| Execution plan (authoritative) | `docs/claude_code_plan_v3.md` |
| Review slide deck (16 slides, .pptx) | `docs/slides/llm_fuzzing_review.pptx` |
| Slide builder script | `core/build_pptx.py` |
| A/B seeds (current = regex) | `dataset/fixtures/re2_ab/phase3_results/seeds/re2/exp1/llama-3.1-8b-instruct/` and `…/exp2_results/seeds/re2/source_only/llama-3.1-8b-instruct/` |
| A/B archived seeds (bytes format) | `dataset/fixtures/re2_ab/{phase3_results,exp2_results}_bytes_v1/` |
| Per-cell coverage profiles | `dataset/fixtures/re2_ab/ab_coverage/{exp1,exp2}.json` |
| Coverage diff output | `dataset/fixtures/re2_ab/ab_coverage/ab_coverage_diff.{json,md}` |
| RE2 upstream source | `dataset/targets/src/re2/upstream/` |
| RE2 coverage build | `dataset/targets/src/re2/build/coverage/` |
| LLM cache | `.cache/llm/` |
| Secrets (git-ignored) | `secrets/llm_key` |

---

## 5. Non-obvious design decisions (don't "fix" these)

**Pipeline**
- `core/dataset_schema.py::FrozenConfig` has `populate_by_name=True` —
  required for `CoverageProfile` JSON round-trip because the writer emits
  field names (`true_taken`) but the reader originally only accepted
  aliases (`true`).
- `core/provenance.py` verifies the first 2 non-empty lines of
  `test_code` against upstream (not 3).
- `dataset/tests/` and `prediction/tests/` have **no**
  `__init__.py` — both would create a top-level `tests` package and
  collide. Pytest uses rootdir discovery instead.
- Root `conftest.py` pins the repo root on `sys.path`.
- `scripts.dataset_schema.Test` carries `__test__ = False` (otherwise
  pytest collects it).
- `pyproject.toml` ignores `E402` in scripts that bootstrap `sys.path`
  before imports (phase*, experiment2, analysis, sanity).
- `synthesis/scripts/build_source_prompt.py::assert_no_tests`
  fails fast if a test sneaks through the path/content filters. This is
  the primary defense for the ablation's validity — do not silence it.
- `finetuning/scripts/prepare_finetune_data.py::_has_provenance`
  drops Tests missing upstream_repo/commit/file/line. Dropped counts
  are logged; the gate is deliberate.
- `synthesis/scripts/generate_random_inputs.py` routes all
  randomness through a seeded `random.Random` — never use `secrets.*`
  here; it breaks reproducibility.
- `synthesis/scripts/dedup_crashes.py` filters `.stderr` sidecars
  in the glob. Adding new sidecar extensions requires updating the
  filter.

**LLM client**
- `_prompt_hash` includes `max_tokens` + `cache_salt`. Callers that need
  multi-sample diversity MUST pass `cache_salt=f"sample={k},…"` —
  otherwise samples cache-collide (fixed in 3 known sites; future
  multi-sample callers need to know).
- `LLMClient.complete()` streams by default with mid-stream loop-abort
  via `core/loop_detector.py`. Pass `abort_on_loop=False` to disable.
- Cache read uses `strict=False` (tolerates raw newlines in cached
  reasoning strings).
- `PredictionResult(extra='ignore')`; other schemas keep `extra='forbid'`
  intentionally.

**A/B / RE2-specific**
- RE2's `util/test.h` minimal test framework **ignores `--gtest_filter`**
  — all registered tests run regardless. The fixture is the full set of
  built tests.
- RE2 harness is `[2 flag bytes][regex string]`, `3 <= size <= 64`. The
  `--input-format regex` path prepends 2 sha256-seeded flag bytes to
  each LLM-produced regex. **Do not switch the RE2 A/B to `--input-format
  bytes`** — it's the bytes-format that made the earlier A/B misleading.
- Fixture tests are `Regexp.BigRef`, `Regexp.NamedCaptures`,
  `Regexp.CaptureNames` (the 3 that actually exist in our `regexp_test`
  binary). Referencing tests from `re2_test.cc` will fail silently.
- `seed_replay_main.cc` exists because the libFuzzer harness injects
  `main()` and collides with gtest's `main()` — we needed a standalone
  driver that reads a byte file and calls `LLVMFuzzerTestOneInput` once.

---

## 6. Verify current state (fresh instance)

```bash
# one-time
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# static — no network, no LLVM
ruff check .
python -c "from scripts.dataset_schema import Test, CoverageProfile, PredictionRecord; print('schemas load')"
make test                                 # expect 106+ passed

# confirm A/B artifacts on disk
ls dataset/fixtures/re2_ab/ab_coverage/
#   exp1.json  exp2.json  ab_coverage_diff.json  ab_coverage_diff.md

cat dataset/fixtures/re2_ab/ab_coverage/ab_coverage_diff.md | head -25
#   Should show: exp1 edges=1243, exp2 edges=1133, exp1_only=175, exp2_only=65.

# confirm seed_replay binary exists
ls dataset/targets/src/re2/build/coverage/seed_replay

# confirm regex templates exist
ls prediction/prompts/input_synthesis_regex.j2
ls synthesis/prompts/source_only_synthesis_regex.j2

# quick smoke: parser round-trips
python -c "
from synthesis.scripts.parse_synthesis import parse_regex_response
r, s = parse_regex_response('{\"regexes\":[{\"regex\":\"(a*)*\"}]}', target='re2', model='t', temperature=0.7, sample_index=0)
assert s == 'ok' and len(r) == 1; print('parser ok')
"
```

If any of the above fail, something has drifted — check git log first.

---

## 7. Reproduce the 2026-04-13 regex A/B

```bash
# exp1 (gap-targeted)
UTCF_LITELLM_URL=https://api.ai.it.ufl.edu UTCF_LLM_RPM=12 \
.venv/bin/python -m synthesis.scripts.generate_inputs \
    --target re2 --model llama-3.1-8b-instruct --samples 3 --experiment exp1 \
    --input-format regex \
    --dataset-root dataset/fixtures/re2_ab \
    --results-root dataset/fixtures/re2_ab/phase3_results

# exp2 (source-only)
UTCF_LITELLM_URL=https://api.ai.it.ufl.edu UTCF_LLM_RPM=12 \
.venv/bin/python -m synthesis.scripts.generate_source_inputs \
    --target re2 --model llama-3.1-8b-instruct --samples 3 \
    --source-max-files 4 --source-token-budget 14000 --max-tokens 8192 \
    --input-format regex \
    --results-root dataset/fixtures/re2_ab/exp2_results

# per-cell coverage
.venv/bin/python -m synthesis.scripts.measure_coverage \
    --binary dataset/targets/src/re2/build/coverage/seed_replay \
    --seeds-dir dataset/fixtures/re2_ab/phase3_results/seeds/re2/exp1/llama-3.1-8b-instruct \
    --source-roots "$(pwd)/dataset/targets/src/re2/upstream" \
    --profile-out dataset/fixtures/re2_ab/ab_coverage/exp1.json

.venv/bin/python -m synthesis.scripts.measure_coverage \
    --binary dataset/targets/src/re2/build/coverage/seed_replay \
    --seeds-dir dataset/fixtures/re2_ab/exp2_results/seeds/re2/source_only/llama-3.1-8b-instruct \
    --source-roots "$(pwd)/dataset/targets/src/re2/upstream" \
    --profile-out dataset/fixtures/re2_ab/ab_coverage/exp2.json

# differential
.venv/bin/python -m analysis.scripts.ab_coverage_diff \
    --exp1-profile dataset/fixtures/re2_ab/ab_coverage/exp1.json \
    --exp2-profile dataset/fixtures/re2_ab/ab_coverage/exp2.json \
    --out-dir dataset/fixtures/re2_ab/ab_coverage \
    --upstream-root "$(pwd)/dataset/targets/src/re2/upstream" \
    --model llama-3.1-8b-instruct --target re2 \
    --exp1-seed-count 20 --exp2-seed-count 30

# rebuild the pptx
.venv/bin/python core/build_pptx.py
```

Cost: ~$0.007.  Wall-clock: ~2 min (dominated by UF proxy RPM throttle).

---

## 8. What is blocked on external resources

| Item | Blocker |
|---|---|
| 24h libFuzzer campaigns | 29,440 CPU-hours, cluster allocation |
| Frontier-model runs (GPT-4o / Claude) | Authorization + budget (~$10 / A/B) |
| Phase 4 fine-tuning | NVIDIA A100 (8B) or A100×8 (70B QLoRA) |
| Multi-target generalization | Phase 1 SHAs + target-specific prompt templates for 2+ more targets |

`DRY_RUN=1 make all` exercises the full pipeline shape without any of
the above.

---

## 9. Target scope

Only **RE2** has real pinned SHAs in `pinned_versions.yaml`. The 10 other
targets have `<FILL>` placeholders. `pinned_loader.py` raises on any
unresolved `<FILL>` when a target is invoked — intentional, not a bug.

`dataset/scripts/build_instrumented.sh` only implements the RE2
build; other targets `exit 1` with "not yet wired." Adding a new target
means: fill `pinned_versions.yaml`, write a target YAML under
`dataset/targets/`, extend `build_instrumented.sh`, and (per the
2026-04-13 A/B lesson) write a target-specific prompt template + parser
pair.

---

## 10. Known small gaps not yet addressed

- `core/llm_client.py` caching is per-process disk cache only (no
  concurrent-writer protection).
- No CI config yet (`.github/workflows/` absent).
- `nltk` BLEU tokenizer not auto-fetched (required for contamination
  probe).
- `core/coverage_utils.py` `source_roots` filter is string-prefix
  only — symlinks may slip past.
- Non-RE2 targets lack `build_instrumented.sh` branches and framework
  extractors.
- Loop-detector signal (b) can false-positive on dense-but-legitimate
  structured output (e.g. long coverage lists where the schema
  legitimately repeats). Mitigation sketched but not landed:
  require low edit-distance between top-K windows, not just high count.

---

## 11. Changelog of this doc

- **2026-04-13** — generalization experiments landed: P0 random baseline
  (`three_way_summary.md`), Experiment A held-out source subset
  (`heldout_summary.md`), Experiment B prompt ablation
  (`ablation_summary.md`). Experiment C (campaign-time) deferred. New
  assets: `synthesis/scripts/generate_ablation_inputs.py`,
  `synthesis/prompts/ablation_synthesis_regex.j2`,
  `analysis/scripts/ablation_diff.py`, `dataset/fixtures/re2_ab_heldout/`.
  Fixed latent bug: `synthesis/scripts/build_synthesis_prompt.py`
  `PROMPTS_DIR` pointed at `prediction/prompts/` after the rearch;
  synthesis templates live under `synthesis/prompts/`. `SYSTEM_PROMPT_PATH`
  now separate.
- **2026-04-13** — **rearchitecture**: phase dirs renamed to domain names
  (`phase1_dataset` → `dataset`, `phase2_prediction` → `prediction`,
  `phase3_synthesis` → `synthesis`, `phase4_finetuning` → `finetuning`,
  `phase_transfer` → `transfer`), `scripts/` promoted to `core/`,
  `scripts/sanity/` promoted to top-level `sanity/`,
  `experiment2_source_only/` merged into `synthesis/` (scripts kept as
  siblings; `source_only_*` filename prefix disambiguates exp2 assets).
  Four phase `config.py` files consolidated into a single `core/config.py`.
  Single canonical `results/<phase>/<run>/` sink (per-phase `results/`
  placeholders removed). Inner `dataset/dataset/` → `dataset/data/`.
  All 125 tests pass; ruff clean except one pre-existing SIM105 in
  `core/llm_client.py`.
- **2026-04-13** — rewrote as living handoff; folded the stabilization
  narrative into the git log; removed `docs/WORK_SO_FAR.md` and
  `docs/REVIEW_PRESENTATION.md` (both superseded).
- **2026-04-13** — added regex-format A/B rerun section.
- **2026-04-12** — initial STATUS doc after scaffold commit.
