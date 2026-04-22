"""Tests for Phase 7 structural oracles.

The oracles are deterministic pure-Python functions — no LLM, no subprocess.
Every test asserts the verdict dict shape ``{ok, issues, details}`` and
that issue strings contain actionable keywords the orchestrator's logging
keys off.
"""
from __future__ import annotations

import base64

import pytest

from synthesis.scripts.oracles import CHECK_SEED_TOOL_OPENAI, check_seed

# ---- re2 oracle -------------------------------------------------------------


def test_re2_oracle_accepts_valid_regex():
    result = check_seed("re2", content="a(b|c)*d")
    assert result["ok"] is True
    assert result["issues"] == []
    assert result["details"]["length"] == len("a(b|c)*d")


def test_re2_oracle_rejects_invalid_regex():
    # Named group with empty name is a re.error across all Python versions.
    result = check_seed("re2", content="(?P<>badname)")
    assert result["ok"] is False
    assert any("re.error" in issue for issue in result["issues"])


def test_re2_oracle_rejects_empty():
    result = check_seed("re2", content="")
    assert result["ok"] is False
    assert "empty regex" in result["issues"]


def test_re2_oracle_rejects_none_content():
    result = check_seed("re2", content=None)
    assert result["ok"] is False
    assert "empty regex" in result["issues"]


# ---- harfbuzz oracle --------------------------------------------------------


def test_harfbuzz_oracle_accepts_ttf_header():
    blob = b"\x00\x01\x00\x00" + b"\x00" * 20  # 24 bytes
    payload = base64.b64encode(blob).decode()
    result = check_seed("harfbuzz", content_b64=payload)
    assert result["ok"] is True
    assert result["issues"] == []
    assert result["details"]["length"] == 24
    assert result["details"]["first4_hex"] == "00010000"


def test_harfbuzz_oracle_accepts_otto_header():
    blob = b"OTTO" + b"\x00" * 10  # 14 bytes
    payload = base64.b64encode(blob).decode()
    result = check_seed("harfbuzz", content_b64=payload)
    assert result["ok"] is True


def test_harfbuzz_oracle_rejects_bad_header():
    blob = b"XXXX" + b"\x00" * 20  # 24 bytes, wrong magic
    payload = base64.b64encode(blob).decode()
    result = check_seed("harfbuzz", content_b64=payload)
    assert result["ok"] is False
    assert any(
        "not a known sfnt version" in issue for issue in result["issues"]
    )


def test_harfbuzz_oracle_rejects_too_large():
    # 65 bytes exceeds the 64-byte harfbuzz cap.
    blob = b"\x00\x01\x00\x00" + b"A" * 61
    assert len(blob) == 65
    payload = base64.b64encode(blob).decode()
    result = check_seed("harfbuzz", content_b64=payload)
    assert result["ok"] is False
    assert any("cap" in issue for issue in result["issues"])


def test_harfbuzz_oracle_rejects_too_small():
    blob = b"\x00\x01"  # 2 bytes, not enough for a header
    payload = base64.b64encode(blob).decode()
    result = check_seed("harfbuzz", content_b64=payload)
    assert result["ok"] is False
    assert any("too small" in issue for issue in result["issues"])


def test_harfbuzz_oracle_rejects_garbage_base64():
    result = check_seed("harfbuzz", content_b64="not base64!")
    assert result["ok"] is False
    assert any(
        "base64" in issue.lower() or "decode" in issue.lower()
        for issue in result["issues"]
    )


def test_harfbuzz_oracle_rejects_empty_b64():
    result = check_seed("harfbuzz", content_b64="")
    assert result["ok"] is False
    assert result["issues"]  # non-empty


# ---- unsupported targets ---------------------------------------------------


def test_unknown_target_raises():
    with pytest.raises(NotImplementedError):
        check_seed("libxml2", content_b64="AA==")


# ---- schema export ---------------------------------------------------------


def test_openai_tool_schema_shape():
    """The exported OpenAI tool dict must be syntactically correct so the
    UF LiteLLM proxy accepts it (matches probe_tool_use.json's format)."""
    schema = CHECK_SEED_TOOL_OPENAI
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "check_seed"
    assert "description" in fn
    params = fn["parameters"]
    assert params["type"] == "object"
    assert "content" in params["properties"]
    assert "content_b64" in params["properties"]
    # Both args are optional so the model can pass whichever matches the
    # target format (content for re2, content_b64 for harfbuzz).
    assert params["required"] == []
