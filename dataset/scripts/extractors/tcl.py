"""TCL test extractor (SQLite). Stub.

Target framework: parses `.test` files for `do_test`, `do_execsql_test`, and
`do_catchsql_test` invocations. See plan §1.4. Remember stratified subsampling
(seed=42) for few-shot prompt construction.
"""
from __future__ import annotations

from typing import Any

from core.dataset_schema import Test


def extract(target_config: dict[str, Any], repo_root: str) -> list[Test]:  # noqa: ARG001
    raise NotImplementedError("tcl extractor: implement when SQLite is enabled")
