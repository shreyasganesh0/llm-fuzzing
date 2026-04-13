"""End-to-end dry-run inference check (plan §Phase 4 verification:
'Fine-tuned model produces valid JSON predictions on 5 random eval examples')."""
from __future__ import annotations

import json

from finetuning.scripts.run_finetuned import run_one


def test_dry_run_inference_emits_predictions(tmp_path):
    test_path = tmp_path / "test.jsonl"
    rows = [
        {
            "instruction": "Predict coverage.",
            "input": "TEST(Foo, Bar) {}\n---\nint main() { return 0; }",
            "output": json.dumps({"total_lines_covered": 0}),
            "metadata": {"test_name": f"t{i}"},
        }
        for i in range(5)
    ]
    test_path.write_text("\n".join(json.dumps(r) for r in rows))
    out_path = tmp_path / "preds.json"
    result = run_one(
        adapter_dir=tmp_path / "adapter",
        base_model="meta-llama/Llama-3.1-8B-Instruct",
        test_path=test_path,
        out_path=out_path,
        dry_run=True,
    )
    assert result["n_predictions"] == 5
    preds = json.loads(out_path.read_text())
    assert all(p["status"] == "dry_run" for p in preds)
    assert [p["test_name"] for p in preds] == ["t0", "t1", "t2", "t3", "t4"]
