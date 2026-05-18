"""Offline "lost cause" seed-generation auditor — proxy-budget triage.

PURPOSE
-------
Some (strategy, variant) cells can never reach the 150-seed floor no matter
how many more LLM calls are spent on them. The canonical case is
experiment7's `cot_strict` on codestral-22b/RE2: parsing succeeds ~94% of
the time, but the rigid 4-step CoT scaffold collapses generation diversity
so hard that only **142 distinct content_b64 seeds exist across every
cot_strict generation** — strictly fewer than 150. Because seeds are
content-addressed (a repeated regex overwrites the same `seed_*.bin`), the
cell's file count plateaus and the runner's 20-attempt no-gain early-exit
fires (`scripts/_ablation_base.py`, CONSEC_FAIL_WINDOW=20). The runner then
logs its *generic* "synthesis capped: too many parse failures" message even
though parsing was fine — the real cause is a diversity ceiling below 150.

This tool reproduces that diagnosis purely offline (no network / LLM / LLVM)
so we can decide *before* spending more proxy budget which cells to abandon.

WHAT IT READS (read-only, never written/mutated)
-------------------------------------------------
  * .cache/llm/<model>_*.json  — cached responses (response text only; the
                                  prompt is NOT stored, so strategy
                                  attribution is heuristic — see below).
  * synthesis/results/ablation_<X>/seeds/...  — realized seed corpora.
  * results/cost_audit/summary.json  — optional, for historical per-call
                                        token means → $ estimates.
  * /tmp/exp7_*.log  — optional, only to surface the runner's own
                       per-cell n_attempts if present (best-effort).

ABORT SEMANTICS THIS TOOL MIRRORS (so verdicts match the real runner)
---------------------------------------------------------------------
From `scripts/_ablation_base.py` (~lines 290-385):

  * MAX_ATTEMPTS_DEFAULT = 300   — hard per-cell attempt cap.
  * CONSEC_FAIL_WINDOW   = 20    — a cell aborts after 20 consecutive
                                   attempts that add zero NEW seeds.
  * num_seeds            = 150   — the per-cell floor; <150 ⇒ CENSORED.
  * Seeds are content-addressed: `_count_seeds` counts distinct
    `seed_*.bin`, so duplicate content does NOT grow the count. A cell
    whose *universe of distinct producible seeds* is < 150 can never fill,
    independent of attempt budget. That ceiling rule is the load-bearing
    LOST_CAUSE trigger here.

STRATEGY ATTRIBUTION (honest about its limits)
----------------------------------------------
The LLM cache stores only the response, never the prompt, so a cached
response cannot be mapped to its (strategy, variant) cell with certainty.
We use one reliable content heuristic plus an override hook:

  * `cot_strict` mandates the model echo the literal labels
    `Step 1 (Quote):`, `Step 2 (Locate):`, ... in every `reasoning`
    field (see synthesis/prompts/ablation_synthesis_regex_cot.j2). A
    response containing the marker `Step 1 (Quote)` is therefore
    attributable to `cot_strict` with high precision.
  * `few_shot`, `self_critique`, `prompt_chain`, and `default` produce the
    *same* plain regex/binary JSON format — they are NOT distinguishable
    from one another by response content alone. They are reported together
    as the NON-cot_strict aggregate bucket, explicitly labelled as such.
  * `--strategy-marker NAME=SUBSTRING` (repeatable) overrides/extends the
    marker map when a caller knows a distinguishing substring.

We never fabricate per-cell precision we do not have: a bucket that cannot
be split is reported at its aggregate granularity with a stated caveat.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# sys.path bootstrap — mirror analysis/scripts/measure_gap_coverage.py
# (~lines 26-29) so `python -m analysis.scripts.seed_yield_audit` and a
# bare `python analysis/scripts/seed_yield_audit.py` both import the repo.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.llm_client import PRICING_USD_PER_MTOK  # noqa: E402
from core.targets import TARGETS  # noqa: E402
from synthesis.scripts.parse_synthesis import (  # noqa: E402
    parse_regex_response,
    parse_synthesis_response,
)

# ── Constants mirrored from scripts/_ablation_base.py (single source there;
#    we re-declare locally because the task forbids importing it / editing
#    it, and a fresh clone must not depend on its import side-effects). ──
MAX_ATTEMPTS_DEFAULT = 300
CONSEC_FAIL_WINDOW = 20
SEED_FLOOR = 150

# Parser is invoked with the same fixed knobs the runner uses for synthesis.
PARSE_TEMPERATURE = 0.7
PARSE_SAMPLE_INDEX = 0

# The one reliable content marker (see module docstring). Maps a strategy
# name to a substring that, if present in a cached *response*, attributes
# that response to the strategy. Extend via --strategy-marker.
DEFAULT_STRATEGY_MARKERS: dict[str, str] = {
    "cot_strict": "Step 1 (Quote)",
}

# Fallback when results/cost_audit/summary.json is absent: a deliberately
# conservative per-call token estimate. Documented so a reader can audit
# the dollar figure. Chosen to roughly match observed codestral synthesis
# calls (input-heavy context prompt + a short JSON answer).
FALLBACK_INPUT_TOKENS = 5000
FALLBACK_OUTPUT_TOKENS = 450

# Default verdict thresholds (all overridable via CLI; see build_parser()).
DEFAULT_DIVERSITY_MIN = 0.20      # below ⇒ LOST_CAUSE (diversity collapse)
DEFAULT_DIVERSITY_MARGINAL = 0.50  # [min, marginal) ⇒ MARGINAL
DEFAULT_MAX_ATTEMPTS = MAX_ATTEMPTS_DEFAULT  # proj attempts above ⇒ LOST_CAUSE


# ─────────────────────────── data classes ───────────────────────────────


@dataclass
class CellSeedStat:
    """Realized-seed facts for one (strategy, variant) cell on disk."""

    strategy: str
    variant: str
    seeds_dir: str
    realized_unique_seeds: int      # == count of distinct seed_*.bin
    dir_exists: bool


@dataclass
class ResponseGroupStat:
    """Parse-rate + diversity for a group of cached responses.

    A "group" is either one attributable strategy (e.g. cot_strict) or the
    non-attributable aggregate bucket. `is_aggregate=True` means the figures
    span multiple strategies that response content cannot separate.
    """

    label: str
    is_aggregate: bool
    n_responses: int
    parse_ok: int
    parse_ok_rate: float
    total_parsed_seed_instances: int
    unique_content_b64: int
    diversity_ratio: float          # unique / total (0.0 when total == 0)


@dataclass
class CellAudit:
    """The full per-cell-group verdict row that lands in the report."""

    cell: str
    strategy: str
    variant: str
    response_group_label: str
    response_group_is_aggregate: bool
    n_responses: int
    parse_ok_rate: float
    diversity_ratio: float
    realized_unique_seeds: int
    seed_floor: int
    # Yield projection
    distinct_seed_ceiling: int      # best estimate of the producible universe
    new_seed_rate_per_response: float
    projected_attempts_to_floor: float  # inf when rate ≈ 0 / ceiling < floor
    can_ever_fill: bool
    exceeds_attempt_cap: bool
    # Verdict + spend
    verdict: str                    # LOST_CAUSE | MARGINAL | VIABLE
    verdict_reasons: list[str] = field(default_factory=list)
    attempts_spent_estimate: int = 0
    est_dollars_wasted: float = 0.0
    cost_basis: str = ""
    recommendation: str = ""


# ─────────────────────── cache + parsing helpers ─────────────────────────


def _safe_model(model: str) -> str:
    """Mirror core.targets.TargetSpec: model name → on-disk safe form."""
    return model.replace("/", "_")


def list_recent_cache_files(
    cache_dir: Path, model: str, *, last_n: int | None, since_min: float | None
) -> list[Path]:
    """Return cached-response files for `model`, newest first, windowed.

    Window selection (only one applies; `--since-min` wins if both given):
      * since_min  — keep files with mtime within the last `since_min`
                     minutes.
      * last_n     — keep the `last_n` most recently modified files.
    Files are matched by the `<model>_<sha>.json` cache naming convention.
    """
    safe = _safe_model(model)
    candidates = sorted(
        cache_dir.glob(f"{safe}_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if since_min is not None:
        cutoff = time.time() - since_min * 60.0
        return [p for p in candidates if p.stat().st_mtime >= cutoff]
    if last_n is not None:
        return candidates[:last_n]
    return candidates


def parse_one_response(text: str, *, target: str, model: str) -> tuple[bool, list[str]]:
    """Run the correct parser for `target`; return (parse_ok, [content_b64]).

    re2 → parse_regex_response; everything else → parse_synthesis_response.
    Both are called at the runner's synthesis knobs (temp 0.7, sample 0).
    """
    if target == "re2":
        seeds, status = parse_regex_response(
            text,
            target=target,
            model=model,
            temperature=PARSE_TEMPERATURE,
            sample_index=PARSE_SAMPLE_INDEX,
        )
    else:
        seeds, status = parse_synthesis_response(
            text,
            target=target,
            model=model,
            temperature=PARSE_TEMPERATURE,
            sample_index=PARSE_SAMPLE_INDEX,
        )
    return status == "ok", [s.content_b64 for s in seeds]


def attribute_response(text: str, marker_map: dict[str, str]) -> str | None:
    """Return the strategy a response is attributable to, else None.

    Returns the first strategy whose marker substring appears in `text`.
    None means "not attributable" → the aggregate bucket.
    """
    for strategy, marker in marker_map.items():
        if marker and marker in text:
            return strategy
    return None


# ───────────────────── seed-dir resolution ──────────────────────────────


def resolve_cell_seeds_dir(target_name: str, strategy: str, variant: str, model: str) -> Path:
    """Mirror core.targets.TargetSpec.cell_seeds_dir.

    default strategy ⇒ NO <strategy> path segment (legacy layout);
    non-default ⇒ <strategy> inserted under seeds/<target>/.
    """
    spec = TARGETS[target_name]
    return spec.cell_seeds_dir(variant, model, strategy=strategy)


def count_distinct_seeds(seeds_dir: Path) -> int:
    """Distinct realized seeds. Seeds are content-addressed, so the count
    of `seed_*.bin` files == number of distinct content_b64 produced."""
    if not seeds_dir.is_dir():
        return 0
    return sum(1 for _ in seeds_dir.glob("seed_*.bin"))


# ───────────────────── cost-basis resolution ────────────────────────────


@dataclass
class CostBasis:
    """Per-call mean $ for `model`, plus a human-readable source string."""

    per_call_usd: float
    source: str


def resolve_cost_basis(model: str, cost_audit_path: Path) -> CostBasis:
    """Per-call mean cost for `model`.

    Preference order (cited in the report):
      1. results/cost_audit/summary.json — historical mean tokens for this
         model repriced through the canonical PRICING table.
      2. PRICING_USD_PER_MTOK + a documented fallback token estimate.
      3. $0 with an explicit "unknown pricing" note (never silently 0).
    """
    rate = PRICING_USD_PER_MTOK.get(model)

    if cost_audit_path.is_file():
        try:
            audit = json.loads(cost_audit_path.read_text())
            entry = audit.get("by_model", {}).get(model)
            if entry and rate:
                mean_in = float(entry.get("mean_input_tokens", 0.0))
                mean_out = float(entry.get("mean_output_tokens", 0.0))
                per_call = (
                    mean_in * rate["input"] + mean_out * rate["output"]
                ) / 1_000_000
                src = (
                    f"results/cost_audit/summary.json mean tokens "
                    f"(in={mean_in:.0f}, out={mean_out:.0f}) repriced via "
                    f"core.llm_client.PRICING_USD_PER_MTOK[{model}]"
                )
                return CostBasis(per_call_usd=per_call, source=src)
        except (ValueError, KeyError, TypeError):
            pass  # fall through to the pricing-table fallback

    if rate:
        per_call = (
            FALLBACK_INPUT_TOKENS * rate["input"]
            + FALLBACK_OUTPUT_TOKENS * rate["output"]
        ) / 1_000_000
        src = (
            f"core.llm_client.PRICING_USD_PER_MTOK[{model}] x fallback token "
            f"estimate (in={FALLBACK_INPUT_TOKENS}, out={FALLBACK_OUTPUT_TOKENS}); "
            f"cost_audit summary absent"
        )
        return CostBasis(per_call_usd=per_call, source=src)

    return CostBasis(
        per_call_usd=0.0,
        source=f"UNKNOWN pricing for {model} (not in PRICING_USD_PER_MTOK) — $ shown as 0",
    )


# ───────────────────── yield projection + verdict ───────────────────────


def project_yield(
    realized_unique_seeds: int,
    n_responses: int,
    diversity_ratio: float,
    total_parsed_seed_instances: int,
    unique_content_b64: int,
) -> tuple[int, float, float, bool, bool]:
    """Compute the yield projection for a cell-group.

    Returns:
      (distinct_seed_ceiling,
       new_seed_rate_per_response,
       projected_attempts_to_floor,
       can_ever_fill,
       exceeds_attempt_cap)

    `distinct_seed_ceiling` is the best available estimate of how many
    distinct seeds this generator can ever produce. We take the MAX of:
      * realized_unique_seeds (already on disk), and
      * unique_content_b64 observed across the sampled responses
    because both are lower bounds on the true producible universe, and the
    larger observed set is the tighter (more generous) lower bound. If even
    this generous ceiling is < SEED_FLOOR, more attempts provably cannot
    fill the cell — the load-bearing LOST_CAUSE condition (the exact
    cot_strict failure: 142 distinct < 150).

    new_seed_rate_per_response ≈ unique/responses — the marginal rate at
    which fresh distinct seeds arrive. As duplicates dominate this tends
    to 0, so projected attempts → infinity.
    """
    distinct_seed_ceiling = max(realized_unique_seeds, unique_content_b64)

    new_seed_rate_per_response = (
        unique_content_b64 / n_responses if n_responses > 0 else 0.0
    )

    remaining = max(SEED_FLOOR - realized_unique_seeds, 0)

    can_ever_fill = distinct_seed_ceiling >= SEED_FLOOR

    if remaining == 0:
        projected = 0.0
    elif not can_ever_fill or new_seed_rate_per_response <= 0.0:
        # Ceiling below the floor, or no fresh seeds arriving ⇒ unbounded.
        projected = float("inf")
    else:
        projected = remaining / new_seed_rate_per_response

    exceeds_attempt_cap = (
        projected == float("inf") or projected > MAX_ATTEMPTS_DEFAULT
    )
    return (
        distinct_seed_ceiling,
        new_seed_rate_per_response,
        projected,
        can_ever_fill,
        exceeds_attempt_cap,
    )


def decide_verdict(
    *,
    diversity_ratio: float,
    distinct_seed_ceiling: int,
    projected_attempts: float,
    realized_unique_seeds: int,
    diversity_min: float,
    diversity_marginal: float,
    max_attempts: int,
) -> tuple[str, list[str]]:
    """Apply the documented verdict rules. Returns (verdict, reasons).

    REALIZED-FILL OVERRIDE (mirrors the runner, checked FIRST):
      If `realized_unique_seeds >= SEED_FLOOR` the runner already filled
      this cell to the 150 floor on disk. Empirically it is NOT a lost
      cause — the runner's content-addressed dedup (regex + position-
      dependent sha256 flag bytes) demonstrably produced 150 distinct
      `seed_*.bin`. The re-parsed cache diversity is a *regex-level*
      proxy that deliberately collapses the flag-byte entropy (so it
      stays a conservative ceiling signal for SUB-floor cells); it must
      not override the runner's own ground-truth success. ⇒ VIABLE.

    Otherwise, LOST_CAUSE if ANY of:
      (a) diversity_ratio < diversity_min                 (diversity collapse)
      (b) distinct_seed_ceiling < SEED_FLOOR              (CANNOT EVER FILL —
          the load-bearing rule; this is exactly cot_strict's 142 < 150)
      (c) projected_attempts_to_150 > max_attempts        (mathematically
          unable to fill within the runner's attempt cap)
    MARGINAL if not LOST_CAUSE and EITHER:
      diversity_min <= ratio < diversity_marginal, OR
      max_attempts < projected_attempts <= max_attempts (note: the >cap
          case is already LOST_CAUSE; MARGINAL captures the
          150<proj<=cap band — i.e. fillable but expensive).
    Otherwise VIABLE.
    """
    reasons: list[str] = []

    if realized_unique_seeds >= SEED_FLOOR:
        reasons.append(
            f"runner already filled this cell ({realized_unique_seeds} >= "
            f"{SEED_FLOOR} seed_*.bin on disk) — empirically VIABLE; "
            f"re-parsed regex-level diversity {diversity_ratio:.3f} is a "
            f"conservative sub-floor proxy and does not override a "
            f"demonstrated fill"
        )
        return "VIABLE", reasons

    lost = False
    if diversity_ratio < diversity_min:
        lost = True
        reasons.append(
            f"diversity_ratio {diversity_ratio:.3f} < diversity_min {diversity_min:.2f}"
        )
    if distinct_seed_ceiling < SEED_FLOOR:
        lost = True
        reasons.append(
            f"distinct-seed ceiling {distinct_seed_ceiling} < {SEED_FLOOR} "
            f"(CANNOT EVER FILL — more attempts cannot help)"
        )
    if projected_attempts > max_attempts:
        lost = True
        proj_s = "inf" if projected_attempts == float("inf") else f"{projected_attempts:.0f}"
        reasons.append(
            f"projected attempts to {SEED_FLOOR} = {proj_s} > cap {max_attempts}"
        )
    if lost:
        return "LOST_CAUSE", reasons

    marginal = False
    if diversity_min <= diversity_ratio < diversity_marginal:
        marginal = True
        reasons.append(
            f"diversity_ratio {diversity_ratio:.3f} in "
            f"[{diversity_min:.2f}, {diversity_marginal:.2f}) — marginal"
        )
    # "fillable but expensive": below the cap yet still many attempts.
    if SEED_FLOOR < projected_attempts <= max_attempts:
        marginal = True
        reasons.append(
            f"projected attempts {projected_attempts:.0f} in "
            f"({SEED_FLOOR}, {max_attempts}] — fillable but costly"
        )
    if marginal:
        return "MARGINAL", reasons

    reasons.append(
        f"diversity_ratio {diversity_ratio:.3f} >= {diversity_marginal:.2f} "
        f"and projected attempts within budget"
    )
    return "VIABLE", reasons


# ─────────────────────── core audit pipeline ────────────────────────────


def aggregate_response_groups(
    cache_files: list[Path],
    *,
    target: str,
    model: str,
    marker_map: dict[str, str],
) -> dict[str, ResponseGroupStat]:
    """Parse every cached response, bucket by attributed strategy.

    The bucket key is the attributed strategy name, or
    "<non-cot_strict aggregate>" when content cannot attribute it. The
    aggregate bucket's `is_aggregate` flag is True so the report can state
    the precision caveat.
    """
    AGG = "<non-cot_strict aggregate>"
    buckets: dict[str, dict] = {}

    def _bucket(key: str) -> dict:
        return buckets.setdefault(
            key,
            {"n": 0, "ok": 0, "total": 0, "uniq": set()},
        )

    for fp in cache_files:
        try:
            payload = json.loads(fp.read_text())
        except (ValueError, OSError):
            continue
        text = payload.get("content")
        if not isinstance(text, str):
            continue

        attributed = attribute_response(text, marker_map)
        key = attributed if attributed is not None else AGG
        ok, contents = parse_one_response(text, target=target, model=model)

        b = _bucket(key)
        b["n"] += 1
        if ok:
            b["ok"] += 1
        b["total"] += len(contents)
        for c in contents:
            b["uniq"].add(c)

    out: dict[str, ResponseGroupStat] = {}
    for key, b in buckets.items():
        total = b["total"]
        uniq = len(b["uniq"])
        out[key] = ResponseGroupStat(
            label=key,
            is_aggregate=(key == AGG),
            n_responses=b["n"],
            parse_ok=b["ok"],
            parse_ok_rate=(b["ok"] / b["n"]) if b["n"] else 0.0,
            total_parsed_seed_instances=total,
            unique_content_b64=uniq,
            diversity_ratio=(uniq / total) if total else 0.0,
        )
    return out


def estimate_attempts_spent(
    target: str, strategy: str, variant: str, model: str, log_paths: list[Path]
) -> int | None:
    """Best-effort: scrape the runner's own n_attempts for this cell from
    /tmp/exp7_*.log if present. Returns None when not found — callers must
    then fall back to a documented heuristic, never fabricate a number."""
    safe = _safe_model(model)
    found: int | None = None
    for lp in log_paths:
        if not lp.is_file():
            continue
        try:
            for line in lp.read_text(errors="replace").splitlines():
                if "synthesis capped" not in line and "synthesis done" not in line:
                    continue
                if variant not in line or safe not in line and model not in line:
                    continue
                if strategy not in line:
                    continue
                # Structured log line carries n_attempts=<int> somewhere.
                for tok in line.replace(",", " ").replace("'", " ").split():
                    if tok.startswith("n_attempts"):
                        digits = "".join(ch for ch in tok if ch.isdigit())
                        if digits:
                            found = int(digits)
        except OSError:
            continue
    return found


def audit(
    *,
    target: str,
    model: str,
    strategies: list[str],
    variants: list[str],
    cache_dir: Path,
    cost_audit_path: Path,
    log_paths: list[Path],
    last_n: int | None,
    since_min: float | None,
    marker_map: dict[str, str],
    diversity_min: float,
    diversity_marginal: float,
    max_attempts: int,
) -> dict:
    """Run the full audit and return a JSON-serializable report dict."""
    cache_files = list_recent_cache_files(
        cache_dir, model, last_n=last_n, since_min=since_min
    )
    groups = aggregate_response_groups(
        cache_files, target=target, model=model, marker_map=marker_map
    )
    cost_basis = resolve_cost_basis(model, cost_audit_path)

    cells: list[CellAudit] = []
    total_wasted = 0.0
    recommendations: list[str] = []

    for strategy in strategies:
        for variant in variants:
            seeds_dir = resolve_cell_seeds_dir(target, strategy, variant, model)
            realized = count_distinct_seeds(seeds_dir)

            # Pick the response group that describes this cell. If the
            # strategy is attributable (has a marker) and we saw it, use
            # that group; otherwise fall back to the aggregate bucket and
            # flag the reduced precision.
            if strategy in groups:
                grp = groups[strategy]
            elif "<non-cot_strict aggregate>" in groups:
                grp = groups["<non-cot_strict aggregate>"]
            else:
                grp = ResponseGroupStat(
                    label=f"{strategy} (no cached responses in window)",
                    is_aggregate=True,
                    n_responses=0,
                    parse_ok=0,
                    parse_ok_rate=0.0,
                    total_parsed_seed_instances=0,
                    unique_content_b64=0,
                    diversity_ratio=0.0,
                )

            (
                ceiling,
                rate,
                projected,
                can_fill,
                exceeds_cap,
            ) = project_yield(
                realized_unique_seeds=realized,
                n_responses=grp.n_responses,
                diversity_ratio=grp.diversity_ratio,
                total_parsed_seed_instances=grp.total_parsed_seed_instances,
                unique_content_b64=grp.unique_content_b64,
            )

            verdict, reasons = decide_verdict(
                diversity_ratio=grp.diversity_ratio,
                distinct_seed_ceiling=ceiling,
                projected_attempts=projected,
                realized_unique_seeds=realized,
                diversity_min=diversity_min,
                diversity_marginal=diversity_marginal,
                max_attempts=max_attempts,
            )

            # Attempts spent: prefer the runner's own logged n_attempts;
            # else assume the cell ran to the no-gain early-exit, i.e. it
            # consumed at least CONSEC_FAIL_WINDOW dead attempts on top of
            # whatever produced its realized seeds — a conservative lower
            # bound that we label as such.
            logged_attempts = estimate_attempts_spent(
                target, strategy, variant, model, log_paths
            )
            if logged_attempts is not None:
                attempts_spent = logged_attempts
                attempts_basis = "runner log n_attempts"
            else:
                attempts_spent = realized + CONSEC_FAIL_WINDOW
                attempts_basis = (
                    f"heuristic lower bound: realized {realized} + "
                    f"CONSEC_FAIL_WINDOW {CONSEC_FAIL_WINDOW} (log absent)"
                )

            if verdict == "LOST_CAUSE":
                wasted = attempts_spent * cost_basis.per_call_usd
                total_wasted += wasted
                recommendations.append(
                    f"ABANDON {strategy}/{variant} — "
                    f"would save ~${wasted:.2f} "
                    f"({attempts_spent} attempts x ${cost_basis.per_call_usd:.4f}/call)"
                )
            else:
                wasted = 0.0

            cells.append(
                CellAudit(
                    cell=f"{strategy}/{variant}",
                    strategy=strategy,
                    variant=variant,
                    response_group_label=grp.label,
                    response_group_is_aggregate=grp.is_aggregate,
                    n_responses=grp.n_responses,
                    parse_ok_rate=grp.parse_ok_rate,
                    diversity_ratio=grp.diversity_ratio,
                    realized_unique_seeds=realized,
                    seed_floor=SEED_FLOOR,
                    distinct_seed_ceiling=ceiling,
                    new_seed_rate_per_response=rate,
                    projected_attempts_to_floor=(
                        -1.0 if projected == float("inf") else projected
                    ),
                    can_ever_fill=can_fill,
                    exceeds_attempt_cap=exceeds_cap,
                    verdict=verdict,
                    verdict_reasons=reasons,
                    attempts_spent_estimate=attempts_spent,
                    est_dollars_wasted=round(wasted, 4),
                    cost_basis=f"{cost_basis.source} | attempts: {attempts_basis}",
                    recommendation=(
                        recommendations[-1] if verdict == "LOST_CAUSE" else ""
                    ),
                )
            )

    return {
        "target": target,
        "model": model,
        "window": {
            "last_n": last_n,
            "since_min": since_min,
            "cache_files_considered": len(cache_files),
        },
        "thresholds": {
            "diversity_min": diversity_min,
            "diversity_marginal": diversity_marginal,
            "max_attempts": max_attempts,
            "seed_floor": SEED_FLOOR,
            "consec_fail_window": CONSEC_FAIL_WINDOW,
        },
        "strategy_markers": marker_map,
        "attribution_caveat": (
            "Cache stores responses only (no prompt). cot_strict is "
            "attributable via the mandated 'Step 1 (Quote)' reasoning "
            "marker; few_shot/self_critique/prompt_chain/default share an "
            "identical response format and are reported together as the "
            "non-cot_strict aggregate."
        ),
        "cost_basis": {
            "per_call_usd": cost_basis.per_call_usd,
            "source": cost_basis.source,
        },
        "cells": [asdict(c) for c in cells],
        "total_estimated_dollars_wasted": round(total_wasted, 4),
        "recommendations": recommendations,
    }


# ─────────────────────────── rendering ──────────────────────────────────


def render_markdown(report: dict) -> str:
    """Human-readable markdown table + recommendations."""
    lines: list[str] = []
    lines.append(f"# Seed Yield Audit — {report['target']} / {report['model']}")
    lines.append("")
    th = report["thresholds"]
    lines.append(
        f"Thresholds: diversity_min={th['diversity_min']}, "
        f"diversity_marginal={th['diversity_marginal']}, "
        f"max_attempts={th['max_attempts']}, seed_floor={th['seed_floor']}, "
        f"consec_fail_window={th['consec_fail_window']}"
    )
    lines.append(
        f"Window: {report['window']['cache_files_considered']} cache files "
        f"(last_n={report['window']['last_n']}, "
        f"since_min={report['window']['since_min']})"
    )
    lines.append("")
    lines.append(f"Attribution caveat: {report['attribution_caveat']}")
    lines.append("")
    lines.append(f"Cost basis: {report['cost_basis']['source']} "
                 f"(${report['cost_basis']['per_call_usd']:.4f}/call)")
    lines.append("")
    lines.append(
        "| cell | n_resp | parse_ok% | div_ratio | unique/150 | "
        "proj_attempts | verdict | est_$_wasted | recommendation |"
    )
    lines.append(
        "|------|--------|-----------|-----------|------------|"
        "---------------|---------|--------------|----------------|"
    )
    for c in report["cells"]:
        proj = c["projected_attempts_to_floor"]
        proj_s = "inf" if proj < 0 else (f"{proj:.0f}" if proj else "0")
        rec = c["recommendation"] or "-"
        agg_flag = " (agg)" if c["response_group_is_aggregate"] else ""
        lines.append(
            f"| {c['cell']} "
            f"| {c['n_responses']}{agg_flag} "
            f"| {c['parse_ok_rate'] * 100:.1f}% "
            f"| {c['diversity_ratio']:.3f} "
            f"| {c['realized_unique_seeds']}/{c['seed_floor']} "
            f"| {proj_s} "
            f"| {c['verdict']} "
            f"| ${c['est_dollars_wasted']:.2f} "
            f"| {rec} |"
        )
    lines.append("")
    lines.append(
        f"**Total estimated wasted spend: "
        f"${report['total_estimated_dollars_wasted']:.2f}**"
    )
    lines.append("")
    if report["recommendations"]:
        lines.append("## Recommendations")
        for r in report["recommendations"]:
            lines.append(f"- {r}")
    else:
        lines.append("No LOST_CAUSE cells in window — nothing to abandon.")
    lines.append("")
    return "\n".join(lines)


# ─────────────────────────── CLI plumbing ───────────────────────────────


def expand_variants(spec: str) -> list[str]:
    """Accept a CSV list, or the shorthand `v0..v4` (the 5 standard variants).

    The standard 5-variant order is fixed (v0_none → v4_src_gaps).
    """
    standard = ["v0_none", "v1_src", "v2_src_tests", "v3_all", "v4_src_gaps"]
    spec = spec.strip()
    if spec in ("v0..v4", "v0..4", "all"):
        return list(standard)
    out: list[str] = []
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        out.append(token)
    return out


def parse_marker_overrides(pairs: list[str]) -> dict[str, str]:
    """`NAME=SUBSTRING` pairs → marker map, layered over the defaults."""
    marker_map = dict(DEFAULT_STRATEGY_MARKERS)
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(
                f"--strategy-marker expects NAME=SUBSTRING, got {pair!r}"
            )
        name, _, substring = pair.partition("=")
        marker_map[name.strip()] = substring
    return marker_map


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m analysis.scripts.seed_yield_audit",
        description=(
            "Offline 'lost cause' seed-generation auditor. Flags "
            "(strategy, variant) cells that can never reach the 150-seed "
            "floor so proxy budget is not wasted grinding them."
        ),
    )
    p.add_argument("--target", required=True, help="e.g. re2, harfbuzz")
    p.add_argument("--model", required=True, help="e.g. codestral-22b")
    p.add_argument(
        "--strategies",
        required=True,
        help="CSV of strategy names, e.g. default,cot_strict,few_shot",
    )
    p.add_argument(
        "--variants",
        default="v0..v4",
        help="CSV of variants, or 'v0..v4' for the standard five "
        "(default: v0..v4).",
    )
    p.add_argument(
        "--last-n",
        type=int,
        default=600,
        help="Audit the N most-recent cached responses by mtime "
        "(default: 600). Ignored if --since-min is given.",
    )
    p.add_argument(
        "--since-min",
        type=float,
        default=None,
        help="Instead of --last-n, audit cached responses modified within "
        "the last N minutes.",
    )
    p.add_argument(
        "--diversity-min",
        type=float,
        default=DEFAULT_DIVERSITY_MIN,
        help=f"diversity_ratio below this ⇒ LOST_CAUSE "
        f"(default: {DEFAULT_DIVERSITY_MIN}).",
    )
    p.add_argument(
        "--diversity-marginal",
        type=float,
        default=DEFAULT_DIVERSITY_MARGINAL,
        help=f"diversity_ratio in [diversity-min, this) ⇒ MARGINAL "
        f"(default: {DEFAULT_DIVERSITY_MARGINAL}).",
    )
    p.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help=f"projected attempts above this ⇒ LOST_CAUSE; mirrors the "
        f"runner's MAX_ATTEMPTS_DEFAULT (default: {DEFAULT_MAX_ATTEMPTS}).",
    )
    p.add_argument(
        "--strategy-marker",
        action="append",
        default=[],
        metavar="NAME=SUBSTRING",
        help="Repeatable. Add/override the response-content marker used to "
        "attribute a cached response to a strategy.",
    )
    p.add_argument(
        "--cache-dir",
        type=Path,
        default=REPO_ROOT / ".cache" / "llm",
        help="LLM response cache dir (default: .cache/llm).",
    )
    p.add_argument(
        "--cost-audit",
        type=Path,
        default=REPO_ROOT / "results" / "cost_audit" / "summary.json",
        help="cost_audit summary.json for historical token means.",
    )
    p.add_argument(
        "--log",
        action="append",
        default=None,
        type=Path,
        help="Repeatable. Runner log(s) to scrape n_attempts from. "
        "Default: /tmp/exp7_*.log if present.",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "results" / "seed_yield_audit",
        help="Output dir for report.json + report.md "
        "(default: results/seed_yield_audit).",
    )
    return p


def default_log_paths() -> list[Path]:
    """/tmp/exp7_*.log if any exist (optional input — never required)."""
    return sorted(Path("/tmp").glob("exp7_*.log"))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    variants = expand_variants(args.variants)
    marker_map = parse_marker_overrides(args.strategy_marker)
    log_paths = list(args.log) if args.log else default_log_paths()

    report = audit(
        target=args.target,
        model=args.model,
        strategies=strategies,
        variants=variants,
        cache_dir=args.cache_dir,
        cost_audit_path=args.cost_audit,
        log_paths=log_paths,
        last_n=(None if args.since_min is not None else args.last_n),
        since_min=args.since_min,
        marker_map=marker_map,
        diversity_min=args.diversity_min,
        diversity_marginal=args.diversity_marginal,
        max_attempts=args.max_attempts,
    )

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{args.target}_{_safe_model(args.model)}.json"
    md_path = out_dir / f"{args.target}_{_safe_model(args.model)}.md"
    json_path.write_text(json.dumps(report, indent=2))
    md = render_markdown(report)
    md_path.write_text(md)

    # Also echo the markdown to stdout so the tool is usable in a pipe.
    print(md)
    print(f"\n[written] {json_path}")
    print(f"[written] {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
