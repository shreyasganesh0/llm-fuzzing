"""End-to-end Phase 1 smoke test using a synthetic mini-target.

Writes a tiny upstream tree under `tmp_path`, points the target YAML at it via
monkeypatching, and verifies extract->audit-gate produces `dataset_stats.json`
with full provenance.
"""
from __future__ import annotations

import json
from pathlib import Path

from dataset.scripts import build_dataset, extract_tests

FIXTURE = Path(__file__).parent / "fixtures" / "mini_test.cc"


def test_build_dataset_full_provenance(tmp_path, monkeypatch):
    # Lay out a fake upstream repo.
    upstream = tmp_path / "dataset" / "targets" / "src" / "re2" / "upstream"
    (upstream / "re2" / "testing").mkdir(parents=True)
    (upstream / "re2" / "testing" / "mini_test.cc").write_text(FIXTURE.read_text())

    # Minimal target YAML referencing the fake upstream.
    target_yaml = tmp_path / "dataset" / "targets" / "re2.yaml"
    target_yaml.parent.mkdir(parents=True, exist_ok=True)
    target_yaml.write_text(
        """
name: re2
tier: 1
fuzzbench_benchmark: re2-2014-12-09
upstream:
  repo: https://example.com/re2.git
  commit: deadbeef
tests:
  framework: googletest
  locations:
    - re2/testing/mini_test.cc
fuzzbench:
  harness_source: fuzzer-test-suite
  harness_file: re2-2014-12-09/target.cc
  dictionary: null
  seeds: none
  libfuzzer_extra_flags: {timeout: 25, rss_limit_mb: 2048, max_len: 4096}
""".strip()
    )

    # Neutralise pinned_versions lookup (none of the fields are !from_pinned here).
    # Point build_dataset/extract_tests at the fake repo root.
    monkeypatch.setattr(extract_tests, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(build_dataset, "REPO_ROOT", tmp_path)

    dataset_root = tmp_path / "dataset" / "dataset"
    stats = build_dataset.build(
        "re2", dataset_root=dataset_root, skip_coverage=True, skip_gaps=True
    )

    assert stats["test_count"] == 3
    assert stats["provenance_audit"]["tests_rejected"] == 0
    assert stats["provenance_audit"]["tests_with_provenance"] == 3

    tests_json = dataset_root / "re2" / "tests.json"
    assert tests_json.is_file()
    loaded = json.loads(tests_json.read_text())
    assert loaded[0]["test_name"] == "RE2.FullMatch"
    assert loaded[0]["upstream_commit"] == "deadbeef"
