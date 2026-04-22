"""CLI + preflight tests for `AblationRunner.main` (Phase 8).

Pure-Python tests: no network, no LLM, no `.cache/llm/` access, no
subprocess execution. We stub `phase_*` methods to assert they do NOT
run under `--dry-run`, and we drive `main([...])` with controlled argv
lists to exercise argparse and the preflight matrix.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from core.targets import TARGETS
from core.variants import STANDARD_VARIANTS
from scripts._ablation_base import AblationRunner

# ── fixtures ──────────────────────────────────────────────────────────────

HB_MODELS = [
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
    "llama-3.1-8b-instruct",
    "llama-3.1-70b-instruct",
    "llama-3.3-70b-instruct",
    "codestral-22b",
    "nemotron-3-super-120b-a12b",
]


def _make_runner(models: list[str] | None = None) -> AblationRunner:
    return AblationRunner(
        target=TARGETS["harfbuzz"],
        variants=list(STANDARD_VARIANTS),
        models=models if models is not None else list(HB_MODELS),
    )


def _stub_phases(runner: AblationRunner) -> dict:
    """Replace phase methods with counters; returns the counter dict."""
    counts = {"prep": 0, "synthesis": 0, "random": 0, "m1": 0, "m2": 0}

    def _prep(*a, **kw):
        counts["prep"] += 1
    def _syn(*a, **kw):
        counts["synthesis"] += 1
    def _rnd(*a, **kw):
        counts["random"] += 1
    def _metric(metric, *a, **kw):
        counts[metric.name] = counts.get(metric.name, 0) + 1

    runner.phase_prep = _prep  # type: ignore[method-assign]
    runner.phase_synthesis = _syn  # type: ignore[method-assign]
    runner.phase_random = _rnd  # type: ignore[method-assign]
    runner.phase_metric = _metric  # type: ignore[method-assign]
    return counts


# ── tests ────────────────────────────────────────────────────────────────

def test_list_strategies_exits_0(capsys):
    runner = _make_runner()
    counts = _stub_phases(runner)
    rc = runner.main(["--list-strategies"])
    assert rc == 0
    out = capsys.readouterr().out
    for name in ("default", "cot_strict", "few_shot",
                 "self_critique", "prompt_chain", "tool_use"):
        assert name in out, f"missing strategy {name!r} in list output"
    # No phase may fire under --list-strategies.
    assert all(v == 0 for v in counts.values()), counts


def test_dry_run_prints_matrix_and_exits_0(capsys):
    runner = _make_runner()
    counts = _stub_phases(runner)
    rc = runner.main(["--phase", "all", "--skip-existing", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "=== Ablation run ===" in out
    assert "Total cells:" in out
    # Full default matrix: 5 variants x 7 models x 1 strategy = 35 cells.
    assert "Total cells:  35" in out
    assert all(v == 0 for v in counts.values()), counts


def test_variants_csv_restricts_run(capsys):
    runner = _make_runner()
    _stub_phases(runner)
    rc = runner.main([
        "--phase", "synthesis", "--variants", "v0_none,v2_src_tests",
        "--dry-run",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Variants (2): v0_none, v2_src_tests" in out
    # 2 variants x 7 models x 1 strategy = 14
    assert "Total cells:  14" in out


def test_unknown_variant_raises(capsys):
    runner = _make_runner()
    _stub_phases(runner)
    rc = runner.main(["--phase", "synthesis", "--variants", "v0_none,zzz", "--dry-run"])
    assert rc != 0
    err = capsys.readouterr().err
    # Error must list the full known set so the user can spot the typo.
    for name in ("v0_none", "v1_src", "v2_src_tests", "v3_all", "v4_src_gaps"):
        assert name in err, f"missing variant name {name!r} in error: {err!r}"


def test_num_seeds_override_emits_warning(capsys):
    runner = _make_runner()
    _stub_phases(runner)
    rc = runner.main(["--phase", "synthesis", "--num-seeds", "5", "--dry-run"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert "--num-seeds=5" in captured.err
    assert "Seeds/cell:   5" in captured.out


def test_num_seeds_default_no_warning(capsys):
    runner = _make_runner()
    _stub_phases(runner)
    rc = runner.main(["--phase", "synthesis", "--dry-run"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "WARNING" not in captured.err
    assert "Seeds/cell:   150" in captured.out


def test_preflight_rejects_tool_use_on_llama(capsys):
    runner = _make_runner(models=["llama-3.1-8b-instruct"])
    _stub_phases(runner)
    rc = runner.main([
        "--phase", "synthesis", "--strategy", "tool_use", "--dry-run",
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "supports_tool_use" in err.lower() or "tool-use" in err.lower()
    assert "llama-3.1-8b-instruct" in err


def test_preflight_partial_filter(capsys):
    # gpt-oss-20b supports tool_use; llama does not — partial incompat.
    runner = _make_runner(models=["gpt-oss-20b", "llama-3.1-8b-instruct"])
    _stub_phases(runner)
    rc = runner.main([
        "--phase", "synthesis", "--strategy", "tool_use", "--dry-run",
    ])
    assert rc == 0
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert "llama-3.1-8b-instruct" in captured.err
    # After filter: 5 variants x 1 model x 1 strategy = 5 cells
    assert "Models (1):   gpt-oss-20b" in captured.out
    assert "Total cells:  5" in captured.out


def test_default_args_unchanged(capsys):
    """Backward-compat: default invocation must resolve to exactly the
    matrix an un-flagged run would have produced pre-Phase-8.
    """
    runner = AblationRunner(
        target=TARGETS["re2"],
        variants=list(STANDARD_VARIANTS),
        models=["llama-3.1-8b-instruct"],
    )
    _stub_phases(runner)
    rc = runner.main(["--phase", "prep", "--skip-existing", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    # 5 variants x 1 model x 1 strategy (default).
    assert "Variants (5):" in out
    assert "Models (1):   llama-3.1-8b-instruct" in out
    assert "Strategies (1): default" in out
    assert "Total cells:  5" in out
    assert "Seeds/cell:   150" in out


def test_dry_run_does_not_execute_any_phase():
    """Guard-rail: even with --phase all, --dry-run must not fire a phase."""
    runner = _make_runner()
    counts = _stub_phases(runner)
    rc = runner.main(["--phase", "all", "--dry-run"])
    assert rc == 0
    assert all(v == 0 for v in counts.values()), counts


def test_unknown_strategy_returns_error(capsys):
    runner = _make_runner()
    _stub_phases(runner)
    rc = runner.main(["--phase", "synthesis", "--strategy", "nonsense", "--dry-run"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "nonsense" in err or "Unknown" in err


def test_num_seeds_rejects_zero(capsys):
    runner = _make_runner()
    _stub_phases(runner)
    rc = runner.main(["--phase", "synthesis", "--num-seeds", "0", "--dry-run"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--num-seeds" in err


def test_num_seeds_threads_into_runner_state(capsys):
    """Guard: `self.num_seeds` is mutated by the flag, not shadowed."""
    runner = _make_runner()
    _stub_phases(runner)
    runner.main(["--phase", "synthesis", "--num-seeds", "7", "--dry-run"])
    assert runner.num_seeds == 7
    capsys.readouterr()  # drain


@pytest.mark.parametrize("flag", ["--list-strategies"])
def test_list_strategies_works_without_any_other_args(flag, capsys):
    runner = _make_runner()
    _stub_phases(runner)
    # No --phase, no --skip-existing — must still return 0.
    rc = runner.main([flag])
    assert rc == 0
    out = capsys.readouterr().out
    # Sanity: exactly 6 strategies are listed.
    nonempty_lines = [line for line in out.splitlines() if line.strip()]
    assert len(nonempty_lines) == 6, nonempty_lines


def test_dry_run_logs_all_metric_phases(capsys):
    runner = _make_runner()
    _stub_phases(runner)
    rc = runner.main(["--phase", "all", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    # Phases: prep, synthesis, random, m1, m2 — at minimum m1 must appear.
    assert "m1" in out
    assert "m2" in out


def test_wrapper_integration_harfbuzz_dry_run(capsys, monkeypatch):
    """Invoke the top-level wrapper via subprocess-less import path."""
    from scripts import run_ablation_harfbuzz
    # monkeypatch argv so run_ablation_harfbuzz.main() sees our flags
    monkeypatch.setattr(
        "sys.argv", ["run_ablation_harfbuzz.py", "--dry-run"],
    )
    # Patch phases so nothing runs even if dry-run logic regresses.
    with patch.object(AblationRunner, "phase_prep"), \
         patch.object(AblationRunner, "phase_synthesis"), \
         patch.object(AblationRunner, "phase_random"), \
         patch.object(AblationRunner, "phase_metric"):
        rc = run_ablation_harfbuzz.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "Target:       harfbuzz" in out
    # Default HB wrapper ships 7 models; 5 variants x 7 x 1 = 35.
    assert "Total cells:  35" in out
