"""LoRA config files match plan §4.1b hyperparameters."""
from __future__ import annotations

from pathlib import Path

import yaml

from finetuning.scripts.finetune import load_plan

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "finetuning" / "configs"


def test_lora_8b_has_plan_fields():
    cfg = yaml.safe_load((CONFIG_DIR / "lora_8b.yaml").read_text())
    assert cfg["model_name"].endswith("8B-Instruct")
    assert cfg["lora"]["r"] == 16
    assert cfg["lora"]["alpha"] == 32
    assert cfg["training"]["epochs"] == 3
    assert abs(cfg["training"]["learning_rate"] - 2.0e-4) < 1e-9
    assert cfg["training"]["max_seq_length"] == 4096


def test_lora_70b_enables_4bit():
    cfg = yaml.safe_load((CONFIG_DIR / "lora_70b.yaml").read_text())
    assert cfg["model_name"].endswith("70B-Instruct")
    assert cfg["quantization"]["load_in_4bit"] is True
    assert cfg["quantization"]["bnb_4bit_quant_type"] == "nf4"


def test_load_plan_resolves_fields(tmp_path):
    plan = load_plan(
        CONFIG_DIR / "lora_8b.yaml",
        data_dir=tmp_path,
        out_dir=tmp_path / "out",
    )
    assert plan.lora_r == 16
    assert plan.epochs == 3
    assert plan.train_path == tmp_path / "train.jsonl"
    assert plan.val_path == tmp_path / "val.jsonl"


def test_stratify_by_target():
    for name in ("lora_8b.yaml", "lora_70b.yaml"):
        cfg = yaml.safe_load((CONFIG_DIR / name).read_text())
        assert cfg["data"]["stratify_by"] == "target"
