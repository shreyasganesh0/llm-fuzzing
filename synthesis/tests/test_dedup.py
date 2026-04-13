"""Crash dedup logic (plan §Phase 3 test_dedup)."""
from __future__ import annotations

from synthesis.scripts.dedup_crashes import coverage_hash, dedup, stack_hash


def test_stack_hash_same_frames_same_hash():
    a = "#0 0xdeadbeef in foo\n#1 0x12345 in bar\n"
    b = "#0 0xcafebabe in foo\n#1 0xbeefface in bar\n"
    assert stack_hash(a) == stack_hash(b)


def test_stack_hash_different_frames_different_hash():
    a = "#0 0xdeadbeef in foo\n#1 0x12345 in bar\n"
    b = "#0 0xdeadbeef in foo\n#1 0x12345 in qux\n"
    assert stack_hash(a) != stack_hash(b)


def test_stack_hash_no_frames_falls_back_to_header():
    assert stack_hash("totally-empty-report") != stack_hash("different-empty")


def test_coverage_hash_stable_across_calls():
    assert coverage_hash(b"abc") == coverage_hash(b"abc")
    assert coverage_hash(b"abc") != coverage_hash(b"xyz")


def test_dedup_merges_duplicates(tmp_path):
    work = tmp_path / "trial_00"
    work.mkdir()
    (work / "crash-aaaa").write_bytes(b"payload-1")
    (work / "crash-aaaa.stderr").write_text("#0 0x1 in foo\n#1 0x2 in bar\n")
    (work / "crash-bbbb").write_bytes(b"payload-1")  # same bytes -> same coverage hash
    (work / "crash-bbbb.stderr").write_text("#0 0xdead in foo\n#1 0xbeef in bar\n")
    (work / "crash-cccc").write_bytes(b"payload-2")
    (work / "crash-cccc.stderr").write_text("#0 0x3 in zzz\n#1 0x4 in yyy\n")

    records = dedup(target="re2", config_name="llm_seeds", campaign_work_dir=tmp_path)
    # crash-aaaa and crash-bbbb collapse (same stack hash + same coverage).
    # crash-cccc is distinct. -> 2 unique.
    assert len(records) == 2
