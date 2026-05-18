"""experiment6 — FOLLOW-UP C: per-seed mechanism check (NO new LLM calls).

Implements ``docs/experiment6/FOLLOWUP.md`` EXACTLY. The pre-registration in
that doc is FROZEN (surrogate §3, predictions §5, interpretation rule §5);
this module does not redraw any of it. Everything here is offline plumbing
over experiment6's already-on-disk over-generated pools — there is zero LLM
cost. The per-seed M1 step does drive the unmodified LLVM coverage pipeline
(real CPU compute), but touches no research invariant.

What this answers (FOLLOWUP.md §1): experiment6 found, against its
pre-registered prediction, that the LOW-entropy subsample carried the M2
union. That is a *union-level* result; the *per-seed* mechanism ("low-entropy
seeds pass early parsing and reach deep code") was untested. This module
tests it per seed using only existing data.

Inputs (FOLLOWUP.md §2, all verified on disk; NO regeneration):

- Per-seed mean payload entropy:
  ``results/experiment6/{v1_src,v3_all}/entropies.json``
  (schema ``{"entropies": {input_id: float}, ...}``).
- Per-seed × 50-branch hit matrix:
  ``results/experiment6/{v1_src,v3_all}/{v1_src,v3_all}/pool/gap_hits.jsonl``
  (one JSON line per ``(seed_id, target_idx)``; ``seed_id`` carries a
  ``seed_`` prefix that ``input_id`` in entropies.json does not — they are
  aligned by stripping that prefix). Used for DEEP-reach.
- Logprob sidecars:
  ``synthesis/results/experiment6/logprobs/harfbuzz/ablation/{v}/codestral-22b/<input_id>.json``
  used for PREFIX entropy (mean per-token entropy over only the FIRST 8
  payload tokens), reusing ``experiment6_entropy``'s masking/entropy
  internals verbatim (imported, never copied), with the SAME drop rules.
- Per-seed M1: NOT on disk. Computed here with the unmodified coverage
  pipeline (replay ONE pool seed through ``TARGETS['harfbuzz']``'s coverage
  binary, ``llvm-profdata merge`` + ``llvm-cov export``, count distinct
  covered edges using the same edge definition as the official
  ``synthesis.scripts.measure_coverage`` M1 metric). Cached to
  ``results/experiment6/followup/per_seed_m1_<v>.json`` so a rerun is fast;
  the replay pass is resumable and skips (and counts) any seed that
  times out / errors.

Frozen DEEP/EARLY surrogate (FOLLOWUP.md §3, NOT re-drawn here): a frozen
harfbuzz m2 branch is DEEP iff its file basename matches ``hb-ot`` OR
``hb-shape``; EARLY iff it matches ``hb-blob`` OR ``hb-open``. Verified
counts on the 50 frozen targets: EARLY=13, DEEP=37 (total 50).

CLI::

    python -m analysis.scripts.experiment6_followup \
        --pools v1_src,v3_all --out-dir results/experiment6/followup
    # add --skip-m1-replay to reuse cached per_seed_m1_*.json (or skip the
    # replay entirely) for a fast structural dry-run.

Writes ``<out-dir>/{summary.json, summary.md}`` plus the per-pool
``per_seed_m1_<pool>.json`` edge-count caches.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analysis.scripts.experiment6_entropy import (  # noqa: E402
    STATUS_DROP_B64_UNLOCATABLE,
    STATUS_DROP_NO_PAYLOAD_TOKENS,
    STATUS_DROP_RECONSTRUCTION_MISMATCH,
    STATUS_OK,
    payload_token_indices,
    reconstruct,
    token_entropy_bits,
)
from core.coverage_utils import parse_llvm_cov_json  # noqa: E402
from core.targets import TARGETS  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# experiment6 used codestral-22b only (FOLLOWUP.md §2).
MODEL = "codestral-22b"

# Prefix entropy = mean per-token entropy over only the FIRST 8 payload
# tokens (FOLLOWUP.md §2/§4). Frozen here, not a tunable.
PREFIX_TOKEN_COUNT = 8

# Number of entropy quartiles (FOLLOWUP.md §4: Q1=lowest … Q4=highest).
N_QUARTILES = 4

# Pre-registered minimum meaningful Q1−Q4 deep-reach gap (FOLLOWUP.md §5).
MIN_MEANINGFUL_GAP = 0.10

# llvm tool names — same defaults as measure_gap_coverage / measure_coverage.
LLVM_PROFDATA = os.environ.get("LLVM_PROFDATA", "llvm-profdata-15")
LLVM_COV = os.environ.get("LLVM_COV", "llvm-cov-15")

# Per-seed replay timeout (seconds), mirroring measure_gap_coverage's 15 s.
REPLAY_TIMEOUT_S = 15


# ---------------------------------------------------------------------------
# Frozen DEEP / EARLY surrogate (FOLLOWUP.md §3 — DO NOT RE-DRAW)
# ---------------------------------------------------------------------------

DEEP = "DEEP"
EARLY = "EARLY"


def classify_branch_file(file: str) -> str:
    """Map a frozen-branch source file to the frozen DEEP/EARLY surrogate.

    FOLLOWUP.md §3 (frozen, anti-gerrymander): on the file's *basename*,

    - DEEP  = matches ``hb-ot`` OR ``hb-shape``  (OpenType layout / shaping,
              past the structural parse)
    - EARLY = matches ``hb-blob`` OR ``hb-open`` (blob lifetime / sfnt &
              table-directory reading — the first validity gate)

    The frozen set's 50 files are exactly {hb-blob, hb-open*, hb-ot*,
    hb-shape*}, so every target maps; an UNMAPPED return would mean the
    fixture drifted and is surfaced (never silently bucketed).
    """
    base = os.path.basename(file)
    if "hb-ot" in base or "hb-shape" in base:
        return DEEP
    if "hb-blob" in base or "hb-open" in base:
        return EARLY
    return "UNMAPPED"


def load_deep_target_keys(targets_path: Path) -> tuple[set[str], dict[str, int]]:
    """Return (set of DEEP target (file,line) keys, count summary).

    Reads the frozen ``m2_target_branches.json`` (keys ``shown[]`` /
    ``held_back[]``, each entry has ``file`` / ``line`` / ``uncovered_side``)
    and maps each of the 50 targets to DEEP/EARLY by its ``file`` via the
    frozen surrogate. The returned key set is what ``gap_hits.jsonl`` rows
    are matched against to decide per-seed deep-reach.
    """
    raw = json.loads(targets_path.read_text())
    deep_keys: set[str] = set()
    counts = {DEEP: 0, EARLY: 0, "UNMAPPED": 0}
    for slice_name in ("shown", "held_back"):
        for entry in raw[slice_name]:
            cls = classify_branch_file(entry["file"])
            counts[cls] += 1
            if cls == DEEP:
                deep_keys.add(f"{entry['file']}:{entry['line']}")
    if counts["UNMAPPED"]:
        raise ValueError(
            f"frozen surrogate drift: {counts['UNMAPPED']} target file(s) in "
            f"{targets_path} matched neither DEEP nor EARLY"
        )
    return deep_keys, counts


# ---------------------------------------------------------------------------
# Deep-reach from the on-disk per-seed × branch hit matrix
# ---------------------------------------------------------------------------


def seed_deep_reach(gap_hits_path: Path, deep_keys: set[str]) -> dict[str, bool]:
    """Per pooled seed: does it hit ≥1 DEEP branch (gap_hits.jsonl)?

    A seed "reaches deep" iff it has ``hit == True`` for at least one target
    whose ``(target_file, target_line)`` maps to DEEP under the frozen
    surrogate (FOLLOWUP.md §4). The ``seed_`` prefix on ``seed_id`` is
    stripped so the returned keys align with entropies.json ``input_id``s.
    """
    reach: dict[str, bool] = {}
    with gap_hits_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            seed_id = rec["seed_id"]
            input_id = seed_id[5:] if seed_id.startswith("seed_") else seed_id
            reach.setdefault(input_id, False)
            if rec["hit"] and f"{rec['target_file']}:{rec['target_line']}" in deep_keys:
                reach[input_id] = True
    return reach


# ---------------------------------------------------------------------------
# Prefix entropy (first-8-payload-token mean), reusing experiment6_entropy
# ---------------------------------------------------------------------------


def prefix_entropy(sidecar: dict) -> tuple[float | None, str]:
    """Mean entropy over the FIRST ``PREFIX_TOKEN_COUNT`` payload tokens.

    Reuses ``experiment6_entropy`` verbatim (reconstruct / payload masking /
    per-token top-K-head Shannon entropy) and applies the IDENTICAL drop
    rules as ``experiment6_entropy.mean_payload_entropy``:

    - reconstructed text != raw_response → ``drop_reconstruction_mismatch``
    - payload region unlocatable          → ``drop_b64_unlocatable``
    - no payload tokens after masking     → ``drop_no_payload_tokens``
    - a prefix payload token with an unusable head (None) → the whole seed
      drops (``drop_no_payload_tokens``), mirroring experiment6_entropy's
      "do not silently average over a shrunk token set" rule.

    The ONLY difference from the full-payload statistic is that the mean is
    taken over the first ``PREFIX_TOKEN_COUNT`` payload-token indices only
    (a seed with fewer payload tokens uses all of them).
    """
    logprobs_content = sidecar["logprobs"]["content"]
    raw_response = sidecar["raw_response"]
    content_b64 = sidecar["content_b64"]
    input_index_in_response = sidecar["input_index_in_response"]

    full_text, spans = reconstruct(logprobs_content)
    if full_text != raw_response:
        return None, STATUS_DROP_RECONSTRUCTION_MISMATCH

    indices = payload_token_indices(
        full_text, spans, raw_response, content_b64, input_index_in_response
    )
    if indices is None:
        return None, STATUS_DROP_B64_UNLOCATABLE
    if not indices:
        return None, STATUS_DROP_NO_PAYLOAD_TOKENS

    # Only the FIRST PREFIX_TOKEN_COUNT payload tokens (in token order;
    # payload_token_indices already returns ascending span order).
    prefix_indices = indices[:PREFIX_TOKEN_COUNT]

    per_token: list[float] = []
    for idx in prefix_indices:
        record = logprobs_content[idx]
        h = token_entropy_bits(
            record.get("top_logprobs", []),
            record["token"],
            record["logprob"],
        )
        if h is None:
            return None, STATUS_DROP_NO_PAYLOAD_TOKENS
        per_token.append(h)

    mean = sum(per_token) / len(per_token)
    return mean, STATUS_OK


def per_seed_prefix_entropies(sidecar_dir: Path) -> dict:
    """Aggregate prefix entropy over every ``*.json`` sidecar in a dir.

    Mirrors ``experiment6_entropy.per_seed_entropies`` shape so callers /
    tests can treat both the same way::

        {"entropies": {input_id: float}, "dropped": {input_id: status},
         "n_total": int, "n_ok": int}

    Sidecars are processed in sorted-by-filename order for determinism.
    """
    sidecar_dir = Path(sidecar_dir)
    entropies: dict[str, float] = {}
    dropped: dict[str, str] = {}
    n_total = 0
    n_ok = 0
    for path in sorted(sidecar_dir.glob("*.json"), key=lambda p: p.name):
        with path.open("r", encoding="utf-8") as fh:
            sidecar = json.load(fh)
        input_id = sidecar["input_id"]
        value, status = prefix_entropy(sidecar)
        n_total += 1
        if status == STATUS_OK:
            entropies[input_id] = value
            n_ok += 1
        else:
            dropped[input_id] = status
    return {
        "entropies": entropies,
        "dropped": dropped,
        "n_total": n_total,
        "n_ok": n_ok,
    }


# ---------------------------------------------------------------------------
# Per-seed M1 (replay ONE seed) — unmodified coverage pipeline, cached
# ---------------------------------------------------------------------------


def replay_one_seed_edges(
    seed_path: Path,
    work_dir: Path,
    idx: int,
    binary: Path,
    source_roots: list[str],
) -> int | None:
    """Distinct covered edges from replaying exactly ONE seed.

    Mirrors ``measure_gap_coverage.replay_one_seed`` (isolated
    ``LLVM_PROFILE_FILE`` → 15 s timeout → ``llvm-profdata merge -sparse`` →
    ``llvm-cov export --skip-expansions`` → ``parse_llvm_cov_json``) but,
    instead of frozen-branch hit booleans, returns the per-seed edge count
    using the IDENTICAL edge definition as the official M1 metric
    (``synthesis.scripts.measure_coverage``)::

        edges = Σ over files Σ over branches  int(true_taken)+int(false_taken)

    ``source_roots`` is passed through to ``parse_llvm_cov_json`` so the
    count is restricted to upstream sources, exactly like the official M1.
    Returns ``None`` (caller skips + counts) on timeout / missing profraw /
    any subprocess error — the replay pass is robust and resumable.
    """
    profraw = work_dir / f"seed_{idx}.profraw"
    profdata = work_dir / f"seed_{idx}.profdata"
    cov_json = work_dir / f"seed_{idx}.json"

    env = os.environ.copy()
    env["LLVM_PROFILE_FILE"] = str(profraw)
    try:
        subprocess.run(
            [str(binary), str(seed_path)],
            capture_output=True,
            timeout=REPLAY_TIMEOUT_S,
            check=False,
            env=env,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if not profraw.exists():
        return None

    try:
        subprocess.run(
            [LLVM_PROFDATA, "merge", "-sparse", str(profraw), "-o", str(profdata)],
            check=True,
            capture_output=True,
        )
        with open(cov_json, "w") as fh:
            subprocess.run(
                [
                    LLVM_COV,
                    "export",
                    str(binary),
                    f"-instr-profile={profdata}",
                    "--skip-expansions",
                ],
                check=True,
                stdout=fh,
                stderr=subprocess.PIPE,
            )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None

    profile = parse_llvm_cov_json(
        cov_json,
        test_name=f"seed_{idx}",
        upstream_file="",
        upstream_line=1,
        framework="seed",
        source_roots=source_roots,
    )
    return sum(
        int(b.true_taken) + int(b.false_taken)
        for fc in profile.files.values()
        for b in fc.branches.values()
    )


def compute_per_seed_m1(
    seeds_dir: Path,
    cache_path: Path,
    binary: Path,
    source_roots: list[str],
) -> dict[str, int]:
    """Per-seed M1 edge counts for every ``seed_*.bin`` under ``seeds_dir``.

    Resumable + robust: the cache JSON maps ``input_id`` → edge count (an
    int) or the string ``"error"`` for a seed whose replay timed out /
    errored (counted, never silently dropped). Seeds already present in the
    cache (under either form) are skipped, so a rerun only does the missing
    work. The cache is flushed after every seed so a kill mid-pass loses at
    most one seed.
    """
    cache: dict[str, object] = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text())

    seed_paths = sorted(
        p for p in seeds_dir.iterdir() if p.is_file() and p.suffix == ".bin"
    )

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        for idx, sp in enumerate(seed_paths):
            stem = sp.stem  # e.g. "seed_02b05c2fac4f9620"
            input_id = stem[5:] if stem.startswith("seed_") else stem
            if input_id in cache:
                continue
            edges = replay_one_seed_edges(
                sp, td_path, idx, binary, source_roots
            )
            cache[input_id] = edges if edges is not None else "error"
            cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True))
            for ext in (".profraw", ".profdata", ".json"):
                p = td_path / f"seed_{idx}{ext}"
                if p.exists():
                    p.unlink()

    # Return only the successfully-measured (int) counts; "error" entries are
    # excluded from the per-seed M1 aggregate but stay in the cache (counted).
    return {k: v for k, v in cache.items() if isinstance(v, int)}


# ---------------------------------------------------------------------------
# Quartiles + per-quartile aggregation
# ---------------------------------------------------------------------------


def assign_quartiles(scores: dict[str, float]) -> dict[str, int]:
    """Assign each seed an entropy quartile 1..N_QUARTILES (1 = lowest).

    Deterministic: seeds are ordered by (score, input_id) ascending and cut
    into ``N_QUARTILES`` contiguous blocks as evenly as possible (the first
    ``r`` blocks get one extra when ``n`` is not divisible by 4). This is a
    rank-based quartile (equal-count buckets), which is what "assign every
    pooled seed an entropy quartile, Q1=lowest" means for a deep-reach-rate
    and mean-M1 contrast.
    """
    n = len(scores)
    if n == 0:
        return {}
    ordered = sorted(scores.items(), key=lambda kv: (kv[1], kv[0]))
    base, rem = divmod(n, N_QUARTILES)
    out: dict[str, int] = {}
    pos = 0
    for q in range(N_QUARTILES):
        size = base + (1 if q < rem else 0)
        for input_id, _ in ordered[pos : pos + size]:
            out[input_id] = q + 1
        pos += size
    return out


def quartile_table(
    quartiles: dict[str, int],
    deep_reach: dict[str, bool],
    per_seed_m1: dict[str, int],
) -> dict:
    """Per-quartile deep-reach rate + mean per-seed M1 + n.

    Returns::

        {
          "rows": {1: {"n": int, "deep_reach_rate": float|None,
                        "mean_per_seed_M1": float|None,
                        "n_with_m1": int},
                   ... 4},
          "q1_minus_q4_deep_reach": float|None,
        }

    ``deep_reach_rate`` for a quartile is the fraction of its seeds with
    deep-reach True (a seed missing from ``deep_reach`` — i.e. not in the
    pool's gap_hits — is excluded from that quartile's deep-reach base).
    ``mean_per_seed_M1`` is the mean of available per-seed edge counts for
    that quartile (seeds whose replay errored / are uncached are excluded
    and reflected in ``n_with_m1``).
    """
    rows: dict[int, dict] = {}
    for q in range(1, N_QUARTILES + 1):
        ids = [i for i, qq in quartiles.items() if qq == q]
        dr_vals = [deep_reach[i] for i in ids if i in deep_reach]
        m1_vals = [per_seed_m1[i] for i in ids if i in per_seed_m1]
        rows[q] = {
            "n": len(ids),
            "n_with_deep_reach_data": len(dr_vals),
            "deep_reach_rate": (
                sum(1 for v in dr_vals if v) / len(dr_vals) if dr_vals else None
            ),
            "n_with_m1": len(m1_vals),
            "mean_per_seed_M1": (
                sum(m1_vals) / len(m1_vals) if m1_vals else None
            ),
        }
    q1 = rows[1]["deep_reach_rate"]
    q4 = rows[N_QUARTILES]["deep_reach_rate"]
    gap = q1 - q4 if (q1 is not None and q4 is not None) else None
    return {"rows": rows, "q1_minus_q4_deep_reach": gap}


def conditional_m1_differs(
    quartiles: dict[str, int],
    deep_reach: dict[str, bool],
    per_seed_m1: dict[str, int],
) -> tuple[bool, float | None, float | None]:
    """Do Q1 vs Q4 differ on mean per-seed M1 *conditional on reaching deep*?

    FOLLOWUP.md §5's "mechanism DIFFERENT" arm: among seeds that DO reach a
    DEEP branch, compare mean per-seed M1 for Q1 vs Q4. We treat them as
    differing iff the relative difference exceeds 10% of the larger mean
    (a deliberately loose, pre-committed-style threshold so the adjudication
    is mechanical — the §5 rule itself does not pin a number for this arm,
    see the module's resolution note in the final report). Returns
    ``(differs, q1_mean, q4_mean)``; means are ``None`` when that quartile
    has no deep-reaching seed with an M1 value.
    """

    def mean_for(q: int) -> float | None:
        vals = [
            per_seed_m1[i]
            for i, qq in quartiles.items()
            if qq == q
            and deep_reach.get(i, False)
            and i in per_seed_m1
        ]
        return sum(vals) / len(vals) if vals else None

    q1m = mean_for(1)
    q4m = mean_for(N_QUARTILES)
    if q1m is None or q4m is None:
        return False, q1m, q4m
    denom = max(abs(q1m), abs(q4m), 1e-9)
    return (abs(q1m - q4m) / denom) >= 0.10, q1m, q4m


def interpret_pool(
    full_table: dict,
    prefix_table: dict,
    quartiles_full: dict[str, int],
    deep_reach: dict[str, bool],
    per_seed_m1: dict[str, int],
) -> dict:
    """Apply FOLLOWUP.md §5's frozen 3-way interpretation rule for one pool.

    The rule keys off FULL mean payload entropy (the experiment6 statistic);
    prefix entropy is reported alongside as the separation comparison
    requested in §5 ("prefix separates deep-reach at least as well").

    - Q1−Q4 ≥ +0.10                       → "mechanism SUPPORTED"
    - |Q1−Q4| < 0.10 but mean per-seed M1
      differs conditional on deep-reach   → "mechanism DIFFERENT"
    - neither differs                     → "mechanism WRONG / union-level"
    """
    gap_full = full_table["q1_minus_q4_deep_reach"]
    gap_prefix = prefix_table["q1_minus_q4_deep_reach"]

    cond_diff, q1m, q4m = conditional_m1_differs(
        quartiles_full, deep_reach, per_seed_m1
    )

    if gap_full is None:
        verdict = "INDETERMINATE (no deep-reach data for Q1 or Q4)"
    elif gap_full >= MIN_MEANINGFUL_GAP:
        verdict = "mechanism SUPPORTED"
    elif abs(gap_full) < MIN_MEANINGFUL_GAP and cond_diff:
        verdict = "mechanism DIFFERENT"
    else:
        verdict = "mechanism WRONG / union-level"

    # Which entropy metric better separates deep-reach (larger |Q1−Q4|)?
    better = None
    if gap_full is not None and gap_prefix is not None:
        if abs(gap_prefix) > abs(gap_full):
            better = "prefix"
        elif abs(gap_full) > abs(gap_prefix):
            better = "full"
        else:
            better = "tie"

    return {
        "verdict": verdict,
        "q1_minus_q4_full": gap_full,
        "q1_minus_q4_prefix": gap_prefix,
        "conditional_m1_differs": cond_diff,
        "conditional_m1_q1_mean": q1m,
        "conditional_m1_q4_mean": q4m,
        "better_separating_entropy_metric": better,
    }


# ---------------------------------------------------------------------------
# Per-pool driver
# ---------------------------------------------------------------------------


def analyze_pool(
    pool: str,
    *,
    repo_root: Path = REPO_ROOT,
    skip_m1_replay: bool = False,
) -> dict:
    """Run the full FOLLOWUP.md §4 computation for one pool (v1_src/v3_all)."""
    target = TARGETS["harfbuzz"]
    targets_path = Path(target.m2_targets_path)
    deep_keys, surrogate_counts = load_deep_target_keys(targets_path)

    entropies_path = repo_root / "results/experiment6" / pool / "entropies.json"
    full_entropies = json.loads(entropies_path.read_text())["entropies"]

    gap_hits_path = (
        repo_root / "results/experiment6" / pool / pool / "pool" / "gap_hits.jsonl"
    )
    deep_reach = seed_deep_reach(gap_hits_path, deep_keys)

    sidecar_dir = (
        repo_root
        / "synthesis/results/experiment6/logprobs/harfbuzz/ablation"
        / pool
        / MODEL
    )
    prefix_result = per_seed_prefix_entropies(sidecar_dir)
    prefix_entropies = prefix_result["entropies"]

    cache_path = (
        repo_root / "results/experiment6/followup" / f"per_seed_m1_{pool}.json"
    )
    seeds_dir = (
        repo_root
        / "synthesis/results/experiment6/seeds/harfbuzz/ablation"
        / pool
        / MODEL
    )
    if skip_m1_replay:
        per_seed_m1: dict[str, int] = {}
        if cache_path.exists():
            per_seed_m1 = {
                k: v
                for k, v in json.loads(cache_path.read_text()).items()
                if isinstance(v, int)
            }
        m1_source = "cached" if cache_path.exists() else "skipped (no cache)"
    else:
        per_seed_m1 = compute_per_seed_m1(
            seeds_dir,
            cache_path,
            Path(target.coverage_binary),
            [str(p) for p in target.source_roots],
        )
        m1_source = "replayed"

    # Quartile assignment restricted to pooled seeds that have an entropy
    # AND appear in the pool's gap_hits (so deep-reach is defined).
    full_pool_ids = {
        i for i in full_entropies if i in deep_reach
    }
    prefix_pool_ids = {
        i for i in prefix_entropies if i in deep_reach
    }
    full_scores = {i: full_entropies[i] for i in full_pool_ids}
    prefix_scores = {i: prefix_entropies[i] for i in prefix_pool_ids}

    q_full = assign_quartiles(full_scores)
    q_prefix = assign_quartiles(prefix_scores)

    table_full = quartile_table(q_full, deep_reach, per_seed_m1)
    table_prefix = quartile_table(q_prefix, deep_reach, per_seed_m1)

    interpretation = interpret_pool(
        table_full, table_prefix, q_full, deep_reach, per_seed_m1
    )

    return {
        "pool": pool,
        "model": MODEL,
        "surrogate_counts": surrogate_counts,
        "n_seeds_in_pool_gap_hits": len(deep_reach),
        "n_full_entropy_seeds": len(full_entropies),
        "n_full_entropy_in_pool": len(full_pool_ids),
        "prefix_entropy": {
            "n_total": prefix_result["n_total"],
            "n_ok": prefix_result["n_ok"],
            "n_dropped": len(prefix_result["dropped"]),
            "drop_reasons": _drop_histogram(prefix_result["dropped"]),
            "n_in_pool": len(prefix_pool_ids),
        },
        "per_seed_m1": {
            "source": m1_source,
            "n_with_value": len(per_seed_m1),
            "cache_path": str(cache_path),
        },
        "table_full_entropy": _jsonable_table(table_full),
        "table_prefix_entropy": _jsonable_table(table_prefix),
        "interpretation": interpretation,
    }


def _drop_histogram(dropped: dict[str, str]) -> dict[str, int]:
    hist: dict[str, int] = {}
    for status in dropped.values():
        hist[status] = hist.get(status, 0) + 1
    return hist


def _jsonable_table(table: dict) -> dict:
    """Stringify the integer quartile keys so the table round-trips JSON."""
    return {
        "rows": {str(q): row for q, row in table["rows"].items()},
        "q1_minus_q4_deep_reach": table["q1_minus_q4_deep_reach"],
    }


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _fmt(x: float | None, nd: int = 4) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def render_markdown(pools_result: list[dict]) -> str:
    lines: list[str] = []
    lines.append("# experiment6 FOLLOW-UP C — per-seed mechanism check")
    lines.append("")
    lines.append(
        "Offline, no new LLM calls. Frozen DEEP/EARLY surrogate "
        "(FOLLOWUP.md §3): basename `hb-ot`/`hb-shape` = DEEP, "
        "`hb-blob`/`hb-open` = EARLY (verified EARLY=13, DEEP=37)."
    )
    lines.append("")
    for res in pools_result:
        pool = res["pool"]
        lines.append(f"## Pool `{pool}`")
        lines.append("")
        sc = res["surrogate_counts"]
        lines.append(
            f"- surrogate counts: EARLY={sc[EARLY]} DEEP={sc[DEEP]} "
            f"(UNMAPPED={sc['UNMAPPED']})"
        )
        lines.append(
            f"- seeds in pool gap_hits: {res['n_seeds_in_pool_gap_hits']}; "
            f"full-entropy seeds: {res['n_full_entropy_seeds']} "
            f"({res['n_full_entropy_in_pool']} in pool)"
        )
        pe = res["prefix_entropy"]
        lines.append(
            f"- prefix entropy: n_ok={pe['n_ok']}/{pe['n_total']} "
            f"dropped={pe['n_dropped']} {pe['drop_reasons']} "
            f"({pe['n_in_pool']} in pool)"
        )
        m1 = res["per_seed_m1"]
        lines.append(
            f"- per-seed M1: source={m1['source']} "
            f"n_with_value={m1['n_with_value']}"
        )
        lines.append("")
        for label, key in (
            ("FULL mean payload entropy", "table_full_entropy"),
            ("PREFIX entropy (first 8 payload tokens)", "table_prefix_entropy"),
        ):
            tbl = res[key]
            lines.append(f"### {label}")
            lines.append("")
            lines.append(
                "| quartile | n | deep_reach_rate | mean_per_seed_M1 |"
            )
            lines.append("|---|---|---|---|")
            for q in range(1, N_QUARTILES + 1):
                row = tbl["rows"][str(q)]
                lines.append(
                    f"| Q{q} | {row['n']} | "
                    f"{_fmt(row['deep_reach_rate'])} | "
                    f"{_fmt(row['mean_per_seed_M1'], 2)} |"
                )
            lines.append("")
            lines.append(
                f"Q1−Q4 deep-reach gap: "
                f"{_fmt(tbl['q1_minus_q4_deep_reach'])}"
            )
            lines.append("")
        interp = res["interpretation"]
        lines.append("### Interpretation (FOLLOWUP.md §5, pre-committed)")
        lines.append("")
        lines.append(f"- **Verdict: {interp['verdict']}**")
        lines.append(
            f"- Q1−Q4 (full) = {_fmt(interp['q1_minus_q4_full'])}; "
            f"threshold ±{MIN_MEANINGFUL_GAP:.2f}"
        )
        lines.append(
            f"- Q1−Q4 (prefix) = {_fmt(interp['q1_minus_q4_prefix'])}"
        )
        lines.append(
            f"- conditional-on-deep-reach mean M1: "
            f"Q1={_fmt(interp['conditional_m1_q1_mean'], 2)} "
            f"Q4={_fmt(interp['conditional_m1_q4_mean'], 2)} "
            f"(differs={interp['conditional_m1_differs']})"
        )
        lines.append(
            f"- better-separating entropy metric: "
            f"{interp['better_separating_entropy_metric']}"
        )
        lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="experiment6_followup",
        description=(
            "experiment6 FOLLOW-UP C: per-seed mechanism check "
            "(offline, no new LLM calls). Implements docs/experiment6/"
            "FOLLOWUP.md §3-§5 exactly."
        ),
    )
    parser.add_argument(
        "--pools",
        default="v1_src,v3_all",
        help="comma-separated pools (default: v1_src,v3_all).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "results/experiment6/followup",
        help="output directory for summary.json / summary.md.",
    )
    parser.add_argument(
        "--skip-m1-replay",
        action="store_true",
        help=(
            "do NOT run the heavy per-seed replay; reuse "
            "per_seed_m1_<pool>.json if present (fast structural dry-run)."
        ),
    )
    args = parser.parse_args(argv)

    pools = [p.strip() for p in args.pools.split(",") if p.strip()]
    results = [
        analyze_pool(p, skip_m1_replay=args.skip_m1_replay) for p in pools
    ]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "pools": pools,
        "min_meaningful_gap": MIN_MEANINGFUL_GAP,
        "prefix_token_count": PREFIX_TOKEN_COUNT,
        "skip_m1_replay": args.skip_m1_replay,
        "results": results,
    }
    (args.out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    (args.out_dir / "summary.md").write_text(render_markdown(results))

    for res in results:
        print(
            f"{res['pool']}: {res['interpretation']['verdict']} "
            f"(Q1-Q4 full={_fmt(res['interpretation']['q1_minus_q4_full'])}, "
            f"prefix={_fmt(res['interpretation']['q1_minus_q4_prefix'])}, "
            f"better={res['interpretation']['better_separating_entropy_metric']})"
        )
    print(f"-> {args.out_dir / 'summary.json'}")
    print(f"-> {args.out_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
