"""Google Test extractor.

Parses `TEST(Suite, Name)` and `TEST_F(Fixture, Name)` invocations with
tree-sitter-cpp, extracts the function body with balanced braces, and records
provenance (upstream repo, commit, file, 1-based line number of the macro).

Best-effort input harvesting (plan §1.4): for RE2 targets we extract the first
two string-literal arguments from calls to `RE2::FullMatch`, `RE2::PartialMatch`,
`RE2::FindAndConsume`, or the `RE2` constructor. These appear as
`input_data={"pattern": ..., "text": ...}`.

called_functions: all `call_expression` callee tokens inside the function body
that aren't standard-library or gtest macros.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from core.dataset_schema import Test

_TEST_FRAMEWORK_CALLS = {
    "ASSERT_TRUE", "ASSERT_FALSE", "ASSERT_EQ", "ASSERT_NE", "ASSERT_LT",
    "ASSERT_LE", "ASSERT_GT", "ASSERT_GE", "ASSERT_STREQ", "ASSERT_STRNE",
    "ASSERT_NEAR", "ASSERT_THROW", "ASSERT_NO_THROW", "ASSERT_DEATH",
    "EXPECT_TRUE", "EXPECT_FALSE", "EXPECT_EQ", "EXPECT_NE", "EXPECT_LT",
    "EXPECT_LE", "EXPECT_GT", "EXPECT_GE", "EXPECT_STREQ", "EXPECT_STRNE",
    "EXPECT_NEAR", "EXPECT_THROW", "EXPECT_NO_THROW", "EXPECT_DEATH",
    "SCOPED_TRACE", "FAIL", "SUCCEED", "GTEST_SKIP",
    "TEST", "TEST_F", "TEST_P",
}

_STDLIB_CALLS = {
    "assert", "printf", "fprintf", "sprintf", "snprintf", "puts", "fputs",
    "malloc", "calloc", "realloc", "free",
    "strlen", "strcpy", "strncpy", "strcmp", "strncmp", "strcat", "strdup",
    "memcpy", "memmove", "memset", "memcmp",
    "abort", "exit", "atoi", "atol", "atof",
    "fopen", "fclose", "fread", "fwrite",
}

_RE2_INPUT_CALLS = {
    "RE2::FullMatch", "RE2::PartialMatch", "RE2::FindAndConsume",
    "RE2::Consume", "RE2::Replace", "RE2::GlobalReplace",
    "FullMatch", "PartialMatch", "FindAndConsume",
}

# Fallback regex-based scan used if tree-sitter is unavailable at runtime.
_TEST_MACRO_RE = re.compile(
    r"""^(?P<macro>TEST|TEST_F|TEST_P)\s*\(\s*
        (?P<suite>[A-Za-z_][A-Za-z0-9_]*)\s*,\s*
        (?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\)\s*\{""",
    re.MULTILINE | re.VERBOSE,
)


def extract(target_config: dict[str, Any], repo_root: str | Path) -> list[Test]:
    repo_root = Path(repo_root)
    upstream_repo = target_config["upstream"]["repo"]
    upstream_commit = target_config["upstream"]["commit"]

    tests: list[Test] = []
    for rel in target_config["tests"]["locations"]:
        path = repo_root / rel
        if not path.is_file():
            continue
        source = path.read_text(errors="replace")

        parsed = _extract_via_tree_sitter(source) or _extract_via_regex(source)
        for suite, name, start_line, body in parsed:
            test_code = body
            input_data = _harvest_re2_input(body) if target_config.get("name") == "re2" else None
            called = _harvest_called_functions(body)
            tests.append(
                Test(
                    test_name=f"{suite}.{name}",
                    test_code=test_code,
                    test_file=rel,
                    upstream_repo=upstream_repo,
                    upstream_commit=upstream_commit,
                    upstream_file=rel,
                    upstream_line=start_line,
                    framework="googletest",
                    input_data=input_data,
                    called_functions=called,
                )
            )
    return tests


# ----------------------------------------------------------------------------
# tree-sitter path
# ----------------------------------------------------------------------------


def _extract_via_tree_sitter(source: str) -> list[tuple[str, str, int, str]] | None:
    try:
        import tree_sitter_cpp as tscpp
        from tree_sitter import Language, Parser
    except ImportError:
        return None

    try:
        language = Language(tscpp.language())
        parser = Parser(language)
    except Exception:
        return None

    src_bytes = source.encode("utf-8")
    tree = parser.parse(src_bytes)
    root = tree.root_node

    results: list[tuple[str, str, int, str]] = []
    _walk_for_tests(root, src_bytes, results)
    return results


def _walk_for_tests(node, src: bytes, out: list[tuple[str, str, int, str]]) -> None:
    # TEST(...) expands to a function definition; tree-sitter sees either
    # `expression_statement` wrapping a macro call, or a `function_definition`
    # after preprocessing. We look for call_expression whose identifier is
    # TEST / TEST_F / TEST_P and whose parent contains a compound_statement.
    if node.type in ("call_expression", "macro_invocation"):
        callee = _callee_name(node, src)
        if callee in ("TEST", "TEST_F", "TEST_P"):
            args = _string_args(node, src)
            if len(args) >= 2:
                suite, name = args[0], args[1]
                compound = _find_following_compound(node)
                if compound is not None:
                    start_line = node.start_point[0] + 1
                    end_byte = compound.end_byte
                    start_byte = node.start_byte
                    body = src[start_byte:end_byte].decode("utf-8", errors="replace")
                    out.append((suite, name, start_line, body))
    for child in node.children:
        _walk_for_tests(child, src, out)


def _callee_name(node, src: bytes) -> str:
    for child in node.children:
        if child.type in ("identifier", "scoped_identifier", "field_expression", "qualified_identifier"):
            return src[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
    return ""


def _string_args(node, src: bytes) -> list[str]:
    args: list[str] = []
    for child in node.children:
        if child.type == "argument_list":
            for arg in child.children:
                if arg.type == "identifier":
                    args.append(src[arg.start_byte:arg.end_byte].decode("utf-8", errors="replace"))
                elif arg.type == "string_literal":
                    text = src[arg.start_byte:arg.end_byte].decode("utf-8", errors="replace")
                    args.append(text.strip('"'))
    return args


def _find_following_compound(call_node):
    parent = call_node.parent
    if parent is None:
        return None
    siblings = list(parent.children)
    try:
        idx = siblings.index(call_node)
    except ValueError:
        return None
    for sib in siblings[idx + 1:]:
        if sib.type == "compound_statement":
            return sib
    # Fall back: walk up one level (TEST macros often produce function_definition-like subtrees)
    for child in call_node.children:
        if child.type == "compound_statement":
            return child
    return None


# ----------------------------------------------------------------------------
# Regex fallback (no tree-sitter)
# ----------------------------------------------------------------------------


def _extract_via_regex(source: str) -> list[tuple[str, str, int, str]]:
    results: list[tuple[str, str, int, str]] = []
    for match in _TEST_MACRO_RE.finditer(source):
        suite = match.group("suite")
        name = match.group("name")
        start_byte = match.start()
        # The matched prefix ends at the opening brace; record its absolute line.
        start_line = source.count("\n", 0, start_byte) + 1
        brace_pos = match.end() - 1
        body = _extract_balanced_braces(source, brace_pos)
        if body is None:
            continue
        # Include the macro prefix for complete test_code
        full_code = source[start_byte:brace_pos + len(body)]
        results.append((suite, name, start_line, full_code))
    return results


def _extract_balanced_braces(source: str, open_pos: int) -> str | None:
    if open_pos >= len(source) or source[open_pos] != "{":
        return None
    depth = 0
    i = open_pos
    in_str = False
    in_char = False
    escape = False
    while i < len(source):
        c = source[i]
        if escape:
            escape = False
        elif c == "\\":
            escape = True
        elif in_str:
            if c == '"':
                in_str = False
        elif in_char:
            if c == "'":
                in_char = False
        elif c == '"':
            in_str = True
        elif c == "'":
            in_char = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return source[open_pos:i + 1]
        i += 1
    return None


# ----------------------------------------------------------------------------
# Input + call harvesting
# ----------------------------------------------------------------------------


def _harvest_re2_input(body: str) -> dict[str, str] | None:
    """Best-effort: first RE2 match call's (pattern, text) string-literal args."""
    for call in _RE2_INPUT_CALLS:
        call_escaped = re.escape(call)
        m = re.search(
            rf'{call_escaped}\s*\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"',
            body,
        )
        if m:
            return {"pattern": m.group(1), "text": m.group(2)}
    return None


def _harvest_called_functions(body: str) -> list[str]:
    """Regex pass for identifier-ish call expressions (shallow, per plan §1.4)."""
    calls: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)*)\s*\(", body):
        name = m.group(1)
        if name in _TEST_FRAMEWORK_CALLS or name in _STDLIB_CALLS:
            continue
        if name.isupper() and "_" in name:
            # macros like ABSL_LOG — skip heuristically
            continue
        if name in seen:
            continue
        seen.add(name)
        calls.append(name)
    return calls


def _unique(seq: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in seq:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
