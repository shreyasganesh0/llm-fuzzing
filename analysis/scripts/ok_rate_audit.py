"""RE2::ok() compile-success rate per ablation cell.

For each seed .bin in a cell, strip the 2 sha256-seeded flag bytes and try
compiling the remaining UTF-8 body with `google-re2`. Emits per-cell counts
and an overall markdown table.

The `google-re2` Python package tracks upstream RE2 closely; it is not the
exact commit we build against, but compile-rejection behaviour on basic
features (character classes, alternation, lookaheads → error) is stable and
fit-for-purpose for cross-cell comparison.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def compile_ok(body: bytes) -> bool:
    """True iff `google-re2` accepts the body as a valid RE2 pattern."""
    import re2  # noqa: PLC0415 — keep import local so tests can monkeypatch
    try:
        text = body.decode("utf-8", errors="replace")
        re2.compile(text)
        return True
    except Exception:  # noqa: BLE001 — re2 raises multiple error types
        return False


def audit_cell(seeds_dir: Path) -> dict:
    ok, err = 0, 0
    for seed_path in sorted(seeds_dir.glob("*.bin")):
        raw = seed_path.read_bytes()
        body = raw[2:] if len(raw) >= 2 else raw
        if compile_ok(body):
            ok += 1
        else:
            err += 1
    total = ok + err
    return {
        "seeds": total,
        "ok": ok,
        "err": err,
        "ok_rate": (ok / total) if total else 0.0,
    }


def _parse_cell_spec(spec: str) -> tuple[str, Path]:
    name, _, path = spec.partition("=")
    if not (name and path):
        raise SystemExit(f"bad --cell spec: {spec!r} (need name=seeds_dir)")
    return name, Path(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cell", action="append", required=True,
        metavar="name=seeds_dir", help="repeatable",
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    results: dict[str, dict] = {}
    for spec in args.cell:
        name, seeds_dir = _parse_cell_spec(spec)
        results[name] = audit_cell(seeds_dir)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "ok_rate.json").write_text(json.dumps(results, indent=2))

    lines = ["# RE2::ok() compile-success rate per cell", ""]
    lines.append("| cell | seeds | ok | err | ok-rate |")
    lines.append("|---|---:|---:|---:|---:|")
    for name, row in results.items():
        lines.append(
            f"| `{name}` | {row['seeds']} | {row['ok']} | {row['err']} | "
            f"{row['ok_rate']:.3f} |"
        )
    lines.append("")
    (args.out_dir / "ok_rate.md").write_text("\n".join(lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
