"""Deduplicate crashes via stack-hash + coverage-profile (plan §3.6).

Reads reproducer files from each trial's crash directory and produces a
deduplicated `CrashRecord` list keyed by the tuple (top-N stack frames,
coverage profile hash). Manual triage happens downstream; this pass just
removes obvious duplicates so the triage queue stays manageable.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.dataset_schema import CrashRecord
from core.logging_config import get_logger

logger = get_logger("utcf.phase3.dedup")

FRAME_RE = re.compile(r"#\d+\s+0x[0-9a-fA-F]+\s+in\s+(\S+)")
TOP_N_FRAMES = 5


def stack_hash(stderr: str, *, top_n: int = TOP_N_FRAMES) -> str:
    frames = FRAME_RE.findall(stderr)[:top_n]
    if not frames:
        head = stderr[:1000]
        return hashlib.sha256(head.encode()).hexdigest()[:16]
    joined = "|".join(frames)
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def coverage_hash(input_bytes: bytes, *, slug: str = "") -> str:
    # Without running the coverage binary we approximate with a content hash
    # (true coverage-profile hash requires a second pass). Real impl can
    # replace this with the `measure_coverage` edge set per crash.
    h = hashlib.sha256(input_bytes).hexdigest()
    return f"{slug}:{h[:16]}" if slug else h[:16]


def dedup(
    *,
    target: str,
    config_name: str,
    campaign_work_dir: Path,
) -> list[CrashRecord]:
    seen: dict[tuple[str, str], CrashRecord] = {}
    for trial_dir in sorted(campaign_work_dir.glob("trial_*")):
        try:
            trial_index = int(trial_dir.name.rsplit("_", 1)[1])
        except ValueError:
            continue
        for repro in sorted(trial_dir.glob("crash-*")):
            if repro.name.endswith(".stderr"):
                continue
            if not repro.is_file():
                continue
            stderr_path = repro.parent / f"{repro.name}.stderr"
            stderr = stderr_path.read_text(errors="replace") if stderr_path.is_file() else ""
            payload = repro.read_bytes()
            key = (stack_hash(stderr), coverage_hash(payload))
            if key in seen:
                continue
            crash_id = hashlib.sha256(f"{key[0]}|{key[1]}".encode()).hexdigest()[:16]
            seen[key] = CrashRecord(
                crash_id=crash_id,
                target=target,
                config_name=config_name,
                stack_hash=key[0],
                coverage_profile_hash=key[1],
                input_b64=base64.b64encode(payload).decode("ascii"),
                first_seen_trial=trial_index,
                first_seen_elapsed_s=0.0,
                reproducer_path=str(repro),
                stderr_tail=stderr[-2000:],
            )
    return list(seen.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--config-name", required=True)
    parser.add_argument("--campaign-work-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    records = dedup(
        target=args.target,
        config_name=args.config_name,
        campaign_work_dir=args.campaign_work_dir,
    )
    out = args.out or (REPO_ROOT / "synthesis" / "results" / "crashes" / f"{args.target}_{args.config_name}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([r.model_dump() for r in records], indent=2))
    print(f"unique_crashes={len(records)} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
