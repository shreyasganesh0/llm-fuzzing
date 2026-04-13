"""Experiment 2 campaign configs must parse and match FuzzBench constants
(plan §E2.5)."""
from __future__ import annotations

from pathlib import Path

import yaml

from synthesis.scripts.run_fuzzing import load_campaign_config

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "synthesis" / "campaign_configs"


def test_configs_parse():
    for path in CONFIG_DIR.glob("*.yaml"):
        cfg = load_campaign_config(path, target="re2", binary=Path("/missing"))
        assert cfg.trials == 20
        assert cfg.duration_s == 82_800
        assert cfg.snapshot_interval_s == 900


def test_config_names_match_filenames():
    for path in CONFIG_DIR.glob("*.yaml"):
        raw = yaml.safe_load(path.read_text())
        assert raw["name"] == path.stem, f"{path.name} name mismatch"


def test_exp2_configs_have_expected_names():
    names = {p.stem for p in CONFIG_DIR.glob("*.yaml")}
    assert {"source_only_llm_seeds", "source_only_combined"} <= names
