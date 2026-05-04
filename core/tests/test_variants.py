"""Smoke test for the VariantSpec registry."""
from __future__ import annotations

from core.variants import STANDARD_VARIANTS, VARIANTS_BY_NAME, VariantSpec


def test_standard_variants_registry():
    names = [v.name for v in STANDARD_VARIANTS]
    assert names == [
        "v0_none", "v1_src", "v2_src_tests", "v3_all", "v4_src_gaps",
        "v5_topk_gaps",
    ]


def test_variants_are_frozen_dataclasses():
    v = STANDARD_VARIANTS[0]
    assert isinstance(v, VariantSpec)
    import dataclasses
    assert dataclasses.is_dataclass(v)


def test_variant_flags_match_design():
    by_name = VARIANTS_BY_NAME
    assert by_name["v0_none"].include_source is False
    assert by_name["v1_src"].include_source is True
    assert by_name["v1_src"].include_tests is False
    assert by_name["v2_src_tests"].include_tests is True
    assert by_name["v3_all"].include_gaps is True
    assert by_name["v4_src_gaps"].include_gaps is True
    assert by_name["v4_src_gaps"].include_tests is False
    # v5_topk_gaps is the gap-only retrieval variant: no source, no tests.
    v5 = by_name["v5_topk_gaps"]
    assert v5.include_source is False
    assert v5.include_tests is False
    assert v5.include_gaps is True
