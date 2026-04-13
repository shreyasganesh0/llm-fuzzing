"""Campaign config + dry-run trial smoke (plan §Phase 3 test_campaign)."""
from __future__ import annotations

from pathlib import Path

import yaml

from synthesis.scripts.run_fuzzing import (
    load_campaign_config,
    run_single_trial,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "synthesis" / "campaign_configs"


def test_all_campaign_configs_parse():
    for path in CONFIG_DIR.glob("*.yaml"):
        cfg = load_campaign_config(path, target="re2", binary=Path("/nonexistent/binary"))
        assert cfg.target == "re2"
        assert cfg.trials == 20
        assert cfg.duration_s == 82_800
        assert cfg.snapshot_interval_s == 900


def test_dry_run_trial_produces_stub_result(tmp_path):
    cfg = load_campaign_config(
        CONFIG_DIR / "llm_seeds.yaml",
        target="re2",
        binary=tmp_path / "missing-binary",
    )
    trial = run_single_trial(cfg, trial_index=3, work_dir=tmp_path / "work", dry_run=True)
    assert trial.trial_index == 3
    assert trial.seed == 1003
    assert trial.status == "ok"
    assert trial.snapshots == []


def test_campaign_config_names_match_filenames():
    for path in CONFIG_DIR.glob("*.yaml"):
        raw = yaml.safe_load(path.read_text())
        assert raw["name"] == path.stem, f"{path.name} has mismatched name={raw['name']}"
