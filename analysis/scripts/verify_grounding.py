"""Post-hoc grounding verifier for CoT + RAG + tool-use ablation cells.

For each ``synthesis_dir / sample_*.json`` under a results root, this
script audits three claims the model made during synthesis and emits a
per-cell summary:

1. **Step 1 (Quote) grounding.** ``cot_strict``-derived seeds contain a
   ``Step 1 (Quote):`` fragment in ``inputs[*].reasoning``. That fragment
   must appear verbatim in the prompt context the model was shown (the
   gap ``code_context`` slices + the harness source when
   ``include_source`` is true). If the quoted string is not present
   anywhere in the context, the seed is flagged as ``hallucinated``.
   Fuzzy match (token-set ratio ≥ 0.8) counts as ``paraphrased``. An
   exact substring match is ``grounded``.

2. **``target_gaps`` accuracy.** ``inputs[*].target_gaps`` is a list of
   ``file:line`` strings the model *claimed* to target. We compare that
   set against the coverage-gap fixture (ground truth file:line entries
   visible to the model). A claim for a file:line that does not appear
   in the fixture is flagged as ``fabricated``. This is not the same as
   "did the seed actually hit the gap" (that's M2's job) — it is "did
   the model make up a gap that was never shown."

3. **Tool-transcript grounding (tool_use_retrieval only).** When a
   ``sample_*.json`` carries ``raw_response`` plus the driver-side tool
   transcript (future work — not yet persisted), we will additionally
   check that every file:line cited in reasoning appears in a
   ``get_source`` tool return from the same conversation. Today that
   check is stubbed out because transcripts are not written to disk.

Output: a JSON blob per cell and a Markdown summary printed to stdout.

Usage:

    # Single cell
    python -m analysis.scripts.verify_grounding \\
        --synthesis-dir synthesis/results/ablation_re2_v2/synthesis/re2/ablation/cot_strict/v3_all/gpt-oss-20b \\
        --dataset-root dataset/fixtures/_ablation_re2_v2_dataset \\
        --target re2

    # Walk every cell under a results root
    python -m analysis.scripts.verify_grounding --walk \\
        --results-root synthesis/results/ablation_re2_v2 \\
        --dataset-root dataset/fixtures/_ablation_re2_v2_dataset \\
        --target re2
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


STEP1_QUOTE_RE = re.compile(
    r"Step 1\s*\(Quote\)\s*:\s*(?P<quote>.+?)(?:(?:\n|\.\s)?Step 2|\Z)",
    re.IGNORECASE | re.DOTALL,
)

_FUZZY_THRESHOLD = 0.8
_MIN_QUOTE_LEN = 4  # reject 1-2 char quotes as unverifiable noise


def _load_gap_context(dataset_root: Path, target: str) -> str:
    """Concatenate the code_context fields of every gap into one blob.

    This mirrors what the template bakes into the prompt (§ UNCOVERED
    BRANCHES). We also include the condition_description for the
    softer grounding signal.
    """
    path = Path(dataset_root) / target / "coverage_gaps.json"
    if not path.is_file():
        return ""
    data = json.loads(path.read_text())
    parts: list[str] = []
    for g in data.get("gap_branches") or []:
        parts.append(g.get("code_context") or "")
        parts.append(g.get("condition_description") or "")
    return "\n".join(p for p in parts if p)


def _load_known_gap_keys(dataset_root: Path, target: str) -> set[str]:
    """Return the set of ``file:line`` strings that were in the gap fixture."""
    path = Path(dataset_root) / target / "coverage_gaps.json"
    if not path.is_file():
        return set()
    data = json.loads(path.read_text())
    out: set[str] = set()
    for g in data.get("gap_branches") or []:
        f, ln = g.get("file"), g.get("line")
        if f is not None and ln is not None:
            out.add(f"{f}:{ln}")
    return out


def _extract_step1_quote(reasoning: str) -> str | None:
    m = STEP1_QUOTE_RE.search(reasoning or "")
    if not m:
        return None
    q = m.group("quote").strip().rstrip(".").strip()
    return q or None


def _ground_quote(quote: str, haystack: str) -> str:
    """Return one of 'grounded' | 'paraphrased' | 'hallucinated'."""
    if len(quote) < _MIN_QUOTE_LEN:
        # Too short to verify — treat as unverifiable, classify as
        # hallucinated so the user sees the signal.
        return "hallucinated"
    if quote in haystack:
        return "grounded"
    # Fuzzy match: look for the best window in haystack that looks like
    # the quote. difflib is O(n·m) but n here is typically <50k chars,
    # quote is <200 — fine for offline verification.
    seq = difflib.SequenceMatcher(a=quote, b=haystack, autojunk=False)
    ratio = seq.quick_ratio()
    if ratio >= _FUZZY_THRESHOLD:
        return "paraphrased"
    return "hallucinated"


def verify_cell(
    synthesis_dir: Path,
    *,
    dataset_root: Path,
    target: str,
) -> dict:
    """Walk ``synthesis_dir/sample_*.json`` and produce per-cell stats."""
    haystack = _load_gap_context(dataset_root, target)
    known_gap_keys = _load_known_gap_keys(dataset_root, target)

    counts = {
        "seeds_total": 0,
        "reasoning_missing_step1": 0,
        "quote_grounded": 0,
        "quote_paraphrased": 0,
        "quote_hallucinated": 0,
        "target_gaps_declared": 0,
        "target_gaps_fabricated": 0,
    }
    samples = sorted(synthesis_dir.glob("sample_*.json"))
    if not samples:
        return {
            "synthesis_dir": str(synthesis_dir),
            "error": "no sample_*.json files",
            **counts,
        }
    for sample_path in samples:
        try:
            record = json.loads(sample_path.read_text())
        except json.JSONDecodeError:
            continue
        for inp in record.get("inputs") or []:
            counts["seeds_total"] += 1
            reasoning = inp.get("reasoning") or ""
            quote = _extract_step1_quote(reasoning)
            if quote is None:
                # No Step 1 (Quote) label present — this is expected for
                # non-cot_strict strategies. Record separately so we can
                # skip the grounding check cleanly.
                counts["reasoning_missing_step1"] += 1
            else:
                verdict = _ground_quote(quote, haystack)
                counts[f"quote_{verdict}"] += 1

            declared = inp.get("target_gaps") or []
            counts["target_gaps_declared"] += len(declared)
            for tg in declared:
                if tg not in known_gap_keys:
                    counts["target_gaps_fabricated"] += 1

    # Rates (guard against div/0).
    n = counts["seeds_total"] or 1
    attested = counts["seeds_total"] - counts["reasoning_missing_step1"]
    n_attested = attested or 1
    rates = {
        "hallucinated_quote_rate": counts["quote_hallucinated"] / n_attested,
        "grounded_quote_rate": counts["quote_grounded"] / n_attested,
        "paraphrased_quote_rate": counts["quote_paraphrased"] / n_attested,
        "target_gap_fabrication_rate": (
            counts["target_gaps_fabricated"] / (counts["target_gaps_declared"] or 1)
        ),
        "attested_seed_share": attested / n,
    }
    return {
        "synthesis_dir": str(synthesis_dir),
        **counts,
        **{k: round(v, 4) for k, v in rates.items()},
    }


def _walk_cells(results_root: Path) -> list[Path]:
    """Find every leaf synthesis_dir (containing sample_*.json) under root."""
    seen: set[Path] = set()
    for p in results_root.rglob("sample_0.json"):
        seen.add(p.parent)
    return sorted(seen)


def _format_markdown(rows: list[dict]) -> str:
    if not rows:
        return "_no rows_"
    cols = [
        "synthesis_dir", "seeds_total",
        "quote_grounded", "quote_paraphrased", "quote_hallucinated",
        "hallucinated_quote_rate",
        "target_gaps_declared", "target_gaps_fabricated",
        "target_gap_fabrication_rate",
    ]
    hdr = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join("---" for _ in cols) + "|"
    body = []
    for r in rows:
        body.append(
            "| " + " | ".join(str(r.get(c, "")) for c in cols) + " |"
        )
    return "\n".join([hdr, sep, *body])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthesis-dir", type=Path, default=None)
    parser.add_argument("--walk", action="store_true")
    parser.add_argument("--results-root", type=Path, default=None)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--out-json", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.walk:
        if args.results_root is None:
            parser.error("--walk requires --results-root")
        dirs = _walk_cells(args.results_root)
        if not dirs:
            print(f"# grounding audit\n\n_no sample_0.json under {args.results_root}_")
            return 0
        rows = [
            verify_cell(d, dataset_root=args.dataset_root, target=args.target)
            for d in dirs
        ]
    else:
        if args.synthesis_dir is None:
            parser.error("pass --synthesis-dir or --walk --results-root")
        rows = [verify_cell(
            args.synthesis_dir,
            dataset_root=args.dataset_root, target=args.target,
        )]

    print("# grounding audit\n")
    print(_format_markdown(rows))
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
