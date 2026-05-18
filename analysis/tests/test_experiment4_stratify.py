"""Unit tests for analysis/scripts/experiment4_stratify.py.

Synthetic, deterministic, fully offline (no network/LLVM/LLM). Every pool
and output dir is a pytest ``tmp_path`` so reruns never collide.

The Invariant-3 guard (``test_s_random_matches_random_random_42``) recomputes
the expected sample with the *same* ``random.Random(42).sample(sorted(...),
k)`` idiom and asserts byte-equality with the module's output, so any
accidental change to the sampling base list or seed is caught.
"""
from __future__ import annotations

import importlib.util
import json
import random
from pathlib import Path

import pytest

# Repo pyproject sets pythonpath=["."]; import the module under test by its
# package-relative path. analysis/ has no __init__ at the scripts level, so
# load it via importlib from its file path to avoid import-path coupling.

_MOD_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "experiment4_stratify.py"
)
_spec = importlib.util.spec_from_file_location("experiment4_stratify", _MOD_PATH)
assert _spec is not None and _spec.loader is not None
e6 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(e6)


# ── synthetic fixtures ───────────────────────────────────────────────────


def _mk_id(n: int) -> str:
    """Deterministic 16-hex input_id from an integer."""
    return f"{n:016x}"


def _make_pool(pool_dir: Path, n: int, *, content_prefix: bytes = b"BLOB") -> list[str]:
    """Create ``n`` seed_<id>.bin files; return the sorted list of ids."""
    pool_dir.mkdir(parents=True, exist_ok=True)
    ids = []
    for i in range(n):
        iid = _mk_id(i)
        (pool_dir / f"seed_{iid}.bin").write_bytes(content_prefix + iid.encode())
        ids.append(iid)
    return sorted(ids)


def _entropy_map(ids: list[str]) -> dict[str, float]:
    """Distinct strictly-increasing entropy per id (no ties)."""
    return {iid: float(idx) for idx, iid in enumerate(sorted(ids))}


# ── load_pool ────────────────────────────────────────────────────────────


def test_load_pool_maps_ids_to_paths(tmp_path: Path):
    pool = tmp_path / "pool"
    ids = _make_pool(pool, 5)
    mapping = e6.load_pool(pool)
    assert sorted(mapping) == ids
    for iid in ids:
        assert mapping[iid] == pool / f"seed_{iid}.bin"
        assert mapping[iid].is_file()


def test_load_pool_ignores_non_seed_files(tmp_path: Path):
    pool = tmp_path / "pool"
    _make_pool(pool, 3)
    (pool / "notes.txt").write_text("ignore me")
    (pool / "random_corpus.bin").write_bytes(b"x")  # no seed_ prefix
    mapping = e6.load_pool(pool)
    assert len(mapping) == 3


def test_load_pool_missing_dir_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        e6.load_pool(tmp_path / "does_not_exist")


# ── determinism ──────────────────────────────────────────────────────────


def test_select_is_deterministic_across_two_calls(tmp_path: Path):
    ids = [_mk_id(i) for i in range(400)]
    ent = _entropy_map(ids)
    a = e6.select_subsamples(ids, ent, k=150)
    b = e6.select_subsamples(list(reversed(ids)), ent, k=150)  # input order varied
    assert a["S_random"] == b["S_random"]
    assert a["S_high"] == b["S_high"]
    assert a["S_low"] == b["S_low"]
    for name in e6.SUBSAMPLE_NAMES:
        assert len(a[name]) == 150


def test_s_random_matches_random_random_42(tmp_path: Path):
    """Invariant 3 guard: S_random == random.Random(42).sample(sorted(elig),k)."""
    ids = [_mk_id(i) for i in range(370)]
    ent = _entropy_map(ids)
    out = e6.select_subsamples(ids, ent, k=150, rng_seed=42)

    eligible_sorted = sorted(iid for iid in set(ids) if iid in ent)
    expected = random.Random(42).sample(eligible_sorted, 150)
    assert out["S_random"] == expected


def test_s_random_seed_changes_selection(tmp_path: Path):
    ids = [_mk_id(i) for i in range(400)]
    ent = _entropy_map(ids)
    out42 = e6.select_subsamples(ids, ent, k=150, rng_seed=42)
    out7 = e6.select_subsamples(ids, ent, k=150, rng_seed=7)
    assert out42["S_random"] != out7["S_random"]
    # entropy strata do not depend on the rng seed
    assert out42["S_high"] == out7["S_high"]
    assert out42["S_low"] == out7["S_low"]


# ── entropy ordering + tie-break ─────────────────────────────────────────


def test_high_low_ordering_no_ties(tmp_path: Path):
    ids = [_mk_id(i) for i in range(300)]
    # entropy = i, so id _mk_id(i) has entropy i
    ent = {iid: float(int(iid, 16)) for iid in ids}
    out = e6.select_subsamples(ids, ent, k=150)

    # S_high: the 150 highest-entropy ids = ids 299..150
    expected_high = sorted(ids, key=lambda x: (-ent[x], x))[:150]
    assert out["S_high"] == expected_high
    # S_low: the 150 lowest-entropy ids = ids 0..149
    expected_low = sorted(ids, key=lambda x: (ent[x], x))[:150]
    assert out["S_low"] == expected_low
    # disjoint when n=300, k=150 and entropies are distinct
    assert set(out["S_high"]).isdisjoint(out["S_low"])


def test_tiebreak_is_input_id_ascending(tmp_path: Path):
    """All entropies identical → S_high and S_low both order by id ascending."""
    ids = [_mk_id(i) for i in range(400)]
    ent = {iid: 3.14 for iid in ids}  # exact ties everywhere
    out = e6.select_subsamples(ids, ent, k=150)

    sorted_ids = sorted(ids)
    # tie-break is input_id ASC for BOTH strata, so first 150 sorted ids.
    assert out["S_high"] == sorted_ids[:150]
    assert out["S_low"] == sorted_ids[:150]
    assert len(out["S_high"]) == 150
    assert len(out["S_low"]) == 150


def test_partial_ties_break_by_input_id(tmp_path: Path):
    """Two ids share the top entropy: the lexicographically smaller wins
    the S_high ordering at that entropy level."""
    ids = [_mk_id(i) for i in range(160)]
    ent = {iid: 1.0 for iid in ids}
    # Make exactly two ids the unique maximum, with a known lexico order.
    hi_a = _mk_id(1000)  # "00000000000003e8"
    hi_b = _mk_id(2000)  # "00000000000007d0"  (lexicographically larger)
    ids += [hi_a, hi_b]
    ent[hi_a] = 9.0
    ent[hi_b] = 9.0
    out = e6.select_subsamples(ids, ent, k=150)
    # Both share entropy 9.0; tie-break input_id ASC -> hi_a before hi_b.
    assert out["S_high"][0] == hi_a
    assert out["S_high"][1] == hi_b


# ── eligibility ──────────────────────────────────────────────────────────


def test_ids_missing_entropy_excluded_from_all_subsamples(tmp_path: Path):
    ids = [_mk_id(i) for i in range(200)]
    # Drop entropy for 40 ids -> only 160 eligible (>= k=150 still).
    dropped = set(ids[:40])
    ent = {iid: float(i) for i, iid in enumerate(ids) if iid not in dropped}
    out = e6.select_subsamples(ids, ent, k=150)
    for name in e6.SUBSAMPLE_NAMES:
        assert dropped.isdisjoint(out[name]), f"dropped id leaked into {name}"
        assert len(out[name]) == 150
        # every selected id has a measured entropy
        assert all(iid in ent for iid in out[name])


def test_entropy_ids_not_in_pool_are_ignored(tmp_path: Path):
    ids = [_mk_id(i) for i in range(160)]
    ent = {iid: float(i) for i, iid in enumerate(ids)}
    # Add phantom entropy entries for ids not present in the pool.
    ent["dead00000000beef"] = 99.0
    ent["dead00000000cafe"] = 99.0
    out = e6.select_subsamples(ids, ent, k=150)
    pool_set = set(ids)
    for name in e6.SUBSAMPLE_NAMES:
        assert all(iid in pool_set for iid in out[name])


# ── Invariant 4: no silent shrink ────────────────────────────────────────


def test_too_few_eligible_raises_valueerror(tmp_path: Path):
    ids = [_mk_id(i) for i in range(149)]  # < k
    ent = _entropy_map(ids)
    with pytest.raises(ValueError, match=r"149 eligible.*k=150"):
        e6.select_subsamples(ids, ent, k=150)


def test_enough_pool_but_too_few_with_entropy_raises(tmp_path: Path):
    ids = [_mk_id(i) for i in range(300)]  # plenty of pool
    # but only 100 have a measured entropy
    ent = {iid: float(i) for i, iid in enumerate(ids[:100])}
    with pytest.raises(ValueError, match=r"100 eligible.*k=150"):
        e6.select_subsamples(ids, ent, k=150)


def test_exactly_k_eligible_is_allowed(tmp_path: Path):
    ids = [_mk_id(i) for i in range(150)]
    ent = _entropy_map(ids)
    out = e6.select_subsamples(ids, ent, k=150)
    for name in e6.SUBSAMPLE_NAMES:
        assert sorted(out[name]) == sorted(ids)  # all eligible selected


# ── materialisation ──────────────────────────────────────────────────────


def test_persist_and_materialize_copies_k_files(tmp_path: Path):
    pool = tmp_path / "pool"
    ids = _make_pool(pool, 300)
    ent = {iid: float(int(iid, 16)) for iid in ids}
    pool_paths = e6.load_pool(pool)
    subs = e6.select_subsamples(list(pool_paths), ent, k=150)
    out_root = tmp_path / "out"

    manifest = e6.persist_and_materialize(subs, pool_paths, out_root)

    for name in e6.SUBSAMPLE_NAMES:
        json_path = out_root / f"{name}.json"
        seed_dir = out_root / name
        assert json_path.is_file()
        assert seed_dir.is_dir()

        bins = sorted(seed_dir.glob("seed_*.bin"))
        assert len(bins) == 150, f"{name} has {len(bins)} != 150 .bin files"

        # filenames are exactly seed_<id>.bin and content is preserved
        for b in bins:
            iid = b.stem[len("seed_"):]
            assert b.name == f"seed_{iid}.bin"
            assert b.read_bytes() == (pool / f"seed_{iid}.bin").read_bytes()

        record = json.loads(json_path.read_text())
        assert record["name"] == name
        assert record["k"] == 150
        assert len(record["seed_ids"]) == 150
        if name == "S_random":
            assert record["rng_seed"] == 42
            assert record["entropy_sorted"] is False
        else:
            assert record["rng_seed"] is None
            assert record["entropy_sorted"] is True

        m = manifest["subsamples"][name]
        assert m["k"] == 150
        assert m["n_files_copied"] == 150
        assert Path(m["seed_dir"]) == seed_dir


def test_materialize_is_idempotent_and_clears_stale(tmp_path: Path):
    pool = tmp_path / "pool"
    ids = _make_pool(pool, 300)
    ent = {iid: float(int(iid, 16)) for iid in ids}
    pool_paths = e6.load_pool(pool)
    subs = e6.select_subsamples(list(pool_paths), ent, k=150)
    out_root = tmp_path / "out"

    e6.persist_and_materialize(subs, pool_paths, out_root)
    # Plant a stale file that must be wiped on rerun.
    stale = out_root / "S_high" / "seed_deadbeefdeadbeef.bin"
    stale.write_bytes(b"STALE")
    assert stale.is_file()

    e6.persist_and_materialize(subs, pool_paths, out_root)
    assert not stale.exists(), "rerun did not clear the stale seed"

    for name in e6.SUBSAMPLE_NAMES:
        bins = list((out_root / name).glob("seed_*.bin"))
        assert len(bins) == 150


def test_materialize_copies_not_symlinks(tmp_path: Path):
    pool = tmp_path / "pool"
    ids = _make_pool(pool, 160)
    ent = {iid: float(int(iid, 16)) for iid in ids}
    pool_paths = e6.load_pool(pool)
    subs = e6.select_subsamples(list(pool_paths), ent, k=150)
    out_root = tmp_path / "out"
    e6.persist_and_materialize(subs, pool_paths, out_root)

    sample = next((out_root / "S_random").glob("seed_*.bin"))
    assert not sample.is_symlink(), "METHODS §6 requires copies, not symlinks"
    # Mutating the source must NOT change the copy.
    src_id = sample.stem[len("seed_"):]
    (pool / f"seed_{src_id}.bin").write_bytes(b"MUTATED")
    assert sample.read_bytes() != b"MUTATED"


# ── CLI smoke ────────────────────────────────────────────────────────────


def test_main_cli_end_to_end(tmp_path: Path, capsys):
    pool = tmp_path / "pool"
    ids = _make_pool(pool, 300)
    ent = {iid: float(int(iid, 16)) for iid in ids}
    entropy_json = tmp_path / "entropy.json"
    entropy_json.write_text(
        json.dumps({"entropies": ent, "n_dropped": 0, "provenance": "synthetic"})
    )
    out_root = tmp_path / "out"

    rc = e6.main([
        "--pool-dir", str(pool),
        "--entropy-json", str(entropy_json),
        "--out-root", str(out_root),
    ])
    assert rc == 0

    printed = json.loads(capsys.readouterr().out)
    assert printed["n_pool"] == 300
    assert printed["n_eligible"] == 300
    assert printed["k"] == 150
    for name in e6.SUBSAMPLE_NAMES:
        assert printed["subsamples"][name]["n_files_copied"] == 150
        assert len(list((out_root / name).glob("seed_*.bin"))) == 150


def test_main_cli_rejects_entropy_json_without_entropies_key(tmp_path: Path):
    pool = tmp_path / "pool"
    _make_pool(pool, 160)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"not_entropies": {}}))
    with pytest.raises(ValueError, match="no dict 'entropies' key"):
        e6.main([
            "--pool-dir", str(pool),
            "--entropy-json", str(bad),
            "--out-root", str(tmp_path / "out"),
        ])
