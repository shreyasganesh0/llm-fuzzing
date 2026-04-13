"""Perl TAP / ADD_TEST extractor (OpenSSL). Stub.

Target framework: extract at the C `ADD_TEST()` granularity (plan §1.4).
The Perl `.t` files orchestrate C test programs; we extract from the C layer.
"""
from __future__ import annotations

from typing import Any

from core.dataset_schema import Test


def extract(target_config: dict[str, Any], repo_root: str) -> list[Test]:  # noqa: ARG001
    raise NotImplementedError("perl_tap extractor: implement when OpenSSL is enabled")
