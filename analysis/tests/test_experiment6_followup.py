"""Unit tests for analysis/scripts/experiment6_followup.py.

Pure offline plumbing tests on SYNTHETIC fixtures only — synthetic
``gap_hits.jsonl`` + synthetic entropies + a fake per-seed-M1 cache +
hand-built logprob sidecars. They never invoke the harfbuzz ``seed_replay``
binary, LLVM, the LLM, or any network, so the suite stays green without the
coverage toolchain (matching the experiment6_entropy / experiment7_rank test
discipline). The one real-replay path is guarded with skipif on the
harfbuzz coverage binary's presence.

Coverage:
  * surrogate file -> DEEP/EARLY mapping (hb-ot/hb-shape=DEEP,
    hb-blob/hb-open=EARLY) + the verified EARLY=13/DEEP=37 counts on the
    real frozen fixture.
  * deep-reach math on a hand-checked synthetic hit matrix.
  * quartile assignment (Q1=lowest, equal-count rank buckets, ties broken
    by input_id, uneven n).
  * quartile_table deep_reach_rate / mean_per_seed_M1 + Q1-Q4 gap.
  * the §5 3-way interpretation-rule branch selection (SUPPORTED /
    DIFFERENT / WRONG-union-level).
  * prefix entropy uses ONLY the first 8 payload tokens (sidecar with >8
    payload tokens; assert exactly 8 counted).
  * a skipif-guarded real per-seed-M1 replay smoke (binary present).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from analysis.scripts.experiment6_followup import (
    DEEP,
    EARLY,
    PREFIX_TOKEN_COUNT,
    assign_quartiles,
    classify_branch_file,
    compute_per_seed_m1,
    conditional_m1_differs,
    interpret_pool,
    load_deep_target_keys,
    per_seed_prefix_entropies,
    prefix_entropy,
    quartile_table,
    replay_one_seed_edges,
    seed_deep_reach,
)
from core.targets import TARGETS

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_TARGETS_PATH = Path(TARGETS["harfbuzz"].m2_targets_path)
HB_BINARY = Path(TARGETS["harfbuzz"].coverage_binary)


# ---------------------------------------------------------------------------
# Frozen DEEP / EARLY surrogate (FOLLOWUP.md §3)
# ---------------------------------------------------------------------------


def test_classify_branch_file_surrogate():
    # DEEP = hb-ot OR hb-shape (by basename)
    assert classify_branch_file("src/hb-ot-shape.cc") == DEEP
    assert classify_branch_file("src/hb-ot-layout-gsub-table.hh") == DEEP
    assert classify_branch_file("src/hb-ot-map.cc") == DEEP
    assert classify_branch_file("src/hb-shape-plan.cc") == DEEP
    # EARLY = hb-blob OR hb-open (by basename)
    assert classify_branch_file("src/hb-blob.cc") == EARLY
    assert classify_branch_file("src/hb-open-file-private.hh") == EARLY
    assert classify_branch_file("src/hb-open-type-private.hh") == EARLY
    # basename only — a directory called hb-ot must not leak DEEP onto a
    # non-matching basename.
    assert classify_branch_file("src/hb-ot/hb-buffer.cc") == "UNMAPPED"


def test_load_deep_target_keys_real_fixture_counts():
    """The real frozen fixture must verify EARLY=13, DEEP=37, total 50."""
    deep_keys, counts = load_deep_target_keys(REAL_TARGETS_PATH)
    assert counts[EARLY] == 13
    assert counts[DEEP] == 37
    assert counts["UNMAPPED"] == 0
    assert counts[EARLY] + counts[DEEP] == 50
    assert len(deep_keys) == 37
    # Keys are "file:line" and every one classifies DEEP.
    for key in deep_keys:
        file = key.rsplit(":", 1)[0]
        assert classify_branch_file(file) == DEEP


def test_load_deep_target_keys_surrogate_drift_raises(tmp_path: Path):
    bad = tmp_path / "m2.json"
    bad.write_text(
        json.dumps(
            {
                "shown": [
                    {"file": "src/hb-cff-table.hh", "line": 5,
                     "uncovered_side": "true"}
                ],
                "held_back": [],
            }
        )
    )
    with pytest.raises(ValueError, match="surrogate drift"):
        load_deep_target_keys(bad)


# ---------------------------------------------------------------------------
# Deep-reach from a hand-checked synthetic gap_hits.jsonl
# ---------------------------------------------------------------------------


def _write_gap_hits(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_seed_deep_reach_hand_checked(tmp_path: Path):
    deep_keys = {"src/hb-ot-shape.cc:100", "src/hb-ot-map.cc:42"}
    gp = tmp_path / "gap_hits.jsonl"
    _write_gap_hits(
        gp,
        [
            # seed A: hits a DEEP branch -> reaches deep
            {"seed_id": "seed_a", "target_file": "src/hb-ot-shape.cc",
             "target_line": 100, "hit": True},
            {"seed_id": "seed_a", "target_file": "src/hb-blob.cc",
             "target_line": 1, "hit": True},
            # seed B: only hits an EARLY branch -> NOT deep
            {"seed_id": "seed_b", "target_file": "src/hb-blob.cc",
             "target_line": 1, "hit": True},
            {"seed_id": "seed_b", "target_file": "src/hb-ot-shape.cc",
             "target_line": 100, "hit": False},
            # seed C: hit==True on a DEEP file but a NON-target line -> not
            # in deep_keys -> NOT deep.
            {"seed_id": "seed_c", "target_file": "src/hb-ot-shape.cc",
             "target_line": 999, "hit": True},
            # seed D: hits the other DEEP key
            {"seed_id": "seed_d", "target_file": "src/hb-ot-map.cc",
             "target_line": 42, "hit": True},
            # seed E: appears but never hits anything
            {"seed_id": "seed_e", "target_file": "src/hb-ot-map.cc",
             "target_line": 42, "hit": False},
        ],
    )
    reach = seed_deep_reach(gp, deep_keys)
    # seed_ prefix stripped
    assert reach == {"a": True, "b": False, "c": False, "d": True, "e": False}


# ---------------------------------------------------------------------------
# Quartile assignment
# ---------------------------------------------------------------------------


def test_assign_quartiles_even_split_q1_is_lowest():
    # 8 seeds, scores 0..7 -> 2 per quartile, Q1 = lowest two.
    scores = {f"s{i}": float(i) for i in range(8)}
    q = assign_quartiles(scores)
    assert {q[f"s{i}"] for i in (0, 1)} == {1}
    assert {q[f"s{i}"] for i in (2, 3)} == {2}
    assert {q[f"s{i}"] for i in (4, 5)} == {3}
    assert {q[f"s{i}"] for i in (6, 7)} == {4}


def test_assign_quartiles_uneven_front_loaded_and_tie_break():
    # 6 seeds -> base=1, rem=2 -> sizes [2,2,1,1].
    scores = {"a": 1.0, "b": 1.0, "c": 2.0, "d": 3.0, "e": 4.0, "f": 5.0}
    q = assign_quartiles(scores)
    # ties (a,b both 1.0) broken by input_id ascending -> both Q1.
    assert q["a"] == 1 and q["b"] == 1
    assert q["c"] == 2 and q["d"] == 2
    assert q["e"] == 3
    assert q["f"] == 4
    # block sizes
    sizes = [sum(1 for v in q.values() if v == k) for k in (1, 2, 3, 4)]
    assert sizes == [2, 2, 1, 1]


def test_assign_quartiles_empty():
    assert assign_quartiles({}) == {}


# ---------------------------------------------------------------------------
# quartile_table deep-reach rate + mean per-seed M1 + Q1-Q4 gap
# ---------------------------------------------------------------------------


def test_quartile_table_hand_checked():
    # 8 seeds, Q1..Q4 = 2 each by entropy.
    scores = {f"s{i}": float(i) for i in range(8)}
    q = assign_quartiles(scores)
    # deep_reach: Q1 (s0,s1) both deep; Q4 (s6,s7) neither deep.
    deep_reach = {
        "s0": True, "s1": True,
        "s2": True, "s3": False,
        "s4": False, "s5": True,
        "s6": False, "s7": False,
    }
    per_seed_m1 = {f"s{i}": 10 * i for i in range(8)}
    tbl = quartile_table(q, deep_reach, per_seed_m1)
    rows = tbl["rows"]
    # Q1: 2/2 deep -> 1.0 ; mean M1 = (0+10)/2 = 5
    assert rows[1]["deep_reach_rate"] == 1.0
    assert rows[1]["mean_per_seed_M1"] == 5.0
    assert rows[1]["n"] == 2
    # Q2: 1/2 -> 0.5
    assert rows[2]["deep_reach_rate"] == 0.5
    # Q3: 1/2 -> 0.5
    assert rows[3]["deep_reach_rate"] == 0.5
    # Q4: 0/2 -> 0.0 ; mean M1 = (60+70)/2 = 65
    assert rows[4]["deep_reach_rate"] == 0.0
    assert rows[4]["mean_per_seed_M1"] == 65.0
    # Q1 - Q4 = 1.0 - 0.0 = 1.0
    assert tbl["q1_minus_q4_deep_reach"] == 1.0


def test_quartile_table_missing_m1_and_deep_data_excluded():
    scores = {f"s{i}": float(i) for i in range(4)}
    q = assign_quartiles(scores)  # 1 per quartile
    deep_reach = {"s0": True, "s1": False}  # s2,s3 absent
    per_seed_m1 = {"s0": 100}  # only s0 has M1
    tbl = quartile_table(q, deep_reach, per_seed_m1)
    rows = tbl["rows"]
    assert rows[1]["deep_reach_rate"] == 1.0
    assert rows[1]["mean_per_seed_M1"] == 100.0
    # s3 in Q4 has no deep-reach data -> None, gap is None.
    assert rows[4]["deep_reach_rate"] is None
    assert rows[4]["mean_per_seed_M1"] is None
    assert tbl["q1_minus_q4_deep_reach"] is None


# ---------------------------------------------------------------------------
# §5 3-way interpretation rule
# ---------------------------------------------------------------------------


def _table_with_gap(gap: float | None) -> dict:
    return {"rows": {}, "q1_minus_q4_deep_reach": gap}


def test_interpret_supported_when_q1_dominates():
    full = _table_with_gap(0.20)  # >= +0.10
    prefix = _table_with_gap(0.05)
    interp = interpret_pool(full, prefix, {}, {}, {})
    assert interp["verdict"] == "mechanism SUPPORTED"
    assert interp["better_separating_entropy_metric"] == "full"


def test_interpret_different_when_gap_small_but_conditional_m1_differs():
    full = _table_with_gap(0.02)  # |gap| < 0.10
    prefix = _table_with_gap(0.02)
    # Q1 deep-reachers have small M1, Q4 deep-reachers large M1.
    quartiles = {"a": 1, "b": 4}
    deep_reach = {"a": True, "b": True}
    per_seed_m1 = {"a": 100, "b": 500}
    interp = interpret_pool(full, prefix, quartiles, deep_reach, per_seed_m1)
    assert interp["verdict"] == "mechanism DIFFERENT"
    assert interp["conditional_m1_differs"] is True


def test_interpret_wrong_union_level_when_neither_differs():
    full = _table_with_gap(0.01)  # tiny gap
    prefix = _table_with_gap(0.0)
    quartiles = {"a": 1, "b": 4}
    deep_reach = {"a": True, "b": True}
    per_seed_m1 = {"a": 300, "b": 305}  # ~no conditional difference
    interp = interpret_pool(full, prefix, quartiles, deep_reach, per_seed_m1)
    assert interp["verdict"] == "mechanism WRONG / union-level"
    assert interp["conditional_m1_differs"] is False


def test_conditional_m1_differs_threshold():
    quartiles = {"a": 1, "b": 4}
    deep_reach = {"a": True, "b": True}
    differs, q1m, q4m = conditional_m1_differs(
        quartiles, deep_reach, {"a": 100.0, "b": 130.0}
    )
    assert differs is True  # 30/130 ~ 0.23 >= 0.10
    assert q1m == 100.0 and q4m == 130.0
    differs2, _, _ = conditional_m1_differs(
        quartiles, deep_reach, {"a": 100.0, "b": 105.0}
    )
    assert differs2 is False  # 5/105 < 0.10


# ---------------------------------------------------------------------------
# Prefix entropy uses ONLY the first 8 payload tokens
# ---------------------------------------------------------------------------


def _sidecar_with_n_payload_tokens(n: int, per_tok_logprob: float) -> dict:
    """Build a synthetic sidecar with exactly ``n`` 2-char payload tokens.

    raw_response = {"content_b64":"<2n chars>","x":1}. Every token is an
    ordinary (no-metaspace) token so detokenize is identity. Each payload
    token's head is a single entry => its top-K Shannon entropy is exactly
    0 bits; we instead distinguish "which tokens were counted" by making
    the prefix tokens carry a 2-entry uniform head (entropy = 1 bit) and
    the tail tokens a 1-entry head (entropy = 0 bits): the prefix mean is
    then 1.0 iff and only iff exactly the first 8 are averaged.
    """
    payload_toks = [f"{i:02d}" for i in range(n)]
    open_tok = '{"content_b64":"'
    close_tok = '","x":1}'
    toks = [open_tok, *payload_toks, close_tok]
    content = "".join(toks)

    def rec(tok: str, idx_in_payload: int | None) -> dict:
        if idx_in_payload is None:
            return {"token": tok, "logprob": -0.1,
                    "top_logprobs": [{"token": tok, "logprob": -0.1}]}
        if idx_in_payload < PREFIX_TOKEN_COUNT:
            # 2-entry UNIFORM head -> H = 1.0 bit exactly.
            return {
                "token": tok,
                "logprob": math.log(0.5),
                "top_logprobs": [
                    {"token": tok, "logprob": math.log(0.5)},
                    {"token": "ZZ", "logprob": math.log(0.5)},
                ],
            }
        # 1-entry head -> H = 0.0 bits.
        return {
            "token": tok,
            "logprob": -0.1,
            "top_logprobs": [{"token": tok, "logprob": -0.1}],
        }

    recs = [rec(open_tok, None)]
    for i, t in enumerate(payload_toks):
        recs.append(rec(t, i))
    recs.append(rec(close_tok, None))

    payload_b64 = "".join(payload_toks)
    return {
        "input_id": "deadbeefdeadbeef",
        "variant": "v1_src",
        "content_b64": payload_b64,
        "raw_response": content,
        "input_index_in_response": 0,
        "logprobs": {"content": recs},
    }


def test_prefix_entropy_uses_only_first_8_payload_tokens():
    # 12 payload tokens: first 8 each H=1 bit, last 4 each H=0 bits.
    sc = _sidecar_with_n_payload_tokens(12, -0.1)
    value, status = prefix_entropy(sc)
    assert status == "ok"
    # If only the first 8 are averaged: mean = (8*1.0)/8 = 1.0.
    # If all 12 were (wrongly) averaged: mean = 8/12 = 0.667.
    assert value == pytest.approx(1.0)


def test_prefix_entropy_fewer_than_8_payload_tokens_uses_all():
    # 3 payload tokens, all H=1 bit -> mean = 1.0 over the 3 available.
    sc = _sidecar_with_n_payload_tokens(3, -0.1)
    value, status = prefix_entropy(sc)
    assert status == "ok"
    assert value == pytest.approx(1.0)


def test_prefix_entropy_reconstruction_mismatch_drops():
    sc = _sidecar_with_n_payload_tokens(10, -0.1)
    sc["raw_response"] = sc["raw_response"] + "EXTRA"  # force mismatch
    value, status = prefix_entropy(sc)
    assert value is None
    assert status == "drop_reconstruction_mismatch"


def test_prefix_entropy_unlocatable_payload_drops():
    sc = _sidecar_with_n_payload_tokens(10, -0.1)
    # Ask for the 2nd content_b64 region when only 1 exists.
    sc["input_index_in_response"] = 1
    value, status = prefix_entropy(sc)
    assert value is None
    assert status == "drop_b64_unlocatable"


def test_per_seed_prefix_entropies_aggregates(tmp_path: Path):
    d = tmp_path / "sidecars"
    d.mkdir()
    ok = _sidecar_with_n_payload_tokens(10, -0.1)
    ok["input_id"] = "aaaaaaaaaaaaaaaa"
    (d / "aaaaaaaaaaaaaaaa.json").write_text(json.dumps(ok))
    bad = _sidecar_with_n_payload_tokens(10, -0.1)
    bad["input_id"] = "bbbbbbbbbbbbbbbb"
    bad["raw_response"] += "X"  # reconstruction mismatch
    (d / "bbbbbbbbbbbbbbbb.json").write_text(json.dumps(bad))
    res = per_seed_prefix_entropies(d)
    assert res["n_total"] == 2
    assert res["n_ok"] == 1
    assert "aaaaaaaaaaaaaaaa" in res["entropies"]
    assert res["dropped"]["bbbbbbbbbbbbbbbb"] == "drop_reconstruction_mismatch"


# ---------------------------------------------------------------------------
# Per-seed M1 cache: resumable + counts errors
# ---------------------------------------------------------------------------


def test_compute_per_seed_m1_uses_cache_and_skips(tmp_path: Path, monkeypatch):
    seeds_dir = tmp_path / "seeds"
    seeds_dir.mkdir()
    for name in ("seed_aa.bin", "seed_bb.bin", "seed_cc.bin"):
        (seeds_dir / name).write_bytes(b"\x00")
    cache_path = tmp_path / "cache.json"
    # Pre-seed the cache: aa already measured, bb already errored.
    cache_path.write_text(json.dumps({"aa": 123, "bb": "error"}))

    calls: list[str] = []

    def fake_replay(seed_path, work_dir, idx, binary, source_roots):
        calls.append(seed_path.name)
        return 777  # cc -> 777

    monkeypatch.setattr(
        "analysis.scripts.experiment6_followup.replay_one_seed_edges",
        fake_replay,
    )
    out = compute_per_seed_m1(
        seeds_dir, cache_path, Path("/nonexistent/bin"), []
    )
    # Only cc was (re)played; aa & bb were cached and skipped.
    assert calls == ["seed_cc.bin"]
    # Returned dict has only int values; the "error" entry is excluded.
    assert out == {"aa": 123, "cc": 777}
    persisted = json.loads(cache_path.read_text())
    assert persisted == {"aa": 123, "bb": "error", "cc": 777}


def test_compute_per_seed_m1_records_error(tmp_path: Path, monkeypatch):
    seeds_dir = tmp_path / "seeds"
    seeds_dir.mkdir()
    (seeds_dir / "seed_zz.bin").write_bytes(b"\x00")
    cache_path = tmp_path / "cache.json"

    monkeypatch.setattr(
        "analysis.scripts.experiment6_followup.replay_one_seed_edges",
        lambda *a, **k: None,  # simulate timeout/error
    )
    out = compute_per_seed_m1(
        seeds_dir, cache_path, Path("/nonexistent/bin"), []
    )
    assert out == {}  # no int values
    assert json.loads(cache_path.read_text()) == {"zz": "error"}


# ---------------------------------------------------------------------------
# Real per-seed-M1 replay smoke (skipif binary missing)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not HB_BINARY.is_file(),
    reason="harfbuzz coverage seed_replay binary not built",
)
def test_real_replay_one_seed_edges_smoke(tmp_path: Path):
    """A trivially tiny input should replay without crashing the harness.

    We only assert the function returns either an int >= 0 or None (error
    path) — not a specific edge count (that depends on the built binary).
    """
    seed = tmp_path / "seed_test.bin"
    seed.write_bytes(b"\x00\x01\x02\x03")
    out = replay_one_seed_edges(
        seed,
        tmp_path,
        0,
        HB_BINARY,
        [str(p) for p in TARGETS["harfbuzz"].source_roots],
    )
    assert out is None or (isinstance(out, int) and out >= 0)


# ---------------------------------------------------------------------------
# analyze_pool end-to-end on a fully synthetic on-disk pool
# ---------------------------------------------------------------------------


def test_analyze_pool_end_to_end_synthetic(tmp_path: Path, monkeypatch):
    pool = "v1_src"
    # results/experiment6/<pool>/entropies.json
    ent_dir = tmp_path / "results/experiment6" / pool
    ent_dir.mkdir(parents=True)
    entropies = {f"id{i:02d}": float(i) for i in range(8)}
    (ent_dir / "entropies.json").write_text(
        json.dumps({"entropies": entropies})
    )
    # results/experiment6/<pool>/<pool>/pool/gap_hits.jsonl
    gh_dir = ent_dir / pool / "pool"
    gh_dir.mkdir(parents=True)
    rows = []
    for i in range(8):
        # low-entropy ids (id00..id03) hit a DEEP branch; high ones do not.
        hit_deep = i < 4
        rows.append({
            "seed_id": f"seed_id{i:02d}",
            "target_file": "src/hb-ot-shape.cc",
            "target_line": 100,
            "hit": hit_deep,
        })
    _write_gap_hits(gh_dir / "gap_hits.jsonl", rows)
    # synthesis/.../logprobs/.../<pool>/codestral-22b/*.json
    sc_dir = (
        tmp_path
        / "synthesis/results/experiment6/logprobs/harfbuzz/ablation"
        / pool
        / "codestral-22b"
    )
    sc_dir.mkdir(parents=True)
    for i in range(8):
        sc = _sidecar_with_n_payload_tokens(10, -0.1)
        sc["input_id"] = f"id{i:02d}"
        (sc_dir / f"id{i:02d}.json").write_text(json.dumps(sc))
    # per_seed_m1 cache
    fu_dir = tmp_path / "results/experiment6/followup"
    fu_dir.mkdir(parents=True)
    (fu_dir / f"per_seed_m1_{pool}.json").write_text(
        json.dumps({f"id{i:02d}": 100 + i for i in range(8)})
    )

    # Patch the frozen targets path to a synthetic DEEP target so
    # gap_hits' (hb-ot-shape.cc:100) maps DEEP.
    syn_targets = tmp_path / "m2.json"
    syn_targets.write_text(json.dumps({
        "shown": [{"file": "src/hb-ot-shape.cc", "line": 100,
                   "uncovered_side": "true"}],
        "held_back": [{"file": "src/hb-blob.cc", "line": 1,
                       "uncovered_side": "true"}],
    }))

    import analysis.scripts.experiment6_followup as mod

    # Swap the TARGETS registry the module reads for a tiny fake whose
    # m2_targets_path / coverage_binary / source_roots point at synthetic
    # data (analyze_pool only touches those three attributes; skip_m1_replay
    # means coverage_binary is never executed).
    class _FakeSpec:
        m2_targets_path = str(syn_targets)
        coverage_binary = "/nonexistent/seed_replay"
        source_roots = ()

    monkeypatch.setattr(mod, "TARGETS", {"harfbuzz": _FakeSpec()})
    res = mod.analyze_pool(pool, repo_root=tmp_path, skip_m1_replay=True)

    assert res["pool"] == pool
    assert res["surrogate_counts"][DEEP] == 1
    assert res["surrogate_counts"][EARLY] == 1
    # Low-entropy Q1 all reach deep; high-entropy Q4 none -> gap = 1.0.
    tf = res["table_full_entropy"]
    assert tf["rows"]["1"]["deep_reach_rate"] == 1.0
    assert tf["rows"]["4"]["deep_reach_rate"] == 0.0
    assert tf["q1_minus_q4_deep_reach"] == 1.0
    assert res["interpretation"]["verdict"] == "mechanism SUPPORTED"
    assert res["per_seed_m1"]["source"] == "cached"
    assert res["per_seed_m1"]["n_with_value"] == 8
