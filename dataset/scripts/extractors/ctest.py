"""CTest extractor (libjpeg-turbo). Stub.

Target framework: parses `CMakeLists.txt` for `add_test()` invocations. See plan §1.4.
"""
from __future__ import annotations

from typing import Any

from core.dataset_schema import Test


def extract(target_config: dict[str, Any], repo_root: str) -> list[Test]:  # noqa: ARG001
    raise NotImplementedError("ctest extractor: implement when libjpeg-turbo is enabled")
