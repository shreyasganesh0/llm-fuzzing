# Experiment iteration summary — follow-ups A, B, C

One page. All three pre-registered (frozen at commit `44d8104`,
2026-05-18, before any run); staged under a $25-proxy-cap cost gate
(estimate $1.52 ≪ $5 abort threshold; actual cumulative litellm
≈ $19.3, cap never hit, 0 budget errors). Branch `experiment-followups`.

## 1. Pre-registered predictions vs. outcomes

| # | Pre-registered prediction | Outcome | Verdict |
|--|--|--|--|
| A | `cot_strict_no_labels` fills ≈ default; `cot_strict_no_examples` & `_rotated` stay CENSORED (i.e. **rigid labels** cause the collapse) | `no_labels` **CENSORED** (50, collapsed onto the example regexes); `no_examples` **FILLED 150 @ M2 0.800** (= default); `rotated` **FILLED 150 @ M2 0.667** | **FALSIFIED** (all 3 directions wrong) |
| B | `cot_strict@harfbuzz/v3_all` CENSORED or fills with M2 ≪ default 0.260 → collapse is **general** | **FILLED 150** (no collapse), M2 **0.120** vs default 0.260 (Δ −0.140) | Pre-reg "general" branch fired; sharper joint reading below |
| C | low-entropy quartile dominates deep-reach (Q1−Q4 ≥ +0.10) → per-seed "valid→reaches deep" mechanism **SUPPORTED** | deep-reach ~flat & non-monotone (Q1−Q4 = 0.008 / 0.061 ≪ 0.10); per-seed M1 ~flat (~540) → **"mechanism WRONG / union-level"** | **FALSIFIED** (per-seed mechanism) |

Two of three pre-registrations were falsified — the high-information
outcome the design explicitly sought.

## 2. Implied mechanism story (revised)

The `cot_strict` collapse is caused by the **static, fixed in-template
example list**, not the rigid 4-step labels: removing or rotating the
list fully recovers fill *and* M2 to default's 0.800 even with the
labels kept, while keeping the static list collapses even with the
labels removed (A). That collapse is therefore RE2-example-list-specific
— on harfbuzz, whose cot template has the labels but no such list,
`cot_strict` filled 150 with no collapse (B, corroborating A) — but the
rigid-label scaffold *separately* imposes a **general seed-quality (M2)
penalty** (harfbuzz M2 0.120 vs 0.260, filled, B). Independently, the
experiment-6 finding that the **low-entropy 150-seed subsample carries
the M2 union** is real but is **not** a per-seed validity effect:
low-entropy seeds are no more likely to reach deep code and contribute
the same ~540 edges each (C) — the advantage is **union-level
complementarity** (a low-entropy set covers a less-redundant spread of
hard branches). Net: for codestral on structured targets, *corpus-level
diversity/complementarity* — not per-seed reasoning depth, per-seed
validity, or scaffolding — is the dominant lever.

## 3. L1–L9 levers (EXPERIMENT_DEEP_DIVE.md §7) — what the data now says

- **L2 (cot_strict self-anchoring example list): STRONGLY SUPPORTED.**
  Direct cause confirmed; remove or rotate the in-template example list
  → `cot_strict` recovers to default-level M2 (A). Highest-value,
  lowest-risk implementation change.
- **L1 (dedup keying / diversity definition): SUPPORTED, reframed.**
  The binding constraint is corpus *complementarity*, not per-seed
  quality (C) — reporting a diversity/complementarity statistic as
  first-class is now well-motivated.
- **L5 (is union-M2 the right objective; low-entropy selection): the
  C-relevant half is REFRAMED.** Low-entropy selection helps via
  union-complementarity, not per-seed validity — so "select low-entropy
  seeds" is a *set-construction* heuristic, not a per-seed quality
  filter; legitimate but must be framed as union-level.
- **L7 (reliability/diversity as a first-class metric): SUPPORTED** (A
  fill-rate + B fill-vs-quality split + C complementarity all argue for
  it).
- **L8 (few_shot's soft anchor survived where cot's rigid one didn't):
  partially explained** — A shows it is the *static fixed example
  enumeration* under a mandated rationale that anchors; few_shot's
  exemplars are style anchors without a mandated structured-reasoning
  field. Consistent; not separately tested.
- **L3, L4, L6, L9: SILENT** (not probed by A/B/C). L6 fail-safe
  performed correctly (aborted `no_labels` at 60 attempts; nothing to
  revise yet).
- The original EXPERIMENT_DEEP_DIVE §6.1 "rigid labels cause the
  collapse" claim is now **FALSIFIED** and should be superseded by §2
  above.

## 4. What I would do next (with cost estimates)

1. **Ship L2 as a default-template change** (zero LLM): rotate or drop
   the static in-template example list for *all* strategies (not just a
   cot variant) and re-confirm `cot_strict` ≈ default on RE2. ~1 cell
   re-run ≈ **$0.4**; high value.
2. **Generality of L2 across models** (the convergent finding is still
   codestral-only): run `cot_strict` vs `cot_strict_no_examples` on RE2
   v3_all for one more reliable free model (e.g. `llama-3.1-70b`) — 2
   cells ≈ **$1**. Tests whether example-anchoring is model-general.
3. **C's union-complementarity claim, direct test** (zero LLM): from
   the existing exp4 pools, compute the marginal hard-branch coverage
   added by each seed in entropy order vs random order (greedy union
   curves) for v1_src/v3_all — confirms "low-entropy set = less
   redundant" directly. **$0**, ~1h compute.
4. Defer L3/L4/L6/L9 until 1–3 resolve (they depend on whether L2
   already explains most of the strategy-axis variance).

Cumulative spend for A+B+C ≈ **$0.7** (within the gate); the proxy cap
was never approached. All raw numbers: `docs/experiment6/RESULTS.md`,
`docs/experiment7/RESULTS.md`, `docs/experiment4/FOLLOWUP.md`.
