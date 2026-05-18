# experiment4 — STAGE 0 RESULT (logprob capability gate)

**Status: COMPLETE. Classification = `FULL`. Gate CLEARED → Stage 1 proceeds.**

Probe: one call to `codestral-22b` through the existing
`LLMClient.complete()` path with `logprobs=True, top_logprobs=20`,
`use_cache=False`, on the UF LiteLLM proxy `https://api.ai.it.ufl.edu`.
Driver: `analysis/scripts/experiment4_stage0_probe.py`.
Artifact: `results/experiment4/stage0_probe.json`.
Run at commit `a5bc4c61f8fe2b9d94031d10b3b4c8144861d7ba`, 2026-05-17.

## Classification table

| Model probed | Provider | Observed logprob | Top-k alternatives | Classification |
|---|---|---|---|---|
| `codestral-22b` | UF LiteLLM proxy | yes — all 6/6 tokens | yes — 20/20 per token (requested K=20) | **FULL** |

Gate logic: `FULL` ⇒ proceed to Stage 1. (`PARTIAL` ⇒ stop & ask;
`NONE`/`ERROR` ⇒ stop & surface. Neither applies.)

## Evidence (from `results/experiment4/stage0_probe.json`)

```json
{
  "n_tokens": 6,
  "n_tokens_with_observed_logprob": 6,
  "n_tokens_with_top_k_alternatives": 6,
  "max_top_k_alternatives_seen": 20,
  "top_logprobs_requested": 20
}
```

- Completion content: `' {"ok": true}'` (6 tokens, in=21 / out=6 tokens).
- Every token carries both an observed `logprob` and a full list of 20
  ranked `top_logprobs` alternatives ⇒ per-token Shannon entropy over the
  top-K head (METHODS §4) is computable.

## Raw response excerpt (first token, alternatives truncated to 6 of 20)

```json
{
  "token": "▁{\"",
  "logprob": -0.864391028881073,
  "top_logprobs": [
    {"token": "▁{\"",   "logprob": -0.864391028881073},
    {"token": "▁Sure",  "logprob": -1.4893910884857178},
    {"token": "▁{",     "logprob": -1.6143910884857178},
    {"token": "▁Here",  "logprob": -2.x},
    {"token": "▁```",   "logprob": -3.x},
    {"token": "{\"",         "logprob": -3.x}
  ]
}
```

(Full 20-alternative records for all tokens are in the artifact JSON.)

## Tokenizer note (recorded before any entropy is computed)

The proxy's codestral-22b tokens use the SentencePiece convention: a
leading space is encoded as `▁` (U+2581) prefixed onto the token (e.g.
the first token is `▁{"`, representing ` {"`). Therefore **naively
concatenating `.token` strings does NOT reproduce the literal completion
text** (it yields `▁` where spaces should be). METHODS §3's token→char
mapping is refined accordingly: the reconstruction normalises each token
by replacing a leading `▁` with a single space (SentencePiece detokenise
rule) before computing char spans, and the reconstruction is asserted
equal to `resp.content` at runtime (mismatch ⇒ that response's seeds are
dropped from the entropy-stratified pool and counted, same disclosure
rule as METHODS §3). This refinement is logged in `EXECUTION_LOG.md`; it
is a pre-computation methods detail, not a post-hoc rationalisation.
