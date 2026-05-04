"""Process-spanning USD budget enforcement for LLM calls.

The orchestrator fans out synthesis into subprocesses (one per batch),
so the budget tracker has to be persistent across processes. It writes
append-only JSONL to `.cache/budget/<run_id>.jsonl` with fcntl-locked
reads before each decision. Cumulative spend is read from the ledger
each time; callers check `assert_can_spend(estimate)` before a call and
`record(actual)` after.

Three caps, all configurable by env var:

- `UTCF_MAX_SPEND_USD`        — global cap across the ledger.
- `UTCF_PER_CELL_CAP_USD`     — per-cell cap (cell_key passed in).
- `UTCF_PER_CALL_CAP_USD`     — single-call estimate refused above this.

Missing cap → no enforcement for that tier (permissive default). The
5-cell experiment sets all three via env before the subprocess fan-out.

Failure mode: when a cap trips, we raise `BudgetExceededError`. The
subprocess exits non-zero; the orchestrator counts that as a failed
batch in its consecutive-fail window and eventually aborts the cell.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_LEDGER_DIR = Path(os.environ.get(
    "UTCF_BUDGET_LEDGER_DIR", ".cache/budget"
))
DEFAULT_RUN_ID = os.environ.get("UTCF_BUDGET_RUN_ID", "default")


class BudgetExceededError(RuntimeError):
    """Raised when a pre-call estimate or post-call total trips a cap."""


def _env_float(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


@dataclass
class BudgetCaps:
    global_cap: float | None = None
    per_cell_cap: float | None = None
    per_call_cap: float | None = None

    @classmethod
    def from_env(cls) -> "BudgetCaps":
        return cls(
            global_cap=_env_float("UTCF_MAX_SPEND_USD"),
            per_cell_cap=_env_float("UTCF_PER_CELL_CAP_USD"),
            per_call_cap=_env_float("UTCF_PER_CALL_CAP_USD"),
        )


class BudgetTracker:
    """File-backed spend ledger shared across subprocesses."""

    def __init__(
        self,
        *,
        run_id: str = DEFAULT_RUN_ID,
        ledger_dir: Path | str = DEFAULT_LEDGER_DIR,
        caps: BudgetCaps | None = None,
    ) -> None:
        self.run_id = run_id
        self.ledger_dir = Path(ledger_dir)
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.ledger_dir / f"{run_id}.jsonl"
        self.caps = caps or BudgetCaps.from_env()

    # ── Ledger I/O ───────────────────────────────────────────────────

    def _read_totals(self) -> tuple[float, dict[str, float]]:
        """Return (global_total_usd, per_cell_total_usd)."""
        if not self.ledger_path.is_file():
            return 0.0, {}
        total = 0.0
        by_cell: dict[str, float] = {}
        with self.ledger_path.open("r") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_SH)
            try:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    usd = float(entry.get("cost_usd", 0.0))
                    total += usd
                    cell = entry.get("cell_key")
                    if cell:
                        by_cell[cell] = by_cell.get(cell, 0.0) + usd
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return total, by_cell

    def _append(self, entry: dict[str, Any]) -> None:
        with self.ledger_path.open("a") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    # ── Enforcement ──────────────────────────────────────────────────

    def assert_can_spend(
        self, estimate_usd: float, *, cell_key: str | None = None,
    ) -> None:
        """Raise BudgetExceededError if spending ``estimate_usd`` would trip a cap.

        Cheap defense: refuse outright if the single-call estimate exceeds
        ``per_call_cap`` (guards against a runaway prompt).
        """
        if estimate_usd < 0:
            raise ValueError(f"negative estimate: {estimate_usd}")

        if self.caps.per_call_cap is not None and estimate_usd > self.caps.per_call_cap:
            raise BudgetExceededError(
                f"single-call estimate ${estimate_usd:.4f} exceeds "
                f"per-call cap ${self.caps.per_call_cap:.4f}"
            )

        total, by_cell = self._read_totals()
        projected_total = total + estimate_usd
        if self.caps.global_cap is not None and projected_total > self.caps.global_cap:
            raise BudgetExceededError(
                f"global spend cap tripped: current=${total:.4f} + "
                f"estimate=${estimate_usd:.4f} = ${projected_total:.4f} "
                f"> cap ${self.caps.global_cap:.4f} "
                f"(ledger={self.ledger_path})"
            )

        if cell_key and self.caps.per_cell_cap is not None:
            cell_total = by_cell.get(cell_key, 0.0)
            projected_cell = cell_total + estimate_usd
            if projected_cell > self.caps.per_cell_cap:
                raise BudgetExceededError(
                    f"per-cell cap tripped for {cell_key!r}: "
                    f"current=${cell_total:.4f} + estimate=${estimate_usd:.4f} "
                    f"= ${projected_cell:.4f} > cap ${self.caps.per_cell_cap:.4f}"
                )

    def record(
        self,
        actual_usd: float,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cell_key: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Append a ledger row. Idempotent-unsafe: one call = one row."""
        entry: dict[str, Any] = {
            "ts": time.time(),
            "run_id": self.run_id,
            "model": model,
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "cost_usd": float(actual_usd),
            "cell_key": cell_key,
        }
        if extra:
            entry["extra"] = extra
        self._append(entry)

    # ── Reporting ────────────────────────────────────────────────────

    def totals(self) -> dict[str, Any]:
        total, by_cell = self._read_totals()
        return {
            "run_id": self.run_id,
            "ledger_path": str(self.ledger_path),
            "global_total_usd": round(total, 6),
            "by_cell_usd": {k: round(v, 6) for k, v in by_cell.items()},
            "caps": {
                "global_cap_usd": self.caps.global_cap,
                "per_cell_cap_usd": self.caps.per_cell_cap,
                "per_call_cap_usd": self.caps.per_call_cap,
            },
        }


_default_tracker: BudgetTracker | None = None


def get_default_tracker() -> BudgetTracker:
    global _default_tracker
    if _default_tracker is None:
        _default_tracker = BudgetTracker()
    return _default_tracker


def reset_default_tracker() -> None:
    """Test hook — clears the module-level cached tracker."""
    global _default_tracker
    _default_tracker = None
