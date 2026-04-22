"""Audit real API spend by walking `.cache/llm/`.

The cache stores one JSON per response with `model`, `input_tokens`,
`output_tokens`, `cost_usd`, and `timestamp`. Summing `cost_usd` gives the
authoritative on-disk spend — doc prose should not hand-estimate pricing.

The original request messages are NOT cached (only the response), so target
attribution is best-effort: we substring-match the cached `content` for
known target identifiers. Cells whose response content is opaque to the
regex land in the `unknown` bucket.

Cross-check: we also re-price via `PRICING_USD_PER_MTOK` from the recorded
token counts and flag any disagreement >1¢/call against the stored
`cost_usd`. A mismatch means either a stale pricing table entry or a model
missing from the table.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from core.llm_client import PRICING_USD_PER_MTOK, _estimate_cost

CACHE_DIR_DEFAULT = Path(".cache/llm")

# Substring signatures for per-target attribution. Matched against the
# cached response content (case-insensitive). First match wins; order matters.
TARGET_SIGNATURES: list[tuple[str, list[str]]] = [
    ("re2", ["re2/parser.cc", "re2/regexp.cc", "re2/nfa.cc", "re2/dfa.cc",
             "google-re2", "re2::", "\"regex\":", "\"regexes\":"]),
    ("harfbuzz", ["harfbuzz", "hb_blob", "hb-ot-", "hb-shape", "hb_face",
                  "\"blob\":", "\"blobs\":", "hb_buffer"]),
]


def infer_target(content: str) -> str:
    lowered = content.lower()
    for name, sigs in TARGET_SIGNATURES:
        for sig in sigs:
            if sig.lower() in lowered:
                return name
    return "unknown"


def iter_records(cache_dir: Path) -> Iterable[dict]:
    for path in sorted(cache_dir.glob("*.json")):
        try:
            yield json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue


def audit(cache_dir: Path) -> dict:
    by_model: dict[str, dict] = defaultdict(
        lambda: {
            "calls": 0, "input_tokens": 0, "output_tokens": 0,
            "cost_usd": 0.0, "cost_repriced": 0.0, "missing_pricing": 0,
            "by_target": defaultdict(lambda: {"calls": 0, "cost_usd": 0.0}),
            "by_day": defaultdict(lambda: {"calls": 0, "cost_usd": 0.0}),
            "mean_input_tokens": 0.0, "mean_output_tokens": 0.0,
        }
    )
    mismatches: list[dict] = []

    for rec in iter_records(cache_dir):
        model = rec.get("model", "unknown")
        cost = float(rec.get("cost_usd", 0.0) or 0.0)
        in_tok = int(rec.get("input_tokens", 0) or 0)
        out_tok = int(rec.get("output_tokens", 0) or 0)
        ts = str(rec.get("timestamp", ""))
        day = ts[:10] if ts else "unknown"
        target = infer_target(str(rec.get("content", "")))

        m = by_model[model]
        m["calls"] += 1
        m["input_tokens"] += in_tok
        m["output_tokens"] += out_tok
        m["cost_usd"] += cost

        if model in PRICING_USD_PER_MTOK:
            repriced = _estimate_cost(model, in_tok, out_tok)
            m["cost_repriced"] += repriced
            if abs(repriced - cost) > 0.01:
                mismatches.append({
                    "model": model, "prompt_hash": rec.get("prompt_hash", ""),
                    "stored": cost, "repriced": repriced,
                })
        else:
            m["missing_pricing"] += 1

        m["by_target"][target]["calls"] += 1
        m["by_target"][target]["cost_usd"] += cost
        m["by_day"][day]["calls"] += 1
        m["by_day"][day]["cost_usd"] += cost

    for m in by_model.values():
        if m["calls"]:
            m["mean_input_tokens"] = m["input_tokens"] / m["calls"]
            m["mean_output_tokens"] = m["output_tokens"] / m["calls"]
        m["by_target"] = dict(m["by_target"])
        m["by_day"] = dict(m["by_day"])

    return {"by_model": dict(by_model), "mismatches": mismatches}


def _provider_of(model: str) -> str:
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith(("gpt-", "o1-")) and not model.startswith("gpt-oss"):
        return "openai"
    return "litellm"


def render_markdown(result: dict, cache_dir: Path) -> str:
    by_model = result["by_model"]
    grand_cost = sum(m["cost_usd"] for m in by_model.values())
    grand_calls = sum(m["calls"] for m in by_model.values())

    by_provider: dict[str, float] = defaultdict(float)
    for model, m in by_model.items():
        by_provider[_provider_of(model)] += m["cost_usd"]

    lines = [
        "# Cost audit",
        "",
        f"Source: `{cache_dir}` — response cache (authoritative on-disk spend).",
        "",
        f"- **Grand total:** ${grand_cost:.2f} across {grand_calls:,} cached responses.",
    ]
    for prov in ("anthropic", "openai", "litellm"):
        if by_provider.get(prov):
            lines.append(f"- **{prov}:** ${by_provider[prov]:.2f}")
    lines += ["", "## Per-model breakdown", "",
              "| model | calls | input tok | output tok | "
              "mean in | mean out | stored $ | repriced $ |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for model in sorted(by_model, key=lambda k: -by_model[k]["cost_usd"]):
        m = by_model[model]
        lines.append(
            f"| `{model}` | {m['calls']:,} | {m['input_tokens']:,} | "
            f"{m['output_tokens']:,} | {m['mean_input_tokens']:.0f} | "
            f"{m['mean_output_tokens']:.0f} | ${m['cost_usd']:.2f} | "
            f"${m['cost_repriced']:.2f} |"
        )

    lines += ["", "## Per-target breakdown",
              "(target inferred from response content; `unknown` = no signature match)",
              "",
              "| model | target | calls | cost |", "|---|---|---:|---:|"]
    for model in sorted(by_model):
        for target in sorted(by_model[model]["by_target"]):
            row = by_model[model]["by_target"][target]
            lines.append(
                f"| `{model}` | {target} | {row['calls']:,} | "
                f"${row['cost_usd']:.2f} |"
            )

    mismatches = result["mismatches"]
    if mismatches:
        lines += ["", f"## Pricing-table mismatches ({len(mismatches)})",
                  "Stored `cost_usd` disagrees with re-pricing via "
                  "`PRICING_USD_PER_MTOK` by >1¢. Check for stale vendor "
                  "pricing or a recently-renamed model.", ""]
        for row in mismatches[:20]:
            lines.append(
                f"- `{row['model']}` prompt={row['prompt_hash'][:12]} "
                f"stored=${row['stored']:.4f} repriced=${row['repriced']:.4f}"
            )
        if len(mismatches) > 20:
            lines.append(f"- (+{len(mismatches) - 20} more)")

    lines.append("")
    return "\n".join(lines)


def write_csv(result: dict, out_path: Path) -> None:
    by_model = result["by_model"]
    with out_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "model", "provider", "calls", "input_tokens", "output_tokens",
            "mean_input_tokens", "mean_output_tokens",
            "cost_usd_stored", "cost_usd_repriced", "missing_pricing",
        ])
        for model, m in sorted(by_model.items()):
            writer.writerow([
                model, _provider_of(model), m["calls"],
                m["input_tokens"], m["output_tokens"],
                f"{m['mean_input_tokens']:.2f}",
                f"{m['mean_output_tokens']:.2f}",
                f"{m['cost_usd']:.6f}", f"{m['cost_repriced']:.6f}",
                m["missing_pricing"],
            ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR_DEFAULT)
    parser.add_argument(
        "--out-dir", type=Path, default=Path("results/cost_audit"),
    )
    args = parser.parse_args(argv)

    if not args.cache_dir.is_dir():
        raise SystemExit(f"cache dir not found: {args.cache_dir}")

    result = audit(args.cache_dir)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    md = render_markdown(result, args.cache_dir)
    (args.out_dir / "summary.md").write_text(md)

    # Full structured dump for programmatic consumers (estimate_cost.py reads
    # this to get per-(model) historical token means).
    (args.out_dir / "summary.json").write_text(json.dumps(result, indent=2))
    write_csv(result, args.out_dir / "summary.csv")

    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
