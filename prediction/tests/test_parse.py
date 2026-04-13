"""Response-parsing tests (JSON variants + free-text variant A)."""
from __future__ import annotations

from prediction.scripts.parse_response import (
    parse_free_text_response,
    parse_json_response,
)


def test_parses_clean_json():
    text = """{"functions_covered": ["a", "b"], "functions_not_covered": [], "branches": [], "estimated_line_coverage_pct": 42.5, "reasoning": "ok"}"""
    pred, status = parse_json_response(text)
    assert status == "ok"
    assert pred.functions_covered == ["a", "b"]
    assert pred.estimated_line_coverage_pct == 42.5


def test_parses_triple_backtick_json():
    text = "here is the result:\n```json\n{\"functions_covered\": [\"a\"], \"functions_not_covered\": [], \"branches\": [], \"estimated_line_coverage_pct\": 1.0, \"reasoning\": \"\"}\n```"
    pred, status = parse_json_response(text)
    assert status == "ok"
    assert pred.functions_covered == ["a"]


def test_parses_json_with_trailing_prose():
    text = '{"functions_covered": ["a"], "functions_not_covered": [], "branches": [], "estimated_line_coverage_pct": 1.0, "reasoning": ""}  thanks!'
    pred, status = parse_json_response(text)
    assert status == "ok"


def test_rejects_totally_broken_response():
    text = "sorry, I can't help with that"
    pred, status = parse_json_response(text)
    assert status == "parse_failure"
    assert pred is None


def test_parses_json_branches_and_coerces_bools():
    text = '{"functions_covered": ["a"], "functions_not_covered": [], "branches": [{"location": "x.cc:10", "true_taken": true, "false_taken": false}], "estimated_line_coverage_pct": 10.0, "reasoning": ""}'
    pred, status = parse_json_response(text)
    assert status == "ok"
    assert pred.branches[0].location == "x.cc:10"
    assert pred.branches[0].true_taken is True


def test_free_text_extracts_three_funcs_and_pct():
    text = "This test calls `RE2::FullMatch`, covers RE2::Init, and exercises RE2::Compile. Line x.cc:42 is taken. Overall about 37% coverage."
    pred, status = parse_free_text_response(text)
    assert status == "ok"
    assert set(pred.functions_covered) >= {"RE2::FullMatch", "RE2::Init", "RE2::Compile"}
    assert pred.estimated_line_coverage_pct == 37.0
    assert any(b.location == "x.cc:42" for b in pred.branches)


def test_free_text_parse_failure_when_too_few_functions():
    text = "calls RE2::FullMatch. That's it."
    pred, status = parse_free_text_response(text)
    assert status == "parse_failure"
    assert pred is None


def test_free_text_filters_unknown_functions_when_set_provided():
    text = "This test calls foo, covers bar, and exercises baz. 10% coverage."
    pred, status = parse_free_text_response(text, known_functions={"foo", "bar"})
    assert status == "parse_failure"  # only 2 after filtering -> fail
    assert pred is None
