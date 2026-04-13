"""End-to-end sensitivity smoke test (mocked LLM + mocked build_prompts)."""
from __future__ import annotations

from prediction.scripts import prompt_sensitivity
from prediction.scripts import run_prediction as rp_mod


class _StubResp:
    def __init__(self, content: str):
        self.content = content
        self.model = "stub"
        self.temperature = 0.0
        self.top_p = 1.0
        self.input_tokens = 10
        self.output_tokens = 10
        self.cost_usd = 0.0
        self.latency_ms = 1.0
        self.prompt_hash = "h"
        self.timestamp = "t"
        self.generation_wall_clock_s = 0.0
        self.cached = False


class _StubClient:
    def __init__(self):
        self.calls = 0

    def complete(self, **kwargs):  # noqa: ARG002
        self.calls += 1
        # Return JSON for primary + rephrase_b, free-text for rephrase_a.
        variant = kwargs.get("messages", [{}])[-1]["content"]
        if "natural language" in variant or "Do NOT produce JSON" in variant:
            return _StubResp(
                "This test calls foo, covers bar, and exercises baz. 10% coverage."
            )
        return _StubResp(
            '{"functions_covered": ["foo"], "functions_not_covered": [], '
            '"branches": [], "estimated_line_coverage_pct": 10.0, "reasoning": ""}'
        )


def _stub_build_prompts(*args, **kwargs):
    from prediction.scripts.build_prompt import BuiltPrompt
    variant = kwargs.get("prompt_variant", "primary")
    # Encode variant-specific marker so _StubClient routes correctly (it sees
    # only the rendered message content, not the variant name directly).
    if variant == "rephrase_a":
        rendered = "Please answer in natural language. Do NOT produce JSON."
    else:
        rendered = "Return strict JSON with schema {functions_covered, ...}."
    return [
        BuiltPrompt(
            rendered=rendered,
            target_test_name="S.T",
            few_shot_count=kwargs.get("few_shot", 5),
            context_size=kwargs.get("context_size", "file"),
            prompt_variant=variant,
            token_estimate=1,
        )
    ]


def test_sensitivity_smoke(tmp_path, monkeypatch):
    dataset_root = tmp_path / "dataset"
    (dataset_root / "re2" / "tests").mkdir(parents=True)
    results_root = tmp_path / "results"

    # Inject stubs so we don't touch the filesystem-dependent prompt builder.
    stub_client = _StubClient()
    monkeypatch.setattr(rp_mod, "build_prompts", _stub_build_prompts)
    monkeypatch.setattr(rp_mod, "LLMClient", lambda: stub_client)

    report = prompt_sensitivity.run_sensitivity(
        "re2",
        model="gpt-4o-2024-08-06",
        few_shot=5,
        dataset_root=dataset_root,
        results_root=results_root,
    )
    assert set(report["per_variant"].keys()) == {"primary", "rephrase_a", "rephrase_b"}
    # Variant A returns free-text with 3 funcs -> should parse OK.
    assert report["per_variant"]["rephrase_a"]["parse_failures"] == 0
    assert report["per_variant"]["primary"]["parse_failures"] == 0
    out_files = list(results_root.rglob("prompt_sensitivity.*.json"))
    assert out_files, "sensitivity report not written"


def test_sensitivity_counts_parse_failures(tmp_path, monkeypatch):
    class _BadClient:
        def complete(self, **kwargs):  # noqa: ARG002
            return _StubResp("totally broken response")

    dataset_root = tmp_path / "dataset"
    (dataset_root / "re2" / "tests").mkdir(parents=True)
    results_root = tmp_path / "results"
    monkeypatch.setattr(rp_mod, "build_prompts", _stub_build_prompts)
    monkeypatch.setattr(rp_mod, "LLMClient", lambda: _BadClient())

    report = prompt_sensitivity.run_sensitivity(
        "re2", model="gpt-4o-2024-08-06", few_shot=5,
        dataset_root=dataset_root, results_root=results_root,
    )
    assert report["per_variant"]["primary"]["parse_failures"] == 1
    assert report["per_variant"]["rephrase_a"]["parse_failures"] == 1
    assert report["per_variant"]["rephrase_b"]["parse_failures"] == 1
