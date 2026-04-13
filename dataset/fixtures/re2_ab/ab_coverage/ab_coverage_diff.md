# A/B coverage differential — exp1 (gap-targeted) vs exp2 (source-only)

## Setup
- **target**: re2
- **model**: llama-3.1-8b-instruct
- **exp1_seeds**: 20
- **exp2_seeds**: 30

## Headline numbers
| metric | exp1 | exp2 | union | intersection |
|---|---:|---:|---:|---:|
| lines | 2530 | 2385 | 2661 | 2254 |
| edges | 1243 | 1133 | 1308 | 1068 |

- edges ONLY in exp1: **175**
- edges ONLY in exp2: **65**
- Jaccard (edges): 0.8165
- Jaccard (lines): 0.847

## Top files exp1 reaches that exp2 misses
| file | unique edges |
|---|---:|
| `re2/parse.cc` | 86 |
| `re2/regexp.cc` | 50 |
| `re2/simplify.cc` | 14 |
| `re2/compile.cc` | 9 |
| `re2/prog.cc` | 6 |
| `re2/re2.cc` | 3 |
| `re2/dfa.cc` | 3 |
| `util/logging.h` | 2 |
| `util/stringprintf.cc` | 1 |
| `util/hash.cc` | 1 |

## Top files exp2 reaches that exp1 misses
| file | unique edges |
|---|---:|
| `re2/parse.cc` | 20 |
| `re2/simplify.cc` | 19 |
| `re2/compile.cc` | 13 |
| `re2/regexp.cc` | 7 |
| `re2/onepass.cc` | 3 |
| `re2/re2.cc` | 1 |
| `re2/dfa.cc` | 1 |
| `re2/prog.h` | 1 |
