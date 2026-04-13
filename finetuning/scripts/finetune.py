"""LoRA / QLoRA fine-tune driver (plan §4.1b).

This is a skeleton — the GPU-bound work happens on the training machine
via HuggingFace `transformers` + `peft` + `trl`. The entry point here
validates the config, resolves paths, and dispatches to
`trl.SFTTrainer` when the GPU is available. Dry-run mode only parses
the config and echoes the resolved training plan.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.logging_config import get_logger

logger = get_logger("utcf.phase4.finetune")


@dataclass
class LoRAPlan:
    model_name: str
    lora_r: int
    lora_alpha: int
    lora_dropout: float
    target_modules: list[str]
    epochs: int
    learning_rate: float
    batch_size: int
    gradient_accumulation_steps: int
    max_seq_length: int
    bf16: bool
    load_in_4bit: bool
    train_path: Path
    val_path: Path
    out_dir: Path


def load_plan(config_path: Path, data_dir: Path, out_dir: Path) -> LoRAPlan:
    raw = yaml.safe_load(config_path.read_text())
    lora = raw["lora"]
    training = raw["training"]
    quant = raw.get("quantization", {})
    return LoRAPlan(
        model_name=raw["model_name"],
        lora_r=int(lora["r"]),
        lora_alpha=int(lora["alpha"]),
        lora_dropout=float(lora["dropout"]),
        target_modules=list(lora["target_modules"]),
        epochs=int(training["epochs"]),
        learning_rate=float(training["learning_rate"]),
        batch_size=int(training["batch_size"]),
        gradient_accumulation_steps=int(training["gradient_accumulation_steps"]),
        max_seq_length=int(training["max_seq_length"]),
        bf16=bool(training.get("bf16", True)),
        load_in_4bit=bool(quant.get("load_in_4bit", False)),
        train_path=data_dir / "train.jsonl",
        val_path=data_dir / "val.jsonl",
        out_dir=out_dir,
    )


def run_finetune(plan: LoRAPlan, *, dry_run: bool = False) -> dict:
    plan.out_dir.mkdir(parents=True, exist_ok=True)
    plan_json = plan.out_dir / "training_plan.json"
    plan_json.write_text(json.dumps(
        {
            "model_name": plan.model_name,
            "lora_r": plan.lora_r,
            "lora_alpha": plan.lora_alpha,
            "lora_dropout": plan.lora_dropout,
            "target_modules": plan.target_modules,
            "epochs": plan.epochs,
            "learning_rate": plan.learning_rate,
            "batch_size": plan.batch_size,
            "gradient_accumulation_steps": plan.gradient_accumulation_steps,
            "max_seq_length": plan.max_seq_length,
            "bf16": plan.bf16,
            "load_in_4bit": plan.load_in_4bit,
            "train_path": str(plan.train_path),
            "val_path": str(plan.val_path),
            "out_dir": str(plan.out_dir),
        },
        indent=2,
    ))
    if dry_run:
        logger.info("dry-run: wrote plan, skipping training", extra={"path": str(plan_json)})
        return {"status": "dry_run", "plan_path": str(plan_json)}

    # Lazy imports — only needed on the GPU machine.
    from datasets import load_dataset  # type: ignore
    from peft import LoraConfig, get_peft_model  # type: ignore
    from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore
    from trl import SFTConfig, SFTTrainer  # type: ignore

    tokenizer = AutoTokenizer.from_pretrained(plan.model_name, trust_remote_code=True)
    tokenizer.pad_token = tokenizer.eos_token
    model_kwargs = {"torch_dtype": "bfloat16"} if plan.bf16 else {}
    if plan.load_in_4bit:
        from transformers import BitsAndBytesConfig  # type: ignore
        model_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype="bfloat16",
            bnb_4bit_quant_type="nf4",
        )
    model = AutoModelForCausalLM.from_pretrained(plan.model_name, **model_kwargs)

    lora_cfg = LoraConfig(
        r=plan.lora_r,
        lora_alpha=plan.lora_alpha,
        lora_dropout=plan.lora_dropout,
        target_modules=plan.target_modules,
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_cfg)

    data = load_dataset("json", data_files={"train": str(plan.train_path), "val": str(plan.val_path)})
    sft_cfg = SFTConfig(
        output_dir=str(plan.out_dir),
        num_train_epochs=plan.epochs,
        learning_rate=plan.learning_rate,
        per_device_train_batch_size=plan.batch_size,
        gradient_accumulation_steps=plan.gradient_accumulation_steps,
        bf16=plan.bf16,
        max_seq_length=plan.max_seq_length,
    )
    trainer = SFTTrainer(
        model=model,
        args=sft_cfg,
        train_dataset=data["train"],
        eval_dataset=data["val"],
        tokenizer=tokenizer,
    )
    trainer.train()
    trainer.save_model(str(plan.out_dir / "adapter"))
    return {"status": "trained", "adapter_dir": str(plan.out_dir / "adapter")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    plan = load_plan(args.config, args.data_dir, args.out_dir)
    out = run_finetune(plan, dry_run=args.dry_run)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
