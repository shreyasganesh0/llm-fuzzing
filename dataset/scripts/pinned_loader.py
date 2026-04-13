"""YAML loader with `!from_pinned` tag support.

Usage:
    from dataset.scripts.pinned_loader import load_target_yaml
    config = load_target_yaml("dataset/targets/re2.yaml")
    # !from_pinned references resolved against pinned_versions.yaml.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PINNED_VERSIONS_PATH = Path("pinned_versions.yaml")


def _load_pinned(path: Path = PINNED_VERSIONS_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"pinned_versions.yaml not found at {path}")
    with open(path) as f:
        return yaml.safe_load(f)


def _resolve_path(pinned: dict[str, Any], dotted: str) -> Any:
    """Walk a dotted path like 'targets.re2.upstream_commit' through the pinned dict.

    For convenience, a short form like 're2.upstream_commit' is also accepted;
    it is rewritten to 'targets.re2.upstream_commit'.
    """
    parts = dotted.split(".")
    if parts and parts[0] not in pinned and parts[0] not in ("fuzzbench", "fuzzer_test_suite"):
        parts = ["targets"] + parts
    node: Any = pinned
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"!from_pinned {dotted}: missing key {part!r}")
        node = node[part]
    return node


class _PinnedResolvingLoader(yaml.SafeLoader):
    pass


def _make_from_pinned_constructor(pinned: dict[str, Any]):
    def constructor(loader: yaml.Loader, node: yaml.Node) -> Any:
        value = loader.construct_scalar(node)
        return _resolve_path(pinned, value)
    return constructor


def load_target_yaml(
    path: str | Path,
    *,
    pinned_path: Path = PINNED_VERSIONS_PATH,
    require_resolved: bool = True,
) -> dict[str, Any]:
    """Load a target YAML, resolving `!from_pinned` tags against pinned_versions.yaml.

    If `require_resolved=True`, raises ValueError when any resolved value is
    literally `<FILL>` (unresolved placeholder).
    """
    pinned = _load_pinned(pinned_path)
    _PinnedResolvingLoader.add_constructor("!from_pinned", _make_from_pinned_constructor(pinned))

    with open(path) as f:
        config = yaml.load(f, Loader=_PinnedResolvingLoader)  # noqa: S506 — custom safe subclass

    if require_resolved:
        unresolved = _find_fill(config)
        if unresolved:
            raise ValueError(
                f"Target {path} references unresolved <FILL> values: {unresolved}. "
                "Fill pinned_versions.yaml before invoking this target."
            )
    return config


def _find_fill(node: Any, path: str = "") -> list[str]:
    """Walk the config and return dotted paths whose leaf value is '<FILL>'."""
    hits: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            hits.extend(_find_fill(v, f"{path}.{k}" if path else k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            hits.extend(_find_fill(v, f"{path}[{i}]"))
    elif isinstance(node, str) and "<FILL>" in node:
        hits.append(path)
    return hits


def assert_no_unresolved(target_name: str, pinned_path: Path = PINNED_VERSIONS_PATH) -> None:
    """Fail fast if this target's pin block still has <FILL> anywhere."""
    pinned = _load_pinned(pinned_path)
    target = pinned.get("targets", {}).get(target_name)
    if target is None:
        raise KeyError(f"Target {target_name} not found in {pinned_path}")
    unresolved = _find_fill(target, target_name)
    if unresolved:
        raise ValueError(f"Target {target_name} has unresolved placeholders: {unresolved}")
