# N-cell prompt-ablation coverage differential

**Reference cell:** `exp1_full` (edges=1098)

## Headline — one row per cell
| cell | seeds | edges | lines | Δedges vs ref | edges only-in-cell | edges only-in-ref |
|---|---:|---:|---:|---:|---:|---:|
| `exp1_gaps_only` | 51 | 1312 | 2738 | +214 | 223 | 9 |
| `exp2_source` | 60 | 1257 | 2579 | +159 | 173 | 14 |
| `exp2_plus_gaps` | 60 | 1240 | 2532 | +142 | 161 | 19 |
| `exp1_full` | 30 | 1098 | 2308 | +0 | 0 | 0 |

## Top files each cell reaches that `exp1_full` does not
### `exp2_source`
| file | unique edges |
|---|---:|
| `re2/parse.cc` | 66 |
| `re2/regexp.cc` | 50 |
| `re2/simplify.cc` | 19 |
| `re2/compile.cc` | 15 |
| `re2/prog.cc` | 13 |
| `re2/onepass.cc` | 7 |
| `re2/re2.cc` | 2 |
| `util/hash.cc` | 1 |

### `exp2_plus_gaps`
| file | unique edges |
|---|---:|
| `re2/parse.cc` | 65 |
| `re2/compile.cc` | 30 |
| `re2/simplify.cc` | 22 |
| `re2/prog.cc` | 19 |
| `re2/onepass.cc` | 10 |
| `re2/regexp.cc` | 6 |
| `re2/dfa.cc` | 4 |
| `re2/re2.cc` | 3 |
| `re2/prog.h` | 1 |
| `util/hash.cc` | 1 |

### `exp1_gaps_only`
| file | unique edges |
|---|---:|
| `re2/nfa.cc` | 89 |
| `re2/parse.cc` | 42 |
| `re2/simplify.cc` | 23 |
| `re2/compile.cc` | 17 |
| `re2/prog.cc` | 15 |
| `re2/re2.cc` | 10 |
| `util/sparse_array.h` | 10 |
| `re2/onepass.cc` | 7 |
| `re2/regexp.cc` | 4 |
| `re2/dfa.cc` | 3 |
