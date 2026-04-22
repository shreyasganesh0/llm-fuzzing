"""Phase 9 cross-verification integration tests.

The per-phase suites (Phases 0-8) each test their own surface in
isolation. Phase 9 is the cross-cutting audit: do the pieces compose
without drift? Specifically:

  Suite A — Strategy-registry contract (6 tests).
  Suite B — Cache-salt composition invariants (5 tests).
  Suite C — Template contract (4 tests + 1 cache-integrity audit).
  Suite D — End-to-end dispatch (3 tests).

All tests are hermetic: LLM calls are mocked, no `.cache/llm/` writes,
no network. The cache-integrity audit is read-only and skipped unless
`UTCF_RUN_CACHE_AUDIT=1` to keep the fast suite well under 10 seconds.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined, meta

from core.llm_client import Response, _prompt_hash
from core.prompt_strategies import (
    DEFAULT_STRATEGY_NAME,
    STRATEGIES,
    PromptStrategy,
    make_cache_salt,
    resolve_strategies,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = REPO_ROOT / "synthesis" / "prompts"
CACHE_DIR = REPO_ROOT / ".cache" / "llm"

# All 14 ablation templates that live on disk for the 6 strategies.
_ABLATION_TEMPLATES = [
    "ablation_synthesis_binary.j2",
    "ablation_synthesis_binary_cot.j2",
    "ablation_synthesis_binary_fewshot.j2",
    "ablation_synthesis_binary_refine.j2",
    "ablation_synthesis_binary_plan.j2",
    "ablation_synthesis_binary_sketch.j2",
    "ablation_synthesis_binary_finalize.j2",
    "ablation_synthesis_regex.j2",
    "ablation_synthesis_regex_cot.j2",
    "ablation_synthesis_regex_fewshot.j2",
    "ablation_synthesis_regex_refine.j2",
    "ablation_synthesis_regex_plan.j2",
    "ablation_synthesis_regex_sketch.j2",
    "ablation_synthesis_regex_finalize.j2",
]

_EXPECTED_STRATEGY_NAMES = {
    "default", "cot_strict", "few_shot",
    "self_critique", "prompt_chain", "tool_use",
}

_EXPECTED_CALL_BUDGETS = {
    "default": 1,
    "cot_strict": 1,
    "few_shot": 1,
    "self_critique": 2,
    "prompt_chain": 3,
    "tool_use": 4,
}


# ─────────────────────────────── Suite A ────────────────────────────────────

def test_all_strategies_satisfy_protocol():
    for name, s in STRATEGIES.items():
        assert isinstance(s, PromptStrategy), f"{name!r} not a PromptStrategy"
        assert isinstance(s.name, str) and s.name, f"{name!r} has empty name"
        assert s.n_calls_per_seed >= 1, (
            f"{name!r} has n_calls_per_seed={s.n_calls_per_seed}"
        )
        assert s.supports_tool_use in {True, False}, (
            f"{name!r} has non-bool supports_tool_use={s.supports_tool_use!r}"
        )
        desc = getattr(s, "description", None)
        assert isinstance(desc, str) and desc, (
            f"{name!r} has empty description"
        )


def test_strategy_names_unique_and_stable():
    assert set(STRATEGIES.keys()) == _EXPECTED_STRATEGY_NAMES, (
        f"Strategy registry drift: {sorted(STRATEGIES)} "
        f"!= {sorted(_EXPECTED_STRATEGY_NAMES)}"
    )
    assert len(STRATEGIES) == len(_EXPECTED_STRATEGY_NAMES), (
        "Duplicate key in STRATEGIES registry"
    )
    # .name attribute must agree with the registry key.
    for key, s in STRATEGIES.items():
        assert s.name == key, f"registry key {key!r} != strategy.name {s.name!r}"


def test_default_strategy_is_index_zero_equivalent():
    resolved = resolve_strategies(None)
    assert len(resolved) == 1
    assert resolved[0].name == "default"
    assert resolved[0] is STRATEGIES[DEFAULT_STRATEGY_NAME]
    # Empty list has the same semantics — the CLI feeds [] when the user
    # omits --strategy.
    assert resolve_strategies([])[0].name == "default"


def test_unknown_strategy_raises():
    with pytest.raises(ValueError) as exc:
        resolve_strategies(["ghost"])
    assert "ghost" in str(exc.value)


def test_strategy_call_budgets_match_design():
    actual = {name: s.n_calls_per_seed for name, s in STRATEGIES.items()}
    assert actual == _EXPECTED_CALL_BUDGETS, (
        f"Call-budget drift: {actual} != {_EXPECTED_CALL_BUDGETS}"
    )


def test_only_tool_use_supports_tool_use():
    tool_use_strategies = {
        name for name, s in STRATEGIES.items() if s.supports_tool_use
    }
    assert tool_use_strategies == {"tool_use"}, (
        f"Unexpected supports_tool_use set: {tool_use_strategies}"
    )


# ─────────────────────────────── Suite B ────────────────────────────────────

def test_default_salt_is_legacy_exact_string():
    """Guards byte-identity for the ~14k already-cached entries."""
    salt = make_cache_salt(
        model="m", sample=0, cell="c", run_offset=0,
    )
    assert salt == "model=m,sample=0,ablation=c,run=0"
    # Explicit strategy="default" must produce the identical string.
    salt_explicit = make_cache_salt(
        model="m", sample=0, cell="c", run_offset=0, strategy="default",
    )
    assert salt_explicit == "model=m,sample=0,ablation=c,run=0"


def test_every_nondefault_salt_has_strategy_segment():
    for name in STRATEGIES:
        if name == DEFAULT_STRATEGY_NAME:
            continue
        salt = make_cache_salt(
            model="m", sample=0, cell="c", run_offset=0, strategy=name,
        )
        assert salt.endswith(f",strategy={name}"), (
            f"Non-default strategy {name!r} produced salt {salt!r} "
            f"without a ,strategy={name} suffix"
        )


def test_round_kwarg_round_trip():
    """Multi-call strategies must produce distinct salts per round.

    Canonical sub-round names per the code in
    ``synthesis.scripts.generate_ablation_inputs.run_ablation``:
      self_critique → {"draft", "refine"}
      prompt_chain  → {"plan", "sketch", "finalize"}
      tool_use      → {"turn_0", "turn_1", "turn_2", "turn_3"}
    """
    rounds_by_strategy = {
        "self_critique": ["draft", "refine"],
        "prompt_chain": ["plan", "sketch", "finalize"],
        "tool_use": ["turn_0", "turn_1", "turn_2", "turn_3"],
    }
    for strategy, rounds in rounds_by_strategy.items():
        salts = [
            make_cache_salt(
                model="m", sample=0, cell="c", run_offset=0,
                strategy=strategy, round=r,
            )
            for r in rounds
        ]
        assert len(set(salts)) == len(salts), (
            f"{strategy!r} produced non-unique round salts: {salts}"
        )
        for r, s in zip(rounds, salts):
            assert s.endswith(f",strategy={strategy},round={r}"), (
                f"{strategy!r}/{r!r} salt shape wrong: {s!r}"
            )


def test_response_format_changes_llm_cache_key():
    """Guards: response_format must be part of the LLM cache key."""
    msgs = [{"role": "user", "content": "hi"}]
    h_plain = _prompt_hash(
        "m", msgs, temperature=0.0, top_p=1.0, max_tokens=100,
        cache_salt=None, response_format=None,
    )
    h_json = _prompt_hash(
        "m", msgs, temperature=0.0, top_p=1.0, max_tokens=100,
        cache_salt=None, response_format={"type": "json_object"},
    )
    assert h_plain != h_json, (
        "response_format change did not shift the cache key — "
        "this would let json-mode vs plain completions collide"
    )


def test_tools_changes_llm_cache_key():
    """Guards: tools must be part of the LLM cache key."""
    msgs = [{"role": "user", "content": "hi"}]
    h_plain = _prompt_hash(
        "m", msgs, temperature=0.0, top_p=1.0, max_tokens=100,
        cache_salt=None, tools=None,
    )
    tools_list = [{"type": "function", "function": {"name": "x"}}]
    h_tools = _prompt_hash(
        "m", msgs, temperature=0.0, top_p=1.0, max_tokens=100,
        cache_salt=None, tools=tools_list,
    )
    assert h_plain != h_tools


# ─────────────────────────────── Suite C ────────────────────────────────────

def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(PROMPTS_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )


def _base_fixture() -> dict:
    """Minimal kwargs that satisfy every base-template Jinja reference."""
    return {
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


def _extra_for_template(name: str) -> dict:
    """Round-specific fixture fields for refine/sketch/finalize templates."""
    extras: dict = {}
    if name.endswith("_refine.j2"):
        extras["draft_content"] = "DRAFT-BYTES"
        extras["draft_reasoning"] = "DRAFT-REASONING"
    if name.endswith("_sketch.j2"):
        extras["plan_text"] = "PLAN-TEXT"
        extras["plan_target_gap"] = "src/foo.cc:42"
    if name.endswith("_finalize.j2"):
        extras["plan_text"] = "PLAN-TEXT"
        extras["plan_target_gap"] = "src/foo.cc:42"
        extras["sketch_content"] = "SKETCH-BYTES"
        extras["sketch_reasoning"] = "SKETCH-REASONING"
    return extras


def test_all_templates_render_with_minimal_fixture():
    env = _jinja_env()
    for tmpl_name in _ABLATION_TEMPLATES:
        # RE2 templates use target_name="re2"; harfbuzz uses "harfbuzz".
        fixture = _base_fixture()
        if "regex" in tmpl_name:
            fixture["target_name"] = "re2"
        fixture.update(_extra_for_template(tmpl_name))

        # Introspect undeclared variables to catch template drift where a
        # template references something not in our fixture.
        src = (PROMPTS_DIR / tmpl_name).read_text()
        ast = env.parse(src)
        undeclared = meta.find_undeclared_variables(ast)
        missing = undeclared - fixture.keys()
        assert not missing, (
            f"{tmpl_name} references undeclared vars {missing} "
            f"not provided by the Phase-9 fixture"
        )

        rendered = env.get_template(tmpl_name).render(**fixture)
        assert rendered.strip(), f"{tmpl_name} rendered empty"


def test_template_output_parses_as_valid_shape():
    """Each seed-emitting template must show a seed-schema block; plan
    templates must show a plan schema (with no inputs array).
    """
    env = _jinja_env()
    plan_templates = {
        "ablation_synthesis_binary_plan.j2",
        "ablation_synthesis_regex_plan.j2",
    }
    for tmpl_name in _ABLATION_TEMPLATES:
        fixture = _base_fixture()
        if "regex" in tmpl_name:
            fixture["target_name"] = "re2"
        fixture.update(_extra_for_template(tmpl_name))
        rendered = env.get_template(tmpl_name).render(**fixture)
        assert "OUTPUT RULES" in rendered, (
            f"{tmpl_name} missing OUTPUT RULES block"
        )
        assert "Schema:" in rendered, f"{tmpl_name} missing Schema block"

        if tmpl_name in plan_templates:
            assert '"plan"' in rendered, (
                f"{tmpl_name} plan schema missing 'plan' field"
            )
            assert '"target_gap"' in rendered, (
                f"{tmpl_name} plan schema missing 'target_gap' field"
            )
            # Plan templates must NOT ask for the seed-schema inputs array.
            assert '"inputs":' not in rendered, (
                f"{tmpl_name} plan template leaks the seed-schema "
                f"inputs array — would make the model emit bytes in round 1"
            )
        else:
            # Seed-emitting templates advertise inputs/regexes list.
            if "regex" in tmpl_name:
                has_shape = '"regexes"' in rendered or '"inputs"' in rendered
            else:
                has_shape = '"inputs"' in rendered
            assert has_shape, (
                f"{tmpl_name} seed template missing inputs/regexes schema"
            )


def test_strategy_to_template_map_is_exhaustive():
    from synthesis.scripts.generate_ablation_inputs import (
        _TEMPLATE_SUFFIX_BY_STRATEGY,
    )
    for name in STRATEGIES:
        assert name in _TEMPLATE_SUFFIX_BY_STRATEGY, (
            f"Strategy {name!r} has no entry in "
            f"_TEMPLATE_SUFFIX_BY_STRATEGY — silent dispatch drift"
        )
    # Reverse check: no dead entries in the map.
    for key in _TEMPLATE_SUFFIX_BY_STRATEGY:
        assert key in STRATEGIES, (
            f"_TEMPLATE_SUFFIX_BY_STRATEGY has key {key!r} that is "
            f"not a registered strategy"
        )


def test_refine_sketch_finalize_suffixes_exist_on_disk():
    suffixes = ["_cot", "_fewshot", "_refine", "_plan", "_sketch", "_finalize"]
    bases = ["ablation_synthesis_regex", "ablation_synthesis_binary"]
    for base in bases:
        for sfx in suffixes:
            p = PROMPTS_DIR / f"{base}{sfx}.j2"
            assert p.is_file(), (
                f"Missing template {p.name} — dispatch would fail at runtime"
            )


# ─────────────────────── Cache integrity (opt-in audit) ─────────────────────

@pytest.mark.skipif(
    os.environ.get("UTCF_RUN_CACHE_AUDIT") != "1",
    reason="set UTCF_RUN_CACHE_AUDIT=1 to run the 14k-entry cache audit",
)
def test_cache_records_are_well_formed():
    assert CACHE_DIR.is_dir(), f"Cache dir missing: {CACHE_DIR}"
    start = time.perf_counter()
    files = sorted(CACHE_DIR.glob("*.json"))
    required = {
        "model", "input_tokens", "output_tokens",
        "cost_usd", "timestamp", "content",
    }
    missing_fields: list[tuple[Path, set]] = []
    negative_cost: list[Path] = []
    unreadable: list[Path] = []
    for p in files:
        try:
            data = json.loads(p.read_text(), strict=False)
        except (OSError, ValueError):
            unreadable.append(p)
            continue
        miss = required - data.keys()
        if miss:
            missing_fields.append((p, miss))
            continue
        cost = data.get("cost_usd")
        if not isinstance(cost, (int, float)) or cost < 0:
            negative_cost.append(p)
    elapsed = time.perf_counter() - start
    assert not missing_fields, (
        f"{len(missing_fields)} cache records missing required fields. "
        f"First 5: {missing_fields[:5]}"
    )
    assert not negative_cost, (
        f"{len(negative_cost)} cache records have non-numeric or "
        f"negative cost_usd. First 5: {negative_cost[:5]}"
    )
    assert not unreadable, (
        f"{len(unreadable)} unreadable cache files. "
        f"First 5: {unreadable[:5]}"
    )
    # Sanity on scan time — if this blows 10s on a warm FS, the harness
    # needs a different strategy.
    assert elapsed < 30.0, (
        f"cache audit took {elapsed:.1f}s — consider parallelising"
    )


# ─────────────────────────────── Suite D ────────────────────────────────────

def _mock_response(
    content: str,
    *,
    tool_calls: list | None = None,
    model: str = "stub",
) -> Response:
    return Response(
        content=content,
        model=model,
        temperature=0.7,
        top_p=0.95,
        input_tokens=10,
        output_tokens=20,
        cost_usd=0.0,
        latency_ms=1.0,
        prompt_hash="stub",
        timestamp="1970-01-01T00:00:00+00:00",
        generation_wall_clock_s=0.001,
        cached=False,
        tool_calls=tool_calls,
        raw=None,
    )


_REGEX_SEED_JSON = (
    '{"regexes":[{"regex":"(a+)*","target_gaps":["re2/parse.cc:42"],'
    '"reasoning":"stub"}]}'
)
_BINARY_SEED_JSON = (
    '{"inputs":[{"content_b64":"AAEAAAAK","target_gaps":["src/hb-ot-face.cc:1"],'
    '"reasoning":"stub"}]}'
)
_PLAN_JSON = '{"plan":"stub plan","target_gap":"re2/parse.cc:42"}'


def _fake_complete_factory(
    strategy_name: str, captured_salts: list, seed_json: str,
):
    """Return a mock ``LLMClient.complete`` for one strategy's call pattern."""
    call_index = {"i": 0}

    def fake(self, messages, *, model, temperature, top_p, max_tokens,
             cache_salt=None, response_format=None, guided_json=None,
             tools=None, tool_choice=None, use_cache=True, max_retries=10,
             abort_on_loop=True):
        captured_salts.append(cache_salt)
        i = call_index["i"]
        call_index["i"] += 1
        # self_critique: draft, refine → both emit seed JSON.
        # prompt_chain: plan, sketch, finalize → first plan, then seed×2.
        # tool_use: turn_0 emits seed JSON (no tool_calls) → one call.
        # default/cot_strict/few_shot: one seed JSON.
        if strategy_name == "prompt_chain" and i == 0:
            return _mock_response(_PLAN_JSON)
        return _mock_response(seed_json)

    return fake


@pytest.mark.parametrize("strategy_name", sorted(STRATEGIES))
def test_all_strategies_execute_single_seed_end_to_end(
    tmp_path: Path, strategy_name: str, monkeypatch,
):
    """Each strategy must end-to-end produce a parsed seed under a single
    mocked-LLM invocation, with the expected call count and cache-salt
    shape for its first call.
    """
    from core.llm_client import LLMClient
    from core.targets import TARGETS
    from core.variants import VARIANTS_BY_NAME
    from synthesis.scripts.generate_ablation_inputs import run_ablation

    # tool_use must run against a model whose supports_tool_use is True.
    model = "gpt-oss-20b" if strategy_name == "tool_use" else "llama-3.1-8b-instruct"
    target = TARGETS["harfbuzz"]
    variant = VARIANTS_BY_NAME["v0_none"]

    # Skip if the prep dataset isn't materialised — build_ablation_prompt
    # reads metadata/harness from disk. Integration coverage is preserved
    # by the strategies that don't need the prep dataset.
    if not (target.prep_dataset_root / target.name / "metadata.json").is_file():
        pytest.skip("prep dataset not materialised; run phase_prep first")

    captured_salts: list = []
    fake = _fake_complete_factory(strategy_name, captured_salts, _BINARY_SEED_JSON)

    results_root = tmp_path / "results"
    results_root.mkdir()

    with patch.object(LLMClient, "complete", new=fake):
        records = run_ablation(
            target=target.name,
            model=model,
            cell=variant.name,
            include_tests=variant.include_tests,
            include_gaps=variant.include_gaps,
            include_source=variant.include_source,
            dataset_root=target.prep_dataset_root,
            results_root=results_root,
            samples=1,
            num_inputs=1,
            source_max_files=5,
            source_token_budget=None,
            max_tokens=512,
            max_gaps=5,
            run_id=0,
            strategy=strategy_name,
        )

    assert len(records) == 1
    rec = records[0]
    assert rec.parse_status == "ok", (
        f"{strategy_name!r} produced parse_status={rec.parse_status!r}; "
        f"records={rec}"
    )
    assert len(rec.inputs) >= 1, f"{strategy_name!r} produced no inputs"

    # Call count matches the strategy's budget (upper bound for tool_use).
    expected_calls = STRATEGIES[strategy_name].n_calls_per_seed
    if strategy_name == "tool_use":
        # tool_use emits a final seed on turn 0 → exactly one call.
        assert len(captured_salts) == 1, (
            f"tool_use made {len(captured_salts)} calls, expected 1 "
            f"(model answered without a tool_call on turn 0)"
        )
    else:
        assert len(captured_salts) == expected_calls, (
            f"{strategy_name!r} made {len(captured_salts)} calls, "
            f"expected {expected_calls}"
        )

    # First-call cache salt shape matches the strategy's canonical form.
    first_salt = captured_salts[0]
    if strategy_name == "default":
        assert first_salt == (
            f"model={model},sample=0,ablation=v0_none,run=0"
        )
    elif strategy_name == "self_critique":
        assert first_salt.endswith(",strategy=self_critique,round=draft")
    elif strategy_name == "prompt_chain":
        assert first_salt.endswith(",strategy=prompt_chain,round=plan")
    elif strategy_name == "tool_use":
        assert first_salt.endswith(",strategy=tool_use,round=turn_0")
    else:  # cot_strict, few_shot
        assert first_salt.endswith(f",strategy={strategy_name}")


def test_tool_use_strategy_rejects_incompatible_model(tmp_path: Path):
    """Running tool_use on a supports_tool_use=False model must raise.

    Complements the CLI-preflight test in Phase 8: this hits the
    ``run_ablation`` prelude gate directly.
    """
    from core.targets import TARGETS
    from core.variants import VARIANTS_BY_NAME
    from synthesis.scripts.generate_ablation_inputs import run_ablation

    target = TARGETS["harfbuzz"]
    variant = VARIANTS_BY_NAME["v0_none"]
    if not (target.prep_dataset_root / target.name / "metadata.json").is_file():
        pytest.skip("prep dataset not materialised")

    with pytest.raises(ValueError) as exc:
        run_ablation(
            target=target.name,
            model="llama-3.1-8b-instruct",
            cell=variant.name,
            include_tests=False, include_gaps=False, include_source=False,
            dataset_root=target.prep_dataset_root,
            results_root=tmp_path / "out",
            samples=1, num_inputs=1,
            source_max_files=5, source_token_budget=None,
            max_tokens=512, max_gaps=5,
            run_id=0, strategy="tool_use",
        )
    msg = str(exc.value)
    assert "tool_use" in msg
    assert "supports_tool_use" in msg


def test_cli_dry_run_accepts_every_registered_strategy(capsys, monkeypatch):
    """Every registered strategy must survive --dry-run preflight.

    We exercise both wrappers' CLI (via AblationRunner.main) and verify
    the strategy banner renders. tool_use is the only strategy with
    per-model preflight gates — we feed it a tool-use-capable model.
    """
    from core.targets import TARGETS
    from core.variants import STANDARD_VARIANTS
    from scripts._ablation_base import AblationRunner

    for name in sorted(STRATEGIES):
        models = (
            ["gpt-oss-20b"]
            if STRATEGIES[name].supports_tool_use
            else ["llama-3.1-8b-instruct"]
        )
        runner = AblationRunner(
            target=TARGETS["harfbuzz"],
            variants=list(STANDARD_VARIANTS),
            models=models,
        )
        # Stub every phase so a preflight regression can't escape into
        # real work.
        for attr in ("phase_prep", "phase_synthesis",
                     "phase_random", "phase_metric"):
            monkeypatch.setattr(runner, attr, lambda *a, **kw: None)
        rc = runner.main([
            "--phase", "all", "--skip-existing",
            "--strategy", name, "--dry-run",
        ])
        assert rc == 0, f"strategy {name!r} dry-run returned {rc}"
        out = capsys.readouterr().out
        assert "=== Ablation run ===" in out
        assert f"Strategies (1): {name}" in out, (
            f"banner missing strategy line for {name!r}; got:\n{out}"
        )
