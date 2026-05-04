"""Ablation variant registry.

The 5-variant × 7-model design collapses to 5 entries. Both orchestrators
used to re-declare this dict verbatim — now they import from here.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VariantSpec:
    name: str
    include_source: bool
    include_tests: bool
    include_gaps: bool


STANDARD_VARIANTS: list[VariantSpec] = [
    VariantSpec("v0_none",      include_source=False, include_tests=False, include_gaps=False),
    VariantSpec("v1_src",       include_source=True,  include_tests=False, include_gaps=False),
    VariantSpec("v2_src_tests", include_source=True,  include_tests=True,  include_gaps=False),
    VariantSpec("v3_all",       include_source=True,  include_tests=True,  include_gaps=True),
    VariantSpec("v4_src_gaps",  include_source=True,  include_tests=False, include_gaps=True),
    # Gap-only retrieval variant: no full-file source, no tests, only the
    # top-K uncovered branches (each with its own code_context window).
    # The prompt template is the same; the difference is purely which
    # `include_*` flags fire. K is implicitly len(m2_targets['shown']) —
    # for RE2 that is already 10.
    VariantSpec("v5_topk_gaps", include_source=False, include_tests=False, include_gaps=True),
]

VARIANTS_BY_NAME: dict[str, VariantSpec] = {v.name: v for v in STANDARD_VARIANTS}
