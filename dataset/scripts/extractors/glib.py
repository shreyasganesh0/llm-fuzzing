"""GLib test extractor (HarfBuzz). Stub — populate in a future session.

Target framework: parses `g_test_add_func()` registrations to find test names,
then extracts the corresponding function bodies. See plan §1.4.
"""
from __future__ import annotations

from typing import Any

from core.dataset_schema import Test


def extract(target_config: dict[str, Any], repo_root: str) -> list[Test]:  # noqa: ARG001
    raise NotImplementedError("glib extractor: implement when HarfBuzz is enabled")
