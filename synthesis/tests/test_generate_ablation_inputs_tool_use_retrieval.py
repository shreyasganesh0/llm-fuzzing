"""Tool-use + retrieval dispatch tests (cell E of the CoT×RAG×tools ablation).

Covers the iterative tool-use loop in
``synthesis.scripts.generate_ablation_inputs.run_ablation`` for
``strategy="tool_use_retrieval"``. Two new tools
(``list_uncovered_branches``, ``get_source``) are exposed alongside the
existing ``check_seed`` oracle, with max_tool_turns=4 (5-turn cap).

All tests stay offline: LLMClient.complete is monkeypatched; filesystem
fixtures are tmp_path directories populated inline.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from core import llm_client as lc
from synthesis.scripts import generate_ablation_inputs as gai


def _regex_payload(pattern: str = "a+") -> str:
    return json.dumps({
        "regexes": [{
            "regex": pattern,
            "target_gaps": ["re2/parse.cc:100"],
            "reasoning": "tool-use-retrieval emitted final seed",
        }]
    })


def _make_response(
    content: str, *, tool_calls: list[dict] | None = None,
) -> lc.Response:
    return lc.Response(
        content=content, model="gpt-oss-20b",
        temperature=0.7, top_p=0.95,
        input_tokens=100, output_tokens=50, cost_usd=0.0,
        latency_ms=10.0, prompt_hash="hash",
        timestamp="2026-04-23T00:00:00+00:00",
        generation_wall_clock_s=0.01, cached=False,
        tool_calls=tool_calls,
    )


def _tool_call(
    *, call_id: str, name: str, arguments: dict,
) -> dict:
    return {
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


@pytest.fixture
def prep_root(tmp_path: Path) -> Path:
    """Materialize a minimal re2 prep dataset with metadata/harness/gaps."""
    target_dir = tmp_path / "prep" / "re2"
    target_dir.mkdir(parents=True)
    (target_dir / "metadata.json").write_text(json.dumps({
        "target": "re2",
        "harness_path": "harness.cc",
        "harness_code": "int LLVMFuzzerTestOneInput(const uint8_t *d, size_t n){return 0;}",
        "source_language": "c++",
    }))
    (target_dir / "tests.json").write_text(json.dumps({"tests": []}))
    (target_dir / "coverage_gaps.json").write_text(json.dumps({
        "total_upstream_tests": 10,
        "union_coverage_pct": 80.0,
        "gap_branches": [
            {
                "file": "re2/parse.cc", "line": 42,
                "uncovered_side": "true",
                "condition_description": "parse error",
                "code_context": "if (p == end) return false;",
            },
        ],
    }))
    return tmp_path / "prep"


@pytest.fixture
def source_root(tmp_path: Path, monkeypatch) -> Path:
    """Materialize a tiny upstream source tree the retrieval tool can read."""
    src = tmp_path / "src" / "re2"
    src.mkdir(parents=True)
    (src / "parse.cc").write_text(
        "\n".join(f"line {i}" for i in range(1, 100))
    )
    monkeypatch.setenv("UTCF_SOURCE_ROOT", str(tmp_path / "src"))
    return tmp_path / "src"


@pytest.fixture
def patched_llm(monkeypatch):
    call_log: list[dict] = []
    canned: list[lc.Response] = []

    def fake_complete(self, messages, **kwargs):  # noqa: ANN001
        call_log.append({"messages": messages, "kwargs": kwargs})
        if not canned:
            raise AssertionError("no canned responses left")
        return canned.pop(0)

    monkeypatch.setattr(lc.LLMClient, "complete", fake_complete)
    monkeypatch.setattr(
        lc.LLMClient, "__init__",
        lambda self, **kwargs: setattr(self, "provider", "vllm")
        or setattr(self, "cache_dir", Path("/tmp/utcf_test_cache_ignored"))
        or setattr(self, "api_key", "fake")
        or setattr(self, "base_url", None)
        or setattr(self, "_client", None)
        or None,
    )
    return {"canned": canned, "log": call_log}


def _run(tmp_path: Path, prep_root: Path) -> list:
    return gai.run_ablation(
        target="re2", model="gpt-oss-20b", cell="v0_none",
        include_tests=False, include_gaps=False, include_source=False,
        dataset_root=prep_root,
        results_root=tmp_path / "results",
        samples=1, num_inputs=1,
        source_max_files=5, source_token_budget=None,
        max_tokens=2048, max_gaps=5,
        input_format="regex",
        run_id=42000, strategy="tool_use_retrieval",
    )


# --- tests -------------------------------------------------------------------


def test_tool_use_retrieval_exposes_three_tools(
    prep_root, source_root, patched_llm, tmp_path,
):
    """On turn 0 the client.complete call must carry all three tool schemas."""
    patched_llm["canned"].append(_make_response(_regex_payload("a+")))

    _run(tmp_path, prep_root)

    assert len(patched_llm["log"]) == 1
    tools = patched_llm["log"][0]["kwargs"]["tools"]
    names = {t["function"]["name"] for t in tools}
    assert names == {"check_seed", "list_uncovered_branches", "get_source"}


def test_tool_use_retrieval_cache_salt_has_strategy_segment(
    prep_root, source_root, patched_llm, tmp_path,
):
    patched_llm["canned"].append(_make_response(_regex_payload("b+")))
    _run(tmp_path, prep_root)
    salt = patched_llm["log"][0]["kwargs"]["cache_salt"]
    assert salt.endswith(",strategy=tool_use_retrieval,round=turn_0"), salt


def test_tool_use_retrieval_dispatches_list_uncovered_branches(
    prep_root, source_root, patched_llm, tmp_path,
):
    """Turn 0 calls list_uncovered_branches; turn 1 emits the final seed."""
    tc = _tool_call(
        call_id="tc_list_0",
        name="list_uncovered_branches",
        arguments={"k": 5},
    )
    patched_llm["canned"].append(
        _make_response("planning", tool_calls=[tc]),
    )
    patched_llm["canned"].append(_make_response(_regex_payload("c+")))

    records = _run(tmp_path, prep_root)

    assert len(patched_llm["log"]) == 2
    # Turn 1 messages include the verbatim gap list the tool returned.
    turn1_msgs = patched_llm["log"][1]["messages"]
    tool_msgs = [m for m in turn1_msgs if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    verdict = json.loads(tool_msgs[0]["content"])
    assert "gaps" in verdict
    assert verdict["gaps"][0]["file"] == "re2/parse.cc"
    assert verdict["gaps"][0]["line"] == 42
    # Seed parsed on turn 1. The regex target prepends 2 sha256-derived
    # flag bytes, so we just assert the pattern appears in the decoded blob.
    assert records[0].parse_status == "ok"
    decoded = base64.b64decode(records[0].inputs[0].content_b64)
    assert b"c+" in decoded


def test_tool_use_retrieval_dispatches_get_source(
    prep_root, source_root, patched_llm, tmp_path,
):
    """Turn 0 calls get_source; tool returns the actual source bytes."""
    tc = _tool_call(
        call_id="tc_src_0", name="get_source",
        arguments={"file": "re2/parse.cc", "line_start": 10, "line_end": 12},
    )
    patched_llm["canned"].append(
        _make_response("reading source", tool_calls=[tc]),
    )
    patched_llm["canned"].append(_make_response(_regex_payload("d+")))

    _run(tmp_path, prep_root)

    tool_msgs = [
        m for m in patched_llm["log"][1]["messages"]
        if m.get("role") == "tool"
    ]
    verdict = json.loads(tool_msgs[0]["content"])
    assert verdict["file"] == "re2/parse.cc"
    assert verdict["line_start"] == 10
    assert verdict["line_end"] == 12
    # The content is drawn verbatim from the real file on disk — the
    # anti-hallucination guarantee.
    assert verdict["content"].splitlines() == [
        "line 10", "line 11", "line 12",
    ]


def test_tool_use_retrieval_get_source_error_surfaces_as_tool_error(
    prep_root, source_root, patched_llm, tmp_path,
):
    """A bad get_source call must return ok=False, not crash the loop."""
    tc = _tool_call(
        call_id="tc_bad", name="get_source",
        arguments={"file": "no/such/file.cc", "line_start": 1, "line_end": 5},
    )
    patched_llm["canned"].append(
        _make_response("bad path", tool_calls=[tc]),
    )
    patched_llm["canned"].append(_make_response(_regex_payload("e+")))

    _run(tmp_path, prep_root)

    tool_msgs = [
        m for m in patched_llm["log"][1]["messages"]
        if m.get("role") == "tool"
    ]
    verdict = json.loads(tool_msgs[0]["content"])
    assert verdict["ok"] is False
    assert any("FileNotFoundError" in s for s in verdict["issues"])


def test_tool_use_retrieval_honors_five_turn_cap(
    prep_root, source_root, patched_llm, tmp_path,
):
    """max_tool_turns=4 means at most 5 total calls (turn_0..turn_4)."""
    tc = _tool_call(
        call_id="tc_loop", name="list_uncovered_branches",
        arguments={"k": 3},
    )
    # Feed six tool-call responses — the loop must stop at 5 regardless.
    for _ in range(6):
        patched_llm["canned"].append(
            _make_response("still calling", tool_calls=[tc]),
        )

    _run(tmp_path, prep_root)

    assert len(patched_llm["log"]) == 5, (
        f"loop exceeded 5-turn cap: {len(patched_llm['log'])} calls"
    )
    final_salt = patched_llm["log"][-1]["kwargs"]["cache_salt"]
    assert final_salt.endswith(",strategy=tool_use_retrieval,round=turn_4")
