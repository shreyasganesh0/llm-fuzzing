"""Random syntactically-valid baseline (plan §Baseline 6).

Produces the same number of seeds as the LLM output so the random-vs-LLM
comparison is fair. Format depends on the target:
  - re2:            regex + string pairs (uses `exrex` if installed, else random bytes)
  - libxml2:        minimal XML documents (uses `lxml` if installed)
  - sqlite3:        random SQL from a keyword bag
  - default:        uniformly random bytes under `max_len`

The caller usually passes in `count` matching the LLM seed count for this
target so trial sizes line up.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.dataset_schema import GeneratedInput
from core.logging_config import get_logger
from dataset.scripts.pinned_loader import load_target_yaml

logger = get_logger("utcf.phase3.random")

SQL_KEYWORDS = [
    "SELECT", "FROM", "WHERE", "AND", "OR", "INSERT", "INTO", "VALUES",
    "UPDATE", "SET", "DELETE", "CREATE", "TABLE", "DROP", "JOIN", "ON",
    "ORDER BY", "LIMIT", "COUNT", "DISTINCT",
]


def _rand_hex(rng: random.Random, n_bytes: int) -> str:
    return "".join(f"{rng.randint(0, 255):02x}" for _ in range(n_bytes))


def _generate_regex_pair(rng: random.Random, max_len: int) -> bytes:
    try:
        import exrex
        pattern_len = min(12, max_len // 3)
        pattern = "".join(rng.choice("a-z0-9.*+?^$[]()") for _ in range(pattern_len))
        try:
            string = next(exrex.generate(pattern, limit=1)) or ""
        except Exception:
            string = _rand_hex(rng, 8)
        payload = (pattern[: max_len // 2] + "\x00" + string[: max_len // 2]).encode()
        return payload[:max_len]
    except ImportError:
        return _random_bytes(rng, max_len)


_REGEX_ASCII_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    ".*+?^$|\\()[]{}-_,"
)


def _generate_regex_harness_bytes(rng: random.Random, max_len: int) -> bytes:
    """Match the RE2 seed_replay harness: `[2 random flag bytes][ASCII regex body]`.

    Total length is clipped to [3, min(64, max_len)] per the harness contract.
    """
    upper = min(64, max_len)
    total_len = rng.randint(3, upper)
    flags = bytes(rng.getrandbits(8) for _ in range(2))
    body_len = total_len - 2
    body = "".join(rng.choice(_REGEX_ASCII_ALPHABET) for _ in range(body_len))
    return flags + body.encode("ascii")


def _generate_xml(rng: random.Random, max_len: int) -> bytes:
    try:
        from lxml import etree
        root = etree.Element("root")
        for _ in range(rng.randint(1, 5)):
            child = etree.SubElement(root, f"el{rng.randint(0, 99)}")
            child.text = _rand_hex(rng, rng.randint(1, 8))
        return etree.tostring(root, xml_declaration=True, encoding="utf-8")[:max_len]
    except ImportError:
        return b"<?xml version='1.0'?><r>" + _rand_hex(rng, 16).encode() + b"</r>"


def _generate_sql(rng: random.Random, max_len: int) -> bytes:
    parts = [rng.choice(SQL_KEYWORDS) for _ in range(rng.randint(3, 8))]
    return (" ".join(parts) + ";").encode()[:max_len]


def _random_bytes(rng: random.Random, max_len: int) -> bytes:
    n = rng.randint(1, max_len)
    return bytes(rng.getrandbits(8) for _ in range(n))


def _generate_harfbuzz_random(rng: random.Random, max_len: int) -> bytes:
    """Random binary blob for harfbuzz (uniform bytes, no font structure).

    Length drawn uniformly from [4, min(max_len, 4096)] to match realistic
    fuzzer input sizes while keeping most seeds small for fast replay.
    """
    upper = min(max_len, 4096)
    n = rng.randint(4, max(4, upper))
    return bytes(rng.getrandbits(8) for _ in range(n))


GENERATORS = {
    "re2": _generate_regex_pair,
    "libxml2": _generate_xml,
    "sqlite3": _generate_sql,
    "harfbuzz": _generate_harfbuzz_random,
}

REGEX_HARNESS_GENERATORS = {
    "re2": _generate_regex_harness_bytes,
}


def generate_random(
    target: str,
    *,
    count: int,
    max_len: int,
    seed: int = 42,
    input_format: str = "bytes",
) -> list[GeneratedInput]:
    rng = random.Random(seed)
    if input_format == "regex":
        gen = REGEX_HARNESS_GENERATORS.get(target)
        if gen is None:
            raise ValueError(f"--input-format regex not supported for target={target}")
    else:
        gen = GENERATORS.get(target, _random_bytes)
    out: list[GeneratedInput] = []
    for idx in range(count):
        payload = gen(rng, max_len)
        b64 = base64.b64encode(payload).decode("ascii")
        iid = hashlib.sha256(f"random|{target}|{idx}|{b64}".encode()).hexdigest()[:16]
        out.append(
            GeneratedInput(
                input_id=iid,
                content_b64=b64,
                target_gaps=[],
                reasoning="random baseline",
                source="random",
                model=None,
                temperature=None,
                sample_index=None,
                target=target,
                experiment="exp1",
            )
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--results-root", type=Path, default=REPO_ROOT / "synthesis" / "results")
    parser.add_argument(
        "--input-format",
        choices=("bytes", "regex"),
        default="bytes",
        help="regex: target-specific harness layout (RE2 = [2 flag bytes][ASCII regex body])",
    )
    args = parser.parse_args()

    cfg = load_target_yaml(REPO_ROOT / "dataset" / "targets" / f"{args.target}.yaml", require_resolved=True)
    max_len = cfg.get("fuzzbench", {}).get("libfuzzer_extra_flags", {}).get("max_len", 4096)

    inputs = generate_random(
        args.target,
        count=args.count,
        max_len=max_len,
        seed=args.seed,
        input_format=args.input_format,
    )
    seeds_dir = args.results_root / "seeds" / args.target / "random"
    seeds_dir.mkdir(parents=True, exist_ok=True)
    for inp in inputs:
        (seeds_dir / f"seed_{inp.input_id}.bin").write_bytes(base64.b64decode(inp.content_b64))
    logger.info("random seeds written", extra={"target": args.target, "count": len(inputs)})
    print(f"wrote {len(inputs)} random seeds to {seeds_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
