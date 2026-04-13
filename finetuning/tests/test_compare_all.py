"""Config A-I assembly and LaTeX emission (plan §4.3)."""
from __future__ import annotations

from finetuning.scripts.compare_all import CONFIGS, build_rows, write_outputs


def test_all_nine_configs_present():
    names = [c[0] for c in CONFIGS]
    assert names == ["A", "B", "C", "D", "E", "F", "G", "H", "I"]


def test_build_rows_pulls_phase2_metrics():
    phase2 = {
        "summary": [
            {"target": "re2", "model": "gpt-4o", "few_shot": 5, "context_size": "file",
             "prompt_variant": "primary",
             "function_f1_mean": 0.55, "branch_f1_mean": 0.44,
             "coverage_mae_mean": 8.1, "spearman_rho_mean": 0.3,
             "n_predictions": 10, "n_parse_failures": 0},
        ]
    }
    rows = build_rows(phase2_summary=phase2, phase3_summary={}, exp2_summary={})
    b = next(r for r in rows if r.config == "B")
    assert b.branch_f1 == 0.44
    assert b.function_f1 == 0.55


def test_write_outputs_emits_expected_files(tmp_path):
    rows = build_rows(phase2_summary={}, phase3_summary={}, exp2_summary={})
    write_outputs(rows, tmp_path)
    assert (tmp_path / "final_comparison.json").is_file()
    assert (tmp_path / "final_comparison.tex").is_file()
    assert (tmp_path / "final_comparison_head_to_head.tex").is_file()
