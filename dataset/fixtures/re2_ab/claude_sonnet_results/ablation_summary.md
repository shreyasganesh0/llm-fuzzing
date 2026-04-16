# N-cell prompt-ablation coverage differential

**Reference cell:** `exp1_full` (edges=1183)

## Headline — one row per cell
| cell | seeds | edges | lines | Δedges vs ref | edges only-in-cell | edges only-in-ref |
|---|---:|---:|---:|---:|---:|---:|
| `exp1_full` | 30 | 1183 | 2408 | +0 | 0 | 0 |
| `exp2_source` | 30 | 1161 | 2326 | -22 | 168 | 190 |
| `exp1_gaps_only` | 30 | 1095 | 2223 | -88 | 62 | 150 |
| `exp2_plus_gaps` | 30 | 1050 | 2153 | -133 | 43 | 176 |

## Top files each cell reaches that `exp1_full` does not
### `exp2_source`
| file | unique edges |
|---|---:|
| `re2/parse.cc` | 89 |
| `re2/onepass.cc` | 24 |
| `re2/compile.cc` | 17 |
| `re2/re2.cc` | 11 |
| `re2/regexp.cc` | 9 |
| `re2/dfa.cc` | 6 |
| `util/stringpiece.cc` | 6 |
| `re2/simplify.cc` | 4 |
| `re2/prog.h` | 1 |
| `re2/prog.cc` | 1 |

### `exp2_plus_gaps`
| file | unique edges |
|---|---:|
| `re2/onepass.cc` | 23 |
| `re2/re2.cc` | 9 |
| `re2/parse.cc` | 7 |
| `re2/simplify.cc` | 3 |
| `re2/prog.cc` | 1 |

### `exp1_gaps_only`
| file | unique edges |
|---|---:|
| `re2/parse.cc` | 34 |
| `re2/dfa.cc` | 11 |
| `re2/prog.cc` | 6 |
| `re2/regexp.cc` | 4 |
| `re2/simplify.cc` | 3 |
| `re2/onepass.cc` | 2 |
| `re2/compile.cc` | 2 |
