# UTCF — LLM-Guided Fuzzing Seed Synthesis

Research framework for LLM-guided fuzzing seed corpus synthesis. Given a coverage-instrumented
target binary and its upstream test suite, the framework identifies hard-to-reach branches,
prompts an LLM to synthesize inputs targeting those branches, and evaluates the resulting seeds
via seed-time coverage metrics (M1: total edges, M2: hard-branch hit rate).

**Resuming work?** Read `docs/WEEKLY_REVIEW_PROMPT.md` for the current experiment state and
results, or `docs/EXPERIMENT_HANDOFF.md` for pending tasks and resume commands.

## Current status

Two targets fully run with llama-3.1-8b-instruct (150 seeds/cell, 5-variant ablation):

| Target | Format | Best M1 vs random | Best M2 |
|---|---|---|---|
| RE2 (regex engine) | text | +19% (v2_src_tests) | 60% of hard branches (v3_all) |
| harfbuzz (font shaper) | binary | +2% (v0_none) | 4% of hard branches (v0/v3) |

Claude Sonnet/Haiku harfbuzz cells pending (API credits needed). Results in `docs/LLAMA_ABLATION_RESULTS.md`.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run tests (no network, no LLM, no LLVM required)
pytest dataset/tests/ prediction/tests/
```

## Running the ablation experiments

Requires: LLVM 15+ (`clang`, `llvm-cov`, `llvm-profdata`), git, network access.

```bash
# Step 1 — fetch and build coverage-instrumented target
./dataset/scripts/fetch_target.sh dataset/targets/re2.yaml        # or harfbuzz.yaml
./dataset/scripts/build_instrumented.sh dataset/targets/re2.yaml

# Step 2 — freeze hard-branch M2 targets
python -m analysis.scripts.freeze_target_branches --target re2_v2
# or: --target harfbuzz

# Step 3 — run the full ablation (prep → synthesis → m1 → m2)
python scripts/run_ablation_re2.py --phase all           # RE2
python scripts/run_ablation_harfbuzz.py --phase all      # harfbuzz

# Results land under results/ablation_re2_v2/ and results/ablation_harfbuzz/
```

**LLM keys:**
- `secrets/claude_key` — Anthropic API key (Claude Sonnet/Haiku)
- LiteLLM endpoint at `https://api.ai.it.ufl.edu` — for llama via UF AI cluster (no key file needed, uses env)

## 5-variant ablation design

| Variant | Source? | Tests? | Gaps? |
|---|:---:|:---:|:---:|
| v0_none        | ❌ | ❌ | ❌ |
| v1_src         | ✅ | ❌ | ❌ |
| v2_src_tests   | ✅ | ✅ | ❌ |
| v3_all         | ✅ | ✅ | ✅ |
| v4_src_gaps    | ✅ | ❌ | ✅ |

**Hard-branch filter (M2):** A branch qualifies only if hit by ≥1 structured smoke seed
AND by 0 random-format seeds. This makes the random baseline score exactly 0%, ensuring
the metric has a clean floor.

**Seed normalisation:** Synthesis retries until exactly 150 seeds; deterministically
subsampled (RNG seed=42) before measurement to eliminate seed-count as a confound.

## Repository layout

```
scripts/              Experiment orchestrators (run_ablation_re2.py, run_ablation_harfbuzz.py)
synthesis/
  prompts/            Jinja2 templates (ablation_synthesis_regex.j2, ablation_synthesis_binary.j2)
  scripts/            Synthesis drivers, coverage measurement, random input generators
analysis/
  scripts/            freeze_target_branches.py, measure_gap_coverage.py, ablation_summary.py
dataset/
  targets/            Target YAML definitions (re2.yaml, harfbuzz.yaml)
  fixtures/           Frozen M2 target branches, upstream union profiles, prepped datasets
  scripts/            fetch_target.sh, build_instrumented.sh
core/                 Shared config, schema (GeneratedInput, CampaignConfig), logging
results/              Experiment outputs (gitignored — large binary/JSON files)
synthesis/results/    Synthesised seed corpora (gitignored)
docs/
  WEEKLY_REVIEW_PROMPT.md    Full results summary for weekly review presentation
  LLAMA_ABLATION_RESULTS.md  Standalone llama results doc (RE2 v2 + harfbuzz)
  EXPERIMENT_HANDOFF.md      Pending tasks and resume commands
```

## Critical constraints

- **Provenance is sacred** — every test object traces back to `upstream_repo:commit:file:line`.
- **No fabricated tests** — extractors only read upstream code.
- **Deterministic seeds** — RNG seed=42 for subsampling; sha256-seeded flag bytes for RE2 seeds.
- **Hard-branch filter** — never relax `rand_hits==0`; doing so invalidates M2 as a metric
  (see RE2 ablation_v3 post-mortem in `docs/WEEKLY_REVIEW_PROMPT.md` §6).
