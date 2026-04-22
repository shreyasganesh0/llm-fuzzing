"""Pre-experiment cost estimator.

Multiplies `PRICING_USD_PER_MTOK` by the requested (n_calls, mean_in,
mean_out). Historical per-model means come from `cost_audit.summary.json`
when present — so the default estimate uses *observed* per-call averages,
not prose guesses.

Typical usage:

    # What will the remaining Claude HB ablation cost?
    python -m analysis.scripts.estimate_cost \\
        --model claude-sonnet-4-6 --n-calls 400

    # Override means explicitly
    python -m analysis.scripts.estimate_cost \\
        --model claude-sonnet-4-6 --n-calls 400 \\
        --mean-in 11000 --mean-out 1600
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.llm_client import PRICING_USD_PER_MTOK, _estimate_cost

AUDIT_JSON_DEFAULT = Path("results/cost_audit/summary.json")


def load_historical_means(audit_json: Path) -> dict[str, tuple[float, float]]:
    if not audit_json.is_file():
        return {}
    data = json.loads(audit_json.read_text())
    by_model = data.get("by_model", {})
    return {
        model: (m.get("mean_input_tokens", 0.0), m.get("mean_output_tokens", 0.0))
        for model, m in by_model.items()
    }


def estimate(
    model: str, n_calls: int, mean_in: float, mean_out: float,
) -> dict:
    per_call = _estimate_cost(model, int(mean_in), int(mean_out))
    total = per_call * n_calls
    rate = PRICING_USD_PER_MTOK.get(model)
    return {
        "model": model,
        "n_calls": n_calls,
        "mean_input_tokens": mean_in,
        "mean_output_tokens": mean_out,
        "per_call_usd": per_call,
        "total_usd": total,
        "pricing_known": rate is not None,
        "input_usd_per_mtok": rate["input"] if rate else None,
        "output_usd_per_mtok": rate["output"] if rate else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--n-calls", type=int, required=True)
    parser.add_argument(
        "--mean-in", type=float, default=None,
        help="override historical mean input tokens",
    )
    parser.add_argument(
        "--mean-out", type=float, default=None,
        help="override historical mean output tokens",
    )
    parser.add_argument(
        "--audit-json", type=Path, default=AUDIT_JSON_DEFAULT,
        help="cost_audit output; used to default mean tokens per model",
    )
    args = parser.parse_args(argv)

    history = load_historical_means(args.audit_json)
    hist_in, hist_out = history.get(args.model, (0.0, 0.0))
    mean_in = args.mean_in if args.mean_in is not None else hist_in
    mean_out = args.mean_out if args.mean_out is not None else hist_out

    if mean_in == 0 and mean_out == 0:
        raise SystemExit(
            f"No historical data for {args.model!r} in {args.audit_json}. "
            "Pass --mean-in and --mean-out explicitly, or run "
            "`python -m analysis.scripts.cost_audit` first."
        )

    result = estimate(args.model, args.n_calls, mean_in, mean_out)

    source = "override" if args.mean_in is not None else f"historical ({args.audit_json})"
    print(f"# Cost estimate — {result['model']}")
    print(f"- Calls: {result['n_calls']:,}")
    print(
        f"- Mean tokens: in={result['mean_input_tokens']:.0f}, "
        f"out={result['mean_output_tokens']:.0f} (source: {source})"
    )
    if result["pricing_known"]:
        print(
            f"- Rate: input ${result['input_usd_per_mtok']:.2f}/Mtok, "
            f"output ${result['output_usd_per_mtok']:.2f}/Mtok"
        )
    else:
        print(f"- WARNING: no pricing table entry for {args.model!r} — $0 returned")
    print(f"- **Per-call: ${result['per_call_usd']:.4f}**")
    print(f"- **Total:    ${result['total_usd']:.2f}**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
