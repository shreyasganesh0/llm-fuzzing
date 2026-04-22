"""Harvest high-quality prior seeds for in-context exemplar injection (Phase 4).

Walks `synthesis/results/ablation_{re2_v2,harfbuzz}/synthesis/<target>/
ablation/<variant>/<model>/*.json` SynthesisRecord JSONs, picks up to 3
exemplars per target, and freezes them at
`dataset/fixtures/exemplars/<target>.json` for the FewShotStrategy
template context.

Quality signal, best-to-worst:
  1. M2 `shown` slice `union_frac_targets_hit` — ranks cells by how
     many of the prompt-shown hard branches they actually hit. Ties go
     to the smallest model (cheapest "easy win" signal).
  2. M1 `edges_covered` — fallback when no M2 summary exists.
  3. `v2_src_tests` seeds with non-empty reasoning — final fallback that
     preserves the context shape expected by the exemplar template.

`v0_none` cells are always excluded (no context → reasoning is usually
garbage).

Provenance: every exemplar carries {variant, model, sample_index,
seed_path}. No fabrication — if a seed lacks reasoning or fails schema
validation, it is skipped.
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.dataset_schema import GeneratedInput  # noqa: E402
from core.targets import TARGETS  # noqa: E402

EXEMPLARS_PER_TARGET = 3
MAX_REASONING_CHARS = 400
FALLBACK_VARIANT = "v2_src_tests"
EXCLUDED_VARIANTS = frozenset({"v0_none"})

# Rough "smaller-is-preferred" ranking — ties on M2 go to whichever comes
# first here. Used as a tiebreaker, not a quality proxy.
_MODEL_SIZE_ORDER = [
    "llama-3.1-8b-instruct",
    "codestral-22b",
    "claude-haiku-4-5-20251001",
    "llama-3.1-70b-instruct",
    "llama-3.3-70b-instruct",
    "nemotron-3-super-120b-a12b",
    "claude-sonnet-4-6",
]


def _truncate_reasoning(text: str, limit: int = MAX_REASONING_CHARS) -> str:
    """Truncate to <= limit chars, ending with an ellipsis if truncated."""
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    # Reserve 1 char for the ellipsis.
    return text[: max(0, limit - 1)] + "…"


def _model_rank(model: str) -> int:
    try:
        return _MODEL_SIZE_ORDER.index(model)
    except ValueError:
        return len(_MODEL_SIZE_ORDER)


def _git_head_sha() -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def _iter_cell_records(
    synthesis_root: Path, target: str, variant: str, model: str,
) -> Iterable[tuple[Path, dict[str, Any]]]:
    cell_dir = synthesis_root / "synthesis" / target / "ablation" / variant / model
    if not cell_dir.is_dir():
        return
    for p in sorted(cell_dir.glob("sample_*.json")):
        try:
            data = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        yield p, data


def _m2_score(target_results_root: Path, variant: str, model: str) -> float | None:
    """Read `shown` slice union_frac_targets_hit; None if missing."""
    path = target_results_root / "m2" / variant / model / "summary.json"
    if not path.is_file():
        return None
    try:
        summary = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    shown = summary.get("slices", {}).get("shown", {})
    val = shown.get("union_frac_targets_hit")
    if isinstance(val, (int, float)):
        return float(val)
    return None


def _m1_score(target_results_root: Path, variant: str, model: str) -> float | None:
    path = target_results_root / "m1" / variant / model / "summary.json"
    if not path.is_file():
        return None
    try:
        summary = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    val = summary.get("edges_covered")
    if isinstance(val, (int, float)):
        return float(val)
    return None


def _rank_cells(
    target_results_root: Path, variants: list[str], models: list[str],
    *, scorer,
) -> list[tuple[str, str, float]]:
    """Return (variant, model, score) sorted best-first. Ties → smaller model."""
    scored: list[tuple[str, str, float]] = []
    for v in variants:
        if v in EXCLUDED_VARIANTS:
            continue
        for m in models:
            s = scorer(target_results_root, v, m)
            if s is None:
                continue
            scored.append((v, m, s))
    # Sort: score desc, then model size asc.
    scored.sort(key=lambda t: (-t[2], _model_rank(t[1])))
    return scored


def _seed_is_valid(inp_dict: dict[str, Any]) -> bool:
    try:
        GeneratedInput.model_validate(inp_dict)
    except Exception:  # noqa: BLE001 — pydantic ValidationError subtree
        return False
    reasoning = inp_dict.get("reasoning", "")
    return isinstance(reasoning, str) and bool(reasoning.strip())


def _extract_regex_from_content_b64(content_b64: str) -> str | None:
    """RE2 content_b64 = 2 flag bytes + regex body. Return the regex body."""
    try:
        raw = base64.b64decode(content_b64)
    except (ValueError, TypeError):
        return None
    if len(raw) < 3:
        return None
    try:
        return raw[2:].decode("utf-8")
    except UnicodeDecodeError:
        return None


def _build_exemplar(
    *, target: str, variant: str, model: str, sample_index: int,
    seed_path: Path, inp_dict: dict[str, Any], seeds_root: Path,
) -> dict[str, Any] | None:
    """Shape one SynthesisRecord input into the exemplar schema."""
    content_b64 = inp_dict.get("content_b64", "")
    if not isinstance(content_b64, str) or not content_b64:
        return None

    if target == "re2":
        regex_body = _extract_regex_from_content_b64(content_b64)
        if not regex_body:
            return None
        content: str | None = regex_body
        content_b64_field: str | None = None
    else:
        content = None
        content_b64_field = content_b64

    gaps = inp_dict.get("target_gaps") or []
    if not isinstance(gaps, list):
        gaps = []
    try:
        rel_seed_path = seed_path.relative_to(seeds_root).as_posix()
    except ValueError:
        rel_seed_path = seed_path.as_posix()

    return {
        "origin": {
            "variant": variant,
            "model": model,
            "sample_index": int(sample_index),
            "seed_path": rel_seed_path,
        },
        "content": content,
        "content_b64": content_b64_field,
        "reasoning": _truncate_reasoning(str(inp_dict.get("reasoning", ""))),
        "target_gaps": [str(g) for g in gaps],
    }


def _collect_from_cell(
    synthesis_root: Path, target: str, variant: str, model: str,
    *, n_needed: int,
) -> list[dict[str, Any]]:
    """Pull up to n_needed valid exemplars from one cell."""
    out: list[dict[str, Any]] = []
    for path, record in _iter_cell_records(synthesis_root, target, variant, model):
        inputs = record.get("inputs", [])
        if not isinstance(inputs, list):
            continue
        sample_index = record.get("sample_index", 0)
        for inp in inputs:
            if not isinstance(inp, dict) or not _seed_is_valid(inp):
                continue
            ex = _build_exemplar(
                target=target, variant=variant, model=model,
                sample_index=sample_index, seed_path=path,
                inp_dict=inp, seeds_root=synthesis_root,
            )
            if ex is None:
                continue
            out.append(ex)
            if len(out) >= n_needed:
                return out
    return out


def _discover_models(synthesis_root: Path, target: str, variant: str) -> list[str]:
    base = synthesis_root / "synthesis" / target / "ablation" / variant
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir())


def _discover_variants(synthesis_root: Path, target: str) -> list[str]:
    base = synthesis_root / "synthesis" / target / "ablation"
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir())


def harvest_target(
    target: str, *, synthesis_root: Path, results_root: Path,
    n_exemplars: int = EXEMPLARS_PER_TARGET,
) -> tuple[list[dict[str, Any]], str]:
    """Return (exemplars, selection_method)."""
    variants = _discover_variants(synthesis_root, target)
    if not variants:
        return [], "empty_no_seeds_found"

    # All models seen anywhere under this target.
    models: list[str] = []
    seen: set[str] = set()
    for v in variants:
        for m in _discover_models(synthesis_root, target, v):
            if m not in seen:
                seen.add(m)
                models.append(m)
    if not models:
        return [], "empty_no_seeds_found"

    # Try M2-ranked cells first.
    for method, scorer in (
        ("m2_best", _m2_score),
        ("m1_best", _m1_score),
    ):
        ranked = _rank_cells(results_root, variants, models, scorer=scorer)
        out: list[dict[str, Any]] = []
        for v, m, _score in ranked:
            out.extend(_collect_from_cell(
                synthesis_root, target, v, m,
                n_needed=n_exemplars - len(out),
            ))
            if len(out) >= n_exemplars:
                return out[:n_exemplars], method
        if out:
            return out, method

    # Final fallback: v2_src_tests only.
    if FALLBACK_VARIANT in variants:
        cell_models = _discover_models(synthesis_root, target, FALLBACK_VARIANT)
        # Prefer smallest models in the fallback too.
        cell_models.sort(key=_model_rank)
        out: list[dict[str, Any]] = []
        for m in cell_models:
            out.extend(_collect_from_cell(
                synthesis_root, target, FALLBACK_VARIANT, m,
                n_needed=n_exemplars - len(out),
            ))
            if len(out) >= n_exemplars:
                return out[:n_exemplars], "v2_src_tests_fallback"
        if out:
            return out, "v2_src_tests_fallback"

    return [], "empty_no_seeds_found"


def write_fixture(
    *, target: str, exemplars: list[dict[str, Any]], selection_method: str,
    out_path: Path, source_commit: str, harvested_at: str,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "target": target,
        "schema_version": 1,
        "harvested_at": harvested_at,
        "source_commit": source_commit,
        "selection_method": selection_method,
        "exemplars": exemplars,
    }
    out_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target", choices=("re2", "harfbuzz", "all"), default="all",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="output file; only used when --target is a single target",
    )
    parser.add_argument(
        "--seeds-root", type=Path, default=None,
        help=(
            "override synthesis results root "
            "(default: target.synthesis_results_root). "
            "For tests."
        ),
    )
    parser.add_argument(
        "--results-root", type=Path, default=None,
        help=(
            "override results root used for M1/M2 scoring "
            "(default: target.results_root)."
        ),
    )
    parser.add_argument(
        "--fixtures-dir", type=Path,
        default=REPO_ROOT / "dataset" / "fixtures" / "exemplars",
    )
    parser.add_argument("--n", type=int, default=EXEMPLARS_PER_TARGET)
    parser.add_argument(
        "--frozen-now", default=None,
        help="fix harvested_at (for deterministic tests)",
    )
    parser.add_argument(
        "--frozen-commit", default=None,
        help="fix source_commit (for deterministic tests)",
    )
    args = parser.parse_args(argv)

    targets = [args.target] if args.target != "all" else ["re2", "harfbuzz"]
    if args.target == "all" and args.out is not None:
        print("ERROR: --out only valid with a single --target", file=sys.stderr)
        return 1

    harvested_at = args.frozen_now or datetime.now(timezone.utc).isoformat()
    source_commit = args.frozen_commit or _git_head_sha()

    for target in targets:
        spec = TARGETS[target]
        synthesis_root = args.seeds_root or spec.synthesis_results_root
        results_root = args.results_root or spec.results_root
        exemplars, method = harvest_target(
            target,
            synthesis_root=synthesis_root,
            results_root=results_root,
            n_exemplars=args.n,
        )
        out_path = (
            args.out if args.out is not None
            else args.fixtures_dir / f"{target}.json"
        )
        write_fixture(
            target=target, exemplars=exemplars, selection_method=method,
            out_path=out_path, source_commit=source_commit,
            harvested_at=harvested_at,
        )
        print(
            f"{target}: wrote {len(exemplars)} exemplars to {out_path} "
            f"(method={method})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
