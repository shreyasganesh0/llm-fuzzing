# N-cell prompt-ablation coverage differential

**Reference cell:** `exp2_source_on_B` (edges=596)

## Headline — one row per cell
| cell | seeds | edges | lines | Δedges vs ref | edges only-in-cell | edges only-in-ref |
|---|---:|---:|---:|---:|---:|---:|
| `exp1_full_on_B` | 20 | 599 | 1240 | +3 | 21 | 18 |
| `exp2_source_on_B` | 30 | 596 | 1239 | +0 | 0 | 0 |
| `exp1_heldout_on_B` | 30 | 581 | 1237 | -15 | 16 | 31 |
| `random_on_B` | 30 | 557 | 1162 | -39 | 26 | 65 |

## Top files each cell reaches that `exp2_source_on_B` does not
### `exp1_full_on_B`
| file | unique edges |
|---|---:|
| `re2/compile.cc` | 9 |
| `re2/prog.cc` | 6 |
| `re2/dfa.cc` | 3 |
| `re2/re2.cc` | 3 |

### `exp1_heldout_on_B`
| file | unique edges |
|---|---:|
| `re2/compile.cc` | 10 |
| `re2/prog.cc` | 6 |

### `random_on_B`
| file | unique edges |
|---|---:|
| `re2/compile.cc` | 9 |
| `re2/dfa.cc` | 7 |
| `re2/prog.cc` | 6 |
| `re2/re2.cc` | 3 |
| `util/rune.cc` | 1 |
