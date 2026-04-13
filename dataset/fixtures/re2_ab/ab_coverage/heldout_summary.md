# Experiment A — held-out source subset (RE2, llama-3.1-8b-instruct, regex format)

## Setup

- **Set A (visible to gap prompt)** — parser/simplifier: `re2/parse.cc`,
  `re2/regexp.cc`, `re2/simplify.cc`, `re2/tostring.cc`. Gap list for
  `exp1_heldout` filtered to these files (611/2042 gap branches retained).
- **Set B (held-out, coverage measured here)** — execution + utility:
  `re2/compile.cc`, `re2/prog.cc`, `re2/dfa.cc`, `re2/nfa.cc`,
  `re2/onepass.cc`, `re2/bitstate.cc`, `re2/re2.cc`, `util/rune.cc`,
  `util/strutil.cc`.
- Tests.json and fixture metadata unchanged — exp1_heldout still sees the
  same 5 few-shot test examples. Only the gap list is restricted.
- exp2_source prompt is unchanged — it always sees all source files via
  call-graph priority, so it's the "no partitioning" baseline.

## Headline — coverage restricted to set B only

| cell | seeds | B-edges | B-lines | Δ vs random |
|---|---:|---:|---:|---:|
| **exp1_full**    | 20 | **599** | 1533 | +42 |
| **exp2_source**  | 30 | **596** | 1540 | +39 |
| exp1_heldout     | 30 |  581    | 1502 | +24 |
| random baseline  | 30 |  557    | 1436 |  0  |

## Decision rules from the plan

> "if exp1_B − exp2_B ≥ +50, exp1 generalizes; if within ±20 or exp2 leads, hypothesis supported"

- **exp1_full vs exp2_source on B**: +3 edges (within ±20) → **hypothesis supported**.
- **exp1_heldout vs exp2_source on B**: **-15 edges (exp2 leads)** → **hypothesis strongly supported**.

## What this means

exp1_full's big in-distribution advantage over exp2 (+110 edges total on
the full source) collapses to **+3 edges** when we restrict the lens to
files whose gaps exp1 was told about only by happenstance (because the
unfiltered gap list covered them). That says most of exp1's +110 is
"the gap list is a flashlight pointed at specific files," not general
regex-synthesis skill.

When we restrict exp1's gap list to a disjoint set of files (set A) and
then measure on set B — files exp1 has never been pointed at — exp1
actually loses to exp2 (-15 edges). exp2's recipe (give the model the
code, ask it to find interesting inputs) transfers across the file
boundary; exp1's recipe (give the model a specific list of branches to
hit) does not.

Qualitative confirmation from per-file deltas:
- `exp1_full_on_B` vs `exp2_source_on_B`: exp1_full wins +9 edges in
  `re2/compile.cc`, +6 in `re2/prog.cc` — the files the full gap list
  pointed at.
- `exp1_heldout_on_B` vs `exp2_source_on_B`: exp1_heldout wins a few
  edges in `util/rune.cc` + `re2/re2.cc` but loses net. When no
  compile/prog gaps are in its prompt, exp1 stops producing inputs that
  exercise those files.

## Confounds and caveats

- exp1_heldout still sees the 5 few-shot test examples, which reference
  RE2 APIs that implicitly exercise set-B files. That test context is
  probably why exp1_heldout still beats random (+24 edges) rather than
  matching it.
- Per-cell seed counts differ (exp1_full=20 vs exp1_heldout=30 vs
  exp2=30). exp1_full gets fewer seeds but more in-distribution edges
  per seed — consistent with the "flashlight" interpretation.
- n≥20 per cell is enough to see the direction of the effect; a
  rigorous p-value would need n≥30 per cell AND multiple random splits
  of A vs B to rule out file-subset luck.

## Reproduce

```bash
# Filter the gap list to set A only (parser files)
.venv/bin/python -c "
import json, shutil
from pathlib import Path
src = Path('dataset/fixtures/re2_ab/re2')
dst = Path('dataset/fixtures/re2_ab_heldout/re2')
dst.mkdir(parents=True, exist_ok=True)
data = json.loads((src/'coverage_gaps.json').read_text())
SET_A = {'re2/parse.cc', 're2/regexp.cc', 're2/simplify.cc', 're2/tostring.cc'}
data['gap_branches'] = [g for g in data['gap_branches'] if g['file'] in SET_A]
data['gap_functions'] = [g for g in data['gap_functions'] if g['file'] in SET_A]
(dst/'coverage_gaps.json').write_text(json.dumps(data, indent=2))
for fn in ('tests.json','metadata.json'): shutil.copy(src/fn, dst/fn)
"

# Run exp1 synthesis with the restricted gaps
UTCF_LITELLM_URL=https://api.ai.it.ufl.edu UTCF_LLM_RPM=12 \
.venv/bin/python -m synthesis.scripts.generate_inputs \
    --target re2 --model llama-3.1-8b-instruct --samples 3 --experiment exp1 \
    --input-format regex \
    --dataset-root dataset/fixtures/re2_ab_heldout \
    --results-root dataset/fixtures/re2_ab_heldout/phase3_results

# Measure coverage restricted to set B files (each path passed as a --source-roots arg)
UPSTREAM=/home/shreyasganesh/projects/llm-fuzzing/phase1_dataset/targets/src/re2/upstream
.venv/bin/python -m synthesis.scripts.measure_coverage \
    --binary dataset/targets/src/re2/build/coverage/seed_replay \
    --seeds-dir dataset/fixtures/re2_ab_heldout/phase3_results/seeds/re2/exp1/llama-3.1-8b-instruct \
    --source-roots "$UPSTREAM/re2/compile.cc" "$UPSTREAM/re2/prog.cc" "$UPSTREAM/re2/dfa.cc" \
                   "$UPSTREAM/re2/nfa.cc" "$UPSTREAM/re2/onepass.cc" "$UPSTREAM/re2/bitstate.cc" \
                   "$UPSTREAM/re2/re2.cc" "$UPSTREAM/util/rune.cc" "$UPSTREAM/util/strutil.cc" \
    --profile-out dataset/fixtures/re2_ab/ab_coverage/heldoutB_exp1_heldout.json
```

## Artifacts

- `heldoutB_{exp1_full,exp2_source,exp1_heldout,random}.json` — per-cell
  coverage restricted to set B
- `heldoutB_diff.md` — N-cell diff (reference: exp2_source_on_B)
