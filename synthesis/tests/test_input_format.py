"""Parser + input_id stability (plan §Phase 3 test_input_format)."""
from __future__ import annotations

import base64
import json

from synthesis.scripts.parse_synthesis import parse_synthesis_response


def _wrap_inputs(items):
    return json.dumps({"inputs": items})


def test_parse_extracts_base64_inputs():
    payload = base64.b64encode(b"hello").decode()
    txt = _wrap_inputs([{"content_b64": payload, "target_gaps": ["foo.cc:42"]}])
    inputs, status = parse_synthesis_response(
        txt, target="re2", model="m", temperature=0.7, sample_index=0
    )
    assert status == "ok"
    assert len(inputs) == 1
    assert inputs[0].content_b64 == payload
    assert inputs[0].target_gaps == ["foo.cc:42"]


def test_parse_coerces_hex_and_text():
    txt = _wrap_inputs([
        {"content_b64": "48656c6c6f", "target_gaps": []},
        {"input": "free-text", "target_gaps": ["file.cc:1"]},
    ])
    inputs, status = parse_synthesis_response(
        txt, target="re2", model="m", temperature=0.7, sample_index=0
    )
    assert status == "ok"
    assert base64.b64decode(inputs[0].content_b64) == b"Hello"
    assert base64.b64decode(inputs[1].content_b64) == b"free-text"


def test_parse_handles_triple_backticks():
    payload = base64.b64encode(b"abc").decode()
    txt = f"Sure, here are inputs:\n```json\n{{\"inputs\":[{{\"content_b64\":\"{payload}\"}}]}}\n```\nDone."
    inputs, status = parse_synthesis_response(
        txt, target="re2", model="m", temperature=0.7, sample_index=0
    )
    assert status == "ok"
    assert len(inputs) == 1


def test_parse_failure_when_no_inputs():
    txt = "The cat sat on the mat. There is no JSON here."
    inputs, status = parse_synthesis_response(
        txt, target="re2", model="m", temperature=0.7, sample_index=0
    )
    assert status == "parse_failure"
    assert inputs == []


def test_parse_input_ids_are_stable():
    payload = base64.b64encode(b"xyz").decode()
    txt = _wrap_inputs([{"content_b64": payload}])
    a, _ = parse_synthesis_response(txt, target="t", model="m", temperature=0.7, sample_index=0)
    b, _ = parse_synthesis_response(txt, target="t", model="m", temperature=0.7, sample_index=0)
    assert a[0].input_id == b[0].input_id
