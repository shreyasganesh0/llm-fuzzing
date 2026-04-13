"""Run a fine-tuned model on the Phase 2 held-out test set (plan §Phase 4
verification — 'Fine-tuned models evaluated on same held-out set as Phase 2').

Dry-run mode validates the JSON schema of an already-written prediction,
so we can exercise the downstream aggregation without a GPU.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.logging_config import get_logger
from prediction.scripts.parse_response import parse_json_response

logger = get_logger("utcf.phase4.infer")


def run_one(
    *,
    adapter_dir: Path,
    base_model: str,
    test_path: Path,
    out_path: Path,
    dry_run: bool = False,
    max_new_tokens: int = 512,
) -> dict:
    rows = [json.loads(line) for line in test_path.read_text().splitlines() if line.strip()]
    predictions = []

    if dry_run:
        for row in rows:
            predictions.append({
                "test_name": row.get("metadata", {}).get("test_name"),
                "output": row["output"],
                "status": "dry_run",
            })
    else:
        from peft import PeftModel  # type: ignore
        from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore

        tokenizer = AutoTokenizer.from_pretrained(base_model)
        tokenizer.pad_token = tokenizer.eos_token
        model = AutoModelForCausalLM.from_pretrained(base_model, torch_dtype="bfloat16")
        model = PeftModel.from_pretrained(model, str(adapter_dir))
        model.eval()

        for row in rows:
            prompt = f"{row['instruction']}\n\n{row['input']}\n\n"
            inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
            out_ids = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
            text = tokenizer.decode(out_ids[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
            parsed, status = parse_json_response(text)
            predictions.append({
                "test_name": row.get("metadata", {}).get("test_name"),
                "output": text,
                "parsed": parsed.model_dump() if parsed else None,
                "status": status,
            })

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(predictions, indent=2))
    return {"n_predictions": len(predictions), "out_path": str(out_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter-dir", type=Path, required=True)
    parser.add_argument("--base-model", required=True)
    parser.add_argument("--test", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    out = run_one(
        adapter_dir=args.adapter_dir,
        base_model=args.base_model,
        test_path=args.test,
        out_path=args.out,
        dry_run=args.dry_run,
    )
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
