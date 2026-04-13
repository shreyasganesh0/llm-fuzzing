"""Transfer matrix assembly + format stratification (plan §Phase Transfer T.5)."""
from __future__ import annotations

from core.dataset_schema import (
    PredictionMetrics,
    PredictionRecord,
    PromptLogEntry,
    TransferRecord,
)
from transfer.scripts.evaluate_transfer import build_matrix, format_stratification

LOG = PromptLogEntry(
    model="m", temperature=0.0, top_p=1.0, input_tokens=0, output_tokens=0,
    cost_usd=0.0, latency_ms=0, prompt_hash="h", timestamp="1970-01-01T00:00:00Z",
    generation_wall_clock_s=0.0, target="t", phase="transfer",
    experiment_tag="loo", cached=True,
)


def _pred(target: str, fn: float, br: float, mae: float, gcr: float) -> PredictionRecord:
    return PredictionRecord(
        target=target,
        model="m",
        few_shot_count=5,
        context_size="file",
        prompt_variant="primary",
        test_name="test_x",
        prediction=None,
        parse_status="ok",
        raw_response="",
        log=LOG,
        metrics=PredictionMetrics(function_f1=fn, branch_f1=br, coverage_mae=mae, gap_closure_rate=gcr),
    )


def test_matrix_rows_match_held_out_targets():
    records = [
        TransferRecord(held_out_target="re2", source_targets=["libxml2"], model="m",
                       mode="prediction", records=[_pred("re2", 0.7, 0.6, 2.5, 0.3)]),
        TransferRecord(held_out_target="libxml2", source_targets=["re2"], model="m",
                       mode="prediction", records=[_pred("libxml2", 0.8, 0.5, 1.5, 0.4)]),
    ]
    matrix = build_matrix(records)
    assert matrix.rows == ["libxml2", "re2"]
    assert matrix.cols == ["function_f1", "branch_f1", "coverage_mae", "gap_closure_rate"]
    assert matrix.values[0][0] == 0.8
    assert matrix.values[1][1] == 0.6


def test_format_stratification_returns_all_buckets():
    records = [
        TransferRecord(held_out_target="re2", source_targets=["libxml2"], model="m",
                       mode="prediction", records=[_pred("re2", 0.7, 0.7, 2.0, 0.3)]),
        TransferRecord(held_out_target="libpng", source_targets=["re2"], model="m",
                       mode="prediction", records=[_pred("libpng", 0.4, 0.4, 5.0, 0.1)]),
    ]
    strat = format_stratification(records)
    for key in ("text_text", "text_binary", "binary_binary", "delta_same_vs_cross"):
        assert key in strat
    # text_text bucket should be higher than text_binary in this synthetic case.
    assert strat["text_text"] >= strat["text_binary"]
