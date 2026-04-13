"""Framework-specific test extractors.

Each extractor exposes `extract(target_config, repo_root) -> list[Test]`.
Only googletest is implemented in this session; the rest raise
NotImplementedError so Phase 1 fails fast for unsupported targets.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.dataset_schema import Test
from dataset.scripts.extractors import (
    ctest,
    custom_c,
    glib,
    googletest,
    perl_tap,
    tcl,
)

REGISTRY: dict[str, Callable[[dict[str, Any], str], list[Test]]] = {
    "googletest": googletest.extract,
    "glib": glib.extract,
    "custom_c": custom_c.extract,
    "ctest": ctest.extract,
    "tcl": tcl.extract,
    "perl_tap": perl_tap.extract,
}


def get(framework: str) -> Callable[[dict[str, Any], str], list[Test]]:
    try:
        return REGISTRY[framework]
    except KeyError as exc:
        raise KeyError(f"No extractor registered for framework {framework!r}") from exc
