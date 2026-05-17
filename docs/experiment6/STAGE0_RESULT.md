# experiment6 — STAGE 0 RESULT (logprob capability gate)

**Status: PENDING.** Populated after the Stage 0 probe runs.

Gate definition (from the prompt):

| Classification | Condition | Action |
|---|---|---|
| `FULL` | observed logprob **and** top-k alternatives present | proceed to Stage 1 |
| `PARTIAL` | observed logprob only (surprisal computable, not full entropy) | STOP, ask user before any surrogate |
| `NONE` | no logprob fields at all | STOP, surface to user; no fallbacks without approval |

Probe spec: one call to `codestral-22b` through `LLMClient.complete()`
(the existing client path) with `logprobs=True, top_logprobs=20`,
`use_cache=False`. Raw response excerpt + classification table go here.

<!-- results table + raw excerpt appended after the probe -->
