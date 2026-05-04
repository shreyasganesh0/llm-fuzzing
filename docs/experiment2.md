# Experiment 2 — Multi-model context ablation and real-fuzzer campaigns

**Scope.** RE2 + harfbuzz, 7 models (Anthropic + UF LiteLLM proxy), 5-variant
prompt-context grid, 2026-04-16 to 2026-04-19.
**Question.** Lift `experiment1`'s single-fixture llama A/B to a real ablation:
how does prompt-context choice (`v0_none` → `v4_src_gaps`) interact with model
choice across two targets, measured by both seed-time coverage (M1, M2) and
fuzzer-campaign coverage?
**Headline.** RE2 best M2 = **0.867** (`nemotron-120b` at `v1_src`); harfbuzz
best M2 = **0.640** (`claude-sonnet-4-6` at `v0_none`); `codestral-22b` is the
most reliable free model. RE2/libFuzzer with Sonnet seeds beats empty in 3/3
trials (A12=1.000) — though that real-fuzzer cell has documented rigor bugs.

This document covers three sub-versions (`experiment2_0` … `experiment2_2`).
For framework setup commands (build, freeze, env), see `EXPERIMENT_WALKTHROUGH.md §6`.

---

<a id="five-variant-grid"></a>
## 1. The 5-variant prompt grid

Same design used across all sub-versions (registered in `core/variants.py::STANDARD_VARIANTS`).
Per-target detailed M1/M2 themes for each variant are in [§4.1 (RE2)](#re2-themes)
and [§4.2 (harfbuzz)](#hb-themes).

| Variant | Source code | Upstream tests | Coverage gaps | Reads as |
|---|:-:|:-:|:-:|---|
| `v0_none` | ❌ | ❌ | ❌ | "Write seeds for this target." Pure prior. [Themes »](#re2-themes) |
| `v1_src` | ✅ | ❌ | ❌ | Source code only. [Themes »](#re2-themes) |
| `v2_src_tests` | ✅ | ✅ | ❌ | Source + upstream unit tests. [Themes »](#re2-themes) |
| `v3_all` | ✅ | ✅ | ✅ | Source + tests + uncovered-branch list. [Themes »](#re2-themes) |
| `v4_src_gaps` | ✅ | ❌ | ✅ | Source + gaps, no tests. [Themes »](#re2-themes) |

The 6th variant `v5_topk_gaps` (gaps-only retrieval) was added later for
`experiment3` but is registered in the same module.

---

<a id="hard-branch-filter"></a>
## 2. The hard-branch M2 filter

`experiment2_0` used a too-weak M2 filter (`struct_hits ≥ 1`, no constraint on
random) which random seeds satisfied for 41/50 of the chosen "hard" branches —
**invalidating the metric**. `experiment2_1` re-froze with the proper filter:

> A branch qualifies as **hard** iff `struct_hits ≥ 1 AND rand_hits == 0`.

This guarantees the random baseline scores **exactly 0% on M2 by construction**,
so any non-zero M2 is LLM-specific signal. The filter is enforced in
`analysis/scripts/freeze_target_branches.py`.

Per-target frozen sets:

- **RE2 v2.** 15 hard branches (10 shown in prompt for v3/v4, 5 held back). The low
  count is itself a finding — random regex strings accidentally cover most RE2
  parsing paths.
- **Harfbuzz.** 50 hard branches (30 shown, 20 held back).

Frozen at:
`dataset/fixtures/re2_ab_v2/re2/m2_target_branches.json`,
`dataset/fixtures/harfbuzz_ab/harfbuzz/m2_target_branches.json`.
**Do not regenerate** — re-freezing invalidates all prior runs.

---

<a id="experiment2_0"></a>
## 3. experiment2_0 — 4×2 RE2 ablation_v3 (2026-04-16, INVALIDATED)

First multi-model ablation. RE2 only. 4 variants (`v1_src`, `v2_src_tests`,
`v3_all`, `v4_src_gaps`), 3 models (Sonnet 4.6, Haiku 4.5, llama-3.1-8b).
30 seeds/cell from `samples=3 × num_inputs=10`. M2 frozen at 50 hard branches
deterministically sampled (seed=42), 30 shown / 20 held back.

Headline numbers ran cleanly: V3 took 0.82–0.90 of the 50 targets across all
three models; V1 took 0.80–0.90; random anchor 0.70.

**Then the M2 filter was discovered to be too weak.** The original filter was
`struct_hits ≥ 1` (no constraint on `rand_hits`). Random seeds turned out to hit
41/50 (82%) of those "hard" targets, **invalidating the M2 metric** for this
run. The filter was strengthened to `struct_hits ≥ 1 AND rand_hits == 0` for
`experiment2_1` and made the unbreakable load-bearing invariant.

| Cell | M1 (corpus union, 30 seeds) | Notes |
|---|---:|---|
| LLM cells | 998–1245 of 3380 edges | All 12 cells beat random (sanity check passes) |
| Random anchor | 980 | (floor) |

Raw M1 numbers per cell (read directly from
`results/ablation_v3/m1/<variant>/<model>/summary.json`):

```
v0_none / claude-sonnet-4-6        | 1462 / 3380
v0_none / claude-haiku-4-5         | 1369 / 3380
v0_none / llama-3.1-8b             | 1322 / 3380
v1_src  / claude-sonnet-4-6        | 1381 / 3380
v1_src  / claude-haiku-4-5         | 1289 / 3380
v1_src  / llama-3.1-8b             | 1291 / 3380
```

**Status.** Numbers retained for reference; **do not cite as M2 evidence**.

**Artefacts.**
- Fixture: `dataset/fixtures/_ablation_v3_dataset/`
- Per-model outputs: `dataset/fixtures/re2_ab/{claude_sonnet_results, claude_haiku_results, n6_llama_results}/`
  (each carries `citation_audit/`, `ok_rate_audit/`, `coverage/`, `seeds/`, `synthesis/`)
- Aggregate: `results/ablation_v3/{m1,m2}/<variant>/<model>/summary.json`,
  `results/ablation_v3/summary.md`
- Archived orchestrator: `scripts/archive/run_ablation_experiment_v3.py.bak`

The lesson — never relax `rand_hits == 0` — is enforced in the freeze
script (`analysis/scripts/freeze_target_branches.py`) and the M2 metric
(`analysis/scripts/measure_gap_coverage.py`).

---

<a id="experiment2_1"></a>
## 4. experiment2_1 — 5-variant × 7-model ablation (2026-04-19, current headline)

Both targets. All 7 models. All 5 variants. 150 seeds/cell with deterministic
subsampling at `random.Random(42)`. Proper hard-branch filter
(`struct_hits ≥ 1 AND rand_hits == 0`). RE2 re-frozen as **v2** (15 hard
branches, 10 shown / 5 held back). Harfbuzz frozen at 50 hard branches
(30 shown / 20 held back).

**Models.**

| Provider | Models | Key path |
|---|---|---|
| Anthropic | `claude-sonnet-4-6`, `claude-haiku-4-5-20251001` | `secrets/claude_key` |
| UF LiteLLM proxy | `llama-3.1-8b-instruct`, `llama-3.1-70b-instruct`, `llama-3.3-70b-instruct`, `codestral-22b`, `gpt-oss-20b`, `nemotron-3-super-120b-a12b` | none — `UTCF_LITELLM_URL` |

<a id="re2-themes"></a>
### 4.1 RE2 v2 — text format

Random baseline: **1160 edges, M2 = 0.000**.

#### M1 — total edges covered (150-seed corpus union)

| Cell | Edges | % of 3380 | Δ vs random |
|---|---:|---:|---:|
| Random | 1160 | 34.3% | — |
| `v0_none` (best: nemotron-120b) | 1442 | 42.7% | +282 |
| `v1_src` (best: llama-3.1-70b) | **1445** | **42.8%** | **+285 (+24.6%)** |
| `v2_src_tests` (best: codestral-22b) | **1495** | **44.2%** | **+335 (+28.9%)** |
| `v3_all` (best: llama-3.1-70b) | 1417 | 41.9% | +257 |
| `v4_src_gaps` (best: llama-3.1-70b) | 1439 | 42.6% | +279 |

#### M2 — hard-branch hit rate (15-target set)

| Variant | Best M2 (model) | All M2 ≥ 0.5 |
|---|---:|---|
| `v0_none` | 0.733 (sonnet) | sonnet, haiku, nemotron |
| `v1_src` | **0.867 (nemotron-120b)** | nemotron, sonnet, haiku, llama-3.1-8b, llama-3.1-70b |
| `v2_src_tests` | 0.733 (codestral / nemotron) | codestral, nemotron, sonnet, haiku, llama-3.1-8b |
| `v3_all` | 0.800 (haiku / codestral) | haiku, codestral, sonnet, llama-3.1-70b, llama-3.1-8b |
| `v4_src_gaps` | 0.800 (codestral) | codestral, sonnet, llama-3.1-70b, haiku, llama-3.1-8b |

**Themes.**

- M2 roughly doubles from `v0_none` (0.333–0.733) to `v3_all`/`v4_src_gaps`
  (0.667–0.867) across most models. Hard branches *are* reachable with the right
  prompt context.
- **`codestral-22b` is the most reliable free model** — 150 seeds across all 5
  variants, M1 1321–1495, M2 up to 0.800.
- **`nemotron-120b` is the surprise** — best single-cell M2 at `v1_src` (0.867),
  well above Sonnet at the same variant. Collapses without enough seeds at
  `v3_all`+ (only 14 / 2 seeds).
- **`llama-3.3-70b` underperforms** (8–35 seeds per cell) due to JSON formatting
  failures. Results not statistically meaningful.

<a id="hb-themes"></a>
### 4.2 Harfbuzz — binary format

Random baseline: **554 edges, M2 = 0.000**.

#### M1 — total edges covered (150-seed corpus union)

| Variant | Best M1 (model) | Δ vs random |
|---|---:|---:|
| `v0_none` | **998 (sonnet)** | **+444 (+80%)** |
| `v1_src` | 816 (sonnet) | +262 |
| `v2_src_tests` | 742 (codestral) | +188 |
| `v3_all` | 731 (codestral) | +177 |
| `v4_src_gaps` | 745 (codestral) | +191 |

#### M2 — hard-branch hit rate (50-target set)

| Variant | Best M2 (model) |
|---|---:|
| `v0_none` | **0.640 (sonnet)** |
| `v1_src` | 0.460 (codestral) |
| `v2_src_tests` | 0.380 (codestral) |
| `v3_all` | 0.260 (codestral) |
| `v4_src_gaps` | 0.480 (codestral) |

**Themes.**

- **Sonnet dominates `v0_none`** (998 edges, 1.8× random; M2=0.640) but **drops
  as context is added**. Adding source confuses it on the binary format — the
  reverse of the RE2 pattern.
- **Codestral-22b is the best free model on harfbuzz** — consistent ~700–745
  edges, M2 up to 0.480.
- **Binary format is hard for 70b/nemotron** — ~10% parse rate, low seed counts
  (10–107), high variance.
- **Even llama-3.1-8b at v0** (564 edges, M2=0.040) **beats random** (554, 0.000).
- **Caveat.** Claude `v2_src_tests`/`v3_all`/`v4_src_gaps` cells were not run on
  harfbuzz due to Anthropic credit constraints. Only `v0_none` and `v1_src` have
  Anthropic data on this target.

### 4.3 Cross-target summary — best M2 per variant

| Variant | Best HB M2 | Model | Best RE2 M2 | Model |
|---|---:|---|---:|---|
| `v0_none` | **0.640** | sonnet | **0.733** | sonnet |
| `v1_src` | **0.460** | codestral | **0.867** | nemotron-120b |
| `v2_src_tests` | **0.380** | codestral | **0.733** | codestral / nemotron |
| `v3_all` | **0.260** | codestral | **0.800** | haiku / codestral |
| `v4_src_gaps` | **0.480** | codestral | **0.800** | codestral |

### 4.4 Artefacts

- Seeds: `synthesis/results/ablation_<target>/seeds/<target>/ablation/<variant>/<model>/seed_*.bin`
  (per cell, exactly 150 after subsampling).
- Random baseline seeds: `synthesis/results/ablation_<target>/seeds/<target>/random/seed_*.bin`
- Per-cell M1: `results/ablation_<target>/m1/<variant>/<model>/summary.json`
- Per-cell M2: `results/ablation_<target>/m2/<variant>/<model>/summary.json`
- Random anchor: `results/ablation_<target>/m{1,2}/random/summary.json`
  (M2 is always 0.000 by construction).
- Frozen fixtures: `dataset/fixtures/{re2_ab_v2/re2,harfbuzz_ab/harfbuzz}/`
- Prepped dataset: `dataset/fixtures/_ablation_{re2_v2,hb}_dataset/`

<a id="how-to-reproduce-experiment2_1"></a>
### 4.5 How to reproduce

```bash
# Once: build coverage-instrumented binaries.
./dataset/scripts/fetch_target.sh       dataset/targets/re2.yaml
./dataset/scripts/build_instrumented.sh dataset/targets/re2.yaml
./dataset/scripts/fetch_target.sh       dataset/targets/harfbuzz.yaml
./dataset/scripts/build_instrumented.sh dataset/targets/harfbuzz.yaml

# Once: freeze the M2 hard-branch set per target. DO NOT re-run later.
.venv/bin/python -m analysis.scripts.freeze_target_branches --target re2_v2
.venv/bin/python -m analysis.scripts.freeze_target_branches --target harfbuzz

# Run the full ablation (always under nohup — takes hours).
nohup .venv/bin/python scripts/run_ablation_re2.py \
    --phase all --skip-existing >> /tmp/re2.log 2>&1 &

nohup .venv/bin/python scripts/run_ablation_harfbuzz.py \
    --phase all --skip-existing >> /tmp/hb.log 2>&1 &

# Per-cell results land at:
#   results/ablation_{re2_v2,harfbuzz}/{m1,m2}/<variant>/<model>/summary.json
# Bundled aggregator (analysis.scripts.ablation_summary) is currently pinned
# to the invalidated experiment2_0 path (results/ablation_v3/) and takes no
# flags — rolling up the experiment2_1 headline is a known TODO.
```

Available CLI flags on both `run_ablation_*.py` wrappers (forwarded to
`scripts/_ablation_base.py::AblationRunner`):

| Flag | Purpose |
|---|---|
| `--phase {prep,synthesis,random,m1,m2,all}` | Default `all`. Sub-phase invocation. |
| `--skip-existing` | Skip cells with full 150-seed corpora or existing `summary.json`. |
| `--only-models MODEL [MODEL …]` | Restrict synthesis/metrics to a subset of the wrapper's `MODELS` list. |
| `--strategy NAME[,NAME…]` | Comma-separated prompt strategies (default `default`; see `experiment3`). |
| `--variants NAME[,NAME…]` | Comma-separated variant subset (default all 5). |
| `--num-seeds N` | Override the 150-seed target. **Smoke use only**, emits a stderr WARNING. |
| `--attempt-offset N` | Add to every attempt's `run_id` to avoid cached-failure replay on restart. **Bump by ≥5000 on restart.** |
| `--list-strategies` | Print registered prompt strategies + calls/seed + tool-use flag, exit 0. |
| `--dry-run` | Print the strategy × variant × model matrix and exit 0 without running. |

Useful sub-phase invocations:

```bash
# Just rebuild seed corpora; skip already-150 cells.
.venv/bin/python scripts/run_ablation_harfbuzz.py --phase synthesis --skip-existing

# Restart synthesis without replaying cached failures.
.venv/bin/python scripts/run_ablation_harfbuzz.py \
    --phase synthesis --skip-existing --attempt-offset 15000

# Just (re)compute M1 / M2 for cells that already have seeds.
.venv/bin/python scripts/run_ablation_re2.py --phase m1 --skip-existing
.venv/bin/python scripts/run_ablation_re2.py --phase m2 --skip-existing

# Smoke run: 5 seeds, one variant, one model, one strategy.
.venv/bin/python scripts/run_ablation_harfbuzz.py --phase synthesis \
    --variants v1_src --only-models gpt-oss-20b --strategy tool_use \
    --num-seeds 5 --attempt-offset 70000

# Dry-run the full strategy x variant x model matrix.
.venv/bin/python scripts/run_ablation_re2.py --phase all --skip-existing --dry-run
```

Per-model parallelism, attempt caps, and timeouts come from
`core/config.py::ModelDefaults` — see `EXPERIMENT_WALKTHROUGH.md §3.3`.

---

<a id="experiment2_2"></a>
## 5. experiment2_2 — Real-fuzzer campaigns (2026-04-16)

First experiment that drove the seeds through actual fuzzers. **2 targets ×
2 engines × 3 seed conditions × 3 trials × 10 minutes = 36 campaigns.**

A12 = Vargha-Delaney effect-size statistic. Effect-size cells use the
standard thresholds (small ≥ 0.56, medium ≥ 0.64, large ≥ 0.71). Every
"Effect" cell below is provisional until [§5.1 rigor bugs »](#rigor-bugs)
are addressed; do not cite these numbers as evidence.

| Cell | Mean edges | Median | StdDev | A12 vs empty | Effect |
|---|---:|---:|---:|---:|---|
| harfbuzz / aflpp / empty | 2299 | 2306 | 14 | — | — |
| harfbuzz / aflpp / llm_seeds (Haiku) | 2287 | 2295 | 17 | 0.222 | [negligible](#rigor-bugs) |
| harfbuzz / aflpp / random | 2278 | 2275 | 6 | 0.111 | [negligible](#rigor-bugs) |
| harfbuzz / libfuzzer / empty | 2165 | 2171 | 10 | — | — |
| harfbuzz / libfuzzer / llm_seeds (Haiku) | 2147 | 2147 | 18 | 0.111 | [negligible](#rigor-bugs) |
| harfbuzz / libfuzzer / random | 2174 | 2170 | 6 | 0.556 | [negligible](#rigor-bugs) |
| re2 / aflpp / empty | 2548 | 2537 | 19 | — | — |
| re2 / aflpp / llm_seeds (Sonnet) | 2577 | 2599 | 45 | 0.667 | [medium](#rigor-bugs) |
| re2 / aflpp / random | 2559 | 2563 | 5 | 0.667 | [medium](#rigor-bugs) |
| re2 / libfuzzer / empty | 2641 | 2641 | 2 | — | — |
| **re2 / libfuzzer / llm_seeds (Sonnet)** | **2683** | **2683** | **2** | **1.000** | [**large**](#rigor-bugs) |
| re2 / libfuzzer / random | 2628 | 2622 | 20 | 0.333 | [negligible](#rigor-bugs) |

**Headline as run.** RE2/libFuzzer with Sonnet seeds beats empty in all 3 trials
(2683 vs 2641, A12=1.000). No significant effect elsewhere.
[See §5.1 for why this is concept-proof, not evidence »](#rigor-bugs)

<a id="rigor-bugs"></a>
### 5.1 Documented rigor bugs (do not cite as evidence)

The plumbing works but the run should not be cited as evidence for or against
the research question:

1. **Incomplete model × target matrix.** Only `Sonnet × RE2` and `Haiku × harfbuzz`
   ran. Missing 10 of the 12 cells required for a 3×2 factorial (llama × both,
   Sonnet × harfbuzz, Haiku × RE2). Model and target effects are confounded.
2. **Never ran llama seeds through real fuzzers.** All prior llama A/B and ablation
   work measured seed-time coverage via `seed_replay`; the model with the most
   seed-time data has zero real-fuzzer data.
3. **No FuzzBench dictionary.** `synthesis/campaign_configs/*.yaml` has
   `dictionary: null` everywhere. FuzzBench canonically ships dictionaries for
   both targets; running without them inflates the benefit of any seed corpus.
4. **No FuzzBench seed corpus baseline.** Compared LLM seeds only against `empty`
   and `random_bytes`, not against the canonical FuzzBench seed corpus
   (e.g. harfbuzz's `test/shaping/fonts/sha1sum`).
5. **`dataset/data/harfbuzz/coverage_gaps.json` was hand-curated.** Has
   `reachability_score` (float) and natural-language `condition_description`
   fields rather than the pipeline's `true_taken`/`false_taken` booleans. The
   harfbuzz LLM prompts were fed fabricated gap data.
6. **Small trial count + short duration.** 3 trials × 10 min is below the threshold
   where bootstrap 95% CIs are meaningful. FuzzBench standard is 20 trials × 23h.
7. **Seed count mismatch.** Sonnet RE2 = 30 seeds; Haiku harfbuzz = 19; random = 30.

### 5.2 Drivers

| Script | Purpose |
|---|---|
| `synthesis/scripts/run_fuzzing.py` | libFuzzer driver. Emits `CampaignResult` JSON. |
| `synthesis/scripts/run_afl_fuzzing.py` | AFL++ driver. Parses `plot_data` for time-series edges. |
| `analysis/scripts/campaign_summary.py` | Aggregates JSONs → `summary.md` + per-cell coverage CSVs. |
| `scripts/run_campaigns.sh` | Campaign orchestrator (Phase 3 full run, 2-way parallel). |

Both drivers pass crash-tolerance flags:
- libFuzzer: `-ignore_crashes=1 -ignore_ooms=1 -ignore_timeouts=1`,
  status="ok" if any snapshot produced.
- AFL++: `AFL_MAP_SIZE=262144`, `AFL_SKIP_CRASHES=1`,
  `AFL_IGNORE_PROBLEMS=1`, `AFL_IGNORE_SEED_PROBLEMS=1`.

### 5.3 Targets + binaries

| Target | FuzzBench benchmark | Harness | Build variants |
|---|---|---|---|
| re2 | re2-2014-12-09 | `fuzzer-test-suite/re2-2014-12-09/target.cc` | coverage, sanitizer, fuzzer, afl |
| harfbuzz | harfbuzz-1.3.2 | `upstream:test/fuzzing/hb-fuzzer.cc` | coverage, sanitizer, fuzzer, afl |

Pinned commits in `pinned_versions.yaml`:
- RE2: `499ef7eff7455ce9c9fae86111d4a77b6ac335de`
- harfbuzz: `f73a87d9a8c76a181794b74b527ea268048f78e3`

Fuzzers:
- **libFuzzer.** clang-15 `-fsanitize=fuzzer,address`,
  binary at `dataset/targets/src/<target>/build/fuzzer/<target>_fuzzer`.
- **AFL++.** Installed at `~/tools/aflpp/`.
  Binary at `dataset/targets/src/<target>/build/afl/<target>_afl_fuzzer`.

### 5.4 Artefacts

- libFuzzer JSONs: `synthesis/results/campaigns/<target>/{empty,llm_seeds,random_seeds}.json`
- AFL++ JSONs: `synthesis/results/campaigns_afl/<target>/{empty,llm_seeds,random_seeds}_aflpp.json`
- Aggregate: `results/campaigns/summary.md` + per-cell `curve_*.csv`

Per-trial work dirs (corpora, AFL output, crash files) were deleted 2026-04-16
to save ~440 MB; the JSONs are authoritative.

### 5.5 How to reproduce a single cell

```bash
# Verify environment.
./dataset/targets/src/re2/build/fuzzer/re2_fuzzer -help=1 2>&1 | head -3
~/tools/aflpp/afl-fuzz -V 2>&1 | head -1
.venv/bin/python -c "from core.dataset_schema import CampaignConfig; print('ok')"

# libFuzzer single cell.
.venv/bin/python -m synthesis.scripts.run_fuzzing \
    --config synthesis/campaign_configs/llm_seeds.yaml \
    --target re2 \
    --binary dataset/targets/src/re2/build/fuzzer/re2_fuzzer \
    --trials 3 --duration-s 600 --snapshot-interval-s 60 \
    --seed-corpus-dir dataset/fixtures/re2_ab/claude_sonnet_results/seeds/re2/ablation/exp1_full/claude-sonnet-4-6/ \
    --work-root synthesis/results/campaigns

# AFL++ equivalent: swap run_fuzzing → run_afl_fuzzing,
# binary → ..._afl_fuzzer, work-root → campaigns_afl.

# Regenerate aggregate summary.
.venv/bin/python -m analysis.scripts.campaign_summary \
    --results-dirs synthesis/results/campaigns synthesis/results/campaigns_afl \
    --output-dir results/campaigns
```

**Parallelism warning.** Running >2 fuzzer processes concurrently with
`rss_limit_mb=2048` OOMs on a 16GB box. Keep parallelism ≤2 or cap rss_limit
lower.

### 5.7 Campaign-driver flags

Both `synthesis.scripts.run_fuzzing` (libFuzzer) and
`synthesis.scripts.run_afl_fuzzing` (AFL++) accept the same flag set:

| Flag | Required | Default | Purpose |
|---|:-:|---|---|
| `--config` | yes | — | Campaign-config YAML under `synthesis/campaign_configs/` (`empty.yaml`, `llm_seeds.yaml`, `random_seeds.yaml`, `fuzzbench_seeds.yaml`, `unittest_seeds.yaml`, `combined_seeds.yaml`). |
| `--target` | yes | — | Target name (`re2`, `harfbuzz`). |
| `--binary` | yes | — | Path to fuzzer-instrumented binary (`build/fuzzer/<t>_fuzzer` for libFuzzer, `build/afl/<t>_afl_fuzzer` for AFL++). |
| `--trials` | no | `CAMPAIGN_TRIALS = 20` (FuzzBench standard) | Number of independent campaign trials. The 2026-04-16 run used `3`. |
| `--duration-s` | no | `CAMPAIGN_DURATION_S = 82_800` (23h) | Per-trial wall-clock. The 2026-04-16 run used `600` (10 min). |
| `--snapshot-interval-s` | no | `CAMPAIGN_SNAPSHOT_S = 900` (15 min) | How often to snapshot the corpus. The 2026-04-16 run used `60`. |
| `--seed-corpus-dir` | no | `None` | Directory of `*.bin` seed files. Omit (or `None`) for the `empty` cell. |
| `--dictionary` | no | `None` | FuzzBench dictionary path. **Currently null in all configs** — see [§5.1 rigor bug 9.3](#rigor-bugs). |
| `--work-root` | no | `synthesis/results/campaigns` (libFuzzer) or `…/campaigns_afl` (AFL++) | Where per-trial work dirs and the final `<config>.json` (or `<config>_aflpp.json`) are written. |
| `--dry-run` | no | off | Print the planned command without executing. |

Both drivers also unconditionally pass crash-tolerance flags
(see §5.2). All other knobs (`rss_limit_mb`, `timeout_s`, `max_len`,
fuzzer-specific extras) come from the YAML config, not the CLI.

### 5.6 Recommended next run

Before claiming any campaign-time result, fix:

1. Regenerate `dataset/data/harfbuzz/coverage_gaps.json` from the real coverage
   pipeline.
2. Wire the FuzzBench dictionary into the campaign configs.
3. Materialize `synthesis/campaign_configs/fuzzbench_seeds.yaml` with the
   canonical seed corpus path; add as a sixth baseline column.
4. Synthesize the missing corpora — llama × both, Sonnet × harfbuzz,
   Haiku × RE2.
5. Run 2 targets × 2 fuzzers × 6 conditions × N≥10 trials × ≥1h duration.
   At N=10, 1h, parallelism=2 ≈ 60 hours wall.

Tracked in `docs/FUTURE_DIRECTIONS.md`.

---

<a id="experiment2-verdict"></a>
## 6. Verdict

Across `experiment2_1` (seed-time, both targets, all models) and `experiment2_2`
(campaign-time, plumbing-only):

- **Seed-time M1.** LLM seeds dominate random across all variants on both targets
  (RE2 +24% to +29%, harfbuzz +33% to +80%).
- **Seed-time M2.** RE2 best 0.867 (`nemotron-120b`/`v1_src`); harfbuzz best 0.640
  (`sonnet`/`v0_none`). Random by construction = 0.000.
- **Context scaling.** On RE2, more context monotonically helps (M2 doubles
  v0 → v3). On harfbuzz, **Sonnet's M2 *peaks* at v0_none and degrades with
  added context** — a target-shape × model interaction that wasn't predicted.
- **Free vs paid.** `codestral-22b` is the most consistent free model. `nemotron-120b`
  has the highest single-cell M2 on RE2 but cannot fill 150 seeds at higher
  variants on either target.
- **Campaign-time.** One cell (RE2/libFuzzer/Sonnet) shows a large effect
  (A12=1.000, n=3) but the surrounding rigor bugs prevent citing it.

The 5-variant prompt grid + frozen hard-branch M2 is now the load-bearing design
that any further experiment (`experiment3`, future targets) builds on.
