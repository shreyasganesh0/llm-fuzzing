"""Probe which provider/model combos accept tool/function-calling kwargs.

For each model we try two dialect variants of the same trivial ``ping`` tool
and record whether the server accepted the request and whether it actually
emitted a tool call:

  1. ``anthropic_style`` — ``tools=[{"name": ..., "input_schema": ...}]``.
     Native for Claude; some LiteLLM backends accept it too.
  2. ``openai_style``    — ``tools=[{"type": "function", "function": ...}]``.
     Standard OpenAI/LiteLLM dialect.

Phase 0 of the prompt-engineering experiments uses this to decide whether
Phase 7 tool-use with a coverage oracle can run on free LiteLLM models or
has to fall back to Anthropic Haiku.

Design note: this bypasses ``core.llm_client.LLMClient`` for the same
reasons as ``probe_json_mode.py`` — ``complete()`` does not expose
``tools`` and its cache key would not hash it. See that module's header
for the full justification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.llm_client import (
    DEFAULT_SECRETS_PATH,
    _load_key,
    _try_load_key,
    detect_provider,
)

PROBE_CACHE_DIR = Path(os.environ.get("UTCF_PROBE_CACHE", ".cache/llm_probes"))

DEFAULT_MODELS = [
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
    "llama-3.1-8b-instruct",
    "llama-3.1-70b-instruct",
    "llama-3.3-70b-instruct",
    "codestral-22b",
    "nemotron-3-super-120b-a12b",
    "gpt-oss-20b",
]

DIALECTS = ("anthropic_style", "openai_style")

PROBE_PROMPT = 'Call the ping tool with msg="hello" once, then stop.'

TOOL_DEF = {
    "name": "ping",
    "description": "Returns 'pong' for any input.",
    "input_schema": {
        "type": "object",
        "properties": {"msg": {"type": "string"}},
        "required": ["msg"],
    },
}


def _is_anthropic_model(model: str) -> bool:
    return model.startswith("claude-")


def _cache_key(model: str, dialect: str) -> Path:
    payload = json.dumps(
        {"model": model, "dialect": dialect, "prompt": PROBE_PROMPT, "tool": TOOL_DEF},
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    safe_model = model.replace("/", "_")
    return PROBE_CACHE_DIR / f"tooluse_{safe_model}_{dialect}_{digest}.json"


def _load_cached(model: str, dialect: str) -> dict[str, Any] | None:
    path = _cache_key(model, dialect)
    if path.is_file():
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
    return None


def _save_cached(model: str, dialect: str, record: dict[str, Any]) -> None:
    PROBE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_key(model, dialect).write_text(json.dumps(record, ensure_ascii=False))


def _openai_tool_def() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": TOOL_DEF["name"],
            "description": TOOL_DEF["description"],
            "parameters": TOOL_DEF["input_schema"],
        },
    }


def _anthropic_tool_def() -> dict[str, Any]:
    # Exactly the ``tool_def`` spec'd in the task.
    return dict(TOOL_DEF)


def _extract_openai_tool_call(resp: Any) -> tuple[bool, str | None, dict[str, Any]]:
    try:
        choice = resp.choices[0].message
    except (AttributeError, IndexError):
        return False, None, {}
    tool_calls = getattr(choice, "tool_calls", None) or []
    if not tool_calls:
        return False, None, {}
    first = tool_calls[0]
    fn = getattr(first, "function", None)
    if fn is None:
        return False, None, {}
    name = getattr(fn, "name", None)
    raw_args = getattr(fn, "arguments", "") or ""
    try:
        args = json.loads(raw_args) if raw_args else {}
    except (json.JSONDecodeError, ValueError):
        args = {"_raw": str(raw_args)[:200]}
    return True, name, args


def _extract_anthropic_tool_call(resp: Any) -> tuple[bool, str | None, dict[str, Any]]:
    blocks = getattr(resp, "content", None) or []
    for block in blocks:
        if getattr(block, "type", "") == "tool_use":
            name = getattr(block, "name", None)
            args = getattr(block, "input", None) or {}
            if not isinstance(args, dict):
                args = {"_raw": str(args)[:200]}
            return True, name, args
    return False, None, {}


def _classify_error(exc: Exception) -> tuple[bool, str]:
    msg = f"{type(exc).__name__}: {exc}"
    text = str(exc).lower()
    status: int | None = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status is None:
        for code in (400, 401, 403, 404, 422):
            if f" {code} " in f" {text} " or f"status {code}" in text or f"http {code}" in text:
                status = code
                break
    if status is not None and 400 <= status < 500:
        return False, msg
    return False, msg


def call_anthropic(model: str, dialect: str, api_key: str) -> dict[str, Any]:
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key, max_retries=0)
    messages = [{"role": "user", "content": PROBE_PROMPT}]
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": 128,
        "temperature": 0.0,
        "messages": messages,
    }
    if dialect == "anthropic_style":
        kwargs["tools"] = [_anthropic_tool_def()]
    else:
        # Feed Anthropic the OpenAI-dialect schema via extra_body so the
        # server tells us it does not understand it — that's the signal.
        kwargs["extra_body"] = {"tools": [_openai_tool_def()]}

    try:
        resp = client.messages.create(**kwargs)
    except Exception as exc:  # noqa: BLE001 — probe captures all failures
        accepted, err = _classify_error(exc)
        return {
            "accepted_by_server": accepted,
            "tool_call_emitted": False,
            "tool_name": None,
            "tool_args": {},
            "error": err,
        }

    emitted, name, args = _extract_anthropic_tool_call(resp)
    return {
        "accepted_by_server": True,
        "tool_call_emitted": emitted,
        "tool_name": name,
        "tool_args": args,
        "error": None,
    }


def call_openai_compat(
    model: str, dialect: str, api_key: str, base_url: str | None
) -> dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key or "EMPTY", base_url=base_url) if base_url else OpenAI(
        api_key=api_key
    )
    messages = [{"role": "user", "content": PROBE_PROMPT}]
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": 128,
    }
    if dialect == "openai_style":
        kwargs["tools"] = [_openai_tool_def()]
    else:
        # LiteLLM sometimes transparently accepts Anthropic-style tools;
        # feed the anthropic-shaped object and let the server decide.
        kwargs["tools"] = [_anthropic_tool_def()]

    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as exc:  # noqa: BLE001 — probe captures all failures
        accepted, err = _classify_error(exc)
        return {
            "accepted_by_server": accepted,
            "tool_call_emitted": False,
            "tool_name": None,
            "tool_args": {},
            "error": err,
        }

    emitted, name, args = _extract_openai_tool_call(resp)
    return {
        "accepted_by_server": True,
        "tool_call_emitted": emitted,
        "tool_name": name,
        "tool_args": args,
        "error": None,
    }


def _credentials_for(model: str) -> tuple[str, str | None]:
    if _is_anthropic_model(model):
        override = os.environ.get("UTCF_ANTHROPIC_KEY_PATH")
        if override:
            key = _try_load_key(override)
            if key:
                return key, None
        for path in ("secrets/claude_key", DEFAULT_SECRETS_PATH):
            key = _try_load_key(path)
            if key and detect_provider(key) == "anthropic":
                return key, None
        raise RuntimeError("Anthropic key not found (checked secrets/claude_key + llm_key)")

    base_url = os.environ.get("UTCF_LITELLM_URL")
    if not base_url:
        raise RuntimeError(
            f"UTCF_LITELLM_URL not set — cannot reach LiteLLM model {model!r}"
        )
    key = _load_key(DEFAULT_SECRETS_PATH)
    return key, base_url


def run_one(model: str, dialect: str, *, use_cache: bool = True) -> dict[str, Any]:
    if use_cache:
        cached = _load_cached(model, dialect)
        if cached is not None:
            return cached

    api_key, base_url = _credentials_for(model)
    if _is_anthropic_model(model):
        record = call_anthropic(model, dialect, api_key)
    else:
        record = call_openai_compat(model, dialect, api_key, base_url)

    if use_cache:
        _save_cached(model, dialect, record)
    return record


def run_probe(models: list[str], *, use_cache: bool = True) -> dict[str, Any]:
    results: dict[str, dict[str, Any]] = {}
    for model in models:
        per_dialect: dict[str, Any] = {}
        for dialect in DIALECTS:
            try:
                per_dialect[dialect] = run_one(model, dialect, use_cache=use_cache)
            except Exception as exc:  # noqa: BLE001 — surface as record
                per_dialect[dialect] = {
                    "accepted_by_server": False,
                    "tool_call_emitted": False,
                    "tool_name": None,
                    "tool_args": {},
                    "error": f"{type(exc).__name__}: {exc}",
                }
        results[model] = per_dialect
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prompt": PROBE_PROMPT,
        "tool": TOOL_DEF,
        "results": results,
    }


def format_summary(payload: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for model, per_dialect in payload["results"].items():
        parts = [model]
        for dialect in DIALECTS:
            rec = per_dialect.get(dialect, {})
            if rec.get("accepted_by_server") and rec.get("tool_call_emitted"):
                tag = "ok"
            elif rec.get("accepted_by_server"):
                tag = "accepted_no_call"
            else:
                tag = "rejected"
            parts.append(f"{dialect}: {tag}")
        lines.append(" | ".join(parts))
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        help="Comma-separated list of model ids (default: Phase 0 active models).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("results/probes/probe_tool_use.json"),
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the probe cache and re-call every (model, dialect).",
    )
    args = parser.parse_args(argv)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    payload = run_probe(models, use_cache=not args.no_cache)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    for line in format_summary(payload):
        print(line)
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
