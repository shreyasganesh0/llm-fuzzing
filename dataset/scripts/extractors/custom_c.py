"""Custom C test extractor (libxml2, lcms, libpng, zlib). Stub.

Target framework: parses named test functions (`test_*`, `Check*`) via
tree-sitter C parser. See plan §1.4.
"""
from __future__ import annotations

from typing import Any

from core.dataset_schema import Test


def extract(target_config: dict[str, Any], repo_root: str) -> list[Test]:  # noqa: ARG001
    raise NotImplementedError("custom_c extractor: implement when libxml2/lcms/libpng/zlib is enabled")
