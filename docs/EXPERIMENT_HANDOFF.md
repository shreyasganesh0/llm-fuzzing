# LLM-Seeded Fuzzing: Experiment Handoff

**Date:** 2026-04-16
**Repo:** `/home/shreyasganesh/projects/llm-fuzzing`
**Purpose:** Single source of truth for the campaign experiments run to date. Read this before starting any new work — the current run has rigor bugs (documented in §9) that must be fixed before claiming results.

---

## 1. Research question

Do LLM-generated seed corpora help real fuzzers (libFuzzer, AFL++) reach higher edge coverage faster than empty or random-byte baselines, across multiple targets?

Prior work (Sept 2025–Apr 2026) measured LLM seeds at **generation time** via a `seed_replay` binary + llvm-cov: llama-3.1-8b on RE2 found +110 edges over random on an in-distribution held-out split, but collapsed to parity on a fully held-out split. Frontier-model ablations (Claude Sonnet 4.6, Claude Haiku 4.5) improved **edges-per-seed** by ~1.8× over llama but produced fewer absolute edges because they emitted fewer seeds.

This experiment is the first one to drive **real fuzzer campaigns** with these seeds.

---

## 2. Targets + fuzzers

| Target    | FuzzBench benchmark | Harness                                  | Build variants             |
|-----------|---------------------|------------------------------------------|----------------------------|
| re2       | re2-2014-12-09      | `fuzzer-test-suite/re2-2014-12-09/target.cc` | coverage, sanitizer, fuzzer, afl |
| harfbuzz  | harfbuzz-1.3.2      | `upstream:test/fuzzing/hb-fuzzer.cc`     | coverage, sanitizer, fuzzer, afl |

Commits pinned in `pinned_versions.yaml`:
- RE2: `499ef7eff7455ce9c9fae86111d4a77b6ac335de`
- harfbuzz: `f73a87d9a8c76a181794b74b527ea268048f78e3`

Fuzzers:
- **libFuzzer:** clang-15 `-fsanitize=fuzzer,address`, binaries at `dataset/targets/src/<target>/build/fuzzer/<target>_fuzzer`
- **AFL++:** installed at `~/tools/aflpp/` from source. afl-clang-fast++ builds at `dataset/targets/src/<target>/build/afl/<target>_afl_fuzzer`

---

## 3. Campaign matrix (what was run)

2 targets × 2 engines × 3 seed conditions × **3 trials** × 10-minute duration.

| Cell                                 | Seed source                                                                                  |
|--------------------------------------|----------------------------------------------------------------------------------------------|
| re2 × {libfuzzer, aflpp} × empty     | none                                                                                         |
| re2 × {libfuzzer, aflpp} × llm_seeds | `dataset/fixtures/re2_ab/claude_sonnet_results/seeds/re2/ablation/exp1_full/claude-sonnet-4-6/` (30 seeds, Sonnet 4.6) |
| re2 × {libfuzzer, aflpp} × random    | `synthesis/results/seeds/re2/seeds/re2/random/` (30 random-byte seeds)                       |
| harfbuzz × {libfuzzer, aflpp} × empty | none                                                                                        |
| harfbuzz × {libfuzzer, aflpp} × llm_seeds | `synthesis/results/seeds/harfbuzz/seeds/harfbuzz/exp1/claude-haiku-4-5-20251001/` (19 seeds, Haiku 4.5) |
| harfbuzz × {libfuzzer, aflpp} × random | `synthesis/results/seeds/harfbuzz/seeds/harfbuzz/random/` (30 random-byte seeds)            |

**Total:** 12 cells × 3 trials = 36 campaigns. Duration per trial: 600 s. Snapshot interval: 60 s.

Knobs (from `synthesis/campaign_configs/*.yaml`): `rss_limit_mb=2048`, `timeout_s=25`, `max_len=4096`, `dictionary=null`.

---

## 4. Code artifacts

### Drivers

| Script                                   | Purpose                                                     |
|------------------------------------------|-------------------------------------------------------------|
| `synthesis/scripts/run_fuzzing.py`       | libFuzzer driver. Emits `CampaignResult` JSON.              |
| `synthesis/scripts/run_afl_fuzzing.py`   | AFL++ driver. Parses `plot_data` for time-series edges.     |
| `analysis/scripts/campaign_summary.py`   | Aggregates JSONs → `summary.md` + per-cell coverage CSVs.   |
| `scripts/run_campaigns.sh`               | Campaign orchestrator (Phase 3 full run, 2-way parallel).   |
| `/tmp/rerun_failed.sh`                   | **Deleted.** Was the rerun-with-crash-tolerance script.     |

Both drivers pass crash-tolerance flags (added after initial trials failed):
- libFuzzer: `-ignore_crashes=1 -ignore_ooms=1 -ignore_timeouts=1`, status="ok" if any snapshot produced
- AFL++: `AFL_MAP_SIZE=262144`, `AFL_SKIP_CRASHES=1`, `AFL_IGNORE_PROBLEMS=1`, `AFL_IGNORE_SEED_PROBLEMS=1`

### Schemas

`core/dataset_schema.py::CampaignConfig` gained `fuzzer_engine: Literal["libfuzzer","aflpp"]` and `afl_binary: str` fields.

### Build

`dataset/scripts/build_instrumented.sh` now produces fuzzer + afl binaries (link step after static library build) for both RE2 and harfbuzz.

### Harfbuzz dataset

- `dataset/scripts/extractors/glib.py` — real extractor for `g_test_add_func(...)` patterns.
- `dataset/data/harfbuzz/tests.json` — 3 glib tests (test_blob_empty etc.).
- `dataset/data/harfbuzz/coverage_gaps.json` — **hand-curated** (see §9 bugs).
- `dataset/data/harfbuzz/metadata.json` — stub.
- `dataset/targets/src/harfbuzz/harness/seed_replay_main.cc` — copied from RE2 pattern.

---

## 5. Results (after crash-tolerance + parallelism fix)

From `results/campaigns/summary.md`, regenerated 2026-04-16 after rerun:

| Cell                            | Trials | Mean edges | Median | StdDev | VD A12 vs empty | Label      |
|---------------------------------|--------|------------|--------|--------|-----------------|------------|
| harfbuzz/aflpp/empty            | 3      | 2299       | 2306   | 14     | —               | —          |
| harfbuzz/aflpp/llm_seeds        | 3      | 2287       | 2295   | 17     | 0.222           | negligible |
| harfbuzz/aflpp/random_seeds     | 3      | 2278       | 2275   | 6      | 0.111           | negligible |
| harfbuzz/libfuzzer/empty        | 3      | 2165       | 2171   | 10     | —               | —          |
| harfbuzz/libfuzzer/llm_seeds    | 3      | 2147       | 2147   | 18     | 0.111           | negligible |
| harfbuzz/libfuzzer/random_seeds | 3      | 2174       | 2170   | 6      | 0.556           | negligible |
| re2/aflpp/empty                 | 3      | 2548       | 2537   | 19     | —               | —          |
| re2/aflpp/llm_seeds             | 3      | 2577       | 2599   | 45     | 0.667           | medium     |
| re2/aflpp/random_seeds          | 3      | 2559       | 2563   | 5      | 0.667           | medium     |
| re2/libfuzzer/empty             | 3      | 2641       | 2641   | 2      | —               | —          |
| re2/libfuzzer/llm_seeds         | 3      | 2683       | 2683   | 2      | **1.000**       | **large**  |
| re2/libfuzzer/random_seeds      | 3      | 2628       | 2622   | 20     | 0.333           | negligible |

**Headline (as-run):** RE2/libFuzzer with Sonnet seeds beats empty in all 3 trials (2683 vs 2641, A12=1.000). No significant effect in the other 3 cells.

**BUT:** this headline is not trustworthy yet — see §9.

---

## 6. Artifact layout (what is still on disk)

```
dataset/
  data/harfbuzz/{tests,coverage_gaps,metadata}.json       # harfbuzz dataset (gaps hand-curated)
  fixtures/re2_ab/                                        # all prior A/B and ablation fixtures
    claude_sonnet_results/ claude_haiku_results/
    n6_llama_results/ random_results/ ablation_results/
  targets/src/{re2,harfbuzz}/build/{coverage,sanitizer,fuzzer,afl}/
synthesis/
  campaign_configs/*.yaml                                 # empty/llm_seeds/random_seeds + 4 unused
  results/
    seeds/{re2,harfbuzz}/seeds/...                        # random + LLM synthesis outputs
    campaigns/{re2,harfbuzz}/{empty,llm_seeds,random_seeds}.json           # libFuzzer
    campaigns_afl/{re2,harfbuzz}/{empty,llm_seeds,random_seeds}_aflpp.json # AFL++
results/
  campaigns/summary.md + curve_*.csv                      # regenerated 2026-04-16
docs/
  EXPERIMENT_HANDOFF.md   (this file)
  STATUS.md               (older; still useful for deferred work)
  HANDOFF_PACK.md         (pre-campaign; still useful for environment setup)
  WEEKLY_REVIEW_PROMPT.md (presentation draft; numbers corrected but see §9)
  AB_RE2_REPORT.md        (llama A/B writeup, unchanged)
  research_document_v3.md, plan_v3.md (older, keep for reference)
```

Per-trial work dirs (corpora, AFL output, crash files) were deleted 2026-04-16 to save ~440MB — the JSONs are authoritative for all analysis.

---

## 7. Running it again

### Verify environment
```bash
./dataset/targets/src/re2/build/fuzzer/re2_fuzzer -help=1 2>&1 | head -3
~/tools/aflpp/afl-fuzz -V 2>&1 | head -1
.venv/bin/python -c "from core.dataset_schema import CampaignConfig; print('ok')"
```

### Repro a single cell
```bash
.venv/bin/python -m synthesis.scripts.run_fuzzing \
  --config synthesis/campaign_configs/llm_seeds.yaml \
  --target re2 \
  --binary dataset/targets/src/re2/build/fuzzer/re2_fuzzer \
  --trials 3 --duration-s 600 --snapshot-interval-s 60 \
  --seed-corpus-dir dataset/fixtures/re2_ab/claude_sonnet_results/seeds/re2/ablation/exp1_full/claude-sonnet-4-6/ \
  --work-root synthesis/results/campaigns
```

AFL++ equivalent: swap `run_fuzzing` → `run_afl_fuzzing`, binary → `..._afl_fuzzer`, work-root → `campaigns_afl`.

### Regenerate summary
```bash
.venv/bin/python -m analysis.scripts.campaign_summary \
  --results-dirs synthesis/results/campaigns synthesis/results/campaigns_afl \
  --output-dir results/campaigns
```

### Parallelism warning
Running >2 fuzzer processes concurrently with `rss_limit_mb=2048` OOMs on a 16GB box. Every instance of "1.1s fake failure" during dev was OOM, not harness crash. Keep parallelism ≤2 or cap rss_limit lower.

---

## 8. Key non-obvious decisions

- **LLM seed sources:** Sonnet 4.6 was chosen for RE2 (best frontier ablation cell). Haiku 4.5 was chosen for harfbuzz (only frontier run we had for that target). This asymmetry is a **bug** — see §9.
- **Random seeds:** syntactically-unvalidated random bytes from `generate_random_inputs.py`. For RE2 they're not regex; for harfbuzz they're not valid font files.
- **3 trials, 10 min:** deliberately undersized for a concept-proof. FuzzBench standard is 20 trials × 23h.
- **RSS 2GB, timeout 25s:** matches FuzzBench `libfuzzer_extra_flags` in `pinned_versions.yaml`.
- **Dictionary = null everywhere.** See §9.

---

## 9. Rigor bugs + missed combinations (READ BEFORE BUILDING ON THESE RESULTS)

The current run proves the plumbing works but should not be cited as evidence for or against the research question. The following must be fixed before the next run:

### 9.1 Incomplete model × target matrix (confound)

Only two (model, target) pairs were run:
- Sonnet × RE2
- Haiku × harfbuzz

Missing 10 of the 12 cells required for a clean 3×2 factorial:
- llama-3.1-8b × RE2
- llama-3.1-8b × harfbuzz
- Sonnet × harfbuzz
- Haiku × RE2

**Consequence:** we cannot separate model effects from target effects. The "Sonnet wins on RE2/libFuzzer" finding might be because Sonnet is good, because RE2 is easy for frontier models, or because the Haiku+harfbuzz pairing is particularly bad.

### 9.2 Never ran llama seeds through real fuzzers

All prior llama A/B and ablation work measured seed-time coverage via the `seed_replay` binary, never through libFuzzer/AFL++ campaigns. The model we have the most data on has zero real-fuzzer data.

### 9.3 No FuzzBench dictionary used

Every `synthesis/campaign_configs/*.yaml` has `dictionary: null`. FuzzBench canonically ships dictionaries for both RE2 and harfbuzz (check `fuzzer-test-suite/<benchmark>/build.sh` for `cp .../dict .`). Running without them inflates the benefit of any seed corpus because dictionaries are a cheap coverage booster the baseline is missing.

**Fix:** pull the dictionary file per target, wire `--dictionary` into the campaign configs (per-target override in `pinned_versions.yaml` → config).

### 9.4 No FuzzBench seed corpus baseline

FuzzBench benchmarks ship canonical seed corpora (harfbuzz has `test/shaping/fonts/sha1sum`, a few hundred real fonts). We compared LLM seeds only against "empty" and "random bytes" — not against what a real FuzzBench run would start with. The canonical baseline is missing.

**Fix:** add `fuzzbench_seeds` cell (already has a stub YAML at `synthesis/campaign_configs/fuzzbench_seeds.yaml`), point `--seed-corpus-dir` at the canonical path.

### 9.5 Harfbuzz coverage_gaps.json is hand-curated, not pipeline output

`dataset/data/harfbuzz/coverage_gaps.json` has `reachability_score` (float) and natural-language `condition_description` fields. The real `compute_gaps.py` pipeline emits `true_taken`/`false_taken` branch booleans. This file was written by hand to unblock synthesis, not produced by running the coverage pipeline against the harfbuzz test suite.

**Consequence:** the LLM prompt for harfbuzz synthesis was fed fabricated gap data. Any "harfbuzz LLM seeds don't help" conclusion is entangled with this.

**Fix:** actually run `compute_gaps.py` (or equivalent) against the harfbuzz coverage binary + glib test suite and overwrite the file. If that script doesn't exist for harfbuzz's build system, write the minimal version: run tests under `-fprofile-instr-generate`, merge profraw with `llvm-profdata-15 merge`, export with `llvm-cov-15 export`, extract uncovered branches.

### 9.6 Prompt content for harfbuzz

`synthesis/prompts/input_synthesis.j2` does include `harness_code`, `few_shot_examples`, `coverage_gaps`, and `source_context` blocks generically. The **template structure** is rigorous. The **data piped in** is the problem:
- `tests.json`: 3 real glib tests — OK
- `harness_code`: real FuzzBench harness — OK
- `coverage_gaps`: fake (§9.5) — NOT OK
- `source_context`: verify what's being included

### 9.7 Small trial count + short duration

3 trials × 10 min is below the threshold where bootstrap 95% CIs are meaningful. FuzzBench uses 20 × 23h. Current run is concept-proof only. Even A12=1.000 on 3 trials is a weak claim (only 3 matched comparisons).

**Fix:** bump to N≥10 trials, duration ≥1 hour (ideally 23h for direct FuzzBench comparison).

### 9.8 Seed count mismatch across cells

Sonnet RE2 = 30 seeds; Haiku harfbuzz = 19 seeds; random = 30 for both targets. The "llm_seeds" cell isn't comparable across targets, and the harfbuzz LLM cell has fewer seeds than its random baseline — partially reshuffling the concept.

**Fix:** truncate or pad to a fixed M (e.g., M=30) per cell, identically across models.

---

## 10. Recommended next run (for the fresh chat instance)

Before running anything, get user decisions on:

1. **Trials N** per cell (recommend ≥10) and **duration** per trial (recommend ≥3600 s).
2. **Archive** current results (`synthesis/results/campaigns*/` JSONs) to `synthesis/results/_archived_2026-04-16/` so the new run starts clean.
3. **Fixed seed count M** (recommend 30) enforced across every (model, target) pair.
4. **Scope:** keep 3 models (llama-3.1-8b, Sonnet 4.6, Haiku 4.5) or add more? Budget is limited; llama is free via UF LiteLLM proxy, Claude is paid (~$0.01/seed Haiku, ~$0.05/seed Sonnet).

Then, in order:

1. **Fix §9.5** — regenerate `dataset/data/harfbuzz/coverage_gaps.json` from the real coverage pipeline. Verify the schema matches `core.dataset_schema.CoverageGapsReport`.
2. **Fix §9.3** — fetch the FuzzBench dictionary per target, wire it into the campaign configs (add `dictionary: <path>` or per-target override). If the benchmark has no dict, document that.
3. **Fix §9.4** — materialize `synthesis/campaign_configs/fuzzbench_seeds.yaml` with the canonical seed corpus path and add `fuzzbench_seeds` as a sixth baseline column.
4. **Synthesize the missing corpora:**
   - llama × {re2, harfbuzz} — use UF proxy (`UTCF_LITELLM_URL=https://api.ai.it.ufl.edu`, free)
   - Sonnet × harfbuzz — paid, ~$1–2
   - Haiku × re2 — paid, ~$0.30
   All with the corrected harfbuzz gaps from step 1.
5. **Run the full matrix:** 2 targets × 2 fuzzers × 6 seed conditions (empty, random, fuzzbench_seeds, llama, sonnet, haiku) × N trials. At N=10, duration=1h, parallelism=2, that's ~60 hours wall. At N=5 / 30-min, ~15 hours.
6. **Analyze:** reuse `analysis/scripts/campaign_summary.py`. Add per-model and per-target breakdown tables.

### Scripts likely to need a touch

- `synthesis/scripts/run_fuzzing.py` — dictionary wiring if not already per-target.
- `synthesis/scripts/generate_inputs.py` — verify the template receives correct gap data after the §9.5 fix.
- `scripts/run_campaigns.sh` — rewrite the loop to cover the 6-config × 2-target × 2-fuzzer matrix.

---

## 11. 4×2 ablation run — 2026-04-16 (RE2 only, coverage-replay only)

### Design (locked, see `/home/shreyasganesh/.claude/plans/concurrent-wandering-cocke.md`)

- **Target:** RE2 only. **Models:** sonnet-4-6, haiku-4-5, llama-3.1-8b.
- **Variants (4):** `v1_src` (harness+source), `v2_src_tests` (+5 unit tests), `v3_all` (+30 gap branches, **targeted framing**), `v4_src_gaps` (source+gaps, no tests, targeted framing).
- **No real fuzzers.** Both metrics are derived from LLVM coverage replay on the `seed_replay` coverage build.
- **Frozen target set:** N=50 branches, deterministically sampled (seed=42) from `coverage_gaps.json`, smoke-verified reachable. First 30 by (file,line) sort are "shown" in V3/V4 prompts; remaining 20 are held back for generalization.
- **Pinned constants across all cells:** 5 unit tests (V2/V3), 30 gap branches in prompt (V3/V4), 8 k-token source slice, 30 seeds/cell (samples=3 × num_inputs=10). Each seed is a regex ≤ 60 chars.

### Artifacts

| Artifact | Path |
|---|---|
| Frozen targets + shown/held split | `dataset/fixtures/re2_ab/re2/m2_target_branches.json` |
| Union baseline (upstream tests) | `dataset/fixtures/re2_ab/re2/upstream_union_profile.json` |
| Smoke-reachability audit | `dataset/fixtures/re2_ab/re2/m2_smoke_log.json` |
| Per-cell seed corpora | `synthesis/results/ablation_v3/seeds/re2/ablation/<variant>/<model>/*.bin` |
| Random anchor seeds | `synthesis/results/ablation_v3/seeds/re2/random/*.bin` |
| M1 per-cell summaries | `results/ablation_v3/m1/<variant>/<model>/summary.json` |
| M2 per-cell summaries | `results/ablation_v3/m2/<variant>/<model>/summary.json` |
| Aggregate markdown | `results/ablation_v3/summary.md` |

### Scripts (new)

- `analysis/scripts/freeze_target_branches.py` — pre-experiment: union baseline, smoke corpus, deterministic 50-target sample.
- `analysis/scripts/measure_gap_coverage.py` — M2: per-seed isolated replay, union-diff "previously uncovered side" hit matrix, bootstrap CIs.
- `scripts/run_ablation_experiment.py` — orchestrator with phases `prep | random | synthesis | m1 | m2 | all` and `--skip-existing`. Routes Claude models via `UTCF_ANTHROPIC_KEY_PATH=secrets/claude_key` and llama via `UTCF_LITELLM_URL=https://api.ai.it.ufl.edu`.
- `analysis/scripts/ablation_summary.py` — joins M1+M2 on disk, writes `results/ablation_v3/summary.md`.

### Scripts (modified)

- `synthesis/prompts/ablation_synthesis_regex.j2` — variant-aligned task framing: when `include_gaps` is true, prompt asks the LLM to target the listed file:line gaps (V3/V4); else keep the "maximize total coverage" framing (V1/V2).
- `synthesis/scripts/generate_ablation_inputs.py` — `cache_salt` now includes `model=` so multi-model synthesis doesn't collide.
- `core/config.py` — `M2_TARGETS_PATH`, `UPSTREAM_UNION_PROFILE_PATH`, `M2_SMOKE_LOG_PATH`, `M2_TARGET_COUNT=50`, `M2_SHOWN_COUNT=30`, `M2_RNG_SEED=42`, `BOOTSTRAP_ITERS=10_000`, `SOURCE_TOKEN_BUDGET_ALL_MODELS=8_000`.

### Replay note (DWARF paths)

The coverage binary `dataset/targets/src/re2/build/coverage/seed_replay` was built with the pre-rearch `phase1_dataset/...` source prefix baked into DWARF. The M1 source-roots filter in `scripts/run_ablation_experiment.py` uses that prefix; `analysis/scripts/freeze_target_branches.py::_normalize_path` strips up to and including `upstream/` so downstream joins with `coverage_gaps.json` paths (`re2/bitstate.cc`) work across stale/current prefixes.

### Headline results (2026-04-16)

See `results/ablation_v3/summary.md` for full tables.

- **M1 (general edges, corpus union over 30 seeds):** LLM cells cover 998–1245 of 3380 edges; all 12 LLM cells beat the 980-edge random anchor (sanity check (d) passes).
- **M2 (target-branch hits, all-50 slice, union over 30 seeds):** V3 takes 0.82–0.90 of the 50 targets (sonnet 45/50, haiku 44/50, llama 41/50); V1 takes 0.80–0.90. Both dominate the random anchor at 0.70.
- **Per-seed hit rate (M2 mean_frac_per_seed) is lower for targeted variants than for V1 / random.** V3/V4 seeds hit fewer targets each because the targeted framing steers them toward specific branches rather than broadly-covering regexes. This is a real characterization, not a bug: the right aggregate for "did targeting work" is the corpus-union `union_frac_targets_hit`, which is comparable or higher for V3/V4 vs V1.
- **Sanity checks (a)–(d):** (a) V3 ≥ V1 on per-seed mean only for llama8b (1/3; as above, this is per-seed vs union framing mismatch). (b) V3/V4 don't beat random on per-seed mean (same framing caveat). (c) shown ≥ held_back union_frac in 4/6 V3/V4 cells — partial evidence that in-prompt targeting concentrates hits. (d) All 12 LLM cells beat random on M1.

### How to rerun end-to-end

```bash
# 1. Freeze targets (once; writes 3 JSONs under dataset/fixtures/re2_ab/re2/).
.venv/bin/python -m analysis.scripts.freeze_target_branches

# 2. Run everything (synthesis ~6 min, M1+M2 ~1 min total).
.venv/bin/python -m scripts.run_ablation_experiment --phase all --skip-existing

# 3. Aggregate.
.venv/bin/python -m analysis.scripts.ablation_summary
```

The Anthropic key must be at `secrets/claude_key`. llama traffic needs no key (UF proxy).

### Known limitations / deferred work

- `measure_coverage.py` uses `llvm-profdata` / `llvm-cov` on PATH; the system has only `-15` suffixed variants. The first M1 run with the wrong source-roots prefix silently produced 0-edge outputs; after the prefix fix it works. If rebuilt on a box without `llvm-profdata-15`, pass `LLVM_PROFDATA=/path/to/llvm-profdata-15` env.
- `mean_frac_per_seed` is noisy for diverse-target corpora. Future work: add a per-target win-rate table (how many seeds hit target X), currently only surfaced via `gap_hits.jsonl` audit rows.
- M2 union_frac looks saturated (0.70–0.90 everywhere). The 50-target set may be too easy; a harder target set would discriminate variants better. Retain `m2_smoke_log.json` so the sampling can be re-biased deterministically.

---

## 12. Git state at handoff

Branch: `master`. Uncommitted changes (as of 2026-04-16):
- Modified: `core/build_pptx.py`, `core/dataset_schema.py`, `dataset/scripts/build_instrumented.sh`, `dataset/scripts/fetch_target.sh`, `dataset/targets/harfbuzz.yaml`, `docs/slides/llm_fuzzing_review.pptx`, `pinned_versions.yaml`, `synthesis/scripts/parse_synthesis.py`, `synthesis/scripts/run_fuzzing.py`
- New: `analysis/scripts/campaign_summary.py`, `docs/HANDOFF_PACK.md`, `docs/EXPERIMENT_HANDOFF.md` (this file), `docs/slides/llm_fuzzing_refined.pptx`, `docs/slides/utcf_review_deck.pptx`, `scripts/run_campaigns.sh`, `synthesis/scripts/run_afl_fuzzing.py`

Nothing has been pushed. Recommend committing the working state before the next instance starts so the baseline is reproducible.
