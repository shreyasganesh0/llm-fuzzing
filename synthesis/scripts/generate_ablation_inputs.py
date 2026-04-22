"""Ablation synthesis driver: run one (include_gaps, include_tests, include_source) cell.

Supports two input formats:
  regex  — RE2 target: renders ablation_synthesis_regex.j2, parses with parse_regex_response
  binary — harfbuzz and other binary targets: renders ablation_synthesis_binary.j2,
           parses with parse_synthesis_response (base64 content_b64)

Auto-detects format from target name (re2 → regex, everything else → binary).
Override with --input-format {regex,binary}.

Seeds are written to:
  <results-root>/seeds/<target>/ablation/<cell>/<model>/seed_*.bin
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import (
    DEFAULT_GAPS_PER_PROMPT,
    DEFAULT_INPUTS_PER_PROMPT,
    SOURCE_CONTEXT_MAX_FILES,
    SYNTHESIS_MAX_TOKENS,
    SYNTHESIS_SAMPLES,
    SYNTHESIS_TEMPERATURE,
    SYNTHESIS_TOP_P,
)
from core.dataset_schema import PromptLogEntry, SynthesisRecord
from core.llm_client import LLMClient
from core.logging_config import get_logger
from core.prompt_strategies import DEFAULT_STRATEGY_NAME, make_cache_salt
from synthesis.scripts.build_synthesis_prompt import (
    _load_gaps,
    _load_metadata,
    _load_tests,
    _pick_examples,
    _read_harness,
)
from synthesis.scripts.extract_source_context import extract_source_context
from synthesis.scripts.parse_synthesis import parse_regex_response, parse_synthesis_response

logger = get_logger("utcf.ablation.synthesis")

PROMPTS_DIR = REPO_ROOT / "synthesis" / "prompts"
SYSTEM_PROMPT_PATH = REPO_ROOT / "prediction" / "prompts" / "system_prompt.txt"

_JINJA_ENV = Environment(
    loader=FileSystemLoader(str(PROMPTS_DIR)),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)


def _file_dicts(ctx) -> list[dict]:
    return [
        {"path": f.path, "line_count": f.line_count, "content": f.content}
        for f in ctx.source_files
    ]


def _resolve_input_format(target: str, input_format: str | None) -> str:
    """Return 'regex' or 'binary'. Auto-detect from target name if input_format is None."""
    if input_format is not None:
        return input_format
    return "regex" if target == "re2" else "binary"


# Explicit template-suffix mapping per strategy. DefaultStrategy keeps
# the legacy filename (byte-identical output for the ~14k cache entries);
# every other known strategy maps to its own template suffix. Adding a new
# strategy requires a new entry here — unknown strategies raise so we
# never silently degrade to the default template.
_TEMPLATE_SUFFIX_BY_STRATEGY: dict[str, str] = {
    DEFAULT_STRATEGY_NAME: "",
    "cot_strict": "_cot",
    "few_shot": "_fewshot",
    # self_critique's round-1 (draft) reuses the DefaultStrategy base template
    # so the draft call is structurally identical to plain `default`. Round-2
    # (refine) uses the "_refine" suffix below, which is NOT dispatched via
    # this map — it is resolved explicitly inside the self_critique branch.
    "self_critique": "",
    # prompt_chain's round-1 (plan) uses the "_plan" template; rounds 2
    # (sketch) and 3 (finalize) use the "_sketch" / "_finalize" suffixes
    # below, which are resolved explicitly inside the prompt_chain branch.
    "prompt_chain": "_plan",
    # tool_use reuses the DefaultStrategy base template on every turn
    # (turn 0 = initial, turn i = refinement after the i-th oracle call).
    # The refinement prompts are assembled inline in run_ablation by
    # appending assistant + tool messages to the history — no new Jinja
    # template needed.
    "tool_use": "",
}
_REFINE_TEMPLATE_SUFFIX = "_refine"  # only self_critique
_CHAIN_SKETCH_SUFFIX = "_sketch"  # only prompt_chain
_CHAIN_FINALIZE_SUFFIX = "_finalize"  # only prompt_chain


def _default_template_name(fmt: str, *, strategy: str = DEFAULT_STRATEGY_NAME) -> str:
    """Resolve Jinja template filename from input format and strategy."""
    base = "ablation_synthesis_regex" if fmt == "regex" else "ablation_synthesis_binary"
    if strategy not in _TEMPLATE_SUFFIX_BY_STRATEGY:
        raise ValueError(
            f"Unknown prompt strategy: {strategy!r}. "
            f"Known: {sorted(_TEMPLATE_SUFFIX_BY_STRATEGY)}"
        )
    suffix = _TEMPLATE_SUFFIX_BY_STRATEGY[strategy]
    return f"{base}{suffix}.j2"


def _refine_template_name(fmt: str) -> str:
    """Return the Phase-5 refine template filename for the given format."""
    base = "ablation_synthesis_regex" if fmt == "regex" else "ablation_synthesis_binary"
    return f"{base}{_REFINE_TEMPLATE_SUFFIX}.j2"


def _chain_sketch_template_name(fmt: str) -> str:
    """Return the Phase-6 prompt_chain sketch template filename."""
    base = "ablation_synthesis_regex" if fmt == "regex" else "ablation_synthesis_binary"
    return f"{base}{_CHAIN_SKETCH_SUFFIX}.j2"


def _chain_finalize_template_name(fmt: str) -> str:
    """Return the Phase-6 prompt_chain finalize template filename."""
    base = "ablation_synthesis_regex" if fmt == "regex" else "ablation_synthesis_binary"
    return f"{base}{_CHAIN_FINALIZE_SUFFIX}.j2"


def build_ablation_prompt(
    target: str,
    *,
    dataset_root: Path,
    include_tests: bool,
    include_gaps: bool,
    include_source: bool,
    model: str,
    source_max_files: int,
    source_token_budget: int | None,
    num_inputs: int,
    max_gaps: int,
    input_format: str | None = None,
    template_name: str | None = None,
    few_shot_exemplars: list | None = None,
    draft_content: str | None = None,
    draft_reasoning: str | None = None,
    plan_text: str | None = None,
    plan_target_gap: str | None = None,
    sketch_content: str | None = None,
    sketch_reasoning: str | None = None,
) -> str:
    fmt = _resolve_input_format(target, input_format)
    system_prompt = SYSTEM_PROMPT_PATH.read_text()
    metadata = _load_metadata(dataset_root, target)
    harness_path = metadata.get("harness_file", "")
    upstream_root = REPO_ROOT / "dataset" / "targets" / "src" / target / "upstream"
    harness_code = _read_harness(upstream_root, harness_path) if harness_path else ""

    few_shot_examples: list = []
    if include_tests:
        tests = _load_tests(dataset_root, target)
        few_shot_examples = _pick_examples(tests, max_examples=5)

    gaps_report = None
    coverage_gaps: list = []
    total_upstream_tests = 0
    union_coverage_pct = 0.0
    if include_gaps:
        gaps_report = _load_gaps(dataset_root, target)
        if gaps_report is not None:
            coverage_gaps = gaps_report.gap_branches
            total_upstream_tests = gaps_report.total_upstream_tests
            union_coverage_pct = round(gaps_report.union_coverage_pct, 2)

    source_files: list[dict] = []
    if include_source:
        ctx = extract_source_context(
            target,
            model=model,
            max_files=source_max_files,
            token_budget=source_token_budget,
        )
        source_files = _file_dicts(ctx)
        if not harness_code:
            harness_code = ctx.harness_code

    resolved_template = template_name or _default_template_name(fmt)
    template = _JINJA_ENV.get_template(resolved_template)
    render_kwargs: dict = dict(
        system_prompt=system_prompt,
        target_name=target,
        harness_code=harness_code or "<harness unavailable>",
        source_language="cpp",
        include_tests=include_tests,
        few_shot_examples=few_shot_examples,
        include_gaps=include_gaps,
        coverage_gaps=coverage_gaps,
        total_upstream_tests=total_upstream_tests,
        union_coverage_pct=union_coverage_pct,
        max_gaps=max_gaps,
        include_source=include_source,
        source_files=source_files,
        num_inputs=num_inputs,
        few_shot_exemplars=few_shot_exemplars or [],
    )
    # The `*_refine.j2` templates (Phase 5 self_critique) require draft_*
    # fields. StrictUndefined means omitting them on the non-refine
    # templates would blow up, so only attach them when rendering a
    # refine template.
    if resolved_template.endswith(f"{_REFINE_TEMPLATE_SUFFIX}.j2"):
        render_kwargs["draft_content"] = draft_content or ""
        render_kwargs["draft_reasoning"] = draft_reasoning or ""
    # Phase-6 prompt_chain templates:
    #   _plan.j2      — no extra vars (uses only base render_kwargs).
    #   _sketch.j2    — needs plan_text + plan_target_gap.
    #   _finalize.j2  — needs plan_*, sketch_content, sketch_reasoning.
    # StrictUndefined means any leakage of these kwargs onto a template
    # that doesn't reference them would blow up, so we gate each addition
    # on the template's suffix.
    if resolved_template.endswith(f"{_CHAIN_SKETCH_SUFFIX}.j2"):
        render_kwargs["plan_text"] = plan_text or ""
        render_kwargs["plan_target_gap"] = plan_target_gap or ""
    elif resolved_template.endswith(f"{_CHAIN_FINALIZE_SUFFIX}.j2"):
        render_kwargs["plan_text"] = plan_text or ""
        render_kwargs["plan_target_gap"] = plan_target_gap or ""
        render_kwargs["sketch_content"] = sketch_content or ""
        render_kwargs["sketch_reasoning"] = sketch_reasoning or ""
    return template.render(**render_kwargs)


def _maybe_response_format(model: str) -> dict | None:
    """Return ``{"type": "json_object"}`` for models that opted in.

    Phase 5 is the first real consumer of Phase 3's constrained-output
    surface. Models whose ``ModelDefaults.supports_json_object`` is True
    get the constraint applied on both draft and refine rounds; the six
    UF LiteLLM open models qualify, and the two Anthropic models do not
    (they'd raise if we tried — see ``LLMClient.complete``).
    """
    from core.config import defaults as _model_defaults
    if _model_defaults(model).supports_json_object:
        return {"type": "json_object"}
    return None


def _parse_plan_response(text: str) -> tuple[str, str] | None:
    """Extract ``(plan, target_gap)`` from a prompt_chain plan response.

    Uses the same lenient JSON extractor as the seed parsers so markdown
    fences and surrounding prose are tolerated. Returns ``None`` if the
    JSON is absent, malformed, or missing either required field. The
    caller turns None into a ``parse_failure`` :class:`SynthesisRecord`
    and lets the orchestrator's retry loop handle it.
    """
    from synthesis.scripts.parse_synthesis import _extract_json
    data = _extract_json(text)
    if not isinstance(data, dict):
        return None
    plan = data.get("plan")
    target_gap = data.get("target_gap") or data.get("target")
    if not isinstance(plan, str) or not plan.strip():
        return None
    if not isinstance(target_gap, str) or not target_gap.strip():
        return None
    # Bound what we echo back into the next prompt; the sketch/finalize
    # templates already have tight size budgets (LiteLLM 2048-char cap).
    return plan.strip()[:1500], target_gap.strip()[:200]


def _sketch_content_for_finalize(inputs: list, fmt: str) -> str:
    """Extract the one-line sketch content to echo back into finalize.

    Mirrors :func:`_draft_content_for_refine`: the fully-packed
    ``content_b64`` is the stable reference for both regex and binary.
    The finalize template labels the field appropriately per-format.
    """
    if not inputs:
        return ""
    return inputs[0].content_b64


def _draft_content_for_refine(inputs: list, fmt: str) -> str:
    """Extract the one-line draft content to echo back into the refine prompt.

    For regex: returns the raw pattern text (if we still have it via
    reasoning) — otherwise falls back to the base64 blob prepended with
    the flag bytes. For binary: returns the base64 blob.
    """
    if not inputs:
        return ""
    first = inputs[0]
    if fmt == "regex":
        # RE2 harness seeds are flag_bytes + regex; the GeneratedInput
        # carries the fully-packed content_b64. For the critique prompt
        # the human-readable regex is more useful, but we don't keep it
        # separately — the base64 blob is fine as a stable reference.
        return first.content_b64
    return first.content_b64


def run_ablation(
    *,
    target: str,
    model: str,
    cell: str,
    include_tests: bool,
    include_gaps: bool,
    include_source: bool,
    dataset_root: Path,
    results_root: Path,
    samples: int,
    num_inputs: int,
    source_max_files: int,
    source_token_budget: int | None,
    max_tokens: int,
    max_gaps: int,
    input_format: str | None = None,
    run_id: int = 0,
    strategy: str = DEFAULT_STRATEGY_NAME,
) -> list[SynthesisRecord]:
    fmt = _resolve_input_format(target, input_format)
    # Phase 7 — tool_use requires per-model opt-in. Surface the mismatch
    # loudly so the orchestrator doesn't waste wall-clock time running a
    # strategy against a model that will just emit plain text with the
    # tools silently ignored.
    if strategy == "tool_use":
        from core.config import defaults as _model_defaults
        if not _model_defaults(model).supports_tool_use:
            raise ValueError(
                f"strategy=tool_use requires a model with "
                f"supports_tool_use=True; {model!r} does not. "
                f"See results/probes/probe_tool_use.json."
            )
    # Load frozen few-shot exemplars on demand. All other strategies get []
    # and the template skips the exemplar block cleanly.
    exemplars: list = []
    if strategy == "few_shot":
        from core.prompt_strategies import FewShotStrategy, _load_exemplars
        exemplars = _load_exemplars(target, FewShotStrategy().n_exemplars)

    def _build_prompt(*, template: str, draft_content: str | None = None,
                      draft_reasoning: str | None = None,
                      plan_text: str | None = None,
                      plan_target_gap: str | None = None,
                      sketch_content: str | None = None,
                      sketch_reasoning: str | None = None) -> str:
        return build_ablation_prompt(
            target,
            dataset_root=dataset_root,
            include_tests=include_tests,
            include_gaps=include_gaps,
            include_source=include_source,
            model=model,
            source_max_files=source_max_files,
            source_token_budget=source_token_budget,
            num_inputs=num_inputs,
            max_gaps=max_gaps,
            input_format=fmt,
            template_name=template,
            few_shot_exemplars=exemplars,
            draft_content=draft_content,
            draft_reasoning=draft_reasoning,
            plan_text=plan_text,
            plan_target_gap=plan_target_gap,
            sketch_content=sketch_content,
            sketch_reasoning=sketch_reasoning,
        )

    base_template = _default_template_name(fmt, strategy=strategy)
    rendered = _build_prompt(template=base_template)

    safe_model = model.replace("/", "_")
    # Non-default strategies insert a <strategy> segment so the legacy
    # (default) layout is preserved byte-for-byte.
    seeds_base = results_root / "seeds" / target
    synthesis_base = results_root / "synthesis" / target
    if strategy != DEFAULT_STRATEGY_NAME:
        seeds_base = seeds_base / strategy
        synthesis_base = synthesis_base / strategy
    seeds_dir = seeds_base / "ablation" / cell / safe_model
    synthesis_dir = synthesis_base / "ablation" / cell / safe_model
    seeds_dir.mkdir(parents=True, exist_ok=True)
    synthesis_dir.mkdir(parents=True, exist_ok=True)
    log_path = results_root / "log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    client = LLMClient()
    parse_fn = parse_regex_response if fmt == "regex" else parse_synthesis_response
    experiment = "exp1" if include_gaps or include_tests else "exp2"
    response_format = _maybe_response_format(model)
    records: list[SynthesisRecord] = []
    for k in range(samples):
        if strategy == "self_critique":
            # Round 1 — DRAFT. Same prompt as `default`, distinct cache
            # entry (round=draft). The draft informs the refine prompt;
            # parse failures here short-circuit (no refine call) so the
            # orchestrator's retry loop handles it without wasting a 2nd
            # API call on garbage.
            draft_resp = client.complete(
                messages=[
                    {"role": "system", "content": ""},
                    {"role": "user", "content": rendered},
                ],
                model=model,
                temperature=SYNTHESIS_TEMPERATURE,
                top_p=SYNTHESIS_TOP_P,
                max_tokens=max_tokens,
                cache_salt=make_cache_salt(
                    model=model, sample=k, cell=cell,
                    run_offset=run_id, strategy=strategy, round="draft",
                ),
                response_format=response_format,
            )
            draft_inputs, draft_status = parse_fn(
                draft_resp.content,
                target=target, model=model,
                temperature=SYNTHESIS_TEMPERATURE, sample_index=k,
                experiment=experiment,
            )
            if draft_status != "ok" or not draft_inputs:
                # Draft failed; propagate the failure upward so the
                # orchestrator's retry loop can bump `run_id` and retry.
                inputs, status, used_resp = [], "parse_failure", draft_resp
                logger.info(
                    "self_critique draft parse failure",
                    extra={
                        "cell": cell, "model": model, "sample": k,
                        "self_critique_fallback": False,
                    },
                )
            else:
                # Round 2 — REFINE. Pass the draft's bytes + reasoning
                # back to the model with the critique-task template.
                draft_content = _draft_content_for_refine(draft_inputs, fmt)
                draft_reasoning = (draft_inputs[0].reasoning or "")[:1500]
                refine_rendered = _build_prompt(
                    template=_refine_template_name(fmt),
                    draft_content=draft_content,
                    draft_reasoning=draft_reasoning,
                )
                refine_resp = client.complete(
                    messages=[
                        {"role": "system", "content": ""},
                        {"role": "user", "content": refine_rendered},
                    ],
                    model=model,
                    temperature=SYNTHESIS_TEMPERATURE,
                    top_p=SYNTHESIS_TOP_P,
                    max_tokens=max_tokens,
                    cache_salt=make_cache_salt(
                        model=model, sample=k, cell=cell,
                        run_offset=run_id, strategy=strategy, round="refine",
                    ),
                    response_format=response_format,
                )
                refine_inputs, refine_status = parse_fn(
                    refine_resp.content,
                    target=target, model=model,
                    temperature=SYNTHESIS_TEMPERATURE, sample_index=k,
                    experiment=experiment,
                )
                if refine_status == "ok" and refine_inputs:
                    inputs, status, used_resp = refine_inputs, refine_status, refine_resp
                else:
                    # Refine failed — fall back to the draft's parsed
                    # output so the orchestrator still counts a seed and
                    # we don't waste a successful draft call. Structured
                    # log field self_critique_fallback=True is the signal
                    # downstream telemetry looks for.
                    inputs, status, used_resp = draft_inputs, draft_status, draft_resp
                    logger.info(
                        "self_critique refine parse failure; using draft",
                        extra={
                            "cell": cell, "model": model, "sample": k,
                            "self_critique_fallback": True,
                        },
                    )
            resp = used_resp
        elif strategy == "prompt_chain":
            # Round 1 — PLAN. Natural-language attack plan + one gap pick.
            # The plan response is NOT a seed; it feeds the sketch prompt.
            # Parse failures short-circuit (no sketch/finalize calls).
            plan_resp = client.complete(
                messages=[
                    {"role": "system", "content": ""},
                    {"role": "user", "content": rendered},
                ],
                model=model,
                temperature=SYNTHESIS_TEMPERATURE,
                top_p=SYNTHESIS_TOP_P,
                max_tokens=max_tokens,
                cache_salt=make_cache_salt(
                    model=model, sample=k, cell=cell,
                    run_offset=run_id, strategy=strategy, round="plan",
                ),
                response_format=response_format,
            )
            parsed_plan = _parse_plan_response(plan_resp.content)
            if parsed_plan is None:
                # Plan malformed; let the retry loop handle it.
                inputs, status, used_resp = [], "parse_failure", plan_resp
                logger.info(
                    "prompt_chain plan parse failure",
                    extra={
                        "cell": cell, "model": model, "sample": k,
                        "prompt_chain_fallback": False,
                        "fallback_stage": "plan",
                    },
                )
            else:
                plan_text, plan_target_gap = parsed_plan
                # Round 2 — SKETCH. Plan echoed under COMMITTED header.
                sketch_rendered = _build_prompt(
                    template=_chain_sketch_template_name(fmt),
                    plan_text=plan_text,
                    plan_target_gap=plan_target_gap,
                )
                sketch_resp = client.complete(
                    messages=[
                        {"role": "system", "content": ""},
                        {"role": "user", "content": sketch_rendered},
                    ],
                    model=model,
                    temperature=SYNTHESIS_TEMPERATURE,
                    top_p=SYNTHESIS_TOP_P,
                    max_tokens=max_tokens,
                    cache_salt=make_cache_salt(
                        model=model, sample=k, cell=cell,
                        run_offset=run_id, strategy=strategy, round="sketch",
                    ),
                    response_format=response_format,
                )
                sketch_inputs, sketch_status = parse_fn(
                    sketch_resp.content,
                    target=target, model=model,
                    temperature=SYNTHESIS_TEMPERATURE, sample_index=k,
                    experiment=experiment,
                )
                if sketch_status != "ok" or not sketch_inputs:
                    # Sketch failed; do NOT attempt finalize. Propagate
                    # parse_failure so the orchestrator retry loop fires.
                    inputs, status, used_resp = [], "parse_failure", sketch_resp
                    logger.info(
                        "prompt_chain sketch parse failure",
                        extra={
                            "cell": cell, "model": model, "sample": k,
                            "prompt_chain_fallback": False,
                            "fallback_stage": "sketch",
                        },
                    )
                else:
                    # Round 3 — FINALIZE. Plan + sketch under review.
                    sketch_content = _sketch_content_for_finalize(sketch_inputs, fmt)
                    sketch_reasoning = (sketch_inputs[0].reasoning or "")[:1500]
                    finalize_rendered = _build_prompt(
                        template=_chain_finalize_template_name(fmt),
                        plan_text=plan_text,
                        plan_target_gap=plan_target_gap,
                        sketch_content=sketch_content,
                        sketch_reasoning=sketch_reasoning,
                    )
                    finalize_resp = client.complete(
                        messages=[
                            {"role": "system", "content": ""},
                            {"role": "user", "content": finalize_rendered},
                        ],
                        model=model,
                        temperature=SYNTHESIS_TEMPERATURE,
                        top_p=SYNTHESIS_TOP_P,
                        max_tokens=max_tokens,
                        cache_salt=make_cache_salt(
                            model=model, sample=k, cell=cell,
                            run_offset=run_id, strategy=strategy, round="finalize",
                        ),
                        response_format=response_format,
                    )
                    finalize_inputs, finalize_status = parse_fn(
                        finalize_resp.content,
                        target=target, model=model,
                        temperature=SYNTHESIS_TEMPERATURE, sample_index=k,
                        experiment=experiment,
                    )
                    if finalize_status == "ok" and finalize_inputs:
                        inputs, status, used_resp = (
                            finalize_inputs, finalize_status, finalize_resp,
                        )
                    else:
                        # Finalize failed — fall back to the sketch's
                        # parsed output so the seed still counts. Same
                        # semantics as Phase 5's draft fallback.
                        inputs, status, used_resp = (
                            sketch_inputs, sketch_status, sketch_resp,
                        )
                        logger.info(
                            "prompt_chain finalize parse failure; using sketch",
                            extra={
                                "cell": cell, "model": model, "sample": k,
                                "prompt_chain_fallback": True,
                                "fallback_stage": "finalize",
                            },
                        )
            resp = used_resp
        elif strategy == "tool_use":
            # Phase 7 — iterative tool-use loop. Model drafts a seed,
            # optionally calls ``check_seed`` for a structural verdict, and
            # either emits the final seed or retries up to max_tool_turns
            # refinement turns. Gating on supports_tool_use lives one level
            # up (see `run_ablation` prelude) so this branch never runs on
            # unsupported models.
            from core.prompt_strategies import ToolUseStrategy
            from synthesis.scripts.oracles import CHECK_SEED_TOOL_OPENAI, check_seed

            tu = ToolUseStrategy()
            conversation: list[dict] = [
                {"role": "system", "content": ""},
                {"role": "user", "content": rendered},
            ]
            turn_responses: list = []
            oracle_ok_final: bool | None = None
            inputs: list = []
            status = "parse_failure"
            used_resp = None

            for turn_i in range(tu.max_tool_turns + 1):
                turn_resp = client.complete(
                    messages=conversation,
                    model=model,
                    temperature=SYNTHESIS_TEMPERATURE,
                    top_p=SYNTHESIS_TOP_P,
                    max_tokens=max_tokens,
                    cache_salt=make_cache_salt(
                        model=model, sample=k, cell=cell,
                        run_offset=run_id, strategy=strategy,
                        round=f"turn_{turn_i}",
                    ),
                    tools=[CHECK_SEED_TOOL_OPENAI],
                    tool_choice="auto",
                )
                turn_responses.append(turn_resp)
                used_resp = turn_resp

                if turn_resp.tool_calls:
                    # Append assistant message (with tool_calls) and a tool
                    # response message per tool_call, then loop for another
                    # turn. The model should converge to a final message.
                    assistant_msg: dict = {
                        "role": "assistant",
                        "content": turn_resp.content or "",
                        "tool_calls": turn_resp.tool_calls,
                    }
                    conversation.append(assistant_msg)
                    for tc in turn_resp.tool_calls:
                        fn = tc.get("function") or {}
                        fn_name = fn.get("name")
                        fn_args_raw = fn.get("arguments") or "{}"
                        try:
                            fn_args = json.loads(fn_args_raw) if isinstance(
                                fn_args_raw, str) else (fn_args_raw or {})
                        except (TypeError, ValueError):
                            fn_args = {}
                        if fn_name == "check_seed":
                            result = check_seed(
                                target,
                                content=fn_args.get("content"),
                                content_b64=fn_args.get("content_b64"),
                            )
                            oracle_ok_final = bool(result.get("ok"))
                        else:
                            # Unknown tool — still respond with a well-formed
                            # error dict so the conversation stays valid.
                            result = {
                                "ok": False,
                                "issues": [f"unknown tool: {fn_name!r}"],
                                "details": {},
                            }
                        conversation.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id"),
                            "content": json.dumps(result),
                        })
                    continue

                # No tool call — treat as the final seed message.
                inputs, status = parse_fn(
                    turn_resp.content,
                    target=target, model=model,
                    temperature=SYNTHESIS_TEMPERATURE, sample_index=k,
                    experiment=experiment,
                )
                break
            else:
                # Exhausted the turn budget with the model still asking to
                # call tools. Attempt to parse the last response's text as
                # a best-effort fallback; may return parse_failure.
                if used_resp is not None:
                    inputs, status = parse_fn(
                        used_resp.content,
                        target=target, model=model,
                        temperature=SYNTHESIS_TEMPERATURE, sample_index=k,
                        experiment=experiment,
                    )

            tool_turns_used = max(0, len(turn_responses) - 1)
            logger.info(
                "tool_use turns",
                extra={
                    "cell": cell, "model": model, "sample": k,
                    "tool_turns_used": tool_turns_used,
                    "oracle_ok_final": oracle_ok_final,
                    "parse_status": status,
                },
            )
            resp = used_resp
        else:
            resp = client.complete(
                messages=[
                    {"role": "system", "content": ""},
                    {"role": "user", "content": rendered},
                ],
                model=model,
                temperature=SYNTHESIS_TEMPERATURE,
                top_p=SYNTHESIS_TOP_P,
                max_tokens=max_tokens,
                cache_salt=make_cache_salt(
                    model=model, sample=k, cell=cell,
                    run_offset=run_id, strategy=strategy,
                ),
            )
            inputs, status = parse_fn(
                resp.content,
                target=target, model=model,
                temperature=SYNTHESIS_TEMPERATURE, sample_index=k,
                experiment=experiment,
            )

        for inp in inputs:
            (seeds_dir / f"seed_{inp.input_id}.bin").write_bytes(base64.b64decode(inp.content_b64))

        log = PromptLogEntry(
            model=resp.model,
            temperature=resp.temperature,
            top_p=resp.top_p,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            cost_usd=resp.cost_usd,
            latency_ms=resp.latency_ms,
            prompt_hash=resp.prompt_hash,
            timestamp=resp.timestamp,
            generation_wall_clock_s=resp.generation_wall_clock_s,
            target=target,
            phase="ablation",
            experiment_tag=f"cell={cell},sample={k}",
            cached=resp.cached,
        )
        with open(log_path, "a") as fh:
            fh.write(log.model_dump_json() + "\n")

        record = SynthesisRecord(
            target=target,
            model=model,
            experiment=experiment,
            sample_index=k,
            inputs=inputs,
            parse_status=status,
            raw_response=resp.content,
            log=log,
        )
        (synthesis_dir / f"sample_{k}.json").write_text(record.model_dump_json(indent=2))
        records.append(record)

    stats = {
        "target": target,
        "model": model,
        "cell": cell,
        "include_tests": include_tests,
        "include_gaps": include_gaps,
        "include_source": include_source,
        "samples": len(records),
        "seeds_written": sum(len(r.inputs) for r in records),
        "parse_status_counts": {
            s: sum(1 for r in records if r.parse_status == s)
            for s in {r.parse_status for r in records}
        },
    }
    (seeds_dir.parent / "cell_stats.json").write_text(json.dumps(stats, indent=2))
    logger.info("ablation cell done", extra=stats)
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--cell", required=True, help="cell name, e.g. exp1_gaps_only")
    parser.add_argument("--include-tests", action="store_true")
    parser.add_argument("--include-gaps", action="store_true")
    parser.add_argument("--include-source", action="store_true")
    parser.add_argument("--dataset-root", type=Path, default=REPO_ROOT / "dataset" / "dataset")
    parser.add_argument("--results-root", type=Path, default=REPO_ROOT / "synthesis" / "results")
    parser.add_argument("--samples", type=int, default=SYNTHESIS_SAMPLES)
    parser.add_argument("--num-inputs", type=int, default=DEFAULT_INPUTS_PER_PROMPT)
    parser.add_argument("--max-gaps", type=int, default=DEFAULT_GAPS_PER_PROMPT)
    parser.add_argument("--source-max-files", type=int, default=SOURCE_CONTEXT_MAX_FILES)
    parser.add_argument("--source-token-budget", type=int, default=None)
    parser.add_argument("--max-tokens", type=int, default=SYNTHESIS_MAX_TOKENS)
    parser.add_argument(
        "--input-format", choices=("regex", "binary"), default=None,
        help="override input format (default: auto-detect from target name)",
    )
    parser.add_argument(
        "--run-id", type=int, default=0,
        help="unique run identifier added to cache_salt to prevent cache collisions across retries",
    )
    parser.add_argument(
        "--strategy", default=DEFAULT_STRATEGY_NAME,
        help=(
            "prompt strategy name (default: 'default'). Non-default "
            "strategies insert <strategy> into both the results path "
            "and the cache salt."
        ),
    )
    args = parser.parse_args()

    recs = run_ablation(
        target=args.target,
        model=args.model,
        cell=args.cell,
        include_tests=args.include_tests,
        include_gaps=args.include_gaps,
        include_source=args.include_source,
        dataset_root=args.dataset_root,
        results_root=args.results_root,
        samples=args.samples,
        num_inputs=args.num_inputs,
        source_max_files=args.source_max_files,
        source_token_budget=args.source_token_budget,
        max_tokens=args.max_tokens,
        max_gaps=args.max_gaps,
        input_format=args.input_format,
        run_id=args.run_id,
        strategy=args.strategy,
    )
    ok = sum(1 for r in recs if r.parse_status == "ok")
    total = sum(len(r.inputs) for r in recs)
    print(f"cell={args.cell} samples={len(recs)} ok={ok} total_seeds={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
