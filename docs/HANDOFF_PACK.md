# Handoff pack — refine the "LLM-guided fuzzing seed corpora" deck

_Self-contained pack for a Claude web / chat instance with no filesystem
access. Everything needed to refine the slide deck in `docs/slides/llm_fuzzing_review.pptx`
is inlined here: project state, experiment writeups, prompt templates,
real LLM outputs, evaluation harness, and the numbers behind every chart._

**Current deck**: 26 slides, built by `core/build_pptx.py`. The deck is
written in code (python-pptx), not a `.pptx`-native editor — so refinement
means editing `core/build_pptx.py` (slide text, layout, tables), then
rerunning the builder. The visual vocabulary is deliberately minimal
(blank layout, text boxes, tables) so it will render in any viewer.

**What a refiner can change freely**
- Slide order, titles, subtitles, body text.
- Table cell contents (as long as they match the numbers below).
- Adding/splitting slides, merging slides, trimming dense ones.
- Bullet framing, phrasing, emphasis.

**What a refiner must NOT change (these are load-bearing facts)**
- Any number in the result tables (edges, lines, seed counts, Δ).
- The +110 / +3 / −15 / +7 / +117 / +263 / −364 headline deltas.
- The prompt-matrix entries (which cells carry which Jinja blocks).
- The harness format: `[2 flag bytes][UTF-8 regex, 1-62 chars]`, total 3-64 bytes.
- The model (`llama-3.1-8b-instruct`), temperature (0.7), top_p (0.95).

---

# 1. Project state (`docs/STATUS.md`)

_Living handoff; last updated 2026-04-13._

## TL;DR
End-to-end pipeline:
1. Build coverage-instrumented RE2, extract upstream tests, compute baseline coverage + gaps.
2. Call an LLM (llama-3.1-8b-instruct via UF LiteLLM proxy) to synthesise seed inputs in two variants — **exp1** (gap-targeted) and **exp2** (source-only).
3. Measure per-seed-corpus coverage on a standalone `seed_replay` binary and emit a set-wise diff.

The 2026-04-13 regex-format A/B shows exp1 wins by **+110 edges in-distribution** on RE2 (1243 vs 1133). Follow-up generalization experiments run the same day show this advantage is **largely fixture-specific**:

- **Random baseline**: 980 edges. Both LLM cells clear the floor.
- **Experiment A — held-out source subset**: restricting exp1's gap list to parser files (set A) and measuring coverage on execution files (set B), `exp1_heldout` (581) *loses* to `exp2_source` (596) by 15 edges; the in-distribution +110 collapses to **+3** on held-out files.
- **Experiment B — prompt ablation (7 cells)**: new leader is `exp2_plus_gaps` (1250 > `exp1_full`=1243). Source code is load-bearing; gaps amplify it; `exp1_gaps_only` collapsed (879, loop aborts).
- **Experiment C (1h × 3-trial libFuzzer campaigns)**: deferred.

## Pipeline
```
Phase 1  ── build_instrumented.sh ─▶ coverage-instrumented RE2 +
           extract_tests.py        ──▶ tests.json
           run_test_coverage.py    ──▶ per_test + union CoverageProfiles
           compute_gaps.py         ──▶ coverage_gaps.json

Phase 2  ── (exp1 only) LLM predicts hard branches from test context

Phase 3  ── generate_inputs.py --experiment exp1 --input-format regex
              └─▶ renders input_synthesis_regex.j2 with gaps + tests
              └─▶ 3 LLM samples, T=0.7, cache-salted per sample
              └─▶ parse_regex_response prepends 2 sha256-seeded flag bytes,
                  clips to [3, 64] bytes, dedupes
              └─▶ seeds/re2/exp1/<model>/seed_<id>.bin

           generate_source_inputs.py --input-format regex
              └─▶ source-only variant; assert_no_tests guards the prompt
              └─▶ seeds/re2/source_only/<model>/seed_<id>.bin

Evaluate  ── seed_replay_main.cc + llvm-profdata + llvm-cov-15
              └─▶ per-cell CoverageProfile JSON
           ab_coverage_diff.py / ablation_diff.py
              └─▶ set-wise line/edge diff, Jaccard, per-file tables
```

## Non-obvious decisions (the deck should not question these)
- `_prompt_hash` includes `max_tokens` + `cache_salt`; multi-sample callers MUST pass `cache_salt=f"sample={k},…"` to avoid cache collisions.
- `LLMClient.complete()` streams with mid-stream loop-abort via `core/loop_detector.py`.
- RE2's `util/test.h` ignores `--gtest_filter` — all registered tests always run; fixture is the full set of built tests.
- RE2 harness is `[2 flag bytes][regex string]`, `3 <= size <= 64`. `--input-format regex` prepends 2 sha256-seeded flag bytes. **Do not switch RE2 back to `--input-format bytes`** — it's the bytes-format that made the earlier A/B misleading.
- Fixture tests are `Regexp.BigRef`, `Regexp.NamedCaptures`, `Regexp.CaptureNames`.
- `seed_replay_main.cc` exists because the libFuzzer harness injects `main()` and collides with gtest's `main()`.
- `synthesis/scripts/build_source_prompt.py::assert_no_tests` is the primary defense for the ablation's validity — do not silence.

---

# 2. A/B writeup (`docs/AB_RE2_REPORT.md`)

## Headline (regex-text synthesis, 2026-04-13)

We rewrote the synthesis prompts to emit **raw regex strings** (tooling prepends 2 deterministic flag bytes to match the RE2 libFuzzer harness layout). Better fit for an RE2 target than the original base64-encoded-bytes format.

| metric | exp1 (gap) | exp2 (source) | union | intersection |
|---|---:|---:|---:|---:|
| seeds produced | 20 | 30 | — | — |
| ok samples / total | 2 / 3 | 3 / 3 | — | — |
| edges covered | **1243** | 1133 | 1308 | 1068 |
| lines covered | **2530** | 2385 | 2661 | 2254 |
| edges ONLY in this cell | **175** | 65 | — | — |
| Jaccard (edges) | — | — | 0.817 | — |

**Exp1 wins decisively in-distribution** (+110 edges, 175 vs 65 exclusive). Bulk of exp1's exclusive edges land in `re2/parse.cc` (+86), `re2/regexp.cc` (+50), `re2/simplify.cc` (+14). Loop-abort rate dropped 5/6 (bytes) → 1/6 (regex).

## Archived bytes-format run (2026-04-12)

Original result flipped the other way — exp2 won. The flip was a format-mismatch artefact: llama couldn't produce `[flag][pattern]` bytes by luck at small scale. Kept here as the "why the rewrite mattered" story.

| metric | exp1 | exp2 | union | intersection |
|---|---:|---:|---:|---:|
| seeds produced | 5 | 10 | — | — |
| loop-aborted samples | 3 / 3 | 2 / 3 | — | — |
| edges covered | 475 | **513** | 530 | 458 |
| lines covered | 1243 | **1314** | 1337 | 1220 |
| edges ONLY in this cell | 17 | 55 | — | — |
| Jaccard (edges) | — | — | 0.864 | — |

---

# 3. Three-way baseline summary (`three_way_summary.md`)

30 seeds of `[2 random flag bytes][random ASCII 1-62]`, `random.Random(42)`, via `synthesis/scripts/generate_random_inputs.py --input-format regex`.

| cell | seeds | edges | lines | edges over random |
|---|---:|---:|---:|---:|
| exp1 (gap-targeted) | 20 | **1243** | 2530 | **+263** |
| exp2 (source-only)  | 30 | **1133** | 2385 | **+153** |
| random baseline     | 30 |   980 | 2081 |   (floor) |

- exp1 beats random by 263 edges (+27%)
- exp2 beats random by 153 edges (+16%)
- exp1's lead over exp2 (+110 edges) is ~1.7× its own "exp2-would-be-there-anyway" component.

Top files exp1 reaches that random misses:
`re2/parse.cc` (+145), `re2/regexp.cc` (+57), `re2/simplify.cc` (+44), `re2/compile.cc` (+23).

Top files exp2 reaches that random misses:
`re2/parse.cc` (+82), `re2/simplify.cc` (+49), `re2/compile.cc` (+27), `re2/onepass.cc` (+14).

Both LLM variants concentrate their edge over random in the parser/simplifier/compiler.

---

# 4. Experiment A — held-out source subset (`heldout_summary.md`)

## Setup
- **Set A (visible to gap prompt)** — parser/simplifier: `re2/parse.cc`, `re2/regexp.cc`, `re2/simplify.cc`, `re2/tostring.cc`. Gap list for `exp1_heldout` filtered to these files (611/2042 gap branches retained).
- **Set B (held out, coverage measured)** — execution + utility: `re2/compile.cc`, `re2/prog.cc`, `re2/dfa.cc`, `re2/nfa.cc`, `re2/onepass.cc`, `re2/bitstate.cc`, `re2/re2.cc`, `util/rune.cc`, `util/strutil.cc`.
- `tests.json` and fixture metadata unchanged — `exp1_heldout` still sees the same 5 few-shot test examples. Only the gap list is restricted.
- `exp2_source` prompt is unchanged — it always sees all source files via call-graph priority; the "no partitioning" baseline.

## Headline — coverage restricted to set B only

| cell | seeds | B-edges | B-lines | Δ vs random |
|---|---:|---:|---:|---:|
| **exp1_full**    | 20 | **599** | 1533 | +42 |
| **exp2_source**  | 30 | **596** | 1540 | +39 |
| exp1_heldout     | 30 |  581    | 1502 | +24 |
| random baseline  | 30 |  557    | 1436 |  0  |

## Decision rules
- `exp1_full vs exp2_source on B`: +3 edges (within ±20) → **hypothesis supported**.
- `exp1_heldout vs exp2_source on B`: **−15 edges (exp2 leads)** → **hypothesis strongly supported**.

## Interpretation
exp1_full's big in-distribution advantage over exp2 (+110 edges on full source) collapses to **+3 edges** on held-out files. Most of exp1's +110 is "the gap list is a flashlight pointed at specific files," not general regex-synthesis skill. When exp1's gap list is restricted to a *disjoint* set of files (set A) and then measured on set B — files exp1 has never been pointed at — exp1 loses to exp2 (−15 edges).

Per-file confirmation:
- `exp1_full_on_B` vs `exp2_source_on_B`: exp1_full wins +9 edges in `re2/compile.cc`, +6 in `re2/prog.cc` — files the full gap list pointed at.
- `exp1_heldout_on_B` vs `exp2_source_on_B`: exp1_heldout wins a few edges in `util/rune.cc` + `re2/re2.cc` but loses net. When no compile/prog gaps are in its prompt, exp1 stops producing inputs that exercise those files.

## Confounds
- `exp1_heldout` still sees 5 few-shot test examples, which reference RE2 APIs that implicitly exercise set-B files — probably why it still beats random (+24) rather than matching it.
- Per-cell seed counts differ (`exp1_full`=20 vs `exp1_heldout`=30 vs `exp2`=30). `exp1_full` gets fewer seeds but more in-distribution edges per seed.
- n≥20 per cell shows direction; rigorous p-value would need n≥30 per cell AND multiple random A/B splits to rule out file-subset luck.

---

# 5. Experiment B — prompt ablation (`ablation_summary.md`)

## Headline

| cell | gaps? | tests? | source? | seeds | **edges** | Δ vs exp1_full |
|---|:-:|:-:|:-:|---:|---:|---:|
| **exp2_plus_gaps** | ✅ | ❌ | ✅ | 30 | **1250** | **+7** |
| exp1_full         | ✅ | ✅ | ❌ | 20 |  1243 |  0 |
| exp2_plus_tests   | ❌ | ✅ | ✅ | 30 |  1210 | −33 |
| exp2_source       | ❌ | ❌ | ✅ | 30 |  1133 | −110 |
| exp1_tests_only   | ❌ | ✅ | ❌ | 30 |  1093 | −150 |
| random            | — | — | — | 30 |   980 | −263 |
| exp1_gaps_only*   | ✅ | ❌ | ❌ | 10 |   879 | −364 |

*`exp1_gaps_only` produced only 10 seeds — loop detector aborted 2/3 samples. Dense gap list without tests or source triggers llama's degenerate repetition mode. A data point about the prompt itself, not a bug.

## Decision rules
- "If `exp1_gaps_only` ≈ `exp1_full`, the win is from the gaps." — **NOT supported.** exp1_gaps_only falls 364 edges below exp1_full.
- "If `exp2_plus_gaps` > `exp1_full`, gaps stack with source." — **Supported.** +7 edges; 142 "exp2_plus_gaps only" edges not reached by exp1_full.
- "If `exp2_plus_tests` ≥ `exp1_full`, tests are the generalizing piece." — **NOT supported.** exp2_plus_tests is 33 edges below.

## What this says
1. **Source code is the best "information carrier."** Every source-carrying cell ≥1133 edges; source-less cells span 879–1243 (dominated by loop issues).
2. **Gaps amplify source.** `exp2_plus_gaps` beats `exp2_source` by +117 edges — the largest single-variable effect in the table.
3. **Tests alone ≈ source alone.** `exp1_tests_only` (1093) ≈ `exp2_source` (1133) — 5 test examples are worth roughly the same as 14k tokens of source for this fixture.
4. **`exp1_gaps_only` collapse is model-specific** evidence for the hypothesis direction: strip source, force gap-by-gap enumeration, llama derails.

## Follow-ups deferred
- Rerun `exp1_gaps_only` with `--samples 6` to get n≈20–30 past the loop-abort rate.
- Rerun `exp2_plus_gaps` with `--samples 6` (n≈60) to test whether +7 is noise.

---

# 6. Combined verdict (from `AB_RE2_REPORT.md`)

The user's hypothesis — "exp2 generalizes better than exp1" — is **supported with nuance**:
- On the same files the gap list points at, exp1 wins decisively (+110).
- On held-out files within the same target, exp1's advantage vanishes (+3 when it still sees the full prompt, −15 when it doesn't).
- The most efficient single recipe on this fixture is `exp2_plus_gaps` (source code + coverage gaps, no tests) — +7 over pure exp1, +117 over pure exp2.

Clean take: _the right baseline is not exp1 or exp2, it's source + cheap coverage annotations; exp1's +110 was mostly the annotation doing work exp2's source already covered, and on held-out files the annotation stops generalizing._

---

# 7. Prompt templates (Jinja2)

## 7.1 `synthesis/prompts/input_synthesis_regex.j2` — exp1 (gap-targeted)

```jinja
{{ system_prompt }}

You are generating REGEX PATTERNS to exercise uncovered branches in the
{{ target_name }} regular-expression library. {{ target_name }} is a C++
regex engine; the hard-to-reach code lives in the PARSER and COMPILER
(not the matcher), so novel regex syntax is more valuable than exotic
input strings.

=== HARNESS FORMAT (READ THIS CAREFULLY) ===
The libFuzzer harness consumes `[2 flag bytes][regex string bytes]` and
invokes RE2::Compile on the regex. The tooling will prepend random flag
bytes for you.

**YOUR JOB**: produce the REGEX STRING ONLY, as plain text UTF-8.
**DO NOT** base64-encode. **DO NOT** escape. **DO NOT** wrap in quotes
inside the regex field. Just the pattern itself.

Harness source:
```{{ source_language }}
{{ harness_code }}
```

=== WHAT THE UPSTREAM TESTS LOOK LIKE ===
{% for example in few_shot_examples %}
From {{ example.upstream_file }}:{{ example.upstream_line }}:
{{ example.test_code }}
{% endfor %}

=== UNCOVERED BRANCHES ===
The upstream test suite ({{ total_upstream_tests }} tests) achieves
{{ union_coverage_pct }}% branch coverage. These branches are NOT
reached by any existing test — your regex patterns should try to hit
them:

{% for gap in coverage_gaps[:max_gaps] %}
Gap {{ loop.index }}: {{ gap.file }}:{{ gap.line }}
Code:
```{{ source_language }}
{{ gap.code_context }}
```
Hint: {{ gap.condition_description }}

{% endfor %}

=== YOUR TASK ===
Produce {{ num_inputs }} DISTINCT regex patterns likely to hit one or
more of the gap branches above. Keep each pattern 1-60 characters (the
harness rejects anything totaling > 64 bytes).

Examples of the kind of patterns that stress the parser/compiler:
  - Named captures:         (?P<x>a+)
  - Nested repetitions:     (a*)*
  - Unicode classes:        \p{Greek}+
  - Lookarounds (rejected): (?=abc)
  - Large counted reps:     a{1000,}
  - Character classes:      [^a-zA-Z0-9]

OUTPUT RULES — violations cause parse failure:
- Emit EXACTLY ONE JSON object. Stop after the closing `}`.
- Do NOT wrap in markdown code fences.
- `regexes` must contain AT MOST {{ num_inputs }} entries.
- Every `regex` must be UNIQUE.
- `regex` is plain text, NOT base64, NOT escaped JSON — just the raw
  pattern. If your pattern contains `"` or `\`, escape them using
  standard JSON string-escape rules.
- `target_gaps` entries must be real `path:integer` pairs from the list above.

Schema:
{
  "regexes": [
    {
      "regex": "<raw pattern text, 1-60 chars>",
      "target_gaps": ["<file>:<line>", "..."],
      "reasoning": "<why this regex hits those branches>"
    }
  ]
}
```

## 7.2 `synthesis/prompts/source_only_synthesis_regex.j2` — exp2

```jinja
{{ system_prompt_source_only }}

You are generating REGEX PATTERNS to maximize code coverage in the
{{ target_name }} regular-expression library. {{ target_name }} is a C++
regex engine; the hard-to-reach code lives in the PARSER and COMPILER,
so novel regex syntax is more valuable than exotic input strings.

You have ONLY the source code and the fuzzer harness — no existing test
suite, no coverage data.

=== HARNESS FORMAT (READ THIS CAREFULLY) ===
[identical to exp1]

TARGET: {{ target_name }}

[FUZZER HARNESS]
{{ harness_code }}

[LIBRARY SOURCE CODE]
{% for file in source_files %}
=== {{ file.path }} ===
{{ file.content }}

{% endfor %}

=== YOUR TASK ===
Study the source. Identify code paths that require specific, structured
regex syntax to reach — paths a random-mutation fuzzer would struggle
with.

Produce {{ num_inputs }} DISTINCT regex patterns that collectively
exercise as many DIFFERENT code paths as possible. Prioritize DEPTH
(hard-to-reach branches) over BREADTH (shallow branches the fuzzer
hits on its own).

Keep each pattern 1-60 characters (the harness rejects total input
size > 64 bytes).

[same examples + OUTPUT RULES + JSON schema as exp1]
```

Invocation constants: `--source-max-files 4`, `--source-token-budget 14000`, `--max-tokens 8192`. Files picked by call-graph priority; `assert_no_tests` guard rejects the prompt if a test sneaks in.

## 7.3 `synthesis/prompts/ablation_synthesis_regex.j2` — Experiment B

Single template with Jinja toggles `include_tests`, `include_gaps`, `include_source`. Core skeleton:

```jinja
{{ system_prompt }}
[harness format block]

{% if include_tests and few_shot_examples %}
=== WHAT THE UPSTREAM TESTS LOOK LIKE ===
{% for example in few_shot_examples %} ... {% endfor %}
{% endif %}

{% if include_gaps and coverage_gaps %}
=== UNCOVERED BRANCHES ===
{% for gap in coverage_gaps[:max_gaps] %} ... {% endfor %}
{% endif %}

{% if include_source and source_files %}
[LIBRARY SOURCE CODE]
{% for file in source_files %} ... {% endfor %}
{% endif %}

=== YOUR TASK ===
[identical output rules and schema as exp1]
```

The 7 cells in Experiment B correspond to the truth table over {include_gaps, include_tests, include_source} — see matrix in §5.

---

# 8. Example LLM output (real, not synthetic)

_Actual `sample_0.json` from `exp1` run, 2026-04-13. The LLM's raw text
is the `raw_response` field; the parser decodes each regex and emits one
`[2 flag bytes][UTF-8 regex]` seed file per entry._

## 8.1 Raw response (what the model emitted)

```json
{
  "regexes": [
    { "regex": "\\u{1234}+",          "target_gaps": ["re2/bitstate.cc:112","re2/bitstate.cc:121","re2/bitstate.cc:122","re2/bitstate.cc:126","re2/bitstate.cc:131"], "reasoning": "Requires Unicode escape sequence, likely to cause stack growth" },
    { "regex": "(a*)*",                "target_gaps": [...same...], "reasoning": "Nested repetitions may cause stack overflow" },
    { "regex": "[^a-zA-Z0-9]+",        "target_gaps": [...], "reasoning": "Large character class may cause stack growth" },
    { "regex": "(?P<g1>a+)",           "target_gaps": [...], "reasoning": "Named captures may cause stack growth" },
    { "regex": "a{1000,}",             "target_gaps": [...], "reasoning": "Large counted repetition may cause stack overflow" },
    { "regex": "(a|b)*",               "target_gaps": [...], "reasoning": "Nested alternations may cause stack overflow" },
    { "regex": "[a-z]+",               "target_gaps": [...], "reasoning": "Large character class may cause stack growth" },
    { "regex": "\\w+",                 "target_gaps": [...], "reasoning": "Unicode word character may cause stack growth" },
    { "regex": "(?P<g1>a+)(?P<g2>b+)", "target_gaps": [...], "reasoning": "Named captures and repetitions may cause stack growth" },
    { "regex": "(?:a|b)*",             "target_gaps": [...], "reasoning": "Nested non-capturing alternations may cause stack overflow" }
  ]
}
```

Metadata: `input_tokens=5714, output_tokens=822, cost_usd=$0.00144, latency=4.01s`. `sample_index=0` with `cache_salt="sample=0,experiment=exp1"`.

## 8.2 Decoded seed files (what reaches `seed_replay`)

Each regex becomes one `seed_<id>.bin` = `[2 sha256-seeded flag bytes][UTF-8 regex bytes]`:

```
seed_b6c1f73022b790c0.bin : 6D 29  \u{1234}+            (11 bytes)
seed_bd187954ddfab81d.bin : 5A 2A  (a*)*                ( 7 bytes)
seed_12c05165bfe03b31.bin : F4 E2  [^a-zA-Z0-9]+        (15 bytes)
seed_b3e5b41c2fa89da7.bin : DC E3  (?P<g1>a+)           (12 bytes)
seed_1de0eb76fb0b72ac.bin : 05 4C  a{1000,}             (10 bytes)
seed_681b5e15a0e60a45.bin : 8E 87  (a|b)*               ( 8 bytes)
seed_b44a0d75a9fa8166.bin : C5 9A  [a-z]+               ( 8 bytes)
seed_3a865600a776902f.bin : 7F A2  \w+                  ( 5 bytes)
seed_06196b853975ac2e.bin : 53 3F  (?P<g1>a+)(?P<g2>b+) (22 bytes)
seed_8d2c4d57d0cae412.bin : 3C F0  (?:a|b)*             (10 bytes)
```

Flag bytes come from `sha256(target, sample_idx, regex_idx, regex)[:2]` — deterministic and reproducible.

## 8.3 Decoded exp2 seeds (for contrast)

Real decoded seeds from `exp2_results/seeds/re2/source_only/llama-3.1-8b-instruct/`:

```
seed_021724833b98af00.bin : 4A 7A  a{1000,}
seed_0383cea471d4df0c.bin : 31 B3  \p{Greek}+(?P<x>a+)
seed_077ffed0890bf854.bin : C3 6B  a(?P<x>\d+)
seed_0b38b06005673219.bin : 68 88  [a-zA-Z0-9]+(?:\s*[a-zA-Z0-9]+)*
seed_2113a3d3a78bd4b8.bin : 0B C5  \p{Greek}+
seed_284081bc51941b6a.bin : 19 6A  (a*)*(b*)*(c*)
seed_2e0ff41bd1a964df.bin : 1F 40  (?<=abc)a
```

Note exp2's patterns are qualitatively similar (named captures, Unicode classes, nested reps) despite having no coverage-gap hints — the source code leaks the same stylistic targets.

---

# 9. Coverage harness (`dataset/targets/src/re2/harness/seed_replay_main.cc`)

Why it exists: libFuzzer injects `main()` and collides with gtest's `main()`. Needed a standalone driver that reads a byte file and calls `LLVMFuzzerTestOneInput` once.

```cpp
// Standalone driver that invokes the libFuzzer entry point once per seed file.
// Used by synthesis/scripts/measure_coverage.py to replay a seed corpus
// under coverage instrumentation without pulling in libFuzzer itself.
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iterator>
#include <vector>

extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size);

int main(int argc, char **argv) {
  if (argc < 2) {
    std::fprintf(stderr, "usage: %s <seed_file>\n", argv[0]);
    return 1;
  }
  std::ifstream in(argv[1], std::ios::binary);
  if (!in) {
    std::fprintf(stderr, "cannot open %s\n", argv[1]);
    return 2;
  }
  std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(in)),
                             std::istreambuf_iterator<char>());
  LLVMFuzzerTestOneInput(bytes.data(), bytes.size());
  return 0;
}
```

The actual RE2 libFuzzer harness (in RE2's `re2/fuzzing/re2_fuzzer.cc`, simplified for the deck):

```cpp
extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size < 3 || size > 64) return 0;
    uint8_t flag_bytes[2] = { data[0], data[1] };
    std::string pattern(data + 2, data + size);
    RE2::Options opt;
    // flag bytes toggle case/utf8/longest_match/etc. on opt
    apply_flags(opt, flag_bytes);
    RE2 re(pattern, opt);
    if (re.ok()) { std::string s; RE2::FullMatch("abc", re, &s); }
    return 0;
}
```

This is the structure every seed must satisfy: 2 flag bytes + 1-62 regex bytes.

---

# 10. Example coverage-gap entry (what `exp1` feeds into the prompt)

From `dataset/fixtures/re2_ab/re2/coverage_gaps.json` — first gap, shown as rendered into the prompt:

```
Gap 1: re2/bitstate.cc:112
Code:
  // Grow the stack.
  bool BitState::GrowStack() {
    maxjob_ *= 2;
    Job* newjob = new Job[maxjob_];
    memmove(newjob, job_, njob_*sizeof job_[0]);
    delete[] job_;
    job_ = newjob;
    if (njob_ >= maxjob_) {
      LOG(DFATAL) << "Job stack overflow.";
      return false;
    }
    return true;
  }
Hint: Requires the following to evaluate toward the uncovered branch:
      if (njob_ >= maxjob_) { …
```

Exp1 feeds up to `max_gaps` such entries, ordered by reachability score. The prompt contains 5 such entries by default, which is why the 10 seeds in §8 all reference the same 5 gap-line citations — the model picks up the citation list and re-emits it.

---

# 11. Slide deck structure (26 slides)

Current slide titles, in order (built by `core/build_pptx.py`):

```
 1  LLM-Guided Fuzzing Seed Corpora                         (title)
 2  The research question
 3  Pipeline shape (exp1 and exp2 share everything but the prompt)
 4  Data flow — one synthesis sample, end to end
 5  Recap — the 2026-04-12 stabilization pass
 6  First A/B surfaced five more bugs
 7  First A/B result — bytes-format prompt (2026-04-12)
 8  Why bytes-format was a bad fit
 9  Fix — rewrite prompts to emit regex text (2026-04-13)
10  Exp1 prompt in practice — what the LLM sees (abridged)
11  Exp2 prompt in practice — what the LLM sees (abridged)
12  What the LLM returns, and what becomes a seed
13  Second A/B result — regex-format prompt (2026-04-13)
14  Bytes vs regex — what the rewrite actually changed
15  Where exp1 wins uniquely in the regex run
16  The takeaway
17  Generalization follow-up — is exp1's +110 transferable?
18  P0 — Random baseline establishes the floor
19  Experiment A — held-out source-file subset
20  Experiment B — prompt matrix (what each cell actually shows the model)
21  Experiment B — 7-cell prompt ablation
22  Verdict — hypothesis supported, with a sharper recipe
23  What this does NOT prove
24  Reproducibility — the 2026-04-13 regex A/B
25  Decision asks
26  Backing material
```

**Known rough edges in the current deck** (fair game to fix):
- Slide 4 (data flow) is a text-only pseudo-diagram; could become a real box-and-arrow diagram.
- Slide 12 has long decoded-byte lines that wrap awkwardly at 12pt — consider splitting into "raw JSON" + "seed files" as two slides.
- Slides 10/11 show abridged prompts; a skeptical reviewer might want a single slide with the prompt *matrix* across exp1/exp2/ablation side-by-side.
- Slide 21's 7-row table at 14pt is cramped; 13pt helper text below further cramps it.
- Slide 18 stacks a paragraph + a hex-preview + a table + a conclusion paragraph — probably 2 slides worth.
- No single slide shows "what actually changed" vs `exp1_full` as a delta chart; it's all tables.
- "Decision asks" (slide 25) post-dates the generalization experiments; worth re-ordering before "backing material" and tightening.

---

# 12. Numbers that must not drift

Sanity-check these against any refined slide's table before shipping:

| context | cell | seeds | edges | lines |
|---|---|---:|---:|---:|
| 2026-04-12 bytes | exp1 | 5 | 475 | 1243 |
| 2026-04-12 bytes | exp2 | 10 | 513 | 1314 |
| 2026-04-13 regex (headline) | exp1 | 20 | 1243 | 2530 |
| 2026-04-13 regex (headline) | exp2 | 30 | 1133 | 2385 |
| P0 random baseline | random | 30 | 980 | 2081 |
| Exp A on set B | exp1_full | 20 | 599 | 1533 |
| Exp A on set B | exp2_source | 30 | 596 | 1540 |
| Exp A on set B | exp1_heldout | 30 | 581 | 1502 |
| Exp A on set B | random | 30 | 557 | 1436 |
| Exp B | exp2_plus_gaps | 30 | 1250 | — |
| Exp B | exp1_full | 20 | 1243 | — |
| Exp B | exp2_plus_tests | 30 | 1210 | — |
| Exp B | exp2_source | 30 | 1133 | — |
| Exp B | exp1_tests_only | 30 | 1093 | — |
| Exp B | random | 30 |  980 | — |
| Exp B | exp1_gaps_only | 10 |  879 | — |

Unique-edge breakdown (regex A/B headline, exp1 vs exp2 on full source):

| file | exp1 unique | exp2 unique |
|---|---:|---:|
| re2/parse.cc   | 86 | — |
| re2/regexp.cc  | 50 | — |
| re2/simplify.cc| 14 | — |
| re2/compile.cc |  9 | — |
| re2/prog.cc    |  6 | — |
| re2/re2.cc     |  3 | — |
| re2/dfa.cc     |  3 | — |
| util/logging.h |  2 | — |

---

# 13. Open questions the deck should flag, not answer
- Does `exp2_plus_gaps`'s +7 lead over `exp1_full` hold at n≈60? (not yet measured)
- Does `exp1_gaps_only` still collapse on a frontier model (GPT-4o / Claude Sonnet)? Strongest likely confound — 2/3 samples aborted is model-specific.
- Does the parser-vs-execution split in Experiment A replicate on a random A/B split (not picked by the experimenter)?
- Does any of this transfer to a second target? sqlite (SQL idiom) and libpng (binary-header idiom) are both open — neither built.
- Would a 1h × 3-trial libFuzzer campaign from each seed set show exp1's lead widen or close? (Experiment C, deferred.)

---

# 14. License to refine — suggested rewrite angles

Pick any of these and rewrite the deck around them; each is a legitimate framing a refiner could adopt. The data supports all of them:

1. **"Format-matching is the main result, generalization is the corollary."** The bytes→regex rewrite is the single biggest lift (1.7×+ coverage, 5/6→1/6 loop rate). The generalization experiments are a sanity check on a secondary claim.
2. **"The efficient frontier is source + gaps, not gaps + tests."** Lead with the ablation; the bytes/regex story becomes a prerequisite caveat.
3. **"In-distribution wins can mislead; we caught it this time."** The held-out result is the lede; everything else is evidence.
4. **"A small-model result that will not hold for frontier models."** Center the llama-specific loop-abort evidence; frame as "we measured the small-model boundary condition."

Whatever framing — the numbers in §12 are invariant.
