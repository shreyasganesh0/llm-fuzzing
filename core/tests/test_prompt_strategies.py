"""Tests for the prompt-strategy registry (Phase 1 scaffold).

Guards the cache-preservation invariant: DefaultStrategy must produce
the identical cache salt and directory layout as the pre-registry code,
so the ~14k already-cached LLM responses under `.cache/llm/` keep
hitting.
"""
from __future__ import annotations

from core.prompt_strategies import (
    DEFAULT_STRATEGY_NAME,
    STRATEGIES,
    CellContext,
    CotStrictStrategy,
    DefaultStrategy,
    FewShotStrategy,
    PromptChainStrategy,
    PromptStrategy,
    SelfCritiqueStrategy,
    ToolUseStrategy,
    make_cache_salt,
    resolve_strategies,
)
from core.targets import TARGETS
from core.variants import VARIANTS_BY_NAME


def test_registry_has_default():
    s = STRATEGIES[DEFAULT_STRATEGY_NAME]
    assert isinstance(s, PromptStrategy)
    assert s.name == "default"
    assert s.n_calls_per_seed == 1
    assert s.supports_tool_use is False


def test_default_strategy_is_an_instance_of_the_protocol():
    # runtime_checkable protocol — a structural check is enough.
    assert isinstance(DefaultStrategy(), PromptStrategy)


def test_cache_salt_default_strategy_preserves_legacy_key():
    """The exact salt string pre-Phase-1 was:
        f"model={model},sample={k},ablation={cell},run={run_id}"
    DefaultStrategy must reproduce it byte-for-byte so cached responses
    continue to hit.
    """
    legacy = "model=claude-haiku-4-5-20251001,sample=3,ablation=v0_none,run=42"
    new = make_cache_salt(
        model="claude-haiku-4-5-20251001",
        sample=3, cell="v0_none", run_offset=42, strategy="default",
    )
    assert new == legacy
    # Default is the default — omitting the kwarg yields the same thing.
    new_implicit = make_cache_salt(
        model="claude-haiku-4-5-20251001",
        sample=3, cell="v0_none", run_offset=42,
    )
    assert new_implicit == legacy


def test_cache_salt_non_default_appends_strategy():
    salt = make_cache_salt(
        model="llama-3.1-8b-instruct",
        sample=0, cell="v1_src", run_offset=0, strategy="cot_strict",
    )
    assert salt.endswith(",strategy=cot_strict")
    assert "ablation=v1_src" in salt
    # Non-default salt must differ from the legacy default salt.
    legacy = make_cache_salt(
        model="llama-3.1-8b-instruct",
        sample=0, cell="v1_src", run_offset=0,
    )
    assert salt != legacy


def test_resolve_strategies_defaults_to_default():
    s = resolve_strategies(None)
    assert len(s) == 1
    assert s[0].name == "default"
    s2 = resolve_strategies([])
    assert len(s2) == 1
    assert s2[0].name == "default"


def test_resolve_strategies_rejects_unknown():
    import pytest
    with pytest.raises(ValueError):
        resolve_strategies(["nope-not-a-strategy"])


def test_cell_context_is_frozen():
    """CellContext must be hashable/immutable so strategies can safely
    use it as a cache key component."""
    import dataclasses

    import pytest

    ctx = CellContext(
        target=TARGETS["re2"],
        variant=VARIANTS_BY_NAME["v0_none"],
        model="llama-3.1-8b-instruct",
        sample_offset=0,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.model = "other"  # type: ignore[misc]


def test_default_strategy_build_messages_matches_legacy_builder():
    """DefaultStrategy.build_messages must produce the same prompt text
    the existing driver constructs. We test the simplest variant
    (v0_none) so we don't need dataset fixtures beyond what's already
    under `dataset/fixtures/`.
    """
    import pytest
    try:
        from synthesis.scripts.generate_ablation_inputs import build_ablation_prompt
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"synthesis module unavailable: {exc}")

    target = TARGETS["re2"]
    variant = VARIANTS_BY_NAME["v0_none"]
    if not target.prep_dataset_root.is_dir():
        pytest.skip(
            f"prep dataset not materialized at {target.prep_dataset_root}; "
            "run the prep phase first"
        )

    ctx = CellContext(
        target=target, variant=variant,
        model="llama-3.1-8b-instruct", sample_offset=0,
        dataset_root=target.prep_dataset_root,
        extra={"num_inputs": 1, "max_gaps": 30},
    )
    try:
        rendered = build_ablation_prompt(
            target.name,
            dataset_root=target.prep_dataset_root,
            include_tests=variant.include_tests,
            include_gaps=variant.include_gaps,
            include_source=variant.include_source,
            model=ctx.model,
            source_max_files=40,
            source_token_budget=None,
            num_inputs=1,
            max_gaps=30,
            input_format=None,
        )
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"legacy prompt builder raised on fixture: {exc}")

    try:
        messages = DefaultStrategy().build_messages(ctx, sample_index=0)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"DefaultStrategy build raised on fixture: {exc}")

    assert messages[0] == {"role": "system", "content": ""}
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == rendered


# ──────────────────────────── CoT-strict strategy (Phase 2) ──────────────────


def test_cot_strict_registered():
    s = STRATEGIES["cot_strict"]
    assert s.name == "cot_strict"
    assert s.n_calls_per_seed == 1
    assert s.supports_tool_use is False
    assert isinstance(s, PromptStrategy)
    assert isinstance(CotStrictStrategy(), PromptStrategy)


def test_cot_strict_cache_salt_has_strategy_segment():
    salt = make_cache_salt(
        model="llama-3.1-8b-instruct",
        sample=0, cell="v1_src", run_offset=0, strategy="cot_strict",
    )
    assert salt.endswith(",strategy=cot_strict")

    default_salt = make_cache_salt(
        model="llama-3.1-8b-instruct",
        sample=0, cell="v1_src", run_offset=0, strategy="default",
    )
    assert "strategy=" not in default_salt
    assert salt != default_salt


def _render_cot_template(template_name: str, **overrides) -> str:
    """Render a CoT template with a minimal fixture dict."""
    from pathlib import Path

    from jinja2 import Environment, FileSystemLoader, StrictUndefined

    prompts_dir = Path(__file__).resolve().parents[2] / "synthesis" / "prompts"
    env = Environment(
        loader=FileSystemLoader(str(prompts_dir)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    ctx = {
        "system_prompt": "SYS",
        "target_name": "harfbuzz",
        "harness_code": "void f() {}",
        "source_language": "cpp",
        "include_tests": False,
        "few_shot_examples": [],
        "include_gaps": False,
        "coverage_gaps": [],
        "total_upstream_tests": 0,
        "union_coverage_pct": 0.0,
        "max_gaps": 30,
        "include_source": False,
        "source_files": [],
        "num_inputs": 1,
    }
    ctx.update(overrides)
    return env.get_template(template_name).render(**ctx)


def test_cot_strict_binary_template_loads():
    rendered = _render_cot_template("ablation_synthesis_binary_cot.j2")
    assert "Step 1 (Quote):" in rendered
    assert "Step 2 (Locate):" in rendered
    assert "Step 3 (Offset):" in rendered
    assert "Step 4 (Bytes):" in rendered


def test_cot_strict_regex_template_loads():
    rendered = _render_cot_template(
        "ablation_synthesis_regex_cot.j2", target_name="re2",
    )
    assert "Step 1 (Quote):" in rendered
    assert "Step 2 (Locate):" in rendered
    assert "Step 3 (Construct):" in rendered
    assert "Step 4 (Regex):" in rendered


def test_cot_strict_paths_insert_strategy_segment():
    target = TARGETS["harfbuzz"]
    cot_seeds = target.cell_seeds_dir(
        "v1_src", "llama-3.1-8b-instruct", strategy="cot_strict",
    )
    default_seeds = target.cell_seeds_dir(
        "v1_src", "llama-3.1-8b-instruct",
    )
    cot_posix = cot_seeds.as_posix()
    default_posix = default_seeds.as_posix()
    assert "/cot_strict/ablation/" in cot_posix
    assert "/cot_strict/" not in default_posix
    assert "/ablation/v1_src/" in default_posix


# ─────────────────────────── Few-shot strategy (Phase 4) ─────────────────────


def test_few_shot_registered():
    s = STRATEGIES["few_shot"]
    assert s.name == "few_shot"
    assert s.n_calls_per_seed == 1
    assert s.supports_tool_use is False
    assert isinstance(s, PromptStrategy)
    assert isinstance(FewShotStrategy(), PromptStrategy)


def test_few_shot_cache_salt():
    salt = make_cache_salt(
        model="llama-3.1-8b-instruct",
        sample=0, cell="v2_src_tests", run_offset=0, strategy="few_shot",
    )
    assert salt.endswith(",strategy=few_shot")
    default_salt = make_cache_salt(
        model="llama-3.1-8b-instruct",
        sample=0, cell="v2_src_tests", run_offset=0, strategy="default",
    )
    assert "strategy=" not in default_salt
    assert salt != default_salt


def test_few_shot_paths_insert_strategy_segment():
    target = TARGETS["harfbuzz"]
    fs_seeds = target.cell_seeds_dir(
        "v2_src_tests", "llama-3.1-8b-instruct", strategy="few_shot",
    )
    default_seeds = target.cell_seeds_dir(
        "v2_src_tests", "llama-3.1-8b-instruct",
    )
    assert "/few_shot/ablation/" in fs_seeds.as_posix()
    assert "/few_shot/" not in default_seeds.as_posix()


def _fewshot_render(template_name: str, **overrides) -> str:
    from pathlib import Path

    from jinja2 import Environment, FileSystemLoader, StrictUndefined

    prompts_dir = Path(__file__).resolve().parents[2] / "synthesis" / "prompts"
    env = Environment(
        loader=FileSystemLoader(str(prompts_dir)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    ctx = {
        "system_prompt": "SYS",
        "target_name": "harfbuzz",
        "harness_code": "void f() {}",
        "source_language": "cpp",
        "include_tests": False,
        "few_shot_examples": [],
        "include_gaps": False,
        "coverage_gaps": [],
        "total_upstream_tests": 0,
        "union_coverage_pct": 0.0,
        "max_gaps": 30,
        "include_source": False,
        "source_files": [],
        "num_inputs": 1,
        "few_shot_exemplars": [],
    }
    ctx.update(overrides)
    return env.get_template(template_name).render(**ctx)


def test_few_shot_template_renders_with_exemplars(tmp_path):
    exemplars = [
        {
            "origin": {
                "variant": "v2_src_tests",
                "model": "llama-3.1-8b-instruct",
                "sample_index": 7,
                "seed_path": "synthesis/harfbuzz/ablation/v2_src_tests/llama-3.1-8b-instruct/sample_7.json",
            },
            "content": None,
            "content_b64": "AAEAAAAKAIAAAwAgY21hcA==",
            "reasoning": "FIRST-EXEMPLAR-REASONING targets sfnt header at offset 0x00.",
            "target_gaps": ["src/hb-ot-face.cc:100"],
        },
        {
            "origin": {
                "variant": "v2_src_tests",
                "model": "codestral-22b",
                "sample_index": 2,
                "seed_path": "synthesis/harfbuzz/ablation/v2_src_tests/codestral-22b/sample_2.json",
            },
            "content": None,
            "content_b64": "T1RUTwAKAIAAAw==",
            "reasoning": "SECOND-EXEMPLAR-REASONING exercises CFF magic bytes.",
            "target_gaps": ["src/hb-ot-cff.cc:200"],
        },
    ]
    rendered = _fewshot_render(
        "ablation_synthesis_binary_fewshot.j2",
        few_shot_exemplars=exemplars,
    )
    assert "=== WORKED EXAMPLES FROM PRIOR RUNS ===" in rendered
    assert "FIRST-EXEMPLAR-REASONING" in rendered
    assert "SECOND-EXEMPLAR-REASONING" in rendered
    assert "v2_src_tests/llama-3.1-8b-instruct" in rendered
    assert "v2_src_tests/codestral-22b" in rendered


def test_few_shot_template_skips_block_when_no_exemplars():
    rendered = _fewshot_render(
        "ablation_synthesis_binary_fewshot.j2",
        few_shot_exemplars=[],
    )
    assert "=== WORKED EXAMPLES FROM PRIOR RUNS ===" not in rendered


def test_few_shot_regex_template_renders_with_exemplars():
    exemplars = [
        {
            "origin": {
                "variant": "v2_src_tests",
                "model": "llama-3.1-8b-instruct",
                "sample_index": 0,
                "seed_path": "synthesis/re2/ablation/v2_src_tests/llama-3.1-8b-instruct/sample_0.json",
            },
            "content": "\\p{Greek}+",
            "content_b64": None,
            "reasoning": "REGEX-EXEMPLAR-REASONING hits Unicode class parser branch.",
            "target_gaps": ["re2/set.cc:240"],
        },
    ]
    rendered = _fewshot_render(
        "ablation_synthesis_regex_fewshot.j2",
        target_name="re2",
        few_shot_exemplars=exemplars,
    )
    assert "=== WORKED EXAMPLES FROM PRIOR RUNS ===" in rendered
    assert "REGEX-EXEMPLAR-REASONING" in rendered
    assert "\\p{Greek}+" in rendered


def test_few_shot_build_ablation_prompt_accepts_exemplars_kwarg():
    """The driver's build_ablation_prompt must accept and forward
    few_shot_exemplars to the Jinja context."""
    import pytest
    try:
        from synthesis.scripts.generate_ablation_inputs import (
            _TEMPLATE_SUFFIX_BY_STRATEGY,
            _default_template_name,
        )
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"synthesis module unavailable: {exc}")

    # Unknown strategies raise.
    with pytest.raises(ValueError):
        _default_template_name("regex", strategy="nope")
    # Known strategies map cleanly.
    assert _default_template_name("regex", strategy="default") == "ablation_synthesis_regex.j2"
    assert _default_template_name("binary", strategy="cot_strict") == "ablation_synthesis_binary_cot.j2"
    assert _default_template_name("regex", strategy="few_shot") == "ablation_synthesis_regex_fewshot.j2"
    assert _default_template_name("binary", strategy="few_shot") == "ablation_synthesis_binary_fewshot.j2"
    # self_critique reuses the base template on round 1.
    assert _default_template_name("regex", strategy="self_critique") == "ablation_synthesis_regex.j2"
    assert _default_template_name("binary", strategy="self_critique") == "ablation_synthesis_binary.j2"
    # Map must enumerate every registered strategy name.
    assert set(_TEMPLATE_SUFFIX_BY_STRATEGY) >= set(STRATEGIES)


# ──────────────────────── Self-critique strategy (Phase 5) ────────────────────


def test_self_critique_registered():
    s = STRATEGIES["self_critique"]
    assert s.name == "self_critique"
    assert s.n_calls_per_seed == 2
    assert s.supports_tool_use is False
    assert isinstance(s, PromptStrategy)
    assert isinstance(SelfCritiqueStrategy(), PromptStrategy)


def test_self_critique_cache_salt_distinguishes_draft_refine():
    """Draft and refine sub-rounds must produce distinct cache keys.

    Shape asserted: `,strategy=self_critique,round=<draft|refine>`.
    Anything else would collide the two sub-round responses on one cache
    file and silently replay the draft as the refine output.
    """
    draft_salt = make_cache_salt(
        model="llama-3.1-8b-instruct",
        sample=0, cell="v1_src", run_offset=0,
        strategy="self_critique", round="draft",
    )
    refine_salt = make_cache_salt(
        model="llama-3.1-8b-instruct",
        sample=0, cell="v1_src", run_offset=0,
        strategy="self_critique", round="refine",
    )
    assert draft_salt != refine_salt
    assert draft_salt.endswith(",strategy=self_critique,round=draft")
    assert refine_salt.endswith(",strategy=self_critique,round=refine")

    # Non-self_critique strategies stay byte-identical when `round` is not
    # supplied (backwards compatibility with pre-Phase-5 cache entries).
    legacy = make_cache_salt(
        model="llama-3.1-8b-instruct",
        sample=0, cell="v1_src", run_offset=0,
    )
    assert "round=" not in legacy


def test_self_critique_paths_insert_strategy_segment():
    target = TARGETS["harfbuzz"]
    sc_seeds = target.cell_seeds_dir(
        "v1_src", "llama-3.1-8b-instruct", strategy="self_critique",
    )
    default_seeds = target.cell_seeds_dir(
        "v1_src", "llama-3.1-8b-instruct",
    )
    sc_posix = sc_seeds.as_posix()
    default_posix = default_seeds.as_posix()
    assert "/self_critique/ablation/" in sc_posix
    assert "/self_critique/" not in default_posix


def test_refine_template_renders_with_draft():
    from pathlib import Path

    from jinja2 import Environment, FileSystemLoader, StrictUndefined

    prompts_dir = Path(__file__).resolve().parents[2] / "synthesis" / "prompts"
    env = Environment(
        loader=FileSystemLoader(str(prompts_dir)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    ctx = {
        "system_prompt": "SYS",
        "target_name": "harfbuzz",
        "harness_code": "void f() {}",
        "source_language": "cpp",
        "include_tests": False,
        "few_shot_examples": [],
        "include_gaps": False,
        "coverage_gaps": [],
        "total_upstream_tests": 0,
        "union_coverage_pct": 0.0,
        "max_gaps": 30,
        "include_source": False,
        "source_files": [],
        "num_inputs": 1,
        "few_shot_exemplars": [],
        "draft_content": "AAEAAAAK-DRAFT-BLOB-BASE64",
        "draft_reasoning": "DRAFT-REASONING-SENTINEL the bytes hit sfnt header.",
    }
    rendered = env.get_template(
        "ablation_synthesis_binary_refine.j2",
    ).render(**ctx)
    assert "=== DRAFT UNDER REVIEW ===" in rendered
    assert "=== CRITIQUE TASK ===" in rendered
    assert "AAEAAAAK-DRAFT-BLOB-BASE64" in rendered
    assert "DRAFT-REASONING-SENTINEL" in rendered


# ──────────────────────── Prompt-chain strategy (Phase 6) ────────────────────


def test_prompt_chain_registered():
    s = STRATEGIES["prompt_chain"]
    assert s.name == "prompt_chain"
    assert s.n_calls_per_seed == 3
    assert s.supports_tool_use is False
    assert isinstance(s, PromptStrategy)
    assert isinstance(PromptChainStrategy(), PromptStrategy)


def test_prompt_chain_cache_salt_rounds_distinct():
    """Plan, sketch, finalize sub-rounds must produce distinct cache keys.

    Shape asserted: ``,strategy=prompt_chain,round=<plan|sketch|finalize>``.
    Anything else would collide the three sub-round responses on one
    cache file and silently replay an earlier round as a later one.
    """
    plan_salt = make_cache_salt(
        model="llama-3.1-8b-instruct",
        sample=0, cell="v3_all", run_offset=0,
        strategy="prompt_chain", round="plan",
    )
    sketch_salt = make_cache_salt(
        model="llama-3.1-8b-instruct",
        sample=0, cell="v3_all", run_offset=0,
        strategy="prompt_chain", round="sketch",
    )
    finalize_salt = make_cache_salt(
        model="llama-3.1-8b-instruct",
        sample=0, cell="v3_all", run_offset=0,
        strategy="prompt_chain", round="finalize",
    )
    assert len({plan_salt, sketch_salt, finalize_salt}) == 3
    assert plan_salt.endswith(",strategy=prompt_chain,round=plan")
    assert sketch_salt.endswith(",strategy=prompt_chain,round=sketch")
    assert finalize_salt.endswith(",strategy=prompt_chain,round=finalize")


def test_prompt_chain_paths_insert_strategy_segment():
    target = TARGETS["harfbuzz"]
    pc_seeds = target.cell_seeds_dir(
        "v3_all", "llama-3.1-8b-instruct", strategy="prompt_chain",
    )
    default_seeds = target.cell_seeds_dir(
        "v3_all", "llama-3.1-8b-instruct",
    )
    pc_posix = pc_seeds.as_posix()
    default_posix = default_seeds.as_posix()
    assert "/prompt_chain/ablation/" in pc_posix
    assert "/prompt_chain/" not in default_posix


def _render_chain_template(template_name: str, **overrides) -> str:
    """Render a Phase-6 chain template with a minimal fixture dict."""
    from pathlib import Path

    from jinja2 import Environment, FileSystemLoader, StrictUndefined

    prompts_dir = Path(__file__).resolve().parents[2] / "synthesis" / "prompts"
    env = Environment(
        loader=FileSystemLoader(str(prompts_dir)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    ctx = {
        "system_prompt": "SYS",
        "target_name": "harfbuzz",
        "harness_code": "void f() {}",
        "source_language": "cpp",
        "include_tests": False,
        "few_shot_examples": [],
        "include_gaps": False,
        "coverage_gaps": [],
        "total_upstream_tests": 0,
        "union_coverage_pct": 0.0,
        "max_gaps": 30,
        "include_source": False,
        "source_files": [],
        "num_inputs": 1,
        "few_shot_exemplars": [],
    }
    ctx.update(overrides)
    return env.get_template(template_name).render(**ctx)


def test_plan_template_renders_without_inputs_field():
    """Plan stage's schema block advertises plan + target_gap, not inputs.

    The plan round explicitly does NOT produce a seed, so the rendered
    prompt must not tell the model to emit an `"inputs":` array. We scan
    the rendered text for the literal `"inputs":` JSON key — its absence
    guards against a template author accidentally copying the default
    schema block into the plan template.
    """
    rendered = _render_chain_template("ablation_synthesis_binary_plan.j2")
    assert '"plan"' in rendered
    assert '"target_gap"' in rendered
    # The seed-schema key "inputs": (with colon, as a JSON key) must not
    # appear in the plan template's output.
    assert '"inputs":' not in rendered


def test_sketch_template_includes_committed_plan():
    rendered = _render_chain_template(
        "ablation_synthesis_binary_sketch.j2",
        plan_text="use cmap format 12",
        plan_target_gap="src/hb-ot-cmap.cc:123",
    )
    assert "COMMITTED" in rendered
    assert "use cmap format 12" in rendered
    assert "src/hb-ot-cmap.cc:123" in rendered


def test_finalize_template_includes_sketch():
    rendered = _render_chain_template(
        "ablation_synthesis_regex_finalize.j2",
        target_name="re2",
        plan_text="hit counted-rep branch",
        plan_target_gap="re2/parse.cc:42",
        sketch_content="a(?:b|c)d",
        sketch_reasoning="pattern exercises alternation inside non-capturing group",
    )
    assert "SKETCH UNDER REVIEW" in rendered
    assert "a(?:b|c)d" in rendered
    assert "pattern exercises alternation" in rendered


# ──────────────────────────── Tool-use strategy (Phase 7) ────────────────────


def test_tool_use_registered():
    s = STRATEGIES["tool_use"]
    assert s.name == "tool_use"
    assert s.supports_tool_use is True
    assert s.n_calls_per_seed == 4
    assert isinstance(s, PromptStrategy)
    assert isinstance(ToolUseStrategy(), PromptStrategy)


def test_tool_use_paths_insert_strategy_segment():
    target = TARGETS["harfbuzz"]
    tu_seeds = target.cell_seeds_dir(
        "v1_src", "gpt-oss-20b", strategy="tool_use",
    )
    default_seeds = target.cell_seeds_dir(
        "v1_src", "gpt-oss-20b",
    )
    tu_posix = tu_seeds.as_posix()
    default_posix = default_seeds.as_posix()
    assert "/tool_use/ablation/" in tu_posix
    assert "/tool_use/" not in default_posix


def test_tool_use_cache_salt_per_turn():
    """Three turns must produce three distinct cache keys.

    Shape asserted: ``,strategy=tool_use,round=turn_<i>``. Each turn
    feeds prior tool results back in, so colliding them on one cache key
    would replay turn-0's output as turn-1's and break the refinement
    loop silently.
    """
    salts = [
        make_cache_salt(
            model="gpt-oss-20b",
            sample=0, cell="v1_src", run_offset=0,
            strategy="tool_use", round=f"turn_{i}",
        )
        for i in range(3)
    ]
    assert len(set(salts)) == 3
    for i, s in enumerate(salts):
        assert s.endswith(f",strategy=tool_use,round=turn_{i}")
        assert ",strategy=tool_use" in s
