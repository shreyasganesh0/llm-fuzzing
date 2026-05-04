# Experiment 3 — Prompt-strategy ablation

**Scope.** RE2 + harfbuzz, the prompt-strategy axis added orthogonal to the
context axis from `experiment2`. 2026-04-21 onwards.
**Question.** Holding the 5-variant context grid fixed, does the *way* we prompt
the model — chain-of-thought, few-shot exemplars, multi-call self-critique,
plan-sketch-finalize, oracle-gated tool use — change M1/M2 by more than the
context spread does?
**Headline.** Framework shipped at commit `e64bf5e` ("Add prompt-strategy
ablation, tool use, cost audit, and CLI matrix"); 6 strategies registered;
default strategy is byte-identical to pre-work cache (~14k entries preserved).
A targeted 5-cell sub-experiment is queued and **blocked on UF LiteLLM credit**.

This document covers two sub-versions (`experiment3_0`, `experiment3_1`).
For framework setup commands (build, freeze, env), see
`EXPERIMENT_WALKTHROUGH.md §6`. For the underlying context grid and 7-model
matrix, see `experiment2.md`.

---

<a id="strategy-axis"></a>
## 1. The strategy axis

The full ablation surface after this work is
`target × variant × model × strategy × M ∈ {M1, M2}`.

| Strategy | Calls/seed | Templates | Tool use | Hypothesis |
|---|---|---|:-:|---|
| [`default`](#strategy-default) | 1 | base (`ablation_synthesis_{regex,binary}.j2`) | no | Baseline. **Byte-identical to pre-work cache.** [Why »](#strategy-default) |
| [`cot_strict`](#strategy-cot_strict) | 1 | `*_cot.j2` | no | Forced 4-step labelled CoT differs from free-form CoT. [Why »](#strategy-cot_strict) |
| [`few_shot`](#strategy-few_shot) | 1 | `*_fewshot.j2` + frozen exemplars at `dataset/fixtures/exemplars/<target>.json` | no | Concrete prior successes raise M2 hit-rate. [Why »](#strategy-few_shot) |
| [`self_critique`](#strategy-self_critique) | 2 | base → `*_refine.j2` | no | One refinement pass fixes one concrete weakness in the draft. [Why »](#strategy-self_critique) |
| [`prompt_chain`](#strategy-prompt_chain) | 3 | `*_plan.j2` → `*_sketch.j2` → `*_finalize.j2` | no | Forcing an explicit plan beats one-shot and two-shot. [Why »](#strategy-prompt_chain) |
| [`tool_use`](#strategy-tool_use) | ≤4 | base + `check_seed` oracle | yes (gpt-oss-20b, nemotron-120b only) | Oracle-gated refinement beats open-loop self-critique on structural validity. [Why »](#strategy-tool_use) |
| [`tool_use_retrieval`](#strategy-tool_use_retrieval) | ≤5 | base + `check_seed` + retrieval tools | yes (same models) | Oracle + retrieval-driven RAG beats deterministic top-K gap retrieval. Used by [`experiment3_1`](#experiment3_1) cell E. [Why »](#strategy-tool_use_retrieval) |

All strategies live in `core/prompt_strategies.py` and are exposed via the
`STRATEGIES: dict[str, PromptStrategy]` registry. `DEFAULT_STRATEGY_NAME =
"default"` is pinned — changing it invalidates 14k cached entries.

### 1.1 Cache-salt and on-disk path conventions

- **`default` strategy.** Cache salt = legacy
  `f"model={model},sample={k},ablation={cell},run={attempt+offset}"` (no
  strategy segment). On-disk seed path = legacy
  `<results-root>/seeds/<target>/ablation/<variant>/<model>/`.
- **Non-default strategies.** Append `,strategy=<name>` to the cache salt, and
  insert `<name>/` as a leading segment under both seeds and metric output:
  `<results-root>/seeds/<target>/<strategy>/ablation/<variant>/<model>/` and
  `results/ablation_<target>/<strategy>/{m1,m2}/<variant>/<model>/summary.json`.
- **`self_critique` (2-call) cache salt.** Adds a `round=draft|refine` segment
  to disambiguate the two LLM calls per seed.

This preserves the ~14k existing `default`-strategy cache hits and keeps prior
analysis scripts' hardcoded paths working.

### 1.2 Tool-use compatibility

`tool_use` requires `ModelDefaults.supports_tool_use=True`. Per the Phase-0 probe
(`results/probes/probe_tool_use.json`), this is set only for:

- `gpt-oss-20b`
- `nemotron-3-super-120b-a12b`

Other UF LiteLLM models reject `--enable-auto-tool-choice` (proxy-side vLLM
config issue); Anthropic models had zero credits during the probe so the flag
stays False pragmatically.

The orchestrator preflight (`AblationRunner._compat_matrix`) checks every
`(strategy, model)` pair — all-incompatible exits 2; partial filters with a
`WARN:` log line.

### 1.3 Per-strategy rationale

The hypothesis cells in §1's table are one-liners. Fuller reasoning per strategy:

<a id="strategy-default"></a>
**`default` — baseline, byte-identical to pre-work cache.**
One LLM call per seed. Renders the legacy template
(`ablation_synthesis_{regex,binary}.j2`) with no reasoning structure or
multi-call chain. The cache salt format and on-disk layout are pinned to be
byte-identical to pre-strategy-axis behavior so the ~14k cached responses
that drove `experiment2_1` (cumulative spend $100.09) stay valid hits and
results remain reproducible without re-paying. Every other strategy must
keep this contract — see `core/prompt_strategies.py::DEFAULT_STRATEGY_NAME`.

<a id="strategy-cot_strict"></a>
**`cot_strict` — 4-step labelled chain of thought.**
One LLM call per seed using `*_cot.j2`. The template enforces a strict
4-step structure: Step 1 quote the target source/test/gap line being
attacked (grounding); Step 2 reason about which branch will be hit; Step 3
outline the input shape; Step 4 emit the seed bytes/regex. The hypothesis
is that *labelled* CoT differs from the free-form CoT a model emits when
asked open-endedly. Step 1's verbatim quotes are mechanically auditable via
`analysis/scripts/verify_grounding.py` — the strategy was added partly so
we could detect when models hallucinate grounding rather than reading the
provided context.

<a id="strategy-few_shot"></a>
**`few_shot` — frozen exemplars from prior high-M2 cells.**
One LLM call per seed using `*_fewshot.j2`. Adds K exemplars sourced from
`dataset/fixtures/exemplars/<target>.json` — harvested by
`analysis/scripts/harvest_exemplars.py` from cells that scored highest on
M2 in `experiment2_1`. Exemplars are frozen (not regenerated per run) so
the cache key stays stable. The hypothesis is that concrete prior
successes ground the model better than abstract instructions, especially
on binary formats like harfbuzz where format-shape recall matters more than
reasoning ability — and where llama-3.1-70b's parse rate is ~10%.

<a id="strategy-self_critique"></a>
**`self_critique` — draft, then refine.**
Two LLM calls per seed. Call 1 (draft) uses the base template to produce a
candidate. Call 2 (refine) uses `*_refine.j2` to ask the model to identify
one concrete weakness in its own draft and fix it. Cache salt distinguishes
calls via a `round=draft|refine` segment so each call gets its own cache
entry. The hypothesis is that one targeted refinement pass fixes one
concrete weakness more reliably than one-shot generation — and is cheaper
than the 3-call `prompt_chain` strategy.

<a id="strategy-prompt_chain"></a>
**`prompt_chain` — plan, sketch, finalize.**
Three LLM calls per seed. Call 1 (plan, `*_plan.j2`) identifies the target
branch and the reasoning approach. Call 2 (sketch, `*_sketch.j2`) produces
a candidate input shape. Call 3 (finalize, `*_finalize.j2`) emits the exact
bytes/regex. The hypothesis is that explicit decomposition through three
stages beats both one-shot generation and self-critique's two-shot — a
classic decomposition test from prompt-engineering literature. Three times
the cost per seed is the trade-off; budget runs accordingly.

<a id="strategy-tool_use"></a>
**`tool_use` — oracle-gated refinement loop.**
Up to 4 LLM calls per seed using OpenAI-style tool calls. The model can
invoke a `check_seed` oracle (`re.compile(...)` validity for RE2; sfnt
header validity for harfbuzz, both in `synthesis/scripts/oracles.py`).
If the oracle rejects a candidate, the model can revise; if it accepts,
the loop terminates early. Restricted to `gpt-oss-20b` and
`nemotron-3-super-120b-a12b` — the only two models the Phase-0 probe
(`results/probes/probe_tool_use.json`) verified as emitting well-formed
tool calls through the UF LiteLLM proxy. The hypothesis is that an
in-process structural oracle beats open-loop self-critique on validity,
especially on binary formats where ~90% of llama output fails parse.

<a id="strategy-tool_use_retrieval"></a>
**`tool_use_retrieval` — oracle + model-driven retrieval.**
Up to 5 LLM calls per seed (1 initial + 4 refinement turns,
`max_tool_turns=4`). Same OpenAI-style tool-call dialect as `tool_use`,
but the model is also given retrieval tools that fetch additional source
or gap context on demand instead of receiving a deterministic top-K gap
list up-front. Same model gating as `tool_use`. The hypothesis is that
letting the model *ask* for the context it needs beats handing it a
fixed gap list (`v5_topk_gaps`-style retrieval) — the corresponding A/B
is `experiment3_1` cell E vs cell C.

---

<a id="experiment3_0"></a>
## 2. experiment3_0 — Strategy axis added (2026-04-21)

Code-only sub-version: registry, templates, dispatch, CLI surface, integration
test suite. **Numerical results not yet in the top-level docs**; selective
runs exist in the cache.

### 2.1 What landed

- `core/prompt_strategies.py` — `STRATEGIES` registry, `make_cache_salt`,
  `PromptStrategy` protocol.
- 14 new templates under `synthesis/prompts/`:
  `ablation_synthesis_{regex,binary}_{cot,fewshot,plan,sketch,finalize,refine}.j2`.
- `synthesis/scripts/generate_ablation_inputs.py::_TEMPLATE_SUFFIX_BY_STRATEGY`
  — explicit dispatch table.
- `synthesis/scripts/oracles.py` — the `check_seed` oracle that the
  `tool_use` strategy can call mid-generation.
- `dataset/fixtures/exemplars/` — frozen few-shot exemplars (per target,
  harvested via `analysis/scripts/harvest_exemplars.py`).
- CLI matrix flags on `scripts/_ablation_base.py` (`--strategy`, `--variants`,
  `--num-seeds`, `--list-strategies`, `--dry-run`).
- Integration tests at `tests/test_phase9_integration.py` (23 active +
  1 opt-in cache audit).
- Constrained-output plumbing in `core/llm_client.py`
  (`response_format`, `guided_json`).

### 2.2 Test surface

`tests/test_phase9_integration.py` guards:

- Registry shape (every registered strategy has the required attributes).
- Template existence per target × strategy.
- Cache-salt format invariants (default unchanged; non-default appends
  `,strategy=<name>`).
- CLI composition (every flag pair, every preflight branch).
- Optional cache-audit (skipped by default; opt in via env var to walk
  `.cache/llm/` and confirm strategy salts are well-formed).

Status at commit `e64bf5e`: 290 passed, 1 skipped, 1 pre-existing unrelated
failure in `core/tests/test_loop_detector.py`.

### 2.3 Artefacts (when run)

```
synthesis/results/ablation_<target>/seeds/<target>/<strategy>/ablation/<variant>/<model>/seed_*.bin
results/ablation_<target>/<strategy>/{m1,m2}/<variant>/<model>/summary.json
```

The `default` strategy keeps the legacy (no-strategy-segment) layout
unchanged.

### 2.4 How to reproduce

```bash
# List registered strategies.
.venv/bin/python scripts/run_ablation_re2.py --list-strategies

# Dry-run a multi-strategy matrix (no LLM calls).
.venv/bin/python scripts/run_ablation_re2.py \
    --phase all --skip-existing \
    --strategy default,cot_strict,few_shot \
    --dry-run

# Smoke run: one strategy, one variant, one model, 5 seeds.
.venv/bin/python scripts/run_ablation_harfbuzz.py --phase synthesis \
    --variants v1_src --only-models gpt-oss-20b \
    --strategy tool_use \
    --num-seeds 5 --attempt-offset 70000

# Full strategy sweep on RE2 (long-running; nohup).
nohup .venv/bin/python scripts/run_ablation_re2.py \
    --phase all --skip-existing \
    --strategy default,cot_strict,few_shot,self_critique,prompt_chain \
    >> /tmp/re2_strategies.log 2>&1 &
```

The full orchestrator flag table is in
[experiment2.md §4.5](experiment2.md#how-to-reproduce-experiment2_1) —
all 9 flags (`--phase`, `--skip-existing`, `--only-models`, `--strategy`,
`--variants`, `--num-seeds`, `--attempt-offset`, `--list-strategies`,
`--dry-run`) apply unchanged here. `--strategy` is the only one that
behaves differently in this experiment: it accepts a comma-separated list
of strategy names from the registry (`default,cot_strict,few_shot,
self_critique,prompt_chain,tool_use`) and the orchestrator iterates the
full `strategy × variant × model` matrix. The compatibility preflight
filters (strategy, model) pairs where `tool_use` is requested but
`ModelDefaults.supports_tool_use=False`.

---

<a id="experiment3_1"></a>
## 3. experiment3_1 — 5-cell prompt optimization (BLOCKED)

A targeted 5-cell ablation isolating CoT × deterministic RAG × tool-driven RAG
on RE2 with `gpt-oss-20b`. **All code shipped (test suite 329 passed,
1 skipped); blocked on UF LiteLLM proxy returning
`400 Budget has been exceeded! Current cost: 25.01, Max budget: 25.0`** —
account-wide cap on the proxy side, raising our local cap does not help.
Anthropic out of scope for this sub-experiment per user instruction.

### 3.1 The 5-cell matrix

| # | Cell | Strategy | Variant | Isolates |
|--:|---|---|---|---|
| A | baseline | [`default`](#strategy-default) | `v3_all` | no CoT, dumped RAG, no tools. [Detail »](#exp3_1-what-landed) |
| B | +CoT | [`cot_strict`](#strategy-cot_strict) | `v3_all` | strict 4-step CoT. [Detail »](#strategy-cot_strict) |
| C | +deterministic RAG | [`default`](#strategy-default) | `v5_topk_gaps` | gap-only — no source, no tests. [Detail »](#exp3_1-what-landed) |
| D | +CoT +deterministic RAG | [`cot_strict`](#strategy-cot_strict) | `v5_topk_gaps` | CoT × RAG composition. [Detail »](#exp3_1-what-landed) |
| E | +tool-driven RAG | `tool_use_retrieval` | `v0_none` | model-driven retrieval, 5-turn cap. [Detail »](#exp3_1-what-landed) |

The `v5_topk_gaps` variant (gaps-only retrieval — no full-file source, no
tests) was added for this sub-experiment and registered in
`core/variants.py::STANDARD_VARIANTS`. The `tool_use_retrieval` strategy is
distinct from the `tool_use` strategy in `experiment3_0`: it gives the model a
retrieval tool with a 5-turn cap rather than a structural oracle.

### 3.2 Pre-run cost estimate

**$0.39** for a 600-call upper bound using historical gpt-oss-20b means
(7816 in / 5869 out per call at $0.03 / $0.07 per Mtok), via
`analysis/scripts/estimate_cost.py`. Budget tiers below are conservatively
2–3× this estimate.

<a id="exp3_1-what-landed"></a>
### 3.3 What landed

- `tool_use_retrieval` strategy in `core/prompt_strategies.py`.
- `v5_topk_gaps` variant in `core/variants.py`.
- `synthesis/scripts/oracles.py` — the two retrieval tools that
  `tool_use_retrieval` exposes to the model.
- `analysis/scripts/verify_grounding.py` — the grounding audit.
  Checks each generated seed's CoT Step-1 quote actually appears in the
  source, and that `target_gaps` references resolve to real `(file, line)`
  entries in the prepped dataset.
- Per-cell budget gating via `core/budget.py` — sets
  `UTCF_BUDGET_RUN_ID`, `UTCF_MAX_SPEND_USD`, `UTCF_PER_CELL_CAP_USD`,
  `UTCF_PER_CALL_CAP_USD`.
- Retry-on-4xx fix in `core/llm_client.py` so a budget-exceeded response
  doesn't poison the cache.

<a id="exp3_1-budget-env"></a>
### 3.4 Budget-cap env vars (consumed by `core/budget.py`)

The push-button command in §3.5 sets four env vars that gate spend at three
granularities — total, per-cell, and per-call. All four are read by
`core/budget.py` and applied transparently inside `LLMClient.complete()`.

| Env var | Required | Purpose |
|---|:-:|---|
| `UTCF_BUDGET_RUN_ID` | yes | Free-form identifier for this run; appears in the budget ledger so multiple runs can share a cap. |
| `UTCF_MAX_SPEND_USD` | no | Hard cap on the total spend across the entire run. The orchestrator aborts the cell with a `RuntimeError` once this is exceeded. |
| `UTCF_PER_CELL_CAP_USD` | no | Per-cell spend cap. The current cell aborts (cleanly) when exceeded; siblings continue. |
| `UTCF_PER_CALL_CAP_USD` | no | Per-call spend cap. Pre-flight estimate from token-count means; rejects calls projected to exceed the cap before issuing them. |
| `UTCF_BUDGET_CELL_KEY` | auto | Set by `AblationRunner` per cell (`<target>/<strategy>/<variant>/<model>`); do not set manually. |

If any cap env var is unset, that level of gating is disabled. Pricing
comes from `core.llm_client.PRICING_USD_PER_MTOK`.

<a id="exp3_1-push-button"></a>
### 3.5 Push-button command (run when credit returns)

```bash
# Cells A–D (no tool use)
UTCF_BUDGET_RUN_ID=ab_ce_abcd \
UTCF_MAX_SPEND_USD=1.00 \
UTCF_PER_CELL_CAP_USD=0.30 \
UTCF_PER_CALL_CAP_USD=0.02 \
nohup .venv/bin/python scripts/run_ablation_re2.py --phase synthesis \
    --only-models gpt-oss-20b \
    --variants v3_all,v5_topk_gaps \
    --strategy default,cot_strict \
    --num-seeds 150 --attempt-offset 92000 \
    >> /tmp/abcd.log 2>&1 &

# Cell E (tool-driven retrieval — separate run to keep logs clean)
UTCF_BUDGET_RUN_ID=ab_ce_e \
UTCF_MAX_SPEND_USD=1.00 \
UTCF_PER_CELL_CAP_USD=0.30 \
UTCF_PER_CALL_CAP_USD=0.02 \
nohup .venv/bin/python scripts/run_ablation_re2.py --phase synthesis \
    --only-models gpt-oss-20b \
    --variants v0_none \
    --strategy tool_use_retrieval \
    --num-seeds 150 --attempt-offset 93000 \
    >> /tmp/cell_e.log 2>&1 &

# After synthesis, compute M1/M2 for all five cells.
.venv/bin/python scripts/run_ablation_re2.py --phase m1 --skip-existing \
    --only-models gpt-oss-20b \
    --variants v3_all,v5_topk_gaps,v0_none \
    --strategy default,cot_strict,tool_use_retrieval

.venv/bin/python scripts/run_ablation_re2.py --phase m2 --skip-existing \
    --only-models gpt-oss-20b \
    --variants v3_all,v5_topk_gaps,v0_none \
    --strategy default,cot_strict,tool_use_retrieval

# Grounding audit (checks CoT Step-1 quotes + target_gaps accuracy).
.venv/bin/python -m analysis.scripts.verify_grounding --walk \
    --results-root synthesis/results/ablation_re2_v2 \
    --dataset-root dataset/fixtures/_ablation_re2_v2_dataset \
    --target re2
```

### 3.6 Status

- Code: shipped, test-backed (329 passed, 1 skipped — baseline 290).
- LLM calls: not yet made; blocked on credit.
- Numbers: none yet.

---

## 4. Cost accounting

The strategy axis multiplies the call budget. For a single full-matrix run
(7 models × 5 variants × 6 strategies × 150 seeds × 1 call/seed average,
upper-bounded by `prompt_chain` at 3 calls/seed), the upper bound is
~190k LLM calls.

Estimate before any run:

```bash
.venv/bin/python -m analysis.scripts.estimate_cost \
    --model claude-sonnet-4-6 --n-calls 4500 --mean-in 3000 --mean-out 800
```

Audit cumulative spend:

```bash
.venv/bin/python -m analysis.scripts.cost_audit
cat results/cost_audit/summary.md
```

Both backed by `core/llm_client.py::PRICING_USD_PER_MTOK`.

Cumulative spend on disk before this experiment ran end-to-end:
**$100.09** across 14,161 cached responses (Anthropic $86.25, UF
LiteLLM-repriced $13.83). The strategy axis is expected to add
~$5–15 of cached responses for free models (LiteLLM) and ~$50–150 if the
Anthropic models are run end-to-end across all 6 strategies.

---

## 5. Where the strategy results will land

When `experiment3_1` and any subsequent strategy sweeps finish:

- Per-cell seeds: `synthesis/results/ablation_<target>/seeds/<target>/<strategy>/ablation/<variant>/<model>/`
- Per-cell M1: `results/ablation_<target>/<strategy>/m1/<variant>/<model>/summary.json`
- Per-cell M2: `results/ablation_<target>/<strategy>/m2/<variant>/<model>/summary.json`
- Aggregate (when added): `results/ablation_<target>/<strategy>/summary.md`

The default-strategy paths under
`results/ablation_<target>/{m1,m2}/<variant>/<model>/` (no `<strategy>/`
segment) remain `experiment2_1`'s output and are not affected.

`analysis/scripts/ablation_summary.py` will need a `--strategy` flag before
it can walk the multi-strategy tree; this is an open task.
