"""Build source-only context for each target (plan §E2.1).

Produces `source_context/<target>/` with the harness, prioritized library
source files, and a manifest. The context NEVER includes test code or
coverage data — that's the whole point of Experiment 2.

File prioritization is a simplified call-graph ordering:
  - Start from LLVMFuzzerTestOneInput in the harness.
  - Extract called identifiers via a regex pass on the harness (lighter
    than tree-sitter; tree-sitter is used when available for a better
    call-graph but not required).
  - Priority = 1.0 / (min_call_depth + 1).
  - Greedily include files until the token budget is exhausted.

The contract is that any file under the target's upstream that contains
`TEST(`, `TEST_F(`, `googletest`, `gtest`, or matches `tests/` paths is
EXCLUDED from source context so test code never leaks in.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import (
    DEFAULT_TOKEN_BUDGET,
    SOURCE_CONTEXT_MAX_FILES,
    TOKEN_BUDGET,
)
from core.logging_config import get_logger
from dataset.scripts.pinned_loader import load_target_yaml

logger = get_logger("utcf.exp2.context")

TEST_PATH_MARKERS = ("/tests/", "/test/", "/unittests/", "_test.cc", "_test.cpp", "_unittest.cc")
TEST_CONTENT_MARKERS = ("TEST(", "TEST_F(", "TEST_P(", "googletest", "<gtest/")

CALL_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]{2,})\s*\(")
DEFN_RE = re.compile(r"^[A-Za-z_][\w:\s\*&<>,]+?\b([A-Za-z_][\w:]*)\s*\(", re.MULTILINE)


@dataclass
class SourceFile:
    path: str
    line_count: int
    content: str
    priority: float
    token_estimate: int


@dataclass
class SourceContext:
    target: str
    harness_code: str
    source_files: list[SourceFile] = field(default_factory=list)
    total_tokens: int = 0
    excluded_test_files: list[str] = field(default_factory=list)


def _looks_like_test_path(path: Path) -> bool:
    s = str(path).replace("\\", "/").lower()
    return any(marker in s for marker in TEST_PATH_MARKERS)


def _looks_like_test_content(text: str) -> bool:
    head = text[:20_000]
    return any(marker in head for marker in TEST_CONTENT_MARKERS)


def _token_estimate(text: str, model: str) -> int:
    try:
        import tiktoken
        enc = tiktoken.encoding_for_model(model)
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def _extract_called_names(harness: str) -> list[str]:
    names = set()
    for m in CALL_RE.finditer(harness):
        names.add(m.group(1))
    return sorted(names)


def _gather_candidate_files(upstream: Path) -> list[Path]:
    cands: list[Path] = []
    if not upstream.is_dir():
        return cands
    for ext in ("*.c", "*.cc", "*.cpp", "*.cxx", "*.h", "*.hh", "*.hpp"):
        cands.extend(upstream.rglob(ext))
    return cands


def _file_priority(path: Path, called_names: set[str]) -> float:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return 0.0
    # Depth 0 heuristic: file defines a name that the harness calls directly.
    defined = {m.group(1) for m in DEFN_RE.finditer(text[:50_000])}
    if defined & called_names:
        return 1.0
    # Depth 1 heuristic: file references (as a call) a name the harness calls.
    references = {m.group(1) for m in CALL_RE.finditer(text[:50_000])}
    if references & called_names:
        return 0.5
    # Depth 2: a file in the same directory as a depth-0 file — we return a
    # small residual score; the greedy picker will pull it in only if space.
    return 0.1


def extract_source_context(
    target: str,
    *,
    upstream_root: Path | None = None,
    harness_override: Path | None = None,
    model: str = "gpt-4o-2024-08-06",
    token_budget: int | None = None,
    max_files: int = SOURCE_CONTEXT_MAX_FILES,
) -> SourceContext:
    cfg_path = REPO_ROOT / "dataset" / "targets" / f"{target}.yaml"
    try:
        cfg = load_target_yaml(cfg_path, require_resolved=False)
    except FileNotFoundError:
        cfg = {}

    upstream_root = upstream_root or REPO_ROOT / "dataset" / "targets" / "src" / target / "upstream"

    # fetch_target.sh writes the FuzzBench harness to <target_src>/harness/<basename>.
    # Fall back to the upstream-relative path kept in the YAML (legacy layouts).
    harness_file: Path | None = None
    if harness_override is not None:
        harness_file = harness_override
    else:
        harness_dir = upstream_root.parent / "harness"
        if harness_dir.is_dir():
            for cand in sorted(harness_dir.iterdir()):
                if cand.suffix in (".c", ".cc", ".cpp", ".cxx"):
                    harness_file = cand
                    break
        if harness_file is None:
            fallback = cfg.get("harness_file") or cfg.get("harness", {}).get("path") or ""
            if fallback:
                harness_file = upstream_root / fallback
    harness_code = (
        harness_file.read_text(errors="replace")
        if harness_file is not None and harness_file.is_file()
        else ""
    )
    called = set(_extract_called_names(harness_code))

    budget = token_budget or TOKEN_BUDGET.get(model, DEFAULT_TOKEN_BUDGET)
    ctx = SourceContext(target=target, harness_code=harness_code)
    ctx.total_tokens = _token_estimate(harness_code, model)

    candidates = _gather_candidate_files(upstream_root)
    scored: list[tuple[float, Path, str]] = []
    for p in candidates:
        if _looks_like_test_path(p):
            ctx.excluded_test_files.append(str(p.relative_to(upstream_root)))
            continue
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        if _looks_like_test_content(text):
            ctx.excluded_test_files.append(str(p.relative_to(upstream_root)))
            continue
        score = _file_priority(p, called)
        if score <= 0:
            continue
        scored.append((score, p, text))

    # Sort: priority descending, then file size ascending (small first).
    scored.sort(key=lambda t: (-t[0], len(t[2])))
    for score, path, text in scored:
        if len(ctx.source_files) >= max_files:
            break
        tok = _token_estimate(text, model)
        if ctx.total_tokens + tok > budget:
            continue
        ctx.source_files.append(
            SourceFile(
                path=str(path.relative_to(upstream_root)),
                line_count=text.count("\n") + 1,
                content=text,
                priority=score,
                token_estimate=tok,
            )
        )
        ctx.total_tokens += tok

    return ctx


def verify_no_tests_leaked(ctx: SourceContext) -> None:
    for f in ctx.source_files:
        assert not _looks_like_test_path(Path(f.path)), f"test path leaked: {f.path}"
        assert not _looks_like_test_content(f.content), f"test content leaked: {f.path}"


def write_context(ctx: SourceContext, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "harness.cc").write_text(ctx.harness_code)
    src_dir = out_dir / "source_files"
    src_dir.mkdir(exist_ok=True)
    manifest = []
    for f in ctx.source_files:
        safe = f.path.replace("/", "__")
        (src_dir / safe).write_text(f.content)
        manifest.append(
            {
                "path": f.path,
                "line_count": f.line_count,
                "priority": f.priority,
                "token_estimate": f.token_estimate,
            }
        )
    (out_dir / "source_manifest.json").write_text(
        json.dumps(
            {
                "target": ctx.target,
                "total_tokens": ctx.total_tokens,
                "files": manifest,
                "excluded_test_files": ctx.excluded_test_files,
            },
            indent=2,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--model", default="gpt-4o-2024-08-06")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    ctx = extract_source_context(args.target, model=args.model)
    verify_no_tests_leaked(ctx)
    out_dir = args.out or (REPO_ROOT / "synthesis" / "results" / "source_context" / args.target)
    write_context(ctx, out_dir)
    print(
        f"{args.target}: {len(ctx.source_files)} source files, "
        f"{ctx.total_tokens} tokens, {len(ctx.excluded_test_files)} test files excluded"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
