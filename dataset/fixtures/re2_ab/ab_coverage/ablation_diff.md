# N-cell prompt-ablation coverage differential

**Reference cell:** `exp1_full` (edges=1243)

## Headline — one row per cell
| cell | seeds | edges | lines | Δedges vs ref | edges only-in-cell | edges only-in-ref |
|---|---:|---:|---:|---:|---:|---:|
| `exp2_plus_gaps` | 30 | 1250 | 2626 | +7 | 142 | 135 |
| `exp1_full` | 20 | 1243 | 2530 | +0 | 0 | 0 |
| `exp2_plus_tests` | 30 | 1210 | 2524 | -33 | 39 | 72 |
| `exp2_source` | 30 | 1133 | 2385 | -110 | 65 | 175 |
| `exp1_tests_only` | 30 | 1093 | 2293 | -150 | 35 | 185 |
| `random` | 30 | 980 | 2081 | -263 | 59 | 322 |
| `exp1_gaps_only` | 10 | 879 | 1979 | -364 | 8 | 372 |

## Top files each cell reaches that `exp1_full` does not
### `exp2_source`
| file | unique edges |
|---|---:|
| `re2/parse.cc` | 20 |
| `re2/simplify.cc` | 19 |
| `re2/compile.cc` | 13 |
| `re2/regexp.cc` | 7 |
| `re2/onepass.cc` | 3 |
| `re2/dfa.cc` | 1 |
| `re2/re2.cc` | 1 |
| `re2/prog.h` | 1 |

### `exp1_gaps_only`
| file | unique edges |
|---|---:|
| `re2/regexp.cc` | 4 |
| `re2/onepass.cc` | 3 |
| `re2/dfa.cc` | 1 |

### `exp1_tests_only`
| file | unique edges |
|---|---:|
| `re2/parse.cc` | 18 |
| `re2/regexp.cc` | 6 |
| `re2/compile.cc` | 5 |
| `re2/simplify.cc` | 2 |
| `re2/onepass.cc` | 2 |
| `re2/dfa.cc` | 1 |
| `re2/re2.cc` | 1 |

### `exp2_plus_gaps`
| file | unique edges |
|---|---:|
| `re2/nfa.cc` | 89 |
| `re2/parse.cc` | 13 |
| `re2/simplify.cc` | 10 |
| `util/sparse_array.h` | 10 |
| `re2/re2.cc` | 7 |
| `re2/compile.cc` | 5 |
| `re2/regexp.cc` | 4 |
| `re2/onepass.cc` | 3 |
| `re2/dfa.cc` | 1 |

### `exp2_plus_tests`
| file | unique edges |
|---|---:|
| `re2/simplify.cc` | 16 |
| `re2/parse.cc` | 12 |
| `re2/regexp.cc` | 5 |
| `re2/onepass.cc` | 3 |
| `re2/dfa.cc` | 1 |
| `re2/re2.cc` | 1 |
| `re2/compile.cc` | 1 |

### `random`
| file | unique edges |
|---|---:|
| `re2/parse.cc` | 26 |
| `re2/compile.cc` | 9 |
| `re2/regexp.cc` | 8 |
| `re2/dfa.cc` | 7 |
| `re2/simplify.cc` | 3 |
| `re2/re2.cc` | 3 |
| `re2/onepass.cc` | 1 |
| `util/rune.cc` | 1 |
| `re2/prog.h` | 1 |
