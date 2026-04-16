"""Unit tests for the RE2::ok() audit.

Monkeypatches `compile_ok` so the test does not depend on whether
`google-re2` is installed in the pytest environment. A separate smoke test
gated on the real import lives at the bottom — skipped if re2 is missing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from analysis.scripts import ok_rate_audit as audit


def test_audit_cell_counts_ok_and_err(tmp_path: Path, monkeypatch):
    # Two "ok" seeds (flag bytes + `a+`, `b+`), two "err" seeds.
    (tmp_path / "ok0.bin").write_bytes(b"\x01\x02a+")
    (tmp_path / "ok1.bin").write_bytes(b"\x03\x04b+")
    (tmp_path / "bad0.bin").write_bytes(b"\x05\x06(?=x)")  # lookahead
    (tmp_path / "bad1.bin").write_bytes(b"\x07\x08(")      # unclosed

    def fake_compile(body: bytes) -> bool:
        text = body.decode("utf-8", errors="replace")
        return text in {"a+", "b+"}

    monkeypatch.setattr(audit, "compile_ok", fake_compile)
    row = audit.audit_cell(tmp_path)
    assert row["seeds"] == 4
    assert row["ok"] == 2
    assert row["err"] == 2
    assert row["ok_rate"] == 0.5


def test_audit_cell_strips_two_flag_bytes(tmp_path: Path, monkeypatch):
    (tmp_path / "seed.bin").write_bytes(b"\xaa\xbba+")
    seen: list[bytes] = []

    def capture(body: bytes) -> bool:
        seen.append(body)
        return True

    monkeypatch.setattr(audit, "compile_ok", capture)
    audit.audit_cell(tmp_path)
    assert seen == [b"a+"]


def test_audit_cell_handles_short_seed(tmp_path: Path, monkeypatch):
    # Seeds shorter than 2 bytes — keep as-is rather than IndexError out.
    (tmp_path / "tiny.bin").write_bytes(b"x")
    monkeypatch.setattr(audit, "compile_ok", lambda body: True)
    row = audit.audit_cell(tmp_path)
    assert row["seeds"] == 1
    assert row["ok"] == 1


def test_audit_cell_empty_dir(tmp_path: Path):
    row = audit.audit_cell(tmp_path)
    assert row == {"seeds": 0, "ok": 0, "err": 0, "ok_rate": 0.0}


@pytest.mark.skipif(
    pytest.importorskip("re2", reason="google-re2 not installed") is None,
    reason="google-re2 not installed",
)
def test_compile_ok_with_real_re2():
    assert audit.compile_ok(b"a+")
    assert not audit.compile_ok(b"(?=foo)")  # lookahead rejected by RE2
