"""experiment4 — deterministic entropy-stratified subsample selection.

This module is the *selection + materialisation* plumbing for experiment4
(U4 — Entropy-Stratified Seed Selection). It consumes:

  1. A pool directory of ``seed_<input_id>.bin`` files (the over-generated
     450-seed pool for one ``(target, variant, model, strategy)`` cell).
  2. A per-seed *mean payload entropy* map ``{input_id: float}`` produced by
     a sibling module (the payload-masked top-K-head entropy of §3/§4 of
     ``docs/experiment4/METHODS.md``). This module only *consumes* that map;
     it never recomputes entropy and never touches logprobs.

It produces three disjoint 150-seed subsamples and materialises each as its
own directory of *copied* ``.bin`` files so the **unmodified** M2 metric
(`analysis.metrics.M2HardBranchMetric` via
`analysis/scripts/measure_gap_coverage.py`) can be run against each one with
no metric-code fork.

The selection rule is fixed by ``docs/experiment4/METHODS.md`` §6 and
``docs/experiment4/MANIFEST.json`` ``pool_and_subsampling``:

  - ``S_random``: ``sorted(eligible_by_seed_id)`` then
    ``random.Random(42).sample(., 150)``. This deliberately mirrors the
    existing protocol in ``scripts/_ablation_base.py::_subsample_seeds``,
    which does ``random.Random(42).sample(sorted_files, k)``. **Invariant 3
    — RNG seed = 42.**
  - ``S_high``: eligible sorted by ``(-entropy, input_id)`` (entropy
    DESCENDING, ties broken by ``input_id`` ASCENDING for a total order);
    take the first 150.
  - ``S_low``: eligible sorted by ``(entropy, input_id)`` (entropy
    ASCENDING, ties broken by ``input_id`` ASCENDING); take the first 150.

Eligibility: a pool id is eligible iff it is BOTH present in the pool AND
has a measured entropy value. Dropped seeds (no entropy — e.g. the §3 seed
exclusion: non-verbatim ``content_b64`` or zero payload tokens) are
*excluded from all three subsamples*. An unmeasurable seed cannot be
stratified; silently keeping it would either fabricate an entropy or break
the stratification. This is the correct, disclosed behaviour (METHODS §3/§4
"dropped, counted, reported").

Invariant 4 (every scored corpus is exactly 150) is load-bearing: if fewer
than ``k`` eligible seeds exist we raise ``ValueError`` rather than shrink
``k``. The caller decides whether to over-generate more pool.

Pure offline code: no network, no LLVM, no LLM. The only filesystem effects
are reading the pool and writing the subsample JSONs + copied ``.bin`` dirs.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# E402 below is intentional and repo-conventional: scripts under */scripts/
# bootstrap sys.path before importing repo packages (mirrors
# analysis/scripts/measure_gap_coverage.py ~lines 26-29). This module has no
# repo-package imports today, but the bootstrap is kept so a sibling
# refactor that adds e.g. `from core...` does not have to re-add it.

SUBSAMPLE_NAMES = ("S_random", "S_high", "S_low")

# The seed-id is the 16-hex token after the leading "seed_" prefix in a
# pool filename, e.g. "seed_0123456789abcdef.bin" -> "0123456789abcdef".
SEED_PREFIX = "seed_"
SEED_SUFFIX = ".bin"


def _input_id_from_path(path: Path) -> str:
    """Return the input_id for a ``seed_<input_id>.bin`` path.

    input_id = filename stem with a leading ``seed_`` stripped. We do not
    validate the hex shape here — the pool is produced by the synthesis
    pipeline and any non-conforming file is simply skipped by
    :func:`load_pool` (it only globs ``seed_*.bin``).
    """
    stem = path.stem  # drops the trailing ".bin"
    if stem.startswith(SEED_PREFIX):
        return stem[len(SEED_PREFIX):]
    return stem


def load_pool(pool_dir: Path) -> dict[str, Path]:
    """Map ``input_id -> path`` for every ``seed_*.bin`` file in ``pool_dir``.

    Deterministic: the result is built by iterating ``sorted()`` paths so
    that, even though a dict is returned, construction order is stable and
    a duplicate id (should never happen with 16-hex ids, but defensively)
    resolves to the lexicographically-first path deterministically.

    Raises ``FileNotFoundError`` if ``pool_dir`` does not exist — a missing
    pool is a hard error, never an empty selection (Invariant 4 would then
    fail loudly downstream, but failing here is clearer).
    """
    pool_dir = Path(pool_dir)
    if not pool_dir.is_dir():
        raise FileNotFoundError(f"pool dir does not exist or is not a dir: {pool_dir}")

    mapping: dict[str, Path] = {}
    for path in sorted(pool_dir.glob(f"{SEED_PREFIX}*{SEED_SUFFIX}")):
        if not path.is_file():
            continue
        input_id = _input_id_from_path(path)
        # First (lexicographically smallest path) wins; deterministic.
        mapping.setdefault(input_id, path)
    return mapping


def _eligible_ids(pool_ids: list[str], entropies: dict[str, float]) -> list[str]:
    """Ids that are BOTH in the pool AND have a measured entropy value.

    Returned sorted lexicographically by input_id so every downstream
    sort/sample operates on a deterministic, total-ordered base list.
    """
    pool_set = set(pool_ids)
    eligible = [iid for iid in pool_set if iid in entropies]
    return sorted(eligible)


def select_subsamples(
    pool_ids: list[str],
    entropies: dict[str, float],
    k: int = 150,
    rng_seed: int = 42,
) -> dict[str, list[str]]:
    """Select the three disjoint-by-rule subsamples.

    Returns a dict with keys ``S_random``, ``S_high``, ``S_low``; each value
    is a list of exactly ``k`` input_ids.

    - ``S_random``: ``random.Random(rng_seed).sample(sorted(eligible), k)``.
      ``sorted(eligible)`` gives a total lexicographic order on input_ids
      so the sample is fully reproducible (Invariant 3). This mirrors
      ``scripts/_ablation_base.py::_subsample_seeds`` which samples from
      ``sorted`` files with ``random.Random(42)``.
    - ``S_high``: eligible sorted by ``key=(-entropy, input_id)`` (entropy
      DESC, ties input_id ASC), first ``k``.
    - ``S_low``: eligible sorted by ``key=(entropy, input_id)`` (entropy
      ASC, ties input_id ASC), first ``k``.

    Raises ``ValueError`` if fewer than ``k`` eligible ids exist. We do NOT
    silently shrink ``k`` — Invariant 4 (every scored corpus is exactly
    150) is load-bearing; the caller must over-generate more pool instead.
    """
    eligible = _eligible_ids(pool_ids, entropies)
    n_eligible = len(eligible)
    if n_eligible < k:
        raise ValueError(
            f"only {n_eligible} eligible seeds (in pool AND with a measured "
            f"entropy) but k={k} requested; Invariant 4 forbids shrinking k. "
            f"Over-generate more pool (more synthesis passes with a bumped "
            f"--attempt-offset) so the eligible set reaches >= {k}."
        )

    # S_random — sample from the total-ordered eligible list.
    sorted_eligible = sorted(eligible)  # already sorted, but explicit/idempotent
    s_random = random.Random(rng_seed).sample(sorted_eligible, k)

    # S_high — entropy descending, ties by input_id ascending.
    by_entropy_desc = sorted(eligible, key=lambda iid: (-entropies[iid], iid))
    s_high = by_entropy_desc[:k]

    # S_low — entropy ascending, ties by input_id ascending.
    by_entropy_asc = sorted(eligible, key=lambda iid: (entropies[iid], iid))
    s_low = by_entropy_asc[:k]

    assert len(s_random) == k, f"S_random has {len(s_random)} != {k}"
    assert len(s_high) == k, f"S_high has {len(s_high)} != {k}"
    assert len(s_low) == k, f"S_low has {len(s_low)} != {k}"

    return {"S_random": s_random, "S_high": s_high, "S_low": s_low}


def _reset_dir(path: Path) -> None:
    """Clear-and-recreate ``path`` so a rerun materialises a clean dir.

    Idempotent: an existing per-name subdir (possibly with stale ``.bin``
    files from a previous, different selection) is removed wholesale before
    re-population, so the metric's ``iterdir()`` never sees a stale seed.
    """
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def persist_and_materialize(
    subsamples: dict[str, list[str]],
    pool_paths: dict[str, Path],
    out_root: Path,
    rng_seed: int = 42,
) -> dict:
    """Persist subsample JSONs and copy the selected ``.bin`` files.

    For each ``name`` in ``subsamples``:
      - write ``out_root/<name>.json`` =
        ``{"name", "k", "seed_ids", "rng_seed", "entropy_sorted"}``
        where ``rng_seed`` is ``rng_seed`` for ``S_random`` and ``None``
        for the entropy-sorted strata, and ``entropy_sorted`` is True for
        ``S_high``/``S_low`` and False for ``S_random``;
      - clear/recreate ``out_root/<name>/`` and copy each selected file as
        ``out_root/<name>/seed_<input_id>.bin`` via ``shutil.copy2``
        (COPIED, not symlinked — METHODS §6: keeps the metric's
        ``iterdir()`` stable and self-contained).

    Returns a manifest dict with per-subsample counts and paths.
    """
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    manifest: dict = {
        "out_root": str(out_root),
        "subsamples": {},
    }

    for name, seed_ids in subsamples.items():
        is_random = name == "S_random"
        json_path = out_root / f"{name}.json"
        record = {
            "name": name,
            "k": len(seed_ids),
            "seed_ids": list(seed_ids),
            "rng_seed": rng_seed if is_random else None,
            "entropy_sorted": not is_random,
        }
        json_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")

        subdir = out_root / name
        _reset_dir(subdir)
        copied = 0
        for input_id in seed_ids:
            src = pool_paths.get(input_id)
            if src is None:
                # A selected id with no pool path is an identity bug: the
                # selection only ever draws from pool ids. Surface, do not
                # skip-and-shrink.
                raise KeyError(
                    f"selected id {input_id!r} for {name} is not in the pool "
                    f"path map; seed-identity bug — aborting (Invariant 4)."
                )
            dst = subdir / f"{SEED_PREFIX}{input_id}{SEED_SUFFIX}"
            shutil.copy2(src, dst)
            copied += 1

        assert copied == len(seed_ids), (
            f"{name}: copied {copied} files but selected {len(seed_ids)}"
        )
        manifest["subsamples"][name] = {
            "k": len(seed_ids),
            "json_path": str(json_path),
            "seed_dir": str(subdir),
            "n_files_copied": copied,
            "rng_seed": rng_seed if is_random else None,
            "entropy_sorted": not is_random,
        }

    return manifest


def _load_entropy_map(entropy_json: Path) -> dict[str, float]:
    """Read the sibling module's output and return its ``entropies`` sub-dict.

    The sibling module writes ``{"entropies": {input_id: float}, ...}``;
    other top-level keys (drop counts, provenance) are intentionally
    ignored here — this module's only contract is the per-seed entropy map.
    """
    entropy_json = Path(entropy_json)
    blob = json.loads(entropy_json.read_text())
    if "entropies" not in blob or not isinstance(blob["entropies"], dict):
        raise ValueError(
            f"entropy json {entropy_json} has no dict 'entropies' key; got "
            f"top-level keys {sorted(blob) if isinstance(blob, dict) else type(blob)}"
        )
    # Coerce to {str: float} defensively (JSON keys are already str).
    return {str(iid): float(val) for iid, val in blob["entropies"].items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "experiment4: form S_random/S_high/S_low 150-seed subsamples "
            "from a seed pool + per-seed mean payload entropy map, and "
            "materialise each as a directory of copied .bin files."
        )
    )
    parser.add_argument(
        "--pool-dir", required=True, type=Path,
        help="directory of seed_<input_id>.bin pool files",
    )
    parser.add_argument(
        "--entropy-json", required=True, type=Path,
        help="sibling module output JSON with an 'entropies' {id: float} sub-dict",
    )
    parser.add_argument(
        "--out-root", required=True, type=Path,
        help="output root for <name>.json + <name>/ materialised dirs",
    )
    parser.add_argument(
        "--k", type=int, default=150,
        help="scored corpus size (default 150; Invariant 4 — do not shrink)",
    )
    parser.add_argument(
        "--rng-seed", type=int, default=42,
        help="RNG seed for S_random (default 42; Invariant 3)",
    )
    args = parser.parse_args(argv)

    pool_paths = load_pool(args.pool_dir)
    entropies = _load_entropy_map(args.entropy_json)

    subsamples = select_subsamples(
        list(pool_paths.keys()), entropies, k=args.k, rng_seed=args.rng_seed,
    )
    manifest = persist_and_materialize(
        subsamples, pool_paths, args.out_root, rng_seed=args.rng_seed,
    )

    manifest["pool_dir"] = str(args.pool_dir)
    manifest["entropy_json"] = str(args.entropy_json)
    manifest["k"] = args.k
    manifest["n_pool"] = len(pool_paths)
    manifest["n_eligible"] = len(_eligible_ids(list(pool_paths.keys()), entropies))

    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
