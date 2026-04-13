# A/B coverage differential — exp1 (gap-targeted) vs exp2 (source-only)

## Setup
- **target**: re2
- **model**: llama-3.1-8b-instruct
- **exp1_seeds**: 30
- **exp2_seeds**: 30

## Headline numbers
| metric | exp1 | exp2 | union | intersection |
|---|---:|---:|---:|---:|
| lines | 2385 | 2081 | 2438 | 2028 |
| edges | 1133 | 980 | 1195 | 918 |

- edges ONLY in exp1: **215**
- edges ONLY in exp2: **62**
- Jaccard (edges): 0.7682
- Jaccard (lines): 0.8318

## Top files exp1 reaches that exp2 misses
| file | unique edges |
|---|---:|
| `re2/parse.cc` | 82 |
| `re2/simplify.cc` | 49 |
| `re2/compile.cc` | 27 |
| `re2/onepass.cc` | 14 |
| `re2/dfa.cc` | 13 |
| `re2/regexp.cc` | 10 |
| `util/stringpiece.cc` | 8 |
| `re2/prog.cc` | 6 |
| `re2/re2.cc` | 4 |
| `re2/walker-inl.h` | 1 |
| `util/rune.cc` | 1 |

## Top files exp2 reaches that exp1 misses
| file | unique edges |
|---|---:|
| `re2/parse.cc` | 29 |
| `re2/compile.cc` | 9 |
| `re2/dfa.cc` | 7 |
| `re2/prog.cc` | 6 |
| `re2/regexp.cc` | 4 |
| `re2/re2.cc` | 3 |
| `re2/simplify.cc` | 3 |
| `util/rune.cc` | 1 |
