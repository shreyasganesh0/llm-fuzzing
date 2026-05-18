"""experiment6 — payload-token masking + mean payload entropy.

This module implements METHODS.md §3 (the LOAD-BEARING payload-vs-structural
masking rule) and §4 (the per-token top-K-head Shannon entropy formula) for
the experiment6 entropy-stratification hypothesis. It is pure offline
plumbing: it consumes "logprob sidecar" JSON files produced by another
module and emits a per-seed mean payload entropy.

Nothing here touches the network, LLVM, the LLM, or the M2 metric. The
masking rule is fixed *before* any entropy is computed (see METHODS.md §3
preamble) so this code must follow it EXACTLY — it does not improvise an
alternative entropy definition or an alternative masking rule.

Input contract — a "logprob sidecar" is a JSON dict with this schema
(another module produces it; this module only consumes it)::

    {
      "input_id": "<16 hex chars>",
      "variant": "v1_src" | "v3_all",
      "content_b64": "<the parsed seed's base64 string, verbatim>",
      "raw_response": "<the model completion text, verbatim (resp.content)>",
      "input_index_in_response": <int>,   # 0-based occurrence index
      "logprobs": {"content": [
          {"token": "<str>", "logprob": <float>,
           "top_logprobs": [{"token": "<str>", "logprob": <float>}, ...]},
          ...
      ]}
    }

Tokenizer note (STAGE0_RESULT.md + EXECUTION_LOG.md 2026-05-18): tokens are
SentencePiece-style — every space is encoded as ``▁`` (U+2581); newlines and
other non-merged bytes appear as byte-fallback ``<0xHH>`` tokens; ``</s>`` /
``<|...|>`` are zero-text control tokens. See ``detokenize`` for the exact
verified rule. The chosen token is also present as one entry inside its
own ``top_logprobs``
list (observed via the Stage 0 probe). Multiple seeds (up to 3) can share
the same ``raw_response`` / ``logprobs`` but have different ``content_b64``
and ``input_index_in_response``.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# SentencePiece "metaspace" marker: a space is encoded as this codepoint
# (U+2581 LOWER ONE EIGHTH BLOCK) wherever it appears in a token. See
# STAGE0_RESULT.md and EXECUTION_LOG.md (2026-05-18 instrument refinement).
SENTENCEPIECE_SPACE = "▁"  # ▁

# Byte-fallback tokens: the codestral/Mistral SentencePiece vocab renders a
# raw byte the BPE merge could not cover as the literal string ``<0xHH>``
# (uppercase or lowercase hex). On real harfbuzz generations this is almost
# always ``<0x0A>`` (newline) inside the pretty-printed JSON.
_BYTE_FALLBACK_RE = re.compile(r"^<0x([0-9A-Fa-f]{2})>$")

# Control / special vocab tokens that carry NO completion text (the proxy's
# ``resp.content`` excludes them, so the reconstruction must too): BOS/EOS,
# unk/pad, and chat-template sentinels like ``<|im_end|>``.
_SPECIAL_TOKEN_RE = re.compile(r"^(</?s>|<unk>|<pad>|<\|.*\|>)$")

# The four status strings mean_payload_entropy / per_seed_entropies can emit.
STATUS_OK = "ok"
STATUS_DROP_RECONSTRUCTION_MISMATCH = "drop_reconstruction_mismatch"
STATUS_DROP_B64_UNLOCATABLE = "drop_b64_unlocatable"
STATUS_DROP_NO_PAYLOAD_TOKENS = "drop_no_payload_tokens"


def detokenize(tok: str) -> str:
    """Return the literal completion text a single SentencePiece token emits.

    Refined after the real-data reconstruction failure (EXECUTION_LOG.md
    2026-05-18; verified to reproduce ``raw_response`` exactly on sampled
    sidecars). Three token classes:

    1. **Special / control tokens** (``</s>``, ``<s>``, ``<unk>``,
       ``<pad>``, ``<|...|>``) → empty string. The proxy's
       ``resp.content`` does not include them, so the reconstruction
       must not either.
    2. **Byte-fallback tokens** ``<0xHH>`` → the raw byte. ASCII bytes
       (``< 0x80``: newline ``<0x0A>``, tab, the JSON/base64 ASCII set)
       map 1:1 to a character. A ``>= 0x80`` byte is a fragment of a
       multibyte UTF-8 codepoint requiring cross-token assembly, which
       this experiment does not support; we emit U+FFFD so the
       reconstruction provably ``!= raw_response`` and the whole response
       is dropped + counted (METHODS.md §3 disclosure rule) rather than
       silently mis-aligned. On harfbuzz base64-in-JSON this branch is
       never taken (all content is ASCII).
    3. **Ordinary tokens** → every SentencePiece metaspace ``▁`` becomes
       a space (a blanket replace is safe: the base64 alphabet
       ``[A-Za-z0-9+/=]`` and JSON structure never contain U+2581).
    """
    if _SPECIAL_TOKEN_RE.match(tok):
        return ""
    m = _BYTE_FALLBACK_RE.match(tok)
    if m:
        b = int(m.group(1), 16)
        return chr(b) if b < 0x80 else "�"
    return tok.replace(SENTENCEPIECE_SPACE, " ")


def reconstruct(logprobs_content: list[dict]) -> tuple[str, list[tuple[int, int]]]:
    """Concatenate detokenised tokens and return (full_text, spans).

    ``spans[i]`` is the half-open ``[start, end)`` character range that
    token ``i`` occupies in ``full_text``. Spans are contiguous and cover
    ``full_text`` exactly (``spans[i].end == spans[i+1].start``,
    ``spans[0].start == 0``, ``spans[-1].end == len(full_text)``).

    A token that detokenises to the empty string (possible in principle)
    gets a zero-width span ``[k, k)`` at the current cursor and does not
    advance it.
    """
    pieces: list[str] = []
    spans: list[tuple[int, int]] = []
    cursor = 0
    for record in logprobs_content:
        piece = detokenize(record["token"])
        start = cursor
        end = cursor + len(piece)
        spans.append((start, end))
        pieces.append(piece)
        cursor = end
    return "".join(pieces), spans


def _nth_quoted_value_span(
    raw_response: str, content_b64: str, occurrence_index: int
) -> tuple[int, int] | None:
    """Locate the ``occurrence_index``-th verbatim ``"<content_b64>"`` value.

    The model emits JSON ``{"inputs":[{"content_b64":"<BASE64>", ...}, ...]}``.
    Per METHODS.md §3 we find the (occurrence_index)-th occurrence of the
    exact substring ``"<content_b64>"`` — the base64 value wrapped in a pair
    of double quotes — in ``raw_response``. We return the *open interval*
    between the two delimiting quotes, i.e. ``(open_quote_pos, close_quote_pos)``
    where both endpoints are the indices of the quote characters themselves
    (so the value text is ``raw_response[open_quote_pos + 1 : close_quote_pos]``).

    Returns ``None`` if there is no such occurrence at that positional index
    (METHODS.md §3 "unlocatable payload" disclosure rule — the caller drops
    the seed and counts it; it does not fall back to a fuzzy match).
    """
    needle = '"' + content_b64 + '"'
    search_from = 0
    found = 0
    while True:
        hit = raw_response.find(needle, search_from)
        if hit == -1:
            return None
        if found == occurrence_index:
            open_quote_pos = hit
            close_quote_pos = hit + len(needle) - 1
            return (open_quote_pos, close_quote_pos)
        found += 1
        # Advance past this opening quote so overlapping occurrences (rare,
        # but possible if a b64 value is a prefix of another) are still
        # enumerated positionally rather than skipped.
        search_from = hit + 1


def payload_token_indices(
    full_text: str,
    spans: list[tuple[int, int]],
    raw_response: str,
    content_b64: str,
    input_index_in_response: int,
) -> list[int] | None:
    """Return the token indices that emit this seed's base64 payload value.

    Implements METHODS.md §3 exactly:

    1. The reconstructed ``full_text`` must equal ``raw_response`` by exact
       string equality. On mismatch return ``None`` (the caller drops this
       seed and counts it — "rather than guessing an alignment").
    2. Locate the value span of ``content_b64`` as the
       ``input_index_in_response``-th verbatim quoted occurrence in
       ``raw_response``. If it is not present as a verbatim quoted substring
       at that occurrence index, return ``None`` (caller drops; counted —
       the "unlocatable payload" disclosure rule).
    3. A payload token is one whose ENTIRE ``[start, end)`` span lies
       strictly inside the open interval ``(open_quote_pos, close_quote_pos)``.
       A token straddling either delimiting quote is EXCLUDED (the §3
       boundary rule: bias toward pure base64 tokens).

    The returned list may legitimately be empty (e.g. every base64 token
    straddled the closing quote); the caller maps an empty list to
    ``drop_no_payload_tokens``.
    """
    if full_text != raw_response:
        return None

    value_span = _nth_quoted_value_span(raw_response, content_b64, input_index_in_response)
    if value_span is None:
        return None
    open_quote_pos, close_quote_pos = value_span

    # The base64 *value* text occupies the open interval strictly between the
    # two quote characters: char range (open_quote_pos, close_quote_pos).
    # A token at indices [start, end) qualifies iff the whole token sits
    # strictly inside that open interval, i.e.
    #     start  > open_quote_pos    (token starts AFTER the opening quote)
    #     end   <= close_quote_pos   (token ends AT or BEFORE the closing
    #                                 quote char — end is exclusive, so
    #                                 end == close_quote_pos means the last
    #                                 payload char is right before the quote)
    # A token whose end > close_quote_pos straddles/contains the closing
    # quote and is excluded; a token whose start <= open_quote_pos contains
    # or precedes the opening quote and is excluded. Zero-width tokens
    # (start == end) cannot lie strictly inside and are excluded.
    payload: list[int] = []
    for idx, (start, end) in enumerate(spans):
        if start == end:
            continue  # zero-width token contributes no payload characters
        if start > open_quote_pos and end <= close_quote_pos:
            payload.append(idx)
    return payload


def token_entropy_bits(
    top_logprobs: list[dict],
    observed_token: str,
    observed_logprob: float,
) -> float | None:
    """Top-K-head renormalised Shannon entropy for one token (METHODS.md §4).

    The API returns the chosen token's logprob plus the top-K alternatives'
    logprobs. We build the head from ``top_logprobs``. If the observed
    ``(token, logprob)`` is not already present in that list, prepend it
    (STAGE0_RESULT.md notes the chosen token is normally already inside its
    own ``top_logprobs``, so the prepend is a safety net for the rare case
    it is absent — it must not double-count).

    Convert each logprob ℓ to ``p = exp(ℓ)``; these do NOT sum to 1 (a
    truncated head of the full vocab). Renormalise ``q_i = p_i / Σ_j p_j``
    and return ``H = − Σ_i q_i · log2(q_i)`` in bits. ``H ∈ [0, log2(K)]``.

    Returns ``None`` if the head is empty. The "empty head" check is applied
    *after* the observed-token prepend step (METHODS §4 order), so a valid
    observed ``(token, logprob)`` with an empty ``top_logprobs`` is NOT a
    None case — it yields a 1-entry degenerate head, ``H == 0`` bits. The
    only other None path is a fully degenerate head whose probabilities all
    underflow to 0 (``Σ p == 0``): there is no information to estimate
    entropy from, so we surface None rather than fabricating ``H == 0``.
    """
    # Copy so we never mutate the caller's list; keep insertion order.
    head: list[dict] = list(top_logprobs)

    already_present = any(
        entry.get("token") == observed_token and entry.get("logprob") == observed_logprob
        for entry in head
    )
    if not already_present:
        head = [{"token": observed_token, "logprob": observed_logprob}, *head]

    if not head:
        return None

    probs = [math.exp(entry["logprob"]) for entry in head]
    total = math.fsum(probs)
    if total <= 0.0:
        # Degenerate: all logprobs were -inf. No information to estimate
        # entropy from — surface as "head empty" rather than fabricating 0.
        return None

    entropy = 0.0
    for p in probs:
        q = p / total
        if q > 0.0:
            entropy -= q * math.log2(q)
    return entropy


def mean_payload_entropy(sidecar: dict) -> tuple[float | None, str]:
    """Mean per-payload-token entropy for one seed (METHODS.md §3 + §4).

    Returns ``(value, status)`` where ``status`` is one of:

    - ``"ok"`` — value is the mean H over the seed's payload tokens.
    - ``"drop_reconstruction_mismatch"`` — reconstructed text != raw_response.
    - ``"drop_b64_unlocatable"`` — content_b64 not a verbatim quoted
      substring at the requested occurrence index.
    - ``"drop_no_payload_tokens"`` — payload-token set is empty after masking.

    On any drop ``value`` is ``None``. The per-token entropy is computed
    over that token's OWN ``top_logprobs`` (plus its observed token/logprob,
    per ``token_entropy_bits``), and the seed statistic is the arithmetic
    mean over the seed's payload tokens (METHODS.md §4 "Per-seed statistic").
    """
    logprobs_content = sidecar["logprobs"]["content"]
    raw_response = sidecar["raw_response"]
    content_b64 = sidecar["content_b64"]
    input_index_in_response = sidecar["input_index_in_response"]

    full_text, spans = reconstruct(logprobs_content)

    if full_text != raw_response:
        return None, STATUS_DROP_RECONSTRUCTION_MISMATCH

    indices = payload_token_indices(
        full_text, spans, raw_response, content_b64, input_index_in_response
    )
    if indices is None:
        # full_text == raw_response was just checked, so a None here is the
        # "unlocatable payload" case, not a reconstruction mismatch.
        return None, STATUS_DROP_B64_UNLOCATABLE
    if not indices:
        return None, STATUS_DROP_NO_PAYLOAD_TOKENS

    per_token: list[float] = []
    for idx in indices:
        record = logprobs_content[idx]
        h = token_entropy_bits(
            record.get("top_logprobs", []),
            record["token"],
            record["logprob"],
        )
        if h is None:
            # A payload token with an unusable (empty / all -inf) head. We
            # cannot compute its entropy; excluding it would silently bias
            # the per-seed mean toward the computable tokens. Per the §3/§4
            # disclosure philosophy, surface this by dropping the whole seed
            # rather than averaging over a quietly-shrunk token set.
            return None, STATUS_DROP_NO_PAYLOAD_TOKENS
        per_token.append(h)

    mean = math.fsum(per_token) / len(per_token)
    return mean, STATUS_OK


def per_seed_entropies(sidecar_dir: Path) -> dict:
    """Aggregate mean payload entropy across every ``*.json`` sidecar.

    Iterates the sidecars in sorted-by-filename order (determinism). Returns::

        {
          "entropies": {input_id: mean_entropy_float, ...},   # status == ok
          "dropped":   {input_id: status, ...},               # any drop_*
          "n_total":   <int>,   # number of sidecars processed
          "n_ok":      <int>,   # number with status == "ok"
        }

    A sidecar whose ``input_id`` collides with an earlier one is still
    recorded under its id (last write wins) but each file still counts once
    toward ``n_total`` / ``n_ok``, so totals reflect files processed.
    """
    sidecar_dir = Path(sidecar_dir)
    entropies: dict[str, float] = {}
    dropped: dict[str, str] = {}
    n_total = 0
    n_ok = 0

    for path in sorted(sidecar_dir.glob("*.json"), key=lambda p: p.name):
        with path.open("r", encoding="utf-8") as fh:
            sidecar = json.load(fh)
        input_id = sidecar["input_id"]
        value, status = mean_payload_entropy(sidecar)
        n_total += 1
        if status == STATUS_OK:
            entropies[input_id] = value
            n_ok += 1
        else:
            dropped[input_id] = status

    return {
        "entropies": entropies,
        "dropped": dropped,
        "n_total": n_total,
        "n_ok": n_ok,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI: read a sidecar directory, write the per_seed_entropies JSON.

    Runnable as ``python -m analysis.scripts.experiment6_entropy
    --sidecar-dir PATH --out PATH``.
    """
    parser = argparse.ArgumentParser(
        prog="experiment6_entropy",
        description=(
            "Compute experiment6 per-seed mean payload entropy from a "
            "directory of logprob sidecar JSON files (METHODS.md §3 + §4)."
        ),
    )
    parser.add_argument(
        "--sidecar-dir",
        type=Path,
        required=True,
        help="Directory containing *.json logprob sidecars.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Path to write the per_seed_entropies result JSON.",
    )
    args = parser.parse_args(argv)

    result = per_seed_entropies(args.sidecar_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(
        f"experiment6_entropy: n_total={result['n_total']} "
        f"n_ok={result['n_ok']} dropped={len(result['dropped'])} -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
