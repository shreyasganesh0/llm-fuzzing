"""Pre-flight format check for Claude before the full ablation runs.

Sends one minimal prompt (~50 input tokens, max 200 output tokens) asking
the model to emit the same `{"regexes":[...]}` schema the ablation runner
consumes. Parses the response with `parse_regex_response` (the same parser
the runner uses) and asserts (a) `status == "ok"`, (b) at least one seed
came out, (c) the call cost less than $0.02. Exits non-zero on any
violation so the experiment launcher can abort before burning the budget.

Usage:
    UTCF_ANTHROPIC_KEY_PATH=secrets/claude_key \
        python -m synthesis.scripts.claude_smoke_check --model claude-sonnet-4-6
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.llm_client import LLMClient
from synthesis.scripts.parse_synthesis import parse_regex_response

SMOKE_PROMPT = (
    "Emit one JSON object and nothing else. No markdown fences, no preamble.\n"
    "Schema:\n"
    '{"regexes":[{"regex":"<1-60 char regex>",'
    '"target_gaps":["re2/parse.cc:100"],"reasoning":"<why>"}]}\n'
    "Include exactly one entry. Pick a short regex like a+ or [0-9]+."
)

MAX_COST_USD = 0.02


def run_smoke(model: str, *, max_tokens: int = 200) -> int:
    client = LLMClient()
    if client.provider != "anthropic":
        print(
            f"[smoke] expected anthropic provider, got {client.provider}. "
            f"Set UTCF_ANTHROPIC_KEY_PATH to a file containing an sk-ant- key.",
            file=sys.stderr,
        )
        return 2

    resp = client.complete(
        messages=[
            {"role": "system", "content": ""},
            {"role": "user", "content": SMOKE_PROMPT},
        ],
        model=model,
        temperature=0.0,
        top_p=1.0,
        max_tokens=max_tokens,
        cache_salt=f"smoke_check,model={model}",
    )

    inputs, status = parse_regex_response(
        resp.content,
        target="re2",
        model=model,
        temperature=0.0,
        sample_index=0,
    )

    print(
        f"[smoke] model={model} status={status} seeds={len(inputs)} "
        f"in_tokens={resp.input_tokens} out_tokens={resp.output_tokens} "
        f"cost_usd={resp.cost_usd:.4f}"
    )

    if status != "ok":
        print(f"[smoke] FAIL: parse status={status!r}", file=sys.stderr)
        print(f"[smoke] raw response:\n{resp.content[:1000]}", file=sys.stderr)
        return 3
    if not inputs:
        print("[smoke] FAIL: zero seeds extracted", file=sys.stderr)
        print(f"[smoke] raw response:\n{resp.content[:1000]}", file=sys.stderr)
        return 4
    if resp.cost_usd > MAX_COST_USD:
        print(
            f"[smoke] FAIL: cost {resp.cost_usd:.4f} exceeded {MAX_COST_USD:.4f} "
            "— check pricing table or prompt size",
            file=sys.stderr,
        )
        return 5

    print(f"[smoke] OK — first seed bytes: {inputs[0].content_b64[:40]}...")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--max-tokens", type=int, default=200)
    args = parser.parse_args()
    return run_smoke(args.model, max_tokens=args.max_tokens)


if __name__ == "__main__":
    raise SystemExit(main())
