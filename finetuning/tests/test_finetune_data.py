"""Fine-tune data preparation (plan §4.1)."""
from __future__ import annotations

import json

from core.dataset_schema import Test
from finetuning.scripts.prepare_finetune_data import _has_provenance, _to_alpaca


def _test(**overrides) -> Test:
    base = dict(
        test_name="TestS.T",
        test_code="TEST(S, T) { EXPECT_TRUE(true); }",
        test_file="re2/testing/re2_test.cc",
        upstream_repo="https://github.com/google/re2",
        upstream_commit="deadbeef" * 5,
        upstream_file="re2/testing/re2_test.cc",
        upstream_line=42,
        framework="googletest",
        input_data=None,
    )
    base.update(overrides)
    return Test.model_validate(base)


def test_provenance_detector_accepts_complete_test():
    assert _has_provenance(_test())


def test_provenance_detector_rejects_missing_fields():
    assert not _has_provenance(_test(upstream_repo=""))
    assert not _has_provenance(_test(upstream_commit=""))
    assert not _has_provenance(_test(upstream_file=""))


def test_alpaca_row_has_required_fields():
    row = _to_alpaca(_test(), None, "int main() { return 0; }")
    assert row["instruction"]
    assert "TEST(S, T)" in row["input"]
    assert row["metadata"]["upstream_repo"].endswith("re2")
    assert row["metadata"]["upstream_line"] == 42
    assert json.loads(row["output"]) == {}
