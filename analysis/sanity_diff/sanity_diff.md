# Sanity differential: Exp 1 (gap-targeted) vs Exp 2 (source-only)

- Fixture: RE2, 2 models × 2 experiments
- Total cost (scaled sanity): $0.0209

## Headline
- **gpt-oss-20b**: exp1=0 seeds @ $0.0011; exp2=5 seeds @ $0.0036 (Δ seeds = +5, Δ cost = $+0.0025). exp1 synth_ok=0/3, exp2 synth_ok=1/3.
- **llama-3.1-8b-instruct**: exp1=10 seeds @ $0.0011; exp2=10 seeds @ $0.0070 (Δ seeds = +0, Δ cost = $+0.0059). exp1 synth_ok=1/3, exp2 synth_ok=2/3.

## Per-cell metrics

| metric | llama-3.1-8b-instruct / exp1_gap_targeted | llama-3.1-8b-instruct / exp2_source_only | gpt-oss-20b / exp1_gap_targeted | gpt-oss-20b / exp2_source_only |
|--------|---|---|---|---|
| prediction_status | ok | parse_failure | ok | parse_failure |
| prediction_n_pred | 5 | 0 | 5 | 0 |
| prediction_n_actual | 5 | 3 | 4 | 3 |
| synthesis_samples | 3 | 3 | 3 | 3 |
| synthesis_ok | 1 | 2 | 0 | 1 |
| synthesis_seeds | 10 | 10 | 0 | 5 |
| unique_seed_ratio | 1.000 | 1.000 | 0.000 | 1.000 |
| output_tokens_total | 2066 | 2607 | 12288 | 33261 |
| tokens_per_seed | 206.6 | 260.7 | 0.0 | 6652.2 |
| cost_usd | 0.001059 | 0.006991 | 0.001111 | 0.003635 |
| cost_per_seed | 0.000106 | 0.000699 | 0.000000 | 0.000727 |
| loop_truncated_samples | 2 | 1 | 0 | 2 |

## Glossary

- **prediction_status**: whether Phase 2 / source-only prediction produced parseable JSON.
- **synthesis_seeds**: total seed files written to disk across all samples.
- **unique_seed_ratio**: unique(sha256(seed_bytes)) / total_seeds. 1.0 = all distinct.
- **tokens_per_seed**: output tokens spent per written seed (lower is more efficient).
- **loop_truncated_samples**: samples ended by the loop-abort streaming path (detected as parse_failure with output_tokens well below the cap).