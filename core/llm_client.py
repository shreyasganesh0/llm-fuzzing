"""Unified LLM client with provider auto-detection + response caching.

Providers:
  - openai    — keys matching ^sk-(proj-)?[A-Za-z0-9]+
  - anthropic — keys matching ^sk-ant-
  - vllm      — OpenAI-compatible endpoint (local vLLM *or* a LiteLLM proxy).
                Set UTCF_VLLM_URL for local vLLM, or UTCF_LITELLM_URL for a
                LiteLLM proxy (same code path; different env var for intent).

All calls log the mandatory fields (plan §2.3). Responses are cached under
`.cache/llm/` keyed by sha256(model || messages_json || temperature || top_p).
Caching is ON by default; set `use_cache=False` to bypass.

Rate limiting: set UTCF_LLM_RPM to an integer ≥1 to throttle outgoing requests
to at most that many per minute (global across one process). Defaults to 0
(no throttle). The sanity experiments set this to 12 for the UF proxy.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.logging_config import get_logger
from core.loop_detector import is_degenerate_loop

_LOOP_CHECK_EVERY_CHARS = 2048

logger = get_logger("utcf.llm")

DEFAULT_CACHE_DIR = Path(os.environ.get("UTCF_LLM_CACHE", ".cache/llm"))

# Cost table is explicit — keep in sync with vendor pricing pages.
# Values in USD per 1M tokens.
PRICING_USD_PER_MTOK = {
    "gpt-4o-2024-08-06": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini-2024-07-18": {"input": 0.15, "output": 0.60},
    "o1-2024-12-17": {"input": 15.00, "output": 60.00},
    "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
    # LiteLLM proxy models (UF ai.it.ufl.edu). Prices per the proxy dashboard.
    "llama-3.1-8b-instruct": {"input": 0.22, "output": 0.22},
    "llama-3.1-70b-instruct": {"input": 0.40, "output": 0.40},
    "llama-3.3-70b-instruct": {"input": 0.40, "output": 0.40},
    "gpt-oss-20b": {"input": 0.03, "output": 0.07},
    "gpt-oss-120b": {"input": 0.06, "output": 0.15},
    "nemotron-3-nano-30b-a3b": {"input": 0.06, "output": 0.24},
    "nemotron-3-super-120b-a12b": {"input": 0.06, "output": 0.24},
    "mistral-small-3.1": {"input": 0.20, "output": 0.60},
    "mistral-7b-instruct": {"input": 0.15, "output": 0.20},
    "codestral-22b": {"input": 0.20, "output": 0.60},
    "gemma-3-27b-it": {"input": 0.12, "output": 0.20},
    "granite-3.3-8b-instruct": {"input": 0.22, "output": 0.22},
}


# ---- global RPM throttle ---------------------------------------------------

_rpm_lock = threading.Lock()
_last_call_monotonic: float = 0.0


def _throttle_if_needed() -> None:
    """Enforce UTCF_LLM_RPM globally across the process."""
    try:
        rpm = int(os.environ.get("UTCF_LLM_RPM", "0") or 0)
    except ValueError:
        rpm = 0
    if rpm <= 0:
        return
    min_interval = 60.0 / rpm
    global _last_call_monotonic
    with _rpm_lock:
        now = time.monotonic()
        delta = now - _last_call_monotonic
        if delta < min_interval:
            time.sleep(min_interval - delta)
        _last_call_monotonic = time.monotonic()


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rate = PRICING_USD_PER_MTOK.get(model)
    if not rate:
        return 0.0
    return (input_tokens * rate["input"] + output_tokens * rate["output"]) / 1_000_000


def detect_provider(api_key: str) -> str:
    if api_key.startswith("sk-ant-"):
        return "anthropic"
    if api_key.startswith("sk-"):
        return "openai"
    raise ValueError(f"Cannot detect provider from key prefix {api_key[:6]!r}")


def _load_key(secrets_path: str | Path = "secrets/llm_key") -> str:
    p = Path(secrets_path)
    if not p.is_file():
        raise FileNotFoundError(f"LLM key file not found: {p}")
    return p.read_text().strip()


def _try_load_key(secrets_path: str | Path) -> str | None:
    try:
        return _load_key(secrets_path)
    except FileNotFoundError:
        return None


@dataclass
class Response:
    content: str
    model: str
    temperature: float
    top_p: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float
    prompt_hash: str
    timestamp: str
    generation_wall_clock_s: float
    cached: bool = False

    def to_log_dict(self) -> dict:
        return asdict(self)


def _stream_with_loop_abort(
    client,
    model: str,
    messages: list[dict],
    temperature: float,
    top_p: float,
    max_tokens: int,
) -> tuple[str, int, int]:
    """Stream chunks and abort early if the output collapses into a loop.

    Falls back to non-streaming if the provider rejects stream_options. Returns
    (text, input_tokens, output_tokens). When `include_usage` is unsupported,
    output_tokens is estimated as len(text)//4 (GPT-style ~4 chars/token).
    """
    buf: list[str] = []
    total_chars = 0
    last_check = 0
    input_tokens = 0
    output_tokens = 0
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
    except TypeError:
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            stream=True,
        )

    aborted = False
    for chunk in stream:
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            input_tokens = getattr(usage, "prompt_tokens", input_tokens) or input_tokens
            output_tokens = getattr(usage, "completion_tokens", output_tokens) or output_tokens
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        piece = getattr(delta, "content", None)
        if not piece:
            continue
        buf.append(piece)
        total_chars += len(piece)
        if total_chars - last_check >= _LOOP_CHECK_EVERY_CHARS:
            if is_degenerate_loop("".join(buf)):
                aborted = True
                try:
                    stream.close()
                except Exception:  # noqa: BLE001 — close is best-effort
                    pass
                break
            last_check = total_chars

    text = "".join(buf)
    if aborted:
        logger.warning(
            "llm.loop_aborted",
            extra={"model": model, "accumulated_chars": total_chars},
        )
        if output_tokens == 0:
            output_tokens = max(1, total_chars // 4)
    return text, input_tokens, output_tokens


def _prompt_hash(
    model: str,
    messages: list[dict],
    temperature: float,
    top_p: float,
    max_tokens: int,
    cache_salt: str | None = None,
) -> str:
    payload = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "cache_salt": cache_salt,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LLMClient:
    """Thin wrapper over openai / anthropic / vllm clients.

    Construction:
        client = LLMClient()                      # auto-detect from secrets/llm_key
        client = LLMClient(api_key="sk-...")      # explicit
        client = LLMClient(provider="vllm", base_url="http://localhost:8000/v1")
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        provider: str | None = None,
        base_url: str | None = None,
        cache_dir: str | Path = DEFAULT_CACHE_DIR,
        secrets_path: str | Path = "secrets/llm_key",
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Explicit vllm / LiteLLM routing.
        litellm_url = os.environ.get("UTCF_LITELLM_URL")
        if provider == "vllm":
            self.provider = "vllm"
            self.api_key = api_key or _try_load_key(secrets_path) or "EMPTY"
            self.base_url = base_url or os.environ.get("UTCF_VLLM_URL", "http://localhost:8000/v1")
        elif litellm_url and provider is None:
            # LiteLLM proxies are OpenAI-compatible; route through the vllm path
            # (shared OpenAI SDK code) but authenticate with the real key.
            self.provider = "vllm"
            self.api_key = api_key or _load_key(secrets_path)
            self.base_url = base_url or litellm_url
        else:
            self.api_key = api_key or _load_key(secrets_path)
            self.provider = provider or detect_provider(self.api_key)
            self.base_url = base_url

        self._client = None  # lazy init

    # ------------------------------------------------------------------

    def _get_client(self):
        if self._client is not None:
            return self._client
        if self.provider in ("openai", "vllm"):
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url) if self.base_url else OpenAI(api_key=self.api_key)
        elif self.provider == "anthropic":
            from anthropic import Anthropic
            self._client = Anthropic(api_key=self.api_key)
        else:
            raise ValueError(f"Unknown provider: {self.provider}")
        return self._client

    # ------------------------------------------------------------------

    def complete(
        self,
        messages: list[dict],
        *,
        model: str,
        temperature: float = 0.0,
        top_p: float = 1.0,
        max_tokens: int = 2048,
        use_cache: bool = True,
        max_retries: int = 5,
        abort_on_loop: bool = True,
        cache_salt: str | None = None,
    ) -> Response:
        """Send messages to the configured provider; return a normalised Response.

        messages: a list of {role: "system"|"user"|"assistant", content: str} dicts
        (the common OpenAI shape — we adapt to Anthropic internally).

        `cache_salt`: extra value mixed into the cache key. Use this for
        multi-sample calls where messages/temperature/top_p are identical but
        each sample must produce an independent generation (e.g. pass
        `cache_salt=f"sample={k}"`). Without this, samples 1..N collide on the
        sample-0 cache entry and silently replay the same output.
        """
        h = _prompt_hash(model, messages, temperature, top_p, max_tokens, cache_salt)
        cache_path = self.cache_dir / f"{model.replace('/', '_')}_{h}.json"

        if use_cache and cache_path.is_file():
            cached = json.loads(cache_path.read_text(), strict=False)
            cached["cached"] = True
            return Response(**cached)

        wall_start = time.perf_counter()
        latency_start = time.perf_counter()
        text: str = ""
        input_tokens = 0
        output_tokens = 0

        for attempt in range(max_retries):
            try:
                _throttle_if_needed()
                if self.provider in ("openai", "vllm"):
                    client = self._get_client()
                    if abort_on_loop:
                        text, input_tokens, output_tokens = _stream_with_loop_abort(
                            client, model, messages, temperature, top_p, max_tokens
                        )
                    else:
                        resp = client.chat.completions.create(
                            model=model,
                            messages=messages,
                            temperature=temperature,
                            top_p=top_p,
                            max_tokens=max_tokens,
                        )
                        text = resp.choices[0].message.content or ""
                        usage = getattr(resp, "usage", None)
                        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
                        output_tokens = getattr(usage, "completion_tokens", 0) or 0

                elif self.provider == "anthropic":
                    client = self._get_client()
                    system_blocks = [m["content"] for m in messages if m["role"] == "system"]
                    user_blocks = [m for m in messages if m["role"] != "system"]
                    system_prompt = "\n\n".join(system_blocks) if system_blocks else None
                    resp = client.messages.create(
                        model=model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        top_p=top_p,
                        system=system_prompt,
                        messages=user_blocks,
                    )
                    text_parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
                    text = "".join(text_parts)
                    usage = getattr(resp, "usage", None)
                    input_tokens = getattr(usage, "input_tokens", 0) or 0
                    output_tokens = getattr(usage, "output_tokens", 0) or 0

                else:
                    raise ValueError(f"Unknown provider: {self.provider}")
                break

            except Exception as exc:  # noqa: BLE001 — we want to retry on any transient
                if attempt == max_retries - 1:
                    raise
                sleep = min(2**attempt, 30) + random.uniform(0, 1)
                logger.warning(
                    "LLM call failed, retrying",
                    extra={"attempt": attempt, "sleep_s": sleep, "error": str(exc)},
                )
                time.sleep(sleep)

        latency_ms = (time.perf_counter() - latency_start) * 1000
        wall_s = time.perf_counter() - wall_start
        cost = _estimate_cost(model, input_tokens, output_tokens)

        response = Response(
            content=text,
            model=model,
            temperature=temperature,
            top_p=top_p,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            prompt_hash=h,
            timestamp=datetime.now(timezone.utc).isoformat(),
            generation_wall_clock_s=wall_s,
            cached=False,
        )

        if use_cache:
            cache_path.write_text(json.dumps(response.to_log_dict(), ensure_ascii=False))

        logger.info(
            "llm.complete",
            extra={
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost,
                "latency_ms": latency_ms,
                "prompt_hash": h,
                "cached": False,
            },
        )
        return response
