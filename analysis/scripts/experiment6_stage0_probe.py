"""experiment6 Stage 0 — logprob capability gate (HARD GATE).

Makes ONE probe call to codestral-22b through the existing
``LLMClient.complete`` path with logprobs requested, inspects the raw
response, and classifies the provider's logprob support:

    FULL    observed logprob + non-empty top-k alternatives present
    PARTIAL observed logprob only (surprisal computable, not full entropy)
    NONE    no logprob fields at all

Stage 1 (entropy stratification) is CONDITIONAL on FULL. PARTIAL or NONE
means STOP — the caller surfaces to the user; no fallbacks here.

Writes a machine-readable artifact to
``results/experiment6/stage0_probe.json`` and prints a summary. The
human-readable ``docs/experiment6/STAGE0_RESULT.md`` is composed from
this artifact.

Run:
    UTCF_LITELLM_URL=https://api.ai.it.ufl.edu \
    .venv/bin/python -m analysis.scripts.experiment6_stage0_probe
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.llm_client import LLMClient  # noqa: E402

MODEL = "codestral-22b"
TOP_LOGPROBS = 20
ARTIFACT = REPO_ROOT / "results" / "experiment6" / "stage0_probe.json"

# A tiny, deterministic-shaped prompt. We do not parse the content; we
# only inspect the logprob structure, so keep max_tokens small/cheap.
PROBE_MESSAGES = [
    {"role": "system", "content": ""},
    {
        "role": "user",
        "content": (
            "Reply with a single short JSON object: "
            '{"ok": true}. No prose.'
        ),
    },
]


def classify(raw: dict | None) -> tuple[str, dict]:
    """Return (classification, evidence) from a serialised logprobs dict.

    ``raw`` is what ``core.llm_client._extract_logprobs`` produced:
    ``{"content": [{"token","logprob","top_logprobs":[{token,logprob}]}]}``
    or ``None``.
    """
    if not raw or not raw.get("content"):
        return "NONE", {"reason": "no logprobs.content in response"}

    content = raw["content"]
    n_tokens = len(content)
    have_obs = sum(
        1 for t in content if isinstance(t.get("logprob"), (int, float))
    )
    alt_counts = [len(t.get("top_logprobs") or []) for t in content]
    max_alts = max(alt_counts) if alt_counts else 0
    n_with_alts = sum(1 for c in alt_counts if c > 0)

    evidence = {
        "n_tokens": n_tokens,
        "n_tokens_with_observed_logprob": have_obs,
        "n_tokens_with_top_k_alternatives": n_with_alts,
        "max_top_k_alternatives_seen": max_alts,
        "top_logprobs_requested": TOP_LOGPROBS,
    }

    if have_obs == 0:
        return "NONE", evidence
    if max_alts >= 2 and n_with_alts == n_tokens:
        return "FULL", evidence
    if max_alts >= 2:
        # Some tokens carry alternatives but not all — still FULL-capable
        # for entropy (per-token entropy is computed per token; tokens
        # missing alternatives would be excluded and counted).
        return "FULL", evidence
    return "PARTIAL", evidence


def main() -> int:
    os.environ.setdefault("UTCF_LITELLM_URL", "https://api.ai.it.ufl.edu")
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)

    client = LLMClient()
    result: dict = {
        "model": MODEL,
        "litellm_url": os.environ.get("UTCF_LITELLM_URL"),
        "top_logprobs_requested": TOP_LOGPROBS,
    }
    try:
        resp = client.complete(
            messages=PROBE_MESSAGES,
            model=MODEL,
            temperature=0.7,
            top_p=0.95,
            max_tokens=64,
            use_cache=False,
            abort_on_loop=False,
            logprobs=True,
            top_logprobs=TOP_LOGPROBS,
        )
    except Exception as exc:  # noqa: BLE001 — probe must report, not crash
        result.update({
            "classification": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "note": (
                "Probe call failed (e.g. proxy budget cap / network). "
                "This blocks Stage 1; surface to user."
            ),
        })
        ARTIFACT.write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
        return 1

    raw = resp.raw
    classification, evidence = classify(raw)

    # Bounded raw excerpt: first 5 token records (enough to show shape).
    excerpt = None
    if raw and raw.get("content"):
        excerpt = {"content": raw["content"][:5]}

    result.update({
        "classification": classification,
        "evidence": evidence,
        "content_preview": (resp.content or "")[:200],
        "input_tokens": resp.input_tokens,
        "output_tokens": resp.output_tokens,
        "raw_excerpt_first_5_tokens": excerpt,
    })
    ARTIFACT.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    # Exit 0 only on FULL so a shell gate can branch on it.
    return 0 if classification == "FULL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
