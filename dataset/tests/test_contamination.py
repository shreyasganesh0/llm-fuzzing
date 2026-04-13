"""Contamination probe unit tests (mocked LLM)."""
from __future__ import annotations

import json
from pathlib import Path

from dataset.scripts import contamination_probe


class _FakeResp:
    def __init__(self, content: str):
        self.content = content
        self.model = "fake"
        self.temperature = 0.0
        self.top_p = 1.0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = 0.0
        self.latency_ms = 0.0
        self.prompt_hash = "h"
        self.timestamp = "t"
        self.generation_wall_clock_s = 0.0
        self.cached = False


class _FakeClient:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = 0

    def complete(self, **kwargs):  # noqa: ARG002
        self.calls += 1
        # Rotate through provided canned responses; fall back to empty string.
        if self._responses:
            return _FakeResp(self._responses.pop(0))
        return _FakeResp("")


def _write_dataset(tmp_path: Path, *, test_code: str) -> Path:
    dataset_root = tmp_path / "dataset"
    (dataset_root / "re2").mkdir(parents=True)
    tests = [
        {
            "test_name": f"Suite.Test{i}",
            "test_code": test_code,
            "test_file": "t.cc",
            "upstream_repo": "https://example.com/re2.git",
            "upstream_commit": "deadbeef",
            "upstream_file": "t.cc",
            "upstream_line": 1,
            "framework": "googletest",
            "input_data": None,
            "called_functions": [],
        }
        for i in range(10)
    ]
    (dataset_root / "re2" / "tests.json").write_text(json.dumps(tests))
    return dataset_root


def test_probe_classifies_high_when_verbatim(tmp_path):
    test_code = "TEST(Suite, A) {\n  ASSERT_TRUE(true);\n}"
    dataset_root = _write_dataset(tmp_path, test_code=test_code)
    # Return the exact test code for every verbatim probe -> BLEU ~1.0
    client = _FakeClient(responses=[test_code] * 10 + ["Suite.Test0"])
    report = contamination_probe.run_probe("re2", "fake-model", dataset_root=dataset_root, llm_client=client)
    assert report.contamination_risk_level == "HIGH"
    assert len(report.verbatim_bleu_scores) == 10
    assert report.verbatim_exact_match_rate > 0.5


def test_probe_classifies_low_when_gibberish(tmp_path):
    dataset_root = _write_dataset(tmp_path, test_code="TEST(Suite, B) {}")
    client = _FakeClient(responses=["totally unrelated text"] * 10 + [""])
    report = contamination_probe.run_probe("re2", "fake-model", dataset_root=dataset_root, llm_client=client)
    assert report.contamination_risk_level == "LOW"
    assert report.metadata_recall == 0.0


def test_probe_picks_exactly_n_probes(tmp_path):
    dataset_root = _write_dataset(tmp_path, test_code="TEST(Suite, C) {}")
    client = _FakeClient(responses=["x"] * 100)
    report = contamination_probe.run_probe("re2", "fake-model", dataset_root=dataset_root, llm_client=client)
    assert len(report.probe_test_names) == 10
