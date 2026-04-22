"""Probe which provider/model combos accept structured-output kwargs.

For each model we try four progressively-stronger JSON-mode requests and
record (a) whether the server accepted the request (no HTTP 4xx) and
(b) whether the returned content parses as JSON:

  1. ``plain``        — no structure, baseline.
  2. ``json_object``  — ``response_format={"type": "json_object"}``.
  3. ``json_schema``  — ``response_format={"type": "json_schema", ...}``.
  4. ``guided_json``  — LiteLLM/vLLM ``extra_body={"guided_json": {...}}``.

Phase 0 of the prompt-engineering experiments uses this to decide which
LiteLLM-hosted models can support Phase 3 constrained decoding; anything
that only works on Anthropic has to be Anthropic-only.

Design note — why this bypasses ``core.llm_client.LLMClient``:
    ``complete()`` does not currently expose ``response_format`` /
    ``extra_body`` / ``tools`` kwargs, and its cache key does not hash
    them. Threading both through without colliding with existing cache
    entries is Phase 3's job. For the probe we construct the provider
    SDK clients directly and cache probe responses to a sibling dir
    ``.cache/llm_probes/`` so they never alias with synthesis traffic.
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

# Probe cache lives alongside the main LLM cache but in its own dir so probe
# responses do not collide with synthesis traffic keyed off (model, messages).
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

FLAVORS = ("plain", "json_object", "json_schema", "guided_json")

PROBE_PROMPT = (
    'Return one JSON object with key "status" set to "ok". '
    "Reply with JSON only, no prose."
)

PROBE_SCHEMA = {
    "type": "object",
    "properties": {"status": {"type": "string"}},
    "required": ["status"],
}


def _is_anthropic_model(model: str) -> bool:
    return model.startswith("claude-")


def _cache_key(model: str, flavor: str) -> Path:
    payload = json.dumps(
        {"model": model, "flavor": flavor, "prompt": PROBE_PROMPT, "schema": PROBE_SCHEMA},
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    safe_model = model.replace("/", "_")
    return PROBE_CACHE_DIR / f"{safe_model}_{flavor}_{digest}.json"


def _load_cached(model: str, flavor: str) -> dict[str, Any] | None:
    path = _cache_key(model, flavor)
    if path.is_file():
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
    return None


def _save_cached(model: str, flavor: str, record: dict[str, Any]) -> None:
    PROBE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_key(model, flavor).write_text(json.dumps(record, ensure_ascii=False))


def _parses_as_json(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    # Some models wrap JSON in ```json ... ``` fences — strip a single layer.
    if text.startswith("```"):
        stripped = text.strip("`")
        # ```json\n{...}\n```  ->  json\n{...}\n
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
        text = stripped.strip()
    try:
        json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return False
    return True


def _classify_error(exc: Exception) -> tuple[bool, str]:
    """Return (accepted_by_server, error_message).

    Accepted-by-server means no HTTP 4xx. Network/5xx errors also count as
    rejected for our purposes (the provider couldn't produce a valid answer),
    but we label them distinctly so the summary makes the reason legible.
    """
    msg = f"{type(exc).__name__}: {exc}"
    text = str(exc).lower()
    # Anthropic / OpenAI SDKs surface status codes on exception classes by name.
    status: int | None = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status is None:
        for code in (400, 401, 403, 404, 422):
            if f" {code} " in f" {text} " or f"status {code}" in text or f"http {code}" in text:
                status = code
                break
    if status is not None and 400 <= status < 500:
        return False, msg
    return False, msg


def call_anthropic(model: str, flavor: str, api_key: str) -> dict[str, Any]:
    """Run one probe call against the Anthropic API."""
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key, max_retries=0)
    messages = [{"role": "user", "content": PROBE_PROMPT}]
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": 64,
        "temperature": 0.0,
        "messages": messages,
    }
    # Anthropic does not expose response_format / guided_json. For
    # flavors other than "plain" we forward them as extra_body so the
    # server returns a 4xx we can record — that's exactly the signal
    # we want to capture ("Anthropic rejects guided_json").
    if flavor == "json_object":
        kwargs["extra_body"] = {"response_format": {"type": "json_object"}}
    elif flavor == "json_schema":
        kwargs["extra_body"] = {
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "probe", "schema": PROBE_SCHEMA},
            }
        }
    elif flavor == "guided_json":
        kwargs["extra_body"] = {"guided_json": PROBE_SCHEMA}

    try:
        resp = client.messages.create(**kwargs)
    except Exception as exc:  # noqa: BLE001 — probe intentionally records all failures
        accepted, err = _classify_error(exc)
        return {
            "accepted_by_server": accepted,
            "parsed_as_json": False,
            "content": "",
            "error": err,
        }

    parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    text = "".join(parts)
    return {
        "accepted_by_server": True,
        "parsed_as_json": _parses_as_json(text),
        "content": text[:200],
        "error": None,
    }


def call_openai_compat(
    model: str, flavor: str, api_key: str, base_url: str | None
) -> dict[str, Any]:
    """Run one probe call against an OpenAI-compatible endpoint (LiteLLM/vLLM)."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key or "EMPTY", base_url=base_url) if base_url else OpenAI(
        api_key=api_key
    )
    messages = [{"role": "user", "content": PROBE_PROMPT}]
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": 64,
    }
    if flavor == "json_object":
        kwargs["response_format"] = {"type": "json_object"}
    elif flavor == "json_schema":
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "probe", "schema": PROBE_SCHEMA},
        }
    elif flavor == "guided_json":
        kwargs["extra_body"] = {"guided_json": PROBE_SCHEMA}

    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as exc:  # noqa: BLE001 — probe intentionally records all failures
        accepted, err = _classify_error(exc)
        return {
            "accepted_by_server": accepted,
            "parsed_as_json": False,
            "content": "",
            "error": err,
        }

    text = resp.choices[0].message.content or ""
    return {
        "accepted_by_server": True,
        "parsed_as_json": _parses_as_json(text),
        "content": text[:200],
        "error": None,
    }


def _credentials_for(model: str) -> tuple[str, str | None]:
    """Return (api_key, base_url) for the given model.

    Anthropic models read ``secrets/claude_key`` (or ``$UTCF_ANTHROPIC_KEY_PATH``).
    LiteLLM models read ``secrets/llm_key`` and require ``$UTCF_LITELLM_URL``.
    """
    if _is_anthropic_model(model):
        override = os.environ.get("UTCF_ANTHROPIC_KEY_PATH")
        if override:
            key = _try_load_key(override)
            if key:
                return key, None
        # Try common locations in order.
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


def run_one(model: str, flavor: str, *, use_cache: bool = True) -> dict[str, Any]:
    """Run one (model, flavor) probe; cache result unless disabled."""
    if use_cache:
        cached = _load_cached(model, flavor)
        if cached is not None:
            return cached

    api_key, base_url = _credentials_for(model)
    if _is_anthropic_model(model):
        record = call_anthropic(model, flavor, api_key)
    else:
        record = call_openai_compat(model, flavor, api_key, base_url)

    if use_cache:
        _save_cached(model, flavor, record)
    return record


def run_probe(models: list[str], *, use_cache: bool = True) -> dict[str, Any]:
    """Run every (model, flavor) combination and aggregate results."""
    results: dict[str, dict[str, Any]] = {}
    for model in models:
        per_flavor: dict[str, Any] = {}
        for flavor in FLAVORS:
            try:
                per_flavor[flavor] = run_one(model, flavor, use_cache=use_cache)
            except Exception as exc:  # noqa: BLE001 — surface as record, not crash
                per_flavor[flavor] = {
                    "accepted_by_server": False,
                    "parsed_as_json": False,
                    "content": "",
                    "error": f"{type(exc).__name__}: {exc}",
                }
        results[model] = per_flavor
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prompt": PROBE_PROMPT,
        "schema": PROBE_SCHEMA,
        "results": results,
    }


def format_summary(payload: dict[str, Any]) -> list[str]:
    """One-line summary per model."""
    lines: list[str] = []
    for model, per_flavor in payload["results"].items():
        parts = [model]
        for flavor in FLAVORS:
            rec = per_flavor.get(flavor, {})
            if rec.get("accepted_by_server") and rec.get("parsed_as_json"):
                tag = "ok"
            elif rec.get("accepted_by_server"):
                tag = "accepted_no_json"
            else:
                tag = "rejected"
            parts.append(f"{flavor}: {tag}")
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
        default=Path("results/probes/probe_json_mode.json"),
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the probe cache and re-call every (model, flavor).",
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
