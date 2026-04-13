# 3-way coverage summary — exp1 vs exp2 vs random (RE2, llama-3.1-8b-instruct, regex format, 2026-04-13)

## Headline

| cell | seeds | edges | lines | edges over random |
|---|---:|---:|---:|---:|
| exp1 (gap-targeted) | 20 | **1243** | 2530 | **+263** |
| exp2 (source-only)  | 30 | **1133** | 2385 | **+153** |
| random baseline     | 30 |   980 | 2081 |   (floor) |

- exp1 beats random by 263 edges (+27%)
- exp2 beats random by 153 edges (+16%)
- exp1's lead over exp2 (+110 edges) is ~1.7× its own "exp2-would-be-there-anyway" component, so the LLM signal is clearly above the random floor for both cells but exp1's in-distribution advantage is substantial.

Random baseline config: 30 seeds of `[2 random flag bytes][random ASCII 1-62 chars]`, seeded `random.Random(seed=42)`, via `synthesis/scripts/generate_random_inputs.py --input-format regex`.

## Per-file exclusive coverage (vs random)

Top files exp1 reaches that random misses:
- `re2/parse.cc` (+145), `re2/regexp.cc` (+57), `re2/simplify.cc` (+44), `re2/compile.cc` (+23).

Top files exp2 reaches that random misses:
- `re2/parse.cc` (+82), `re2/simplify.cc` (+49), `re2/compile.cc` (+27), `re2/onepass.cc` (+14).

Both LLM variants concentrate their edge over random in the parser/simplifier/compiler — the semantically meaningful paths a random regex body rarely exercises. exp1's extra ~110-edge lead is mostly in `regexp.cc` and the parser tail.

## Artifacts

- `random.json`   — coverage profile (30-seed random corpus)
- `exp1_vs_random/ab_coverage_diff.md` — pairwise diff (exp1 vs random)
- `exp2_vs_random/ab_coverage_diff.md` — pairwise diff (exp2 vs random)
- `ab_coverage_diff.md` (top level) — original exp1 vs exp2 diff (unchanged)

## Reproducibility

```bash
.venv/bin/python -m synthesis.scripts.generate_random_inputs \
    --target re2 --count 30 --seed 42 --input-format regex \
    --results-root dataset/fixtures/re2_ab/random_results

.venv/bin/python -m synthesis.scripts.measure_coverage \
    --binary dataset/targets/src/re2/build/coverage/seed_replay \
    --seeds-dir dataset/fixtures/re2_ab/random_results/seeds/re2/random \
    --source-roots /home/shreyasganesh/projects/llm-fuzzing/phase1_dataset/targets/src/re2/upstream \
    --profile-out dataset/fixtures/re2_ab/ab_coverage/random.json
```

Note: `--source-roots` uses the *pre-rearchitecture* `phase1_dataset/...` prefix because the current coverage build was compiled before the 2026-04-13 phase→domain rename and has the old paths baked into its DWARF debug info. Either pass the old prefix (as above) or rebuild `seed_replay` to refresh the embedded paths.
