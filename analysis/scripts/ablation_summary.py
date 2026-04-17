"""Aggregate the 4x2 ablation experiment into a single markdown summary.

Reads results/ablation_v3/{m1,m2}/<variant>/<model>/summary.json plus the
random-anchor cell, then writes results/ablation_v3/summary.md.

Metrics:
  M1: corpus union edges covered (general breadth)
  M2: fraction of target branches where seed took the specific uncovered side
      Slices: all-50 / shown-30 / held_back-20 / random_miss (targets random never hit)
  Unique: targets hit by at least one LLM cell but ZERO random seeds (pure LLM value)
  Per-target: hit count across all cells to show which branches are hard
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

RESULTS_ROOT = REPO_ROOT / "results/ablation_v3"
OUT_PATH = RESULTS_ROOT / "summary.md"

VARIANTS = ["v0_none", "v1_src", "v2_src_tests", "v3_all", "v4_src_gaps"]
MODELS = ["claude-sonnet-4-6", "claude-haiku-4-5-20251001", "llama-3.1-8b-instruct"]
MODEL_SHORT = {
    "claude-sonnet-4-6": "sonnet",
    "claude-haiku-4-5-20251001": "haiku",
    "llama-3.1-8b-instruct": "llama8b",
}
SLICES = ("all", "shown", "held_back")


def _load(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def _m1(variant: str, model: str | None) -> dict | None:
    if model is None:
        return _load(RESULTS_ROOT / "m1" / variant / "summary.json")
    return _load(RESULTS_ROOT / "m1" / variant / model / "summary.json")


def _m2(variant: str, model: str | None) -> dict | None:
    if model is None:
        return _load(RESULTS_ROOT / "m2" / variant / "summary.json")
    return _load(RESULTS_ROOT / "m2" / variant / model / "summary.json")


def _fmt_ci(ci: list[float]) -> str:
    return f"[{ci[0]:.2f}, {ci[1]:.2f}]"


def _load_hits_jsonl(variant: str, model: str | None) -> list[dict]:
    if model is None:
        p = RESULTS_ROOT / "m2" / variant / "gap_hits.jsonl"
    else:
        p = RESULTS_ROOT / "m2" / variant / model / "gap_hits.jsonl"
    if not p.is_file():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def _random_hit_set() -> set[tuple[str, int]]:
    """Set of (file, line) targets that the random anchor union ever hit."""
    rows = _load_hits_jsonl("random", None)
    return {(r["target_file"], r["target_line"]) for r in rows if r["hit"]}


def render_m1_table() -> str:
    lines = ["### M1 — General edges covered (corpus union over 100 seeds)", ""]
    header_models = " | ".join(MODEL_SHORT[m] for m in MODELS)
    lines.append(f"| variant | {header_models} |")
    lines.append("|---|" + "---|" * len(MODELS))
    for v in VARIANTS:
        row = [v]
        for m in MODELS:
            d = _m1(v, m)
            if d is None:
                row.append("-")
            else:
                pct = 100 * d["edges_covered"] / d["edges_total"] if d["edges_total"] else 0
                row.append(f"{d['edges_covered']} ({pct:.1f}%)")
        lines.append("| " + " | ".join(row) + " |")
    rnd = _m1("random", None)
    if rnd is not None:
        pct = 100 * rnd["edges_covered"] / rnd["edges_total"] if rnd["edges_total"] else 0
        lines.append(f"| random (anchor) | {rnd['edges_covered']} ({pct:.1f}%) | | |")
    lines.append("")
    lines.append("_Cells show `edges_covered (pct of 3380 total)` for the merged corpus._")
    return "\n".join(lines) + "\n"


def render_m2_table(slice_name: str, extra_label: str = "") -> str:
    label_map = {
        "all": "All 50 target branches",
        "shown": "30 in-prompt targets (shown to V3/V4)",
        "held_back": "20 held-back targets (never shown)",
        "random_miss": "Targets the random anchor NEVER hit",
    }
    title = f"### M2 — {label_map.get(slice_name, slice_name)}{extra_label}"
    lines = [title, ""]
    lines.append("| variant | model | union_hit | per_seed_mean [CI95] | frac_seeds_any_hit [CI95] |")
    lines.append("|---|---|---|---|---|")

    random_miss_set = _random_hit_set()

    for v in VARIANTS:
        for m in MODELS:
            d = _m2(v, m)
            if d is None:
                continue
            if slice_name == "random_miss":
                # Compute on-the-fly from gap_hits.jsonl
                rows = _load_hits_jsonl(v, m)
                if not rows:
                    continue
                # Collect seed ids in order
                from collections import defaultdict
                target_keys = sorted({(r["target_file"], r["target_line"]) for r in rows
                                       if (r["target_file"], r["target_line"]) not in random_miss_set},
                                     key=lambda x: x)
                miss_keys = {(r["target_file"], r["target_line"]) for r in rows
                             if (r["target_file"], r["target_line"]) not in random_miss_set}
                # Actually: random_miss = targets NOT hit by random
                miss_targets = {(r["target_file"], r["target_line"]) for r in
                                _load_hits_jsonl("random", None) if not r["hit"]}
                # Re-index: per seed, hits on miss_targets
                seed_hits: dict[str, list[bool]] = defaultdict(list)
                target_list = sorted(miss_targets)
                for (tfile, tline) in target_list:
                    for r in rows:
                        if r["target_file"] == tfile and r["target_line"] == tline:
                            seed_hits[r["seed_id"]].append(r["hit"])
                if not target_list or not seed_hits:
                    continue
                n_t = len(target_list)
                seeds = list(seed_hits.keys())
                union_hit = sum(1 for (tf, tl) in target_list
                                if any(r["hit"] for r in rows
                                       if r["target_file"] == tf and r["target_line"] == tl))
                union_frac = union_hit / n_t if n_t else 0
                per_seed_fracs = [sum(v2) / n_t for v2 in seed_hits.values()]
                mean_frac = sum(per_seed_fracs) / len(per_seed_fracs) if per_seed_fracs else 0
                frac_any = sum(1 for f in per_seed_fracs if f > 0) / len(per_seed_fracs) if per_seed_fracs else 0
                lines.append(
                    f"| {v} | {MODEL_SHORT[m]} | "
                    f"{union_frac:.2f} ({union_hit}/{n_t}) | "
                    f"{mean_frac:.2f} — | "
                    f"{frac_any:.2f} — |"
                )
                continue

            s = d["slices"].get(slice_name)
            if s is None:
                continue
            lines.append(
                f"| {v} | {MODEL_SHORT[m]} | "
                f"{s['union_frac_targets_hit']:.2f} ({s['union_targets_hit']}/{s['n_targets_in_slice']}) | "
                f"{s['mean_frac_per_seed']:.2f} {_fmt_ci(s['mean_frac_per_seed_ci95'])} | "
                f"{s['frac_seeds_with_any_hit']:.2f} {_fmt_ci(s['frac_seeds_with_any_hit_ci95'])} |"
            )

    # Random anchor row
    if slice_name == "random_miss":
        pass  # random never hits its own misses by definition
    else:
        rnd = _m2("random", None)
        if rnd is not None:
            s = rnd["slices"].get(slice_name)
            if s is not None:
                lines.append(
                    f"| random | — | "
                    f"{s['union_frac_targets_hit']:.2f} ({s['union_targets_hit']}/{s['n_targets_in_slice']}) | "
                    f"{s['mean_frac_per_seed']:.2f} {_fmt_ci(s['mean_frac_per_seed_ci95'])} | "
                    f"{s['frac_seeds_with_any_hit']:.2f} {_fmt_ci(s['frac_seeds_with_any_hit_ci95'])} |"
                )
    lines.append("")
    return "\n".join(lines) + "\n"


def render_pairwise() -> str:
    pairs = [
        ("v1_src", "v2_src_tests", "tests added (+tests)"),
        ("v1_src", "v4_src_gaps", "gaps added (+gaps)"),
        ("v3_all", "v4_src_gaps", "tests dropped given gaps (-tests|gaps)"),
        ("v0_none", "v1_src",     "source added (+source)"),
    ]
    lines = ["### Pairwise variant deltas (M2 / all-50, **union_frac_targets_hit**)", ""]
    lines.append("| comparison | model | left | right | delta |")
    lines.append("|---|---|---|---|---|")
    for left, right, label in pairs:
        for m in MODELS:
            dl = _m2(left, m)
            dr = _m2(right, m)
            if dl is None or dr is None:
                continue
            a = dl["slices"]["all"]["union_frac_targets_hit"]
            b = dr["slices"]["all"]["union_frac_targets_hit"]
            lines.append(
                f"| {label} ({left}→{right}) | {MODEL_SHORT[m]} | "
                f"{a:.3f} | {b:.3f} | {b-a:+.3f} |"
            )
    lines.append("")
    return "\n".join(lines) + "\n"


def render_unique_hits() -> str:
    """Targets hit by at least one LLM cell but never by random."""
    random_hit = _random_hit_set()
    lines = ["### Unique LLM value — targets the random anchor never hit", ""]
    lines.append("(union over all seeds in each cell; random hit 0 seeds on these targets)")
    lines.append("")
    lines.append("| variant | model | unique_hits / random_misses | unique_frac |")
    lines.append("|---|---|---|---|")

    # Build random miss set
    rnd_rows = _load_hits_jsonl("random", None)
    all_targets = {(r["target_file"], r["target_line"]) for r in rnd_rows}
    random_miss_targets = all_targets - random_hit

    n_miss = len(random_miss_targets)
    for v in VARIANTS:
        for m in MODELS:
            rows = _load_hits_jsonl(v, m)
            if not rows:
                continue
            llm_hit_on_misses = {
                (r["target_file"], r["target_line"])
                for r in rows
                if r["hit"] and (r["target_file"], r["target_line"]) in random_miss_targets
            }
            n = len(llm_hit_on_misses)
            frac = n / n_miss if n_miss else 0
            lines.append(f"| {v} | {MODEL_SHORT[m]} | {n}/{n_miss} | {frac:.2f} |")
    lines.append("")
    lines.append(f"_Random misses = {n_miss} targets the random anchor never hit._")
    return "\n".join(lines) + "\n"


def render_per_target_hits() -> str:
    """How many cells hit each target (sorted by difficulty = fewest hits first)."""
    rnd_rows = _load_hits_jsonl("random", None)
    if not rnd_rows:
        return ""

    all_targets = sorted({(r["target_file"], r["target_line"]) for r in rnd_rows},
                         key=lambda x: x)

    # Count hits per target across all LLM cells
    hit_counts: dict[tuple, int] = {t: 0 for t in all_targets}
    total_cells = 0
    for v in VARIANTS:
        for m in MODELS:
            rows = _load_hits_jsonl(v, m)
            if not rows:
                continue
            total_cells += 1
            cell_hits = {(r["target_file"], r["target_line"]) for r in rows if r["hit"]}
            for t in all_targets:
                if t in cell_hits:
                    hit_counts[t] += 1

    random_hit_set = {(r["target_file"], r["target_line"]) for r in rnd_rows if r["hit"]}

    # Sort by hit_count ascending (hardest first)
    sorted_targets = sorted(all_targets, key=lambda t: hit_counts[t])

    lines = [f"### Per-target difficulty (hardest first, out of {total_cells} LLM cells)", ""]
    lines.append("| target | line | random_hit | llm_cells_hitting | difficulty |")
    lines.append("|---|---|---|---|---|")
    for (tf, tl) in sorted_targets:
        n = hit_counts[(tf, tl)]
        rh = "yes" if (tf, tl) in random_hit_set else "**no**"
        diff = "hard" if n < total_cells * 0.3 else ("medium" if n < total_cells * 0.7 else "easy")
        lines.append(f"| {tf} | {tl} | {rh} | {n}/{total_cells} | {diff} |")
    lines.append("")
    return "\n".join(lines) + "\n"


def render_sanity_checks() -> str:
    lines = ["### Sanity checks (union_frac as headline metric)", ""]

    # (a) V3 >= V1 on M2/all union_frac for >= 2 of 3 models
    wins_a = 0
    notes_a = []
    for m in MODELS:
        d1 = _m2("v1_src", m)
        d3 = _m2("v3_all", m)
        if d1 is None or d3 is None:
            continue
        a1 = d1["slices"]["all"]["union_frac_targets_hit"]
        a3 = d3["slices"]["all"]["union_frac_targets_hit"]
        won = a3 >= a1
        if won:
            wins_a += 1
        notes_a.append(f"{MODEL_SHORT[m]} V1={a1:.3f} V3={a3:.3f} ({'OK' if won else 'MISS'})")
    lines.append(f"- **(a)** V3 ≥ V1 union_frac/all in {wins_a}/3 models: " + "; ".join(notes_a))

    # (b) V3/V4 > random on M2/all union_frac
    rnd = _m2("random", None)
    rnd_val = rnd["slices"]["all"]["union_frac_targets_hit"] if rnd else None
    beat = 0
    total = 0
    if rnd_val is not None:
        for v in ("v3_all", "v4_src_gaps"):
            for m in MODELS:
                d = _m2(v, m)
                if d is None:
                    continue
                total += 1
                if d["slices"]["all"]["union_frac_targets_hit"] > rnd_val:
                    beat += 1
        lines.append(f"- **(b)** V3/V4 > random ({rnd_val:.3f}) on union_frac/all: {beat}/{total} cells")

    # (c) shown >= held_back union_frac on V3/V4
    ok_c, tot_c = 0, 0
    for v in ("v3_all", "v4_src_gaps"):
        for m in MODELS:
            d = _m2(v, m)
            if d is None:
                continue
            tot_c += 1
            sh = d["slices"]["shown"]["union_frac_targets_hit"]
            hb = d["slices"]["held_back"]["union_frac_targets_hit"]
            if sh >= hb:
                ok_c += 1
    lines.append(f"- **(c)** Targeting effect (shown ≥ held_back union_frac) on V3/V4: {ok_c}/{tot_c}")

    # (d) LLM M1 > random
    rnd_m1 = _m1("random", None)
    rnd_m1_val = rnd_m1["edges_covered"] if rnd_m1 else 0
    beat_d, tot_d = 0, 0
    for v in VARIANTS:
        for m in MODELS:
            d = _m1(v, m)
            if d is None:
                continue
            tot_d += 1
            if d["edges_covered"] > rnd_m1_val:
                beat_d += 1
    lines.append(f"- **(d)** LLM M1 edges > random ({rnd_m1_val}): {beat_d}/{tot_d} cells")

    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parts = [
        "# 4x2 ablation (RE2) — improved run",
        "",
        "**Design:** 5 variants × 3 models, 100 seeds/cell, evaluated by LLVM coverage replay.",
        "Target set: 50 **asymmetric** gap branches (one side covered by upstream tests, one not).",
        "M2 measures whether seeds take the specific uncovered side — not merely whether they reach the branch.",
        "",
        "**Variants:**",
        "- v0_none        = harness format only (no source, no tests, no gaps) — floor baseline",
        "- v1_src         = + library source code",
        "- v2_src_tests   = + 5 RE2 public API unit tests",
        "- v3_all         = + 30 asymmetric gap branches with TRUE/FALSE side hint, targeted CoT framing",
        "- v4_src_gaps    = source + gaps (no tests), same targeted framing",
        "",
        "**Anchor:** 100 random regex-shaped seeds (same format/length budget).",
        "",
        render_m1_table(),
        render_m2_table("all"),
        render_m2_table("shown"),
        render_m2_table("held_back"),
        render_m2_table("random_miss",
                         " _(targets random never hit — pure LLM territory)_"),
        render_pairwise(),
        render_unique_hits(),
        render_per_target_hits(),
        render_sanity_checks(),
    ]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(parts))
    print(f"wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
