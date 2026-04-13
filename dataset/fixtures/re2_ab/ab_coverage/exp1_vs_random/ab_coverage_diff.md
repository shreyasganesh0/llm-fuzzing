# A/B coverage differential — exp1 (gap-targeted) vs exp2 (source-only)

## Setup
- **target**: re2
- **model**: llama-3.1-8b-instruct
- **exp1_seeds**: 20
- **exp2_seeds**: 30

## Headline numbers
| metric | exp1 | exp2 | union | intersection |
|---|---:|---:|---:|---:|
| lines | 2530 | 2081 | 2602 | 2009 |
| edges | 1243 | 980 | 1302 | 921 |

- edges ONLY in exp1: **322**
- edges ONLY in exp2: **59**
- Jaccard (edges): 0.7074
- Jaccard (lines): 0.7721

## Top files exp1 reaches that exp2 misses
| file | unique edges |
|---|---:|
| `re2/parse.cc` | 145 |
| `re2/regexp.cc` | 57 |
| `re2/simplify.cc` | 44 |
| `re2/compile.cc` | 23 |
| `re2/dfa.cc` | 15 |
| `re2/onepass.cc` | 12 |
| `util/stringpiece.cc` | 8 |
| `re2/re2.cc` | 6 |
| `re2/prog.cc` | 6 |
| `util/logging.h` | 2 |
| `util/rune.cc` | 1 |
| `util/stringprintf.cc` | 1 |
| `re2/walker-inl.h` | 1 |
| `util/hash.cc` | 1 |

## Top files exp2 reaches that exp1 misses
| file | unique edges |
|---|---:|
| `re2/parse.cc` | 26 |
| `re2/compile.cc` | 9 |
| `re2/regexp.cc` | 8 |
| `re2/dfa.cc` | 7 |
| `re2/simplify.cc` | 3 |
| `re2/re2.cc` | 3 |
| `re2/onepass.cc` | 1 |
| `re2/prog.h` | 1 |
| `util/rune.cc` | 1 |
