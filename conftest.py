"""Project-root conftest ensuring the repo root is on sys.path so tests can
import `core.*`, `dataset.*`, `prediction.*`, `synthesis.*`, `transfer.*`,
`finetuning.*`, and `sanity.*` as top-level packages regardless of pytest's
rootdir mode."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
