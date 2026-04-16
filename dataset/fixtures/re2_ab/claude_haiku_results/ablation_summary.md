# N-cell prompt-ablation coverage differential

**Reference cell:** `exp1_full` (edges=1001)

## Headline — one row per cell
| cell | seeds | edges | lines | Δedges vs ref | edges only-in-cell | edges only-in-ref |
|---|---:|---:|---:|---:|---:|---:|
| `exp2_source` | 30 | 1138 | 2400 | +137 | 242 | 105 |
| `exp1_gaps_only` | 30 | 1019 | 2195 | +18 | 130 | 112 |
| `exp1_full` | 30 | 1001 | 2111 | +0 | 0 | 0 |
| `exp2_plus_gaps` | 30 | 931 | 1988 | -70 | 68 | 138 |

## Top files each cell reaches that `exp1_full` does not
### `exp2_source`
| file | unique edges |
|---|---:|
| `re2/parse.cc` | 146 |
| `re2/compile.cc` | 26 |
| `re2/re2.cc` | 21 |
| `re2/regexp.cc` | 17 |
| `re2/simplify.cc` | 14 |
| `util/stringpiece.cc` | 9 |
| `util/rune.cc` | 5 |
| `re2/onepass.cc` | 2 |
| `re2/dfa.cc` | 2 |

### `exp2_plus_gaps`
| file | unique edges |
|---|---:|
| `re2/simplify.cc` | 24 |
| `re2/re2.cc` | 21 |
| `re2/parse.cc` | 11 |
| `re2/prog.cc` | 6 |
| `re2/regexp.cc` | 4 |
| `re2/dfa.cc` | 2 |

### `exp1_gaps_only`
| file | unique edges |
|---|---:|
| `re2/parse.cc` | 59 |
| `re2/re2.cc` | 21 |
| `re2/simplify.cc` | 18 |
| `re2/regexp.cc` | 13 |
| `re2/compile.cc` | 7 |
| `re2/prog.cc` | 6 |
| `re2/dfa.cc` | 2 |
| `re2/onepass.cc` | 2 |
| `util/stringpiece.cc` | 2 |
