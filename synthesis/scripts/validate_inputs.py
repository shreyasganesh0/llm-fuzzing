"""Validate generated inputs against the sanitizer build.

For each input:
  1. Write the raw bytes to a temp file.
  2. Invoke the sanitizer binary (ASan + UBSan) with `--` so libFuzzer-style
     harnesses treat it as a single input.
  3. Capture exit code, stderr tail, and a crash signature if non-zero.

When the sanitizer binary isn't available, runs in `--dry-run` mode: each
input is marked parsed=True, crashed=False with runtime_ms=0.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.dataset_schema import InputValidation, SynthesisRecord
from core.logging_config import get_logger

logger = get_logger("utcf.phase3.validate")

ASAN_CRASH_RE = re.compile(r"ERROR:\s*(\S+)\s*:\s*([^\n]*)")


def _signature_from_stderr(stderr: str) -> str | None:
    m = ASAN_CRASH_RE.search(stderr)
    if not m:
        return None
    return f"{m.group(1)}:{m.group(2).strip()}"[:200]


def validate_one(binary: Path | None, payload: bytes, *, timeout_s: int) -> InputValidation:
    input_id = hashlib.sha256(payload).hexdigest()[:16]
    if binary is None or not binary.is_file():
        return InputValidation(input_id=input_id, parsed=True, crashed=False, runtime_ms=0.0)

    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(payload)
        tmp_path = Path(f.name)
    try:
        start = time.perf_counter()
        proc = subprocess.run(
            [str(binary), str(tmp_path)],
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        stderr = proc.stderr.decode(errors="replace")
        sig = _signature_from_stderr(stderr)
        return InputValidation(
            input_id=input_id,
            parsed=proc.returncode == 0,
            crashed=proc.returncode != 0 and sig is not None,
            crash_signature=sig,
            stderr_tail=stderr[-2000:],
            runtime_ms=elapsed_ms,
        )
    except subprocess.TimeoutExpired:
        return InputValidation(
            input_id=input_id,
            parsed=False,
            crashed=False,
            crash_signature="timeout",
            stderr_tail="",
            runtime_ms=timeout_s * 1000,
        )
    finally:
        tmp_path.unlink(missing_ok=True)


def validate_synthesis(
    target: str,
    *,
    synthesis_root: Path,
    sanitizer_binary: Path | None,
    timeout_s: int = 10,
) -> list[InputValidation]:
    records: list[InputValidation] = []
    roots = [p for p in synthesis_root.rglob("sample_*.json")]
    for path in sorted(roots):
        rec = SynthesisRecord.model_validate_json(path.read_text())
        if rec.target != target:
            continue
        for inp in rec.inputs:
            payload = __import__("base64").b64decode(inp.content_b64)
            v = validate_one(sanitizer_binary, payload, timeout_s=timeout_s)
            records.append(v)
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--synthesis-root", type=Path, default=REPO_ROOT / "synthesis" / "results" / "synthesis")
    parser.add_argument("--sanitizer-binary", type=Path, default=None,
                        help="Path to the *_san binary. Omit to run in dry-run mode.")
    parser.add_argument("--timeout-s", type=int, default=10)
    parser.add_argument("--results-root", type=Path, default=REPO_ROOT / "synthesis" / "results")
    args = parser.parse_args()

    target_synthesis = args.synthesis_root / args.target
    if not target_synthesis.is_dir():
        print(f"no synthesis records for {args.target} at {target_synthesis}", file=sys.stderr)
        return 1

    records = validate_synthesis(
        args.target,
        synthesis_root=target_synthesis,
        sanitizer_binary=args.sanitizer_binary,
        timeout_s=args.timeout_s,
    )
    out = args.results_root / "validation" / f"{args.target}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([r.model_dump() for r in records], indent=2))
    n_parsed = sum(1 for r in records if r.parsed)
    n_crashed = sum(1 for r in records if r.crashed)
    print(f"validated={len(records)} parsed={n_parsed} crashed={n_crashed} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
