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


STRATEGIES: dict[str, PromptStrategy] = {
    DEFAULT_STRATEGY_NAME: DefaultStrategy(),
    "cot_strict": CotStrictStrategy(),
    "few_shot": FewShotStrategy(),
    "self_critique": SelfCritiqueStrategy(),
    "prompt_chain": PromptChainStrategy(),
    "tool_use": ToolUseStrategy(),
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
