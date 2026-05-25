"""Prompt-strategy registry.

Axis orthogonal to the 5-variant context ablation (core/variants.py).
A strategy controls *how* we prompt (reasoning style, multi-call
pipelines, tool use, etc.); the variant controls *what context* goes
into the prompt. The ablation matrix becomes
(target x variant x model x strategy).

Invariants:
- DefaultStrategy MUST produce the identical cache key and on-disk
  layout as pre-registry code. Guarded by tests. Concretely:
    * make_cache_salt(..., strategy="default") returns the legacy
      f"model=...,sample=...,ablation=...,run=..." string with no
      strategy segment.
    * TargetSpec.cell_*_dir(..., strategy="default") returns the legacy
      path (no strategy segment).
- Non-default strategies append ",strategy=<name>" to the cache salt and
  insert <name> as a leading segment of results paths.
- This module does NOT itself build prompts: DefaultStrategy.build_messages
  delegates to synthesis.scripts.generate_ablation_inputs.build_ablation_prompt,
  which is the existing single source of truth. Other strategies (later
  phases) may override this.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from core.targets import TargetSpec
from core.variants import VariantSpec

DEFAULT_STRATEGY_NAME = "default"


@dataclass(frozen=True)
class CellContext:
    """Minimal context a strategy needs to build messages for one seed call.

    Kept small on purpose — anything a strategy cares about for cache
    keying or path routing lives here. `extra` is a free-form dict for
    per-strategy knobs (e.g. tool-use budgets) that should NOT affect
    the DefaultStrategy code path.
    """
    target: TargetSpec
    variant: VariantSpec
    model: str
    sample_offset: int = 0
    dataset_root: Path | None = None
    results_root: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def make_cache_salt(
    *, model: str, sample: int, cell: str, run_offset: int,
    strategy: str = DEFAULT_STRATEGY_NAME,
    round: str | None = None,
) -> str:
    """Assemble the synthesis cache salt.

    For backwards compatibility with ~14k already-cached responses, the
    DefaultStrategy salt MUST NOT include a strategy segment. Only
    non-default strategies append ",strategy=<name>".

    Phase 5 cache-salt design:
    -------------------------
    `SelfCritiqueStrategy` issues TWO LLM calls per seed (draft + refine)
    and each call needs its own cache entry. We keep ``strategy="self_critique"``
    (user-facing, one name in `STRATEGIES`) and distinguish the two
    sub-rounds via the new ``round`` kwarg:

        make_cache_salt(..., strategy="self_critique", round="draft")
            -> "...,strategy=self_critique,round=draft"
        make_cache_salt(..., strategy="self_critique", round="refine")
            -> "...,strategy=self_critique,round=refine"

    Default ``round=None`` is backwards-compatible: every existing
    caller (DefaultStrategy, CotStrictStrategy, FewShotStrategy, and
    all pre-Phase-5 cache entries) sees a byte-identical salt string.
    """
    base = f"model={model},sample={sample},ablation={cell},run={run_offset}"
    if strategy != DEFAULT_STRATEGY_NAME:
        base = f"{base},strategy={strategy}"
    if round is not None:
        base = f"{base},round={round}"
    return base


@runtime_checkable
class PromptStrategy(Protocol):
    """How to prompt for one seed cell.

    Attributes:
        name: short identifier used in paths and cache salts.
        n_calls_per_seed: number of LLM API calls this strategy issues
            per requested seed; consumed by cost estimators (Phase 8).
        supports_tool_use: later phases set True for tool-use driven
            strategies so the orchestrator can adjust provider wiring.
    """

    name: str
    n_calls_per_seed: int
    supports_tool_use: bool

    def build_messages(self, ctx: CellContext, sample_index: int) -> list[dict]:
        """Return the messages list that would be passed to LLMClient.complete."""
        ...

    def run_one_seed(self, client: Any, ctx: CellContext, sample_index: int) -> Any:
        """Execute one seed generation round.

        DefaultStrategy delegates to the existing subprocess-based driver
        (`synthesis.scripts.generate_ablation_inputs`) so behavior is
        unchanged. Later strategies may implement multi-call pipelines
        here without touching the orchestrator.
        """
        ...


@dataclass
class DefaultStrategy:
    """Legacy single-call strategy — produces byte-identical outputs.

    This is pure scaffolding: it delegates to the existing prompt
    builder and subprocess-based synthesis driver so every existing
    cache hit and on-disk artifact continues to work.
    """
    name: str = DEFAULT_STRATEGY_NAME
    n_calls_per_seed: int = 1
    supports_tool_use: bool = False
    description: str = "legacy single-call prompt (baseline)"

    def build_messages(self, ctx: CellContext, sample_index: int) -> list[dict]:
        # Import lazily so `core.prompt_strategies` stays importable from
        # lightweight tests that don't have jinja2 / dataset fixtures loaded.
        from synthesis.scripts.generate_ablation_inputs import build_ablation_prompt

        dataset_root = ctx.dataset_root or ctx.target.prep_dataset_root
        rendered = build_ablation_prompt(
            ctx.target.name,
            dataset_root=dataset_root,
            include_tests=ctx.variant.include_tests,
            include_gaps=ctx.variant.include_gaps,
            include_source=ctx.variant.include_source,
            model=ctx.model,
            source_max_files=ctx.extra.get("source_max_files", 40),
            source_token_budget=ctx.extra.get("source_token_budget"),
            num_inputs=ctx.extra.get("num_inputs", 1),
            max_gaps=ctx.extra.get("max_gaps", 30),
            input_format=ctx.extra.get("input_format"),
        )
        return [
            {"role": "system", "content": ""},
            {"role": "user", "content": rendered},
        ]

    def run_one_seed(self, client: Any, ctx: CellContext, sample_index: int) -> Any:
        """Not used by the current ablation runner.

        `AblationRunner._run_synthesis_batch` still invokes
        `generate_ablation_inputs.py` as a subprocess; for the default
        strategy we preserve that call shape exactly (see
        `scripts/_ablation_base.py`). This method exists so future
        strategies can satisfy the PromptStrategy protocol without a
        subprocess boundary.
        """
        raise NotImplementedError(
            "DefaultStrategy.run_one_seed is intentionally unused; "
            "AblationRunner dispatches via the subprocess driver. "
            "Non-default strategies should implement this."
        )


@dataclass
class CotStrictStrategy:
    """Four-step chain-of-thought constrained strategy.

    Forces the model to emit `Step 1 (Quote):`, `Step 2 (Locate):`,
    `Step 3 (Offset|Construct):`, and `Step 4 (Bytes|Regex):` labels in
    the reasoning field BEFORE producing the seed payload. Single LLM
    call (same as default) — the constraint lives in the prompt, not a
    multi-call pipeline.

    Cache behaviour: salt appends `,strategy=cot_strict`; results paths
    insert a `cot_strict/` segment (handled by `TargetSpec.cell_*_dir`
    and the synthesis driver's `seeds_base` / `synthesis_base` routing).
    """
    name: str = "cot_strict"
    n_calls_per_seed: int = 1
    supports_tool_use: bool = False
    description: str = "4-step labeled chain-of-thought in a single call"

    def build_messages(self, ctx: CellContext, sample_index: int) -> list[dict]:
        from synthesis.scripts.generate_ablation_inputs import (
            _default_template_name,
            _resolve_input_format,
            build_ablation_prompt,
        )

        dataset_root = ctx.dataset_root or ctx.target.prep_dataset_root
        fmt = _resolve_input_format(
            ctx.target.name, ctx.extra.get("input_format"),
        )
        rendered = build_ablation_prompt(
            ctx.target.name,
            dataset_root=dataset_root,
            include_tests=ctx.variant.include_tests,
            include_gaps=ctx.variant.include_gaps,
            include_source=ctx.variant.include_source,
            model=ctx.model,
            source_max_files=ctx.extra.get("source_max_files", 40),
            source_token_budget=ctx.extra.get("source_token_budget"),
            num_inputs=ctx.extra.get("num_inputs", 1),
            max_gaps=ctx.extra.get("max_gaps", 30),
            input_format=fmt,
            template_name=_default_template_name(fmt, strategy=self.name),
        )
        return [
            {"role": "system", "content": ""},
            {"role": "user", "content": rendered},
        ]

    def run_one_seed(self, client: Any, ctx: CellContext, sample_index: int) -> Any:
        raise NotImplementedError(
            "CotStrictStrategy.run_one_seed is intentionally unused; "
            "AblationRunner dispatches via the subprocess driver. "
            "The CoT constraint is enforced entirely through the prompt."
        )


@dataclass
class CotStrictNoExamplesStrategy:
    """experiment6 (Follow-up A): cot_strict with the in-template
    "Examples of the kind of patterns…" stress list REMOVED; the rigid
    4-step labels are KEPT. Isolates whether the labels collapse
    diversity without the example crutch (and onto what set).
    """
    name: str = "cot_strict_no_examples"
    n_calls_per_seed: int = 1
    supports_tool_use: bool = False
    description: str = "cot_strict minus the in-template example list (labels kept)"

    def build_messages(self, ctx: CellContext, sample_index: int) -> list[dict]:
        from synthesis.scripts.generate_ablation_inputs import (
            _default_template_name,
            _resolve_input_format,
            build_ablation_prompt,
        )
        dataset_root = ctx.dataset_root or ctx.target.prep_dataset_root
        fmt = _resolve_input_format(ctx.target.name, ctx.extra.get("input_format"))
        rendered = build_ablation_prompt(
            ctx.target.name, dataset_root=dataset_root,
            include_tests=ctx.variant.include_tests,
            include_gaps=ctx.variant.include_gaps,
            include_source=ctx.variant.include_source,
            model=ctx.model,
            source_max_files=ctx.extra.get("source_max_files", 40),
            source_token_budget=ctx.extra.get("source_token_budget"),
            num_inputs=ctx.extra.get("num_inputs", 1),
            max_gaps=ctx.extra.get("max_gaps", 30),
            input_format=fmt,
            template_name=_default_template_name(fmt, strategy=self.name),
        )
        return [{"role": "system", "content": ""},
                {"role": "user", "content": rendered}]

    def run_one_seed(self, client: Any, ctx: CellContext, sample_index: int) -> Any:
        raise NotImplementedError(
            "CotStrictNoExamplesStrategy.run_one_seed is intentionally unused; "
            "AblationRunner dispatches via the subprocess driver."
        )


@dataclass
class CotStrictRotatedExamplesStrategy:
    """experiment6 (Follow-up A): cot_strict whose example block is a
    deterministic per-attempt 3-of-8 rotation of a frozen candidate pool
    (`dataset/fixtures/cot_examples_pool.json`). Same attempt index → same
    3 examples (reproducible); successive attempts rotate. Isolates
    whether VARYING the anchor (not removing it) restores diversity.
    """
    name: str = "cot_strict_rotated_examples"
    n_calls_per_seed: int = 1
    supports_tool_use: bool = False
    description: str = "cot_strict with a per-attempt rotated 3-of-8 example subset"

    def build_messages(self, ctx: CellContext, sample_index: int) -> list[dict]:
        from synthesis.scripts.generate_ablation_inputs import (
            _default_template_name,
            _resolve_input_format,
            build_ablation_prompt,
        )
        dataset_root = ctx.dataset_root or ctx.target.prep_dataset_root
        fmt = _resolve_input_format(ctx.target.name, ctx.extra.get("input_format"))
        rendered = build_ablation_prompt(
            ctx.target.name, dataset_root=dataset_root,
            include_tests=ctx.variant.include_tests,
            include_gaps=ctx.variant.include_gaps,
            include_source=ctx.variant.include_source,
            model=ctx.model,
            source_max_files=ctx.extra.get("source_max_files", 40),
            source_token_budget=ctx.extra.get("source_token_budget"),
            num_inputs=ctx.extra.get("num_inputs", 1),
            max_gaps=ctx.extra.get("max_gaps", 30),
            input_format=fmt,
            template_name=_default_template_name(fmt, strategy=self.name),
        )
        return [{"role": "system", "content": ""},
                {"role": "user", "content": rendered}]

    def run_one_seed(self, client: Any, ctx: CellContext, sample_index: int) -> Any:
        raise NotImplementedError(
            "CotStrictRotatedExamplesStrategy.run_one_seed is intentionally "
            "unused; the subprocess driver computes the per-attempt rotation."
        )


@dataclass
class CotStrictNoLabelsStrategy:
    """experiment6 (Follow-up A): cot_strict with the rigid 4 step
    labels (`Step 1 (Quote): … Step 4 (Regex):`) replaced by a single
    free-form "explain briefly, then emit the regex". Example block KEPT.
    The midpoint of a rigidity gradient: none(default) → free-form(this)
    → rigid-4-step(cot_strict).
    """
    name: str = "cot_strict_no_labels"
    n_calls_per_seed: int = 1
    supports_tool_use: bool = False
    description: str = "cot_strict with free-form reasoning instead of the 4 rigid labels"

    def build_messages(self, ctx: CellContext, sample_index: int) -> list[dict]:
        from synthesis.scripts.generate_ablation_inputs import (
            _default_template_name,
            _resolve_input_format,
            build_ablation_prompt,
        )
        dataset_root = ctx.dataset_root or ctx.target.prep_dataset_root
        fmt = _resolve_input_format(ctx.target.name, ctx.extra.get("input_format"))
        rendered = build_ablation_prompt(
            ctx.target.name, dataset_root=dataset_root,
            include_tests=ctx.variant.include_tests,
            include_gaps=ctx.variant.include_gaps,
            include_source=ctx.variant.include_source,
            model=ctx.model,
            source_max_files=ctx.extra.get("source_max_files", 40),
            source_token_budget=ctx.extra.get("source_token_budget"),
            num_inputs=ctx.extra.get("num_inputs", 1),
            max_gaps=ctx.extra.get("max_gaps", 30),
            input_format=fmt,
            template_name=_default_template_name(fmt, strategy=self.name),
        )
        return [{"role": "system", "content": ""},
                {"role": "user", "content": rendered}]

    def run_one_seed(self, client: Any, ctx: CellContext, sample_index: int) -> Any:
        raise NotImplementedError(
            "CotStrictNoLabelsStrategy.run_one_seed is intentionally unused; "
            "AblationRunner dispatches via the subprocess driver."
        )


EXEMPLARS_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "dataset" / "fixtures" / "exemplars"


def _load_exemplars(target: str, n: int) -> list[dict]:
    """Load up to `n` frozen exemplars for `target`.

    Returns [] if the fixtures file is missing or malformed — the
    template degrades gracefully (no "WORKED EXAMPLES" block emitted).
    """
    path = EXEMPLARS_FIXTURES_DIR / f"{target}.json"
    if not path.is_file():
        return []
    try:
        import json as _json
        doc = _json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    exemplars = doc.get("exemplars", [])
    if not isinstance(exemplars, list):
        return []
    return exemplars[:n]


@dataclass
class FewShotStrategy:
    """In-context exemplar injection (Phase 4).

    Prepends 2–3 previously-successful seeds (with their reasoning
    strings) to the prompt, before the "=== YOUR TASK ===" block.
    Hypothesis: grounding the model in concrete prior successes raises
    hard-branch hit-rate beyond plain `default` and `cot_strict`.

    The exemplars are frozen at `dataset/fixtures/exemplars/<target>.json`
    by `analysis/scripts/harvest_exemplars.py`. Every exemplar carries
    origin metadata (variant, model, sample_index, seed_path) — no
    fabrication. If the fixtures file is missing the strategy silently
    degrades to the same output as the base template (no exemplar block).

    Cache behaviour: salt appends `,strategy=few_shot`; results paths
    insert a `few_shot/` segment via `TargetSpec.cell_*_dir`.
    """
    name: str = "few_shot"
    n_calls_per_seed: int = 1
    supports_tool_use: bool = False
    description: str = "prepend frozen exemplars before the task block"
    n_exemplars: int = 3

    def build_messages(self, ctx: CellContext, sample_index: int) -> list[dict]:
        from synthesis.scripts.generate_ablation_inputs import (
            _default_template_name,
            _resolve_input_format,
            build_ablation_prompt,
        )

        dataset_root = ctx.dataset_root or ctx.target.prep_dataset_root
        fmt = _resolve_input_format(
            ctx.target.name, ctx.extra.get("input_format"),
        )
        exemplars = _load_exemplars(ctx.target.name, self.n_exemplars)
        rendered = build_ablation_prompt(
            ctx.target.name,
            dataset_root=dataset_root,
            include_tests=ctx.variant.include_tests,
            include_gaps=ctx.variant.include_gaps,
            include_source=ctx.variant.include_source,
            model=ctx.model,
            source_max_files=ctx.extra.get("source_max_files", 40),
            source_token_budget=ctx.extra.get("source_token_budget"),
            num_inputs=ctx.extra.get("num_inputs", 1),
            max_gaps=ctx.extra.get("max_gaps", 30),
            input_format=fmt,
            template_name=_default_template_name(fmt, strategy=self.name),
            few_shot_exemplars=exemplars,
        )
        return [
            {"role": "system", "content": ""},
            {"role": "user", "content": rendered},
        ]

    def run_one_seed(self, client: Any, ctx: CellContext, sample_index: int) -> Any:
        raise NotImplementedError(
            "FewShotStrategy.run_one_seed is intentionally unused; "
            "AblationRunner dispatches via the subprocess driver. "
            "Exemplars are injected entirely through the prompt."
        )


@dataclass
class SelfCritiqueStrategy:
    """Two-call self-critique strategy (Phase 5).

    Round 1 (draft): identical prompt to DefaultStrategy — emit ONE
    candidate seed for the current cell.

    Round 2 (refine): feed the draft back to the model with a dedicated
    `*_refine.j2` template that shows the same gap/source/test context
    PLUS a "DRAFT UNDER REVIEW" block and asks for a single revised seed
    that fixes one concrete weakness relative to the uncovered branches.

    Hypothesis: a single round of self-critique outperforms single-shot
    default because the model gets a second look at its own output against
    the gap list.

    Cache behaviour: salt is ``,strategy=self_critique,round=<draft|refine>``.
    The two sub-rounds get distinct cache entries (see
    :func:`make_cache_salt`). Only ``"self_critique"`` is user-facing;
    ``draft`` and ``refine`` are sub-round identifiers, not registered
    strategies.

    Orchestration: this strategy makes 2 API calls per seed. The runner
    (`AblationRunner`) stays unaware — the subprocess driver
    (`synthesis/scripts/generate_ablation_inputs.py`) dispatches both
    calls when ``strategy == "self_critique"``.
    """
    name: str = "self_critique"
    n_calls_per_seed: int = 2
    supports_tool_use: bool = False
    description: str = "draft + refine two-call self-critique"

    def build_messages(self, ctx: CellContext, sample_index: int) -> list[dict]:
        """Return the DRAFT messages list (round 1 only).

        The refine round's messages are built inline by the subprocess
        driver because they depend on the draft response, which this
        method cannot see.
        """
        from synthesis.scripts.generate_ablation_inputs import (
            _default_template_name,
            _resolve_input_format,
            build_ablation_prompt,
        )

        dataset_root = ctx.dataset_root or ctx.target.prep_dataset_root
        fmt = _resolve_input_format(
            ctx.target.name, ctx.extra.get("input_format"),
        )
        rendered = build_ablation_prompt(
            ctx.target.name,
            dataset_root=dataset_root,
            include_tests=ctx.variant.include_tests,
            include_gaps=ctx.variant.include_gaps,
            include_source=ctx.variant.include_source,
            model=ctx.model,
            source_max_files=ctx.extra.get("source_max_files", 40),
            source_token_budget=ctx.extra.get("source_token_budget"),
            num_inputs=ctx.extra.get("num_inputs", 1),
            max_gaps=ctx.extra.get("max_gaps", 30),
            input_format=fmt,
            template_name=_default_template_name(fmt, strategy=self.name),
        )
        return [
            {"role": "system", "content": ""},
            {"role": "user", "content": rendered},
        ]

    def run_one_seed(self, client: Any, ctx: CellContext, sample_index: int) -> Any:
        raise NotImplementedError(
            "SelfCritiqueStrategy.run_one_seed is intentionally unused; "
            "the subprocess driver orchestrates the draft + refine calls."
        )


@dataclass
class PromptChainStrategy:
    """Three-call plan→sketch→finalize pipeline (Phase 6).

    Round 1 (plan): the model picks ONE uncovered branch and writes a
    2–3 sentence natural-language attack plan. No bytes yet. Output
    schema is ``{"plan": "...", "target_gap": "file:line"}``.

    Round 2 (sketch): the plan is echoed back under
    ``=== ATTACK PLAN (COMMITTED) ===`` and the model produces a
    concrete candidate seed (same ``inputs[0]`` schema as default).

    Round 3 (finalize): the plan + sketch are shown under
    ``=== SKETCH UNDER REVIEW ===`` and the model emits the FINAL seed,
    either unchanged (if the sketch is sound) or corrected.

    Hypothesis: forcing an explicit planning stage before drafting, then
    a single refinement pass, beats both ``default`` (one-shot) and
    ``self_critique`` (two-shot without an explicit plan).

    Cache behaviour: salt is
    ``,strategy=prompt_chain,round=<plan|sketch|finalize>``. Each round
    gets a distinct cache entry via the ``round`` kwarg on
    :func:`make_cache_salt`. Only ``"prompt_chain"`` is user-facing; the
    round names are sub-round identifiers, not registered strategies.

    Orchestration: 3 API calls per seed. The runner stays unaware — the
    subprocess driver (``synthesis/scripts/generate_ablation_inputs.py``)
    dispatches all three calls when ``strategy == "prompt_chain"``.
    """
    name: str = "prompt_chain"
    n_calls_per_seed: int = 3
    supports_tool_use: bool = False
    description: str = "plan -> sketch -> finalize three-call pipeline"

    def build_messages(self, ctx: CellContext, sample_index: int) -> list[dict]:
        """Return the PLAN messages list (round 1 only).

        Sketch and finalize rounds are built inline by the subprocess
        driver because they depend on prior-round responses, which this
        method cannot see.
        """
        from synthesis.scripts.generate_ablation_inputs import (
            _default_template_name,
            _resolve_input_format,
            build_ablation_prompt,
        )

        dataset_root = ctx.dataset_root or ctx.target.prep_dataset_root
        fmt = _resolve_input_format(
            ctx.target.name, ctx.extra.get("input_format"),
        )
        rendered = build_ablation_prompt(
            ctx.target.name,
            dataset_root=dataset_root,
            include_tests=ctx.variant.include_tests,
            include_gaps=ctx.variant.include_gaps,
            include_source=ctx.variant.include_source,
            model=ctx.model,
            source_max_files=ctx.extra.get("source_max_files", 40),
            source_token_budget=ctx.extra.get("source_token_budget"),
            num_inputs=ctx.extra.get("num_inputs", 1),
            max_gaps=ctx.extra.get("max_gaps", 30),
            input_format=fmt,
            template_name=_default_template_name(fmt, strategy=self.name),
        )
        return [
            {"role": "system", "content": ""},
            {"role": "user", "content": rendered},
        ]

    def run_one_seed(self, client: Any, ctx: CellContext, sample_index: int) -> Any:
        raise NotImplementedError(
            "PromptChainStrategy.run_one_seed is intentionally unused; "
            "the subprocess driver orchestrates plan+sketch+finalize."
        )


@dataclass
class ToolUseStrategy:
    """Iterative tool-use strategy (Phase 7).

    The model drafts a seed, optionally calls the ``check_seed`` oracle
    (``synthesis.scripts.oracles.check_seed``) for a structural verdict,
    and either emits the final seed or retries up to ``max_tool_turns``
    refinement turns. The oracle is a lightweight *structural* check —
    not coverage — so every turn is a pure-Python function call inside
    the driver process.

    n_calls_per_seed: upper bound (1 initial + ``max_tool_turns``
    refinement turns). Actual turns consumed may be lower if the model
    emits a final seed on turn 0.

    Supported models: only those whose
    ``ModelDefaults.supports_tool_use`` is True. At Phase 7 that's
    ``gpt-oss-20b`` and ``nemotron-3-super-120b-a12b`` on the UF LiteLLM
    proxy. All other models (Anthropic, llama, codestral) raise when
    this strategy is requested — see the guard in
    ``generate_ablation_inputs.run_ablation``.

    Cache behaviour: salt is
    ``,strategy=tool_use,round=turn_<i>`` where ``i`` is the zero-indexed
    turn (``turn_0`` = initial call, ``turn_1`` = first refinement, ...).
    Each turn gets a distinct cache entry so cached partial
    conversations replay correctly.
    """
    name: str = "tool_use"
    n_calls_per_seed: int = 4  # 1 initial + up to 3 refinement turns
    supports_tool_use: bool = True
    description: str = "iterative oracle-backed tool use (model-gated)"
    max_tool_turns: int = 3

    def build_messages(self, ctx: CellContext, sample_index: int) -> list[dict]:
        """Not used — the subprocess driver orchestrates the tool loop.

        Raising here (same pattern as the other multi-call strategies)
        guarantees no accidental single-call dispatch bypasses the
        oracle loop. The driver calls ``build_ablation_prompt`` directly
        using the base template for turn 0.
        """
        raise NotImplementedError(
            "ToolUseStrategy.build_messages is intentionally unused; "
            "the subprocess driver orchestrates the oracle-backed tool loop."
        )

    def run_one_seed(self, client: Any, ctx: CellContext, sample_index: int) -> Any:
        raise NotImplementedError(
            "ToolUseStrategy.run_one_seed is intentionally unused; "
            "the subprocess driver orchestrates the oracle-backed tool loop."
        )


@dataclass
class ToolUseRetrievalStrategy:
    """Tool-driven retrieval strategy (cell E of the CoT×RAG×tools ablation).

    Same iterative-loop shape as ``ToolUseStrategy`` but exposes THREE tools
    instead of one:
      * ``check_seed`` — structural validator (unchanged).
      * ``list_uncovered_branches`` — returns top-K gaps from the pinned
        ``coverage_gaps.json``.
      * ``get_source`` — returns a bounded slice of upstream source.

    The model starts from a lean prompt (``v0_none`` or ``v1_src``) and
    retrieves the context it needs before emitting a seed. The loop caps
    at 5 total calls (1 initial + 4 refinement turns) per the
    experiment-design 5-turn budget.

    Cache salt follows the same ``round=turn_<i>`` convention as
    ``ToolUseStrategy`` — the only differentiator is the strategy name
    segment, so cached ``tool_use`` entries remain byte-identical.
    """
    name: str = "tool_use_retrieval"
    n_calls_per_seed: int = 5  # 1 initial + up to 4 refinement turns
    supports_tool_use: bool = True
    description: str = "oracle + retrieval tool loop (5-turn cap)"
    max_tool_turns: int = 4

    def build_messages(self, ctx: CellContext, sample_index: int) -> list[dict]:
        raise NotImplementedError(
            "ToolUseRetrievalStrategy.build_messages is intentionally unused; "
            "the subprocess driver orchestrates the retrieval-augmented loop."
        )

    def run_one_seed(self, client: Any, ctx: CellContext, sample_index: int) -> Any:
        raise NotImplementedError(
            "ToolUseRetrievalStrategy.run_one_seed is intentionally unused; "
            "the subprocess driver orchestrates the retrieval-augmented loop."
        )


@dataclass
class SelfCritiqueStrictGapStrategy:
    """Variant of self_critique whose refine prompt explicitly enumerates
    the draft's claimed gaps and forbids reusing them.

    Round 1 (draft): identical to DefaultStrategy / SelfCritiqueStrategy
    round 1 (no rigid scaffolding — same prompt the cache already covers
    for ``default``, but distinct cache salt because the strategy name
    appends ``,strategy=self_critique_strict_gap``).
    Round 2 (refine): uses ``ablation_synthesis_regex_scgap.j2`` —
    receives the draft's ``target_gaps`` self-report AND a sample of
    unclaimed gaps from the cell's gap list. The model is instructed to
    pivot to one of those unclaimed gaps.

    Hypothesis (experiment5/FOLLOWUP, post-experiment4 mechanism
    revision): the original ``self_critique`` underperforms because the
    refine round re-reads the same static context as the draft and gets
    no real feedback signal. Showing the draft's self-claimed gaps and
    forcing a pivot is the cheapest possible "feedback that depends on
    the draft" without an in-loop coverage replay.

    Cache behaviour: salt is
    ``,strategy=self_critique_strict_gap,round=<draft|refine>``.
    Orchestration: 2 API calls per seed; dispatched by an explicit branch
    in ``generate_ablation_inputs.run_ablation`` (NOT the
    ``self_critique`` branch — kept distinct so the legacy strategy's
    cache is untouched).
    """
    name: str = "self_critique_strict_gap"
    n_calls_per_seed: int = 2
    supports_tool_use: bool = False
    description: str = "self_critique whose refine pivots to a gap unclaimed by the draft"

    def build_messages(self, ctx: CellContext, sample_index: int) -> list[dict]:
        from synthesis.scripts.generate_ablation_inputs import build_ablation_prompt

        dataset_root = ctx.dataset_root or ctx.target.prep_dataset_root
        rendered = build_ablation_prompt(
            ctx.target.name,
            dataset_root=dataset_root,
            include_tests=ctx.variant.include_tests,
            include_gaps=ctx.variant.include_gaps,
            include_source=ctx.variant.include_source,
            model=ctx.model,
            source_max_files=ctx.extra.get("source_max_files", 40),
            source_token_budget=ctx.extra.get("source_token_budget"),
            num_inputs=ctx.extra.get("num_inputs", 1),
            max_gaps=ctx.extra.get("max_gaps", 30),
            input_format=ctx.extra.get("input_format"),
        )
        return [
            {"role": "system", "content": ""},
            {"role": "user", "content": rendered},
        ]

    def run_one_seed(self, client: Any, ctx: CellContext, sample_index: int) -> Any:
        raise NotImplementedError(
            "SelfCritiqueStrictGapStrategy.run_one_seed is intentionally unused; "
            "the subprocess driver orchestrates draft + gap-pivot refine."
        )


@dataclass
class PromptChainRelaxedStrategy:
    """Variant of prompt_chain that drops the rigid file:line commit in
    the plan stage and lets sketch/finalize pivot freely.

    Round 1 (plan): uses ``ablation_synthesis_regex_pcrlx_plan.j2`` —
    the model may emit a free-form plan and either name a specific
    ``file:line`` target or the literal ``unspecified`` (a soft commit).
    Round 2 (sketch): uses ``..._pcrlx_sketch.j2`` — allowed to pivot
    off the plan's gap.
    Round 3 (finalize): uses ``..._pcrlx_finalize.j2`` — allowed to
    rename the ``target_gaps`` to whatever the sketch actually targeted.

    Hypothesis: prompt_chain collapsed at v3_all (6/150 seeds) because
    the rigid 3-stage commit funnel was too narrow. Relaxing the
    plan-stage commit and letting downstream stages pivot should
    restore fill without losing the multi-stage reasoning benefit.

    Cache behaviour: salt is
    ``,strategy=prompt_chain_relaxed,round=<plan|sketch|finalize>``.
    Orchestration: 3 API calls per seed; dispatched by an explicit
    branch in ``generate_ablation_inputs.run_ablation`` that mirrors
    the ``prompt_chain`` branch but uses the relaxed template names
    and a more permissive plan parser.
    """
    name: str = "prompt_chain_relaxed"
    n_calls_per_seed: int = 3
    supports_tool_use: bool = False
    description: str = "plan->sketch->finalize with soft (or no) file:line commit"

    def build_messages(self, ctx: CellContext, sample_index: int) -> list[dict]:
        from synthesis.scripts.generate_ablation_inputs import (
            _default_template_name,
            _resolve_input_format,
            build_ablation_prompt,
        )

        dataset_root = ctx.dataset_root or ctx.target.prep_dataset_root
        fmt = _resolve_input_format(
            ctx.target.name, ctx.extra.get("input_format"),
        )
        rendered = build_ablation_prompt(
            ctx.target.name,
            dataset_root=dataset_root,
            include_tests=ctx.variant.include_tests,
            include_gaps=ctx.variant.include_gaps,
            include_source=ctx.variant.include_source,
            model=ctx.model,
            source_max_files=ctx.extra.get("source_max_files", 40),
            source_token_budget=ctx.extra.get("source_token_budget"),
            num_inputs=ctx.extra.get("num_inputs", 1),
            max_gaps=ctx.extra.get("max_gaps", 30),
            input_format=fmt,
            template_name=_default_template_name(fmt, strategy=self.name),
        )
        return [
            {"role": "system", "content": ""},
            {"role": "user", "content": rendered},
        ]

    def run_one_seed(self, client: Any, ctx: CellContext, sample_index: int) -> Any:
        raise NotImplementedError(
            "PromptChainRelaxedStrategy.run_one_seed is intentionally unused; "
            "the subprocess driver orchestrates the relaxed plan/sketch/finalize."
        )


@dataclass
class DiversityAwareLiteStrategy:
    """Single-call strategy that injects prior-seed claimed_gaps into the
    prompt to encourage corpus-level complementarity (experiment4
    FOLLOWUP mechanism).

    Each prompt extends the default template with a
    "COVERAGE FROM OTHER SEEDS IN THIS BATCH" block listing the
    deduplicated ``target_gaps`` self-reported by every ``sample_*.json``
    already on disk in this cell's ``synthesis_dir``. The model is
    instructed to target a gap NOT in that list.

    Caveat (architectural honesty): self-reported ``target_gaps`` are an
    unreliable signal — a draft can claim a gap and not actually hit it.
    The "lite" suffix marks this as the cheap version; a full
    coverage-grounded diversity strategy would replay each seed through
    llvm-cov before threading the result back, which is a separate
    architectural project. This is the cheapest test of L1/L7 (corpus
    complementarity as a first-class lever, per experiment_iteration_
    summary §3) achievable without an in-loop coverage hook.

    Cache: prompt depends on disk history, so cache hits within a single
    cell run are near-zero by design. Restarts must bump
    ``--attempt-offset`` >=5000 as usual (invariant 5).

    Worker concurrency: ``AblationRunner`` dispatches up to
    ``worker_count`` synthesis subprocesses in parallel. Each subprocess
    reads whatever sample_*.json sidecars exist at prompt-build time —
    order is non-deterministic across workers but each call sees a
    valid (possibly partial) snapshot. The union metric M2 doesn't
    depend on per-seed ordering, so eventual consistency is acceptable.

    Cache salt: ``,strategy=diversity_aware_lite`` (no round; single-call).
    Dispatched by an explicit branch in
    ``generate_ablation_inputs.run_ablation`` that reads the
    synthesis_dir before assembling the prompt.
    """
    name: str = "diversity_aware_lite"
    n_calls_per_seed: int = 1
    supports_tool_use: bool = False
    description: str = "default + prior-seed gap-history (self-reported, cheap)"
    history_window: int = 30  # cap on prior-claim summary length

    def build_messages(self, ctx: CellContext, sample_index: int) -> list[dict]:
        # Driver builds the actual messages with history injected;
        # this method is only used by tests that exercise the strategy
        # outside the driver (and they pass empty history).
        from synthesis.scripts.generate_ablation_inputs import (
            _default_template_name,
            _resolve_input_format,
            build_ablation_prompt,
        )

        dataset_root = ctx.dataset_root or ctx.target.prep_dataset_root
        fmt = _resolve_input_format(
            ctx.target.name, ctx.extra.get("input_format"),
        )
        rendered = build_ablation_prompt(
            ctx.target.name,
            dataset_root=dataset_root,
            include_tests=ctx.variant.include_tests,
            include_gaps=ctx.variant.include_gaps,
            include_source=ctx.variant.include_source,
            model=ctx.model,
            source_max_files=ctx.extra.get("source_max_files", 40),
            source_token_budget=ctx.extra.get("source_token_budget"),
            num_inputs=ctx.extra.get("num_inputs", 1),
            max_gaps=ctx.extra.get("max_gaps", 30),
            input_format=fmt,
            template_name=_default_template_name(fmt, strategy=self.name),
            prior_claimed_gaps=ctx.extra.get("prior_claimed_gaps", []),
            prior_seed_count=ctx.extra.get("prior_seed_count", 0),
        )
        return [
            {"role": "system", "content": ""},
            {"role": "user", "content": rendered},
        ]

    def run_one_seed(self, client: Any, ctx: CellContext, sample_index: int) -> Any:
        raise NotImplementedError(
            "DiversityAwareLiteStrategy.run_one_seed is intentionally unused; "
            "the subprocess driver orchestrates the history-aware prompt build."
        )


@dataclass
class SelfCritiqueGroundedStrategy:
    """self_critique whose refine round receives REAL coverage of the draft.

    Round 1 (draft): same prompt shape as default.
    Between rounds: the driver replays the draft seed through the RE2
    coverage build (analysis.scripts.per_seed_coverage.per_seed_hits)
    and produces a ground-truth hit/miss summary against the frozen
    15-target set.
    Round 2 (refine): uses ``ablation_synthesis_regex_grounded_refine.j2``
    — the refine prompt embeds the actual coverage feedback (NOT the
    model's self-report). The model is instructed to pivot to one of
    the branches its draft actually missed.

    Cache: salt = ``,strategy=self_critique_grounded,round=<draft|refine>``.
    The refine prompt's text depends on the draft's actual coverage,
    which is deterministic given (draft bytes, coverage binary), so
    cache hits remain meaningful within an attempt block.

    Cost overhead: ~0.5s of CPU per seed (one extra llvm-cov replay
    between calls). For a 150-seed cell that's ~75s of extra wall clock.
    """
    name: str = "self_critique_grounded"
    n_calls_per_seed: int = 2
    supports_tool_use: bool = False
    description: str = "self_critique whose refine sees REAL replay-coverage of the draft"

    def build_messages(self, ctx: CellContext, sample_index: int) -> list[dict]:
        from synthesis.scripts.generate_ablation_inputs import build_ablation_prompt
        dataset_root = ctx.dataset_root or ctx.target.prep_dataset_root
        rendered = build_ablation_prompt(
            ctx.target.name,
            dataset_root=dataset_root,
            include_tests=ctx.variant.include_tests,
            include_gaps=ctx.variant.include_gaps,
            include_source=ctx.variant.include_source,
            model=ctx.model,
            source_max_files=ctx.extra.get("source_max_files", 40),
            source_token_budget=ctx.extra.get("source_token_budget"),
            num_inputs=ctx.extra.get("num_inputs", 1),
            max_gaps=ctx.extra.get("max_gaps", 30),
            input_format=ctx.extra.get("input_format"),
        )
        return [{"role": "system", "content": ""}, {"role": "user", "content": rendered}]

    def run_one_seed(self, client: Any, ctx: CellContext, sample_index: int) -> Any:
        raise NotImplementedError(
            "SelfCritiqueGroundedStrategy.run_one_seed is intentionally unused; "
            "the subprocess driver orchestrates draft + per-seed-replay + refine."
        )


@dataclass
class PromptChainGroundedStrategy:
    """prompt_chain whose finalize round receives REAL coverage of the sketch.

    Plan (round 1): same as prompt_chain_relaxed plan (allows
    `unspecified` target).
    Sketch (round 2): produces a candidate regex; allowed to pivot.
    Between sketch and finalize: replay the sketch seed → coverage
    feedback.
    Finalize (round 3): uses ``..._grounded_finalize.j2`` with real
    coverage of the sketch embedded.

    Cache: ``,strategy=prompt_chain_grounded,round=<plan|sketch|finalize>``.
    """
    name: str = "prompt_chain_grounded"
    n_calls_per_seed: int = 3
    supports_tool_use: bool = False
    description: str = "plan -> sketch -> replay -> grounded finalize"

    def build_messages(self, ctx: CellContext, sample_index: int) -> list[dict]:
        from synthesis.scripts.generate_ablation_inputs import (
            _default_template_name, _resolve_input_format, build_ablation_prompt,
        )
        dataset_root = ctx.dataset_root or ctx.target.prep_dataset_root
        fmt = _resolve_input_format(ctx.target.name, ctx.extra.get("input_format"))
        rendered = build_ablation_prompt(
            ctx.target.name,
            dataset_root=dataset_root,
            include_tests=ctx.variant.include_tests,
            include_gaps=ctx.variant.include_gaps,
            include_source=ctx.variant.include_source,
            model=ctx.model,
            source_max_files=ctx.extra.get("source_max_files", 40),
            source_token_budget=ctx.extra.get("source_token_budget"),
            num_inputs=ctx.extra.get("num_inputs", 1),
            max_gaps=ctx.extra.get("max_gaps", 30),
            input_format=fmt,
            template_name=_default_template_name(fmt, strategy=self.name),
        )
        return [{"role": "system", "content": ""}, {"role": "user", "content": rendered}]

    def run_one_seed(self, client: Any, ctx: CellContext, sample_index: int) -> Any:
        raise NotImplementedError(
            "PromptChainGroundedStrategy.run_one_seed is intentionally unused; "
            "the subprocess driver orchestrates plan -> sketch -> replay -> finalize."
        )


@dataclass
class DiversityAwareGroundedStrategy:
    """Single-call diversity-aware strategy whose prompt embeds REAL
    corpus-level coverage (replayed) of prior seeds in the cell.

    Before each call, the driver:
      1. Enumerates seed_*.bin in this cell's seeds_dir.
      2. For each seed without a cached coverage sidecar, replays it
         and writes a sidecar `seed_<id>_cov.json` containing the
         15-element boolean hit vector.
      3. Unions all sidecars to build the corpus_union vector.
      4. Embeds the still-uncovered branches in the prompt.

    Concurrency: workers race-reading the sidecar dir is fine (union is
    monotonic, eventual consistency is acceptable for M2). Each worker
    pays for its own missing-sidecar replays — the duplicate work is
    bounded because once a sidecar exists, future workers skip it.

    Cache: ``,strategy=diversity_aware_grounded`` (no round; single call).
    Note: the rendered prompt depends on cell state, so cache hits
    within a cell run are essentially zero by design (this is the cost
    of grounded diversity).
    """
    name: str = "diversity_aware_grounded"
    n_calls_per_seed: int = 1
    supports_tool_use: bool = False
    description: str = "diversity-aware with REAL replay-coverage union of prior seeds"

    def build_messages(self, ctx: CellContext, sample_index: int) -> list[dict]:
        from synthesis.scripts.generate_ablation_inputs import (
            _default_template_name, _resolve_input_format, build_ablation_prompt,
        )
        dataset_root = ctx.dataset_root or ctx.target.prep_dataset_root
        fmt = _resolve_input_format(ctx.target.name, ctx.extra.get("input_format"))
        rendered = build_ablation_prompt(
            ctx.target.name,
            dataset_root=dataset_root,
            include_tests=ctx.variant.include_tests,
            include_gaps=ctx.variant.include_gaps,
            include_source=ctx.variant.include_source,
            model=ctx.model,
            source_max_files=ctx.extra.get("source_max_files", 40),
            source_token_budget=ctx.extra.get("source_token_budget"),
            num_inputs=ctx.extra.get("num_inputs", 1),
            max_gaps=ctx.extra.get("max_gaps", 30),
            input_format=fmt,
            template_name=_default_template_name(fmt, strategy=self.name),
            coverage_feedback=ctx.extra.get("coverage_feedback", ""),
        )
        return [{"role": "system", "content": ""}, {"role": "user", "content": rendered}]

    def run_one_seed(self, client: Any, ctx: CellContext, sample_index: int) -> Any:
        raise NotImplementedError(
            "DiversityAwareGroundedStrategy.run_one_seed is intentionally unused; "
            "the subprocess driver assembles corpus-union coverage per call."
        )


STRATEGIES: dict[str, PromptStrategy] = {
    DEFAULT_STRATEGY_NAME: DefaultStrategy(),
    "cot_strict": CotStrictStrategy(),
    "cot_strict_no_examples": CotStrictNoExamplesStrategy(),
    "cot_strict_rotated_examples": CotStrictRotatedExamplesStrategy(),
    "cot_strict_no_labels": CotStrictNoLabelsStrategy(),
    "few_shot": FewShotStrategy(),
    "self_critique": SelfCritiqueStrategy(),
    "self_critique_strict_gap": SelfCritiqueStrictGapStrategy(),
    "self_critique_grounded": SelfCritiqueGroundedStrategy(),
    "prompt_chain": PromptChainStrategy(),
    "prompt_chain_relaxed": PromptChainRelaxedStrategy(),
    "prompt_chain_grounded": PromptChainGroundedStrategy(),
    "diversity_aware_lite": DiversityAwareLiteStrategy(),
    "diversity_aware_grounded": DiversityAwareGroundedStrategy(),
    "tool_use": ToolUseStrategy(),
    "tool_use_retrieval": ToolUseRetrievalStrategy(),
}


def resolve_strategies(names: list[str] | None) -> list[PromptStrategy]:
    """Resolve a list of strategy names to instances; raise on unknown."""
    if not names:
        return [STRATEGIES[DEFAULT_STRATEGY_NAME]]
    resolved: list[PromptStrategy] = []
    for n in names:
        if n not in STRATEGIES:
            raise ValueError(
                f"Unknown prompt strategy: {n!r}. Known: {sorted(STRATEGIES)}"
            )
        resolved.append(STRATEGIES[n])
    return resolved
