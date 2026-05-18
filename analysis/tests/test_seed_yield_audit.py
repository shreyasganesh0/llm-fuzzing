"""Synthetic, deterministic, offline tests for seed_yield_audit.

No network / LLM / LLVM / scipy. Every fixture is hand-built so the
expected parse-rate, diversity, projection, verdict and wasted-$ figures
are checkable by inspection.

The key behaviours pinned here:
  * parse-rate + diversity are computed correctly on a hand-counted set;
  * the "distinct-seed ceiling < 150" rule fires LOST_CAUSE even when
    parse_ok_rate is high — the exact cot_strict pattern (high parse, low
    diversity, ceiling 142 < 150);
  * a high-diversity, fillable set is VIABLE;
  * the wasted-$ arithmetic is exact for a fixed per-call mean cost;
  * verdict thresholds honour the CLI args (changing --diversity-min
    flips a borderline cell).
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analysis.scripts import seed_yield_audit as sya  # noqa: E402

# scipy is not used anywhere in this module or the tool under test.
pytestmark = pytest.mark.filterwarnings("error::DeprecationWarning")


# ───────────────────────── fixture builders ─────────────────────────────


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _regex_response(regexes: list[str], *, cot: bool = False) -> str:
    """A minimal valid RE2 synthesis JSON response.

    parse_regex_response packs the raw `regex` text into content_b64, so
    distinct regexes ⇒ distinct content_b64, identical regexes ⇒ duplicate
    content_b64 (mirrors the real content-addressed dedup).
    """
    items = []
    for r in regexes:
        reasoning = (
            "Step 1 (Quote): if (x). Step 2 (Locate): parser. "
            "Step 3 (Construct): rep. Step 4 (Regex): emitted."
            if cot
            else "targets a parser branch"
        )
        items.append(
            {"regex": r, "target_gaps": ["parse.cc:1"], "reasoning": reasoning}
        )
    return json.dumps({"regexes": items})


def _write_cache(cache_dir: Path, model: str, idx: int, content: str) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Mirror the real `<safe_model>_<sha>.json` naming convention.
    p = cache_dir / f"{model.replace('/', '_')}_{idx:064x}.json"
    p.write_text(json.dumps({"content": content, "model": model}))
    return p


def _write_seed_dir(base: Path, n: int) -> Path:
    """Create a cell seeds dir with `n` distinct content-addressed seeds."""
    base.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (base / f"seed_{i:064x}.bin").write_bytes(f"seed-{i}".encode())
    return base


# ──────────────────────── parse-rate / diversity ────────────────────────


def test_parse_rate_and_diversity_hand_counted(tmp_path: Path):
    """3 OK responses (5 distinct regexes total across 9 instances) +
    1 unparseable response ⇒ ok_rate 3/4, diversity 5/9."""
    cache = tmp_path / ".cache" / "llm"
    model = "codestral-22b"
    # Response A: 3 instances, 3 distinct.
    _write_cache(cache, model, 1, _regex_response(["a+", "b+", "c+"]))
    # Response B: 3 instances, but 2 are dupes of A ⇒ 1 new distinct ("d+").
    _write_cache(cache, model, 2, _regex_response(["a+", "b+", "d+"]))
    # Response C: 3 instances, 1 new distinct ("e+"), 2 dupes.
    _write_cache(cache, model, 3, _regex_response(["a+", "e+", "c+"]))
    # Response D: not JSON at all ⇒ parse_failure, contributes 0 instances.
    _write_cache(cache, model, 4, "this is not json at all")

    files = sya.list_recent_cache_files(
        cache, model, last_n=100, since_min=None
    )
    groups = sya.aggregate_response_groups(
        files, target="re2", model=model, marker_map={}
    )
    # Empty marker_map ⇒ everything lands in the aggregate bucket.
    assert set(groups) == {"<non-cot_strict aggregate>"}
    g = groups["<non-cot_strict aggregate>"]
    assert g.n_responses == 4
    assert g.parse_ok == 3
    assert g.parse_ok_rate == pytest.approx(3 / 4)
    # 9 parsed instances: a+,b+,c+ / a+,b+,d+ / a+,e+,c+
    assert g.total_parsed_seed_instances == 9
    # distinct: {a+, b+, c+, d+, e+} = 5
    assert g.unique_content_b64 == 5
    assert g.diversity_ratio == pytest.approx(5 / 9)


# ─────────────── ceiling < 150 ⇒ LOST_CAUSE despite high parse ──────────


def test_ceiling_below_floor_is_lost_cause_even_with_high_parse(tmp_path: Path):
    """The cot_strict pattern: parse_ok_rate ~100%, but the producible
    universe of distinct seeds is below 150 ⇒ LOST_CAUSE, with the
    load-bearing reason being the ceiling, not parsing."""
    # 200 responses, each parses fine, but they only ever emit 10 distinct
    # regexes ⇒ ceiling 10 << 150, diversity tiny.
    n_resp = 200
    distinct = [f"r{i}+" for i in range(10)]
    cache = tmp_path / ".cache" / "llm"
    model = "codestral-22b"
    for i in range(n_resp):
        # rotate so every response is valid JSON and parses OK
        _write_cache(
            cache, model, i + 1, _regex_response([distinct[i % 10]], cot=True)
        )

    files = sya.list_recent_cache_files(cache, model, last_n=999, since_min=None)
    groups = sya.aggregate_response_groups(
        files,
        target="re2",
        model=model,
        marker_map=sya.DEFAULT_STRATEGY_MARKERS,
    )
    # cot marker present ⇒ attributed to cot_strict, NOT aggregate.
    assert "cot_strict" in groups
    g = groups["cot_strict"]
    assert g.parse_ok_rate == pytest.approx(1.0)  # high parse
    assert g.unique_content_b64 == 10
    assert g.diversity_ratio == pytest.approx(10 / 200)  # 0.05, very low

    seeds_dir = _write_seed_dir(
        tmp_path / "seeds" / "cot_strict" / "v0", n=12
    )
    realized = sya.count_distinct_seeds(seeds_dir)
    assert realized == 12

    ceiling, rate, projected, can_fill, exceeds = sya.project_yield(
        realized_unique_seeds=realized,
        n_responses=g.n_responses,
        diversity_ratio=g.diversity_ratio,
        total_parsed_seed_instances=g.total_parsed_seed_instances,
        unique_content_b64=g.unique_content_b64,
    )
    # ceiling = max(realized 12, observed-unique 10) = 12 < 150
    assert ceiling == 12
    assert can_fill is False
    assert projected == float("inf")
    assert exceeds is True

    verdict, reasons = sya.decide_verdict(
        diversity_ratio=g.diversity_ratio,
        distinct_seed_ceiling=ceiling,
        projected_attempts=projected,
        realized_unique_seeds=realized,
        diversity_min=sya.DEFAULT_DIVERSITY_MIN,
        diversity_marginal=sya.DEFAULT_DIVERSITY_MARGINAL,
        max_attempts=sya.DEFAULT_MAX_ATTEMPTS,
    )
    assert verdict == "LOST_CAUSE"
    # the load-bearing ceiling reason must be present and explicit
    assert any("CANNOT EVER FILL" in r for r in reasons)


# ─────────────────── high-diversity fillable ⇒ VIABLE ───────────────────


def test_high_diversity_fillable_is_viable(tmp_path: Path):
    """Many distinct regexes, high diversity, dir already at the floor."""
    cache = tmp_path / ".cache" / "llm"
    model = "codestral-22b"
    for i in range(50):
        # 4 unique regexes per response, all globally unique ⇒ diversity 1.0
        _write_cache(
            cache,
            model,
            i + 1,
            _regex_response(
                [f"u{i}_{j}+" for j in range(4)]
            ),
        )
    files = sya.list_recent_cache_files(cache, model, last_n=999, since_min=None)
    groups = sya.aggregate_response_groups(
        files, target="re2", model=model, marker_map={}
    )
    g = groups["<non-cot_strict aggregate>"]
    assert g.diversity_ratio == pytest.approx(1.0)

    seeds_dir = _write_seed_dir(tmp_path / "seeds" / "few_shot" / "v0", n=150)
    realized = sya.count_distinct_seeds(seeds_dir)
    ceiling, rate, projected, can_fill, exceeds = sya.project_yield(
        realized_unique_seeds=realized,
        n_responses=g.n_responses,
        diversity_ratio=g.diversity_ratio,
        total_parsed_seed_instances=g.total_parsed_seed_instances,
        unique_content_b64=g.unique_content_b64,
    )
    assert can_fill is True
    assert projected == 0.0  # already at floor
    verdict, _ = sya.decide_verdict(
        diversity_ratio=g.diversity_ratio,
        distinct_seed_ceiling=ceiling,
        projected_attempts=projected,
        realized_unique_seeds=realized,
        diversity_min=sya.DEFAULT_DIVERSITY_MIN,
        diversity_marginal=sya.DEFAULT_DIVERSITY_MARGINAL,
        max_attempts=sya.DEFAULT_MAX_ATTEMPTS,
    )
    assert verdict == "VIABLE"


# ───────────────────────── wasted-$ arithmetic ──────────────────────────


def test_wasted_dollar_math_exact_for_fixed_mean_cost(tmp_path: Path):
    """Given a fixed per-call mean cost from a synthetic cost_audit, the
    wasted-$ for a LOST_CAUSE cell is attempts_spent x per_call exactly."""
    cache = tmp_path / ".cache" / "llm"
    model = "codestral-22b"
    # 30 responses, only 3 distinct ⇒ low diversity ⇒ LOST_CAUSE.
    for i in range(30):
        _write_cache(
            cache, model, i + 1, _regex_response([f"d{i % 3}+"])
        )

    # codestral-22b is in PRICING_USD_PER_MTOK (input 0.20, output 0.60 /Mtok).
    # Pick round token means so per-call cost is exact:
    #   in=1_000_000 tok x 0.20/Mtok = 0.20
    #   out=1_000_000 tok x 0.60/Mtok = 0.60  → per_call = 0.80 USD
    cost_audit = tmp_path / "cost_audit.json"
    cost_audit.write_text(
        json.dumps(
            {
                "by_model": {
                    model: {
                        "mean_input_tokens": 1_000_000,
                        "mean_output_tokens": 1_000_000,
                    }
                }
            }
        )
    )
    basis = sya.resolve_cost_basis(model, cost_audit)
    assert basis.per_call_usd == pytest.approx(0.80)
    assert "cost_audit" in basis.source

    # Build a cell whose seeds dir has 5 realized seeds → with no log,
    # attempts_spent = realized 5 + CONSEC_FAIL_WINDOW 20 = 25.
    seeds_dir = sya.resolve_cell_seeds_dir("re2", "cot_strict", "v0_none", model)
    # We cannot write into the real repo path in a test; instead drive the
    # audit() with a tmp cache + assert the math via the report it returns,
    # using a strategy with NO realized seeds (dir absent ⇒ realized 0).
    report = sya.audit(
        target="re2",
        model=model,
        strategies=["cot_strict"],
        variants=["v0_none"],
        cache_dir=cache,
        cost_audit_path=cost_audit,
        log_paths=[],  # no logs ⇒ heuristic attempts
        last_n=999,
        since_min=None,
        marker_map={},  # force aggregate so the 30 low-div responses apply
        diversity_min=sya.DEFAULT_DIVERSITY_MIN,
        diversity_marginal=sya.DEFAULT_DIVERSITY_MARGINAL,
        max_attempts=sya.DEFAULT_MAX_ATTEMPTS,
    )
    cell = report["cells"][0]
    assert cell["verdict"] == "LOST_CAUSE"
    # realized is whatever exists on disk for that real path (0 on a clean
    # checkout, but could be non-zero); assert the math is internally exact.
    expected = cell["attempts_spent_estimate"] * basis.per_call_usd
    assert cell["est_dollars_wasted"] == pytest.approx(round(expected, 4))
    assert report["total_estimated_dollars_wasted"] == pytest.approx(
        round(expected, 4)
    )
    assert cell["recommendation"].startswith("ABANDON cot_strict/v0_none")
    # seeds_dir resolution mirrors TargetSpec (non-default ⇒ strategy seg).
    assert "/cot_strict/" in str(seeds_dir)


def test_cost_basis_falls_back_to_pricing_table(tmp_path: Path):
    """No cost_audit file ⇒ PRICING table x documented fallback tokens,
    and the source string says so (never silently 0)."""
    basis = sya.resolve_cost_basis(
        "codestral-22b", tmp_path / "does_not_exist.json"
    )
    rate = {"input": 0.20, "output": 0.60}
    expected = (
        sya.FALLBACK_INPUT_TOKENS * rate["input"]
        + sya.FALLBACK_OUTPUT_TOKENS * rate["output"]
    ) / 1_000_000
    assert basis.per_call_usd == pytest.approx(expected)
    assert "fallback token estimate" in basis.source

    unknown = sya.resolve_cost_basis(
        "no-such-model", tmp_path / "nope.json"
    )
    assert unknown.per_call_usd == 0.0
    assert "UNKNOWN pricing" in unknown.source


# ───────────────── verdict thresholds honour CLI args ───────────────────


def test_verdict_thresholds_honour_cli_args():
    """A diversity of 0.30 with a high ceiling: MARGINAL at default 0.20
    min; flips to LOST_CAUSE when --diversity-min is raised to 0.40."""
    common = dict(
        distinct_seed_ceiling=500,   # well above floor
        projected_attempts=10.0,     # cheap to fill
        realized_unique_seeds=140,
        diversity_marginal=sya.DEFAULT_DIVERSITY_MARGINAL,
        max_attempts=sya.DEFAULT_MAX_ATTEMPTS,
    )
    v_default, _ = sya.decide_verdict(
        diversity_ratio=0.30, diversity_min=0.20, **common
    )
    assert v_default == "MARGINAL"

    v_strict, reasons = sya.decide_verdict(
        diversity_ratio=0.30, diversity_min=0.40, **common
    )
    assert v_strict == "LOST_CAUSE"
    assert any("diversity_ratio 0.300 < diversity_min 0.40" in r for r in reasons)

    # And a high-diversity case is VIABLE regardless.
    v_ok, _ = sya.decide_verdict(
        diversity_ratio=0.85, diversity_min=0.20, **common
    )
    assert v_ok == "VIABLE"


def test_realized_fill_override_beats_low_diversity():
    """A cell the runner already filled to 150 is VIABLE even when the
    re-parsed regex-level diversity is low. Mirrors real few_shot cells:
    150 seed_*.bin on disk (flag-byte entropy fills) but a re-parsed
    regex diversity of ~0.26 that would otherwise read MARGINAL.

    Crucially this override must NOT rescue cot_strict: its cells are
    sub-floor (<150 on disk) so the override never fires for them.
    """
    # Filled cell, low regex-level diversity → VIABLE via the override.
    v_filled, reasons = sya.decide_verdict(
        diversity_ratio=0.26,
        distinct_seed_ceiling=128,   # below 150 at regex level...
        projected_attempts=float("inf"),
        realized_unique_seeds=150,   # ...but the runner DID fill it
        diversity_min=sya.DEFAULT_DIVERSITY_MIN,
        diversity_marginal=sya.DEFAULT_DIVERSITY_MARGINAL,
        max_attempts=sya.DEFAULT_MAX_ATTEMPTS,
    )
    assert v_filled == "VIABLE"
    assert any("already filled" in r for r in reasons)

    # Sub-floor cell with the SAME low diversity → still LOST_CAUSE
    # (the override only applies at/above the floor).
    v_subfloor, _ = sya.decide_verdict(
        diversity_ratio=0.10,
        distinct_seed_ceiling=124,
        projected_attempts=float("inf"),
        realized_unique_seeds=124,   # cot_strict/v3_all: 124 < 150
        diversity_min=sya.DEFAULT_DIVERSITY_MIN,
        diversity_marginal=sya.DEFAULT_DIVERSITY_MARGINAL,
        max_attempts=sya.DEFAULT_MAX_ATTEMPTS,
    )
    assert v_subfloor == "LOST_CAUSE"


def test_projected_attempts_over_cap_is_lost_cause():
    """Ceiling is fine and diversity is fine, but the marginal new-seed
    rate is so low that filling needs > max_attempts ⇒ LOST_CAUSE."""
    # 1 new seed every 5 responses, 100 seeds short ⇒ 500 attempts > 300.
    ceiling, rate, projected, can_fill, exceeds = sya.project_yield(
        realized_unique_seeds=50,
        n_responses=1000,
        diversity_ratio=0.9,
        total_parsed_seed_instances=1000,
        unique_content_b64=200,  # ceiling 200 >= 150 (can fill in principle)
    )
    assert can_fill is True
    # rate = 200/1000 = 0.2 new/resp; remaining = 100 ⇒ proj = 500
    assert projected == pytest.approx(500.0)
    assert exceeds is True
    verdict, reasons = sya.decide_verdict(
        diversity_ratio=0.9,
        distinct_seed_ceiling=ceiling,
        projected_attempts=projected,
        realized_unique_seeds=50,
        diversity_min=sya.DEFAULT_DIVERSITY_MIN,
        diversity_marginal=sya.DEFAULT_DIVERSITY_MARGINAL,
        max_attempts=sya.DEFAULT_MAX_ATTEMPTS,
    )
    assert verdict == "LOST_CAUSE"
    assert any("> cap 300" in r for r in reasons)


def test_window_selection_last_n_and_since_min(tmp_path: Path):
    """last_n keeps the newest N; since_min keeps the recent slice."""
    import os
    import time as _t

    cache = tmp_path / ".cache" / "llm"
    model = "codestral-22b"
    paths = []
    for i in range(5):
        p = _write_cache(cache, model, i + 1, _regex_response(["a+"]))
        paths.append(p)
    # Stagger mtimes: file i is i*1000s old.
    now = _t.time()
    for i, p in enumerate(paths):
        old = now - i * 1000
        os.utime(p, (old, old))

    newest3 = sya.list_recent_cache_files(
        cache, model, last_n=3, since_min=None
    )
    assert len(newest3) == 3
    # newest first
    assert newest3[0].stat().st_mtime >= newest3[-1].stat().st_mtime

    # since_min = 25 min ⇒ keeps files within 1500s ⇒ i=0 (0s) & i=1 (1000s).
    recent = sya.list_recent_cache_files(
        cache, model, last_n=None, since_min=25.0
    )
    assert len(recent) == 2


def test_render_markdown_smoke(tmp_path: Path):
    """The markdown renderer produces the documented columns and a total."""
    cache = tmp_path / ".cache" / "llm"
    model = "codestral-22b"
    for i in range(10):
        _write_cache(cache, model, i + 1, _regex_response([f"r{i % 2}+"]))
    report = sya.audit(
        target="re2",
        model=model,
        strategies=["cot_strict"],
        variants=["v0_none"],
        cache_dir=cache,
        cost_audit_path=tmp_path / "absent.json",
        log_paths=[],
        last_n=999,
        since_min=None,
        marker_map={},
        diversity_min=sya.DEFAULT_DIVERSITY_MIN,
        diversity_marginal=sya.DEFAULT_DIVERSITY_MARGINAL,
        max_attempts=sya.DEFAULT_MAX_ATTEMPTS,
    )
    md = sya.render_markdown(report)
    assert "| cell | n_resp | parse_ok% | div_ratio | unique/150 |" in md
    assert "Total estimated wasted spend" in md
    assert "Attribution caveat" in md
