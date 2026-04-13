"""Golden-values tests for evaluate_record."""
from __future__ import annotations

from core.dataset_schema import (
    BranchCoverage,
    BranchPrediction,
    CoverageProfile,
    FileCoverage,
    PredictionRecord,
    PredictionResult,
)
from prediction.scripts.evaluate_prediction import evaluate_record


def _mk_truth() -> CoverageProfile:
    fc = FileCoverage(
        lines_covered=[1, 2, 3],
        lines_not_covered=[4, 5],
        branches={
            "x.cc:10": BranchCoverage.model_construct(true=True, false=True),
            "x.cc:20": BranchCoverage.model_construct(true=True, false=False),
            "x.cc:30": BranchCoverage.model_construct(true=False, false=False),
        },
        functions_covered=["foo", "bar"],
        functions_not_covered=["baz"],
    )
    return CoverageProfile(
        test_name="S.T",
        upstream_file="x.cc",
        upstream_line=1,
        framework="googletest",
        files={"x.cc": fc},
        total_lines_covered=3,
        total_lines_in_source=5,
        total_branches_covered=3,
        total_branches_in_source=6,
    )


def _mk_prediction(funcs, branches, pct) -> PredictionRecord:
    return PredictionRecord(
        target="re2",
        model="m",
        few_shot_count=5,
        test_name="S.T",
        prediction=PredictionResult(
            functions_covered=funcs,
            functions_not_covered=[],
            branches=[BranchPrediction(location=l, true_taken=tt, false_taken=ft) for (l, tt, ft) in branches],
            estimated_line_coverage_pct=pct,
            reasoning="",
        ),
        parse_status="ok",
        raw_response="",
    )


def test_perfect_prediction():
    truth = _mk_truth()
    rec = _mk_prediction(
        funcs=["foo", "bar"],
        branches=[("x.cc:10", True, True), ("x.cc:20", True, False)],
        pct=60.0,  # 3/5 lines
    )
    m = evaluate_record(rec, truth)
    assert m.function_f1 == 1.0
    assert m.branch_f1 == 1.0
    assert m.coverage_mae == 0.0


def test_half_functions_correct():
    truth = _mk_truth()
    rec = _mk_prediction(funcs=["foo", "qux"], branches=[], pct=60.0)
    m = evaluate_record(rec, truth)
    # precision = 0.5, recall = 0.5 -> f1 = 0.5
    assert m.function_precision == 0.5
    assert m.function_recall == 0.5
    assert m.function_f1 == 0.5


def test_coverage_mae():
    truth = _mk_truth()
    rec = _mk_prediction(funcs=["foo"], branches=[], pct=40.0)  # truth 60, pred 40 -> mae 20
    m = evaluate_record(rec, truth)
    assert m.coverage_mae == 20.0


def test_parse_failure_counts():
    truth = _mk_truth()
    rec = PredictionRecord(
        target="re2",
        model="m",
        few_shot_count=5,
        test_name="S.T",
        prediction=None,
        parse_status="parse_failure",
        raw_response="oops",
    )
    m = evaluate_record(rec, truth)
    assert m.n_parse_failures == 1
    assert m.function_f1 == 0.0
