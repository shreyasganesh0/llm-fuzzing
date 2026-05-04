"""Per-target configuration registry.

One `TargetSpec` per fuzzing target carries everything that used to be
scattered across `core/config.py` constants (`HB_*`, `RE2_V2_*`) and the
two orchestrators (`scripts/run_ablation_{harfbuzz,re2}.py`). Adding a
third target (libxml2, sqlite3, ...) becomes a `TargetSpec` entry — no
new orchestrator fork.

Invariants:
- `coverage_binary` paths may have a baked-in DWARF source prefix.
  `source_roots` is a tuple so multiple candidate prefixes can be matched
  against the binary's DWARF — useful when a stale binary still references
  a pre-rearch path while a freshly built one uses the post-rearch path.
- `results_root` is load-bearing: downstream analysis scripts and
  `docs/experiment*.md` hardcode these paths.
- `fixtures_dir` holds the frozen M2 target branches
  (`m2_target_branches.json`) + upstream union profile. Do not
  recompute on the fly — regenerate via
  `analysis/scripts/freeze_target_branches.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class TargetSpec:
    name: str
    input_format: Literal["regex", "binary", "text"]
    coverage_binary: Path
    # One or more DWARF source-path prefixes. The coverage filter keeps any
    # file whose DWARF path startswith one of these; mismatches are dropped.
    # Multi-entry support exists so a stale binary (pre-rearch
    # `phase1_dataset/...` paths) and a freshly built one (current
    # `dataset/targets/src/...` paths) can both produce non-zero M1 with the
    # same `TargetSpec`.
    source_roots: tuple[Path, ...]
    fixtures_dir: Path
    prep_dataset_root: Path
    synthesis_results_root: Path
    results_root: Path
    # A few per-target knobs that used to be magic constants in each orchestrator.
    max_gaps_override: int | None = None   # HB caps at 30; RE2 uses len(shown)
    random_format_flag: str | None = None  # "regex" for RE2, None for HB

    @property
    def m2_targets_path(self) -> Path:
        return self.fixtures_dir / "m2_target_branches.json"

    @property
    def upstream_union_profile_path(self) -> Path:
        return self.fixtures_dir / "upstream_union_profile.json"

    @property
    def m2_smoke_log_path(self) -> Path:
        return self.fixtures_dir / "m2_smoke_log.json"

    @property
    def random_seeds_dir(self) -> Path:
        return self.synthesis_results_root / "seeds" / self.name / "random"

    def cell_seeds_dir(
        self, variant: str, model: str, *, strategy: str = "default",
    ) -> Path:
        """Per-cell seeds directory.

        For `strategy == "default"` the legacy layout is preserved
        (no strategy segment). Non-default strategies insert `<strategy>`
        immediately under `seeds/<target>/` so existing analysis scripts
        keep working unchanged.
        """
        safe_model = model.replace("/", "_")
        base = self.synthesis_results_root / "seeds" / self.name
        if strategy != "default":
            base = base / strategy
        return base / "ablation" / variant / safe_model

    def cell_m1_dir(
        self, variant: str, model: str, *, strategy: str = "default",
    ) -> Path:
        safe_model = model.replace("/", "_")
        base = self.results_root
        if strategy != "default":
            base = base / strategy
        return base / "m1" / variant / safe_model

    def cell_m2_dir(
        self, variant: str, model: str, *, strategy: str = "default",
    ) -> Path:
        safe_model = model.replace("/", "_")
        base = self.results_root
        if strategy != "default":
            base = base / strategy
        return base / "m2" / variant / safe_model


TARGETS: dict[str, TargetSpec] = {
    "re2": TargetSpec(
        name="re2",
        input_format="regex",
        coverage_binary=REPO_ROOT / "dataset/targets/src/re2/build/coverage/seed_replay",
        # Two prefixes so both the locally-checked-in binary (whose static
        # library was built when the repo lived under `phase1_dataset/`)
        # AND a freshly built binary (post-rearch `dataset/targets/src/`)
        # produce non-zero M1.
        source_roots=(
            REPO_ROOT / "dataset/targets/src/re2/upstream",
            REPO_ROOT / "phase1_dataset/targets/src/re2/upstream",
        ),
        fixtures_dir=REPO_ROOT / "dataset/fixtures/re2_ab_v2/re2",
        prep_dataset_root=REPO_ROOT / "dataset/fixtures/_ablation_re2_v2_dataset",
        synthesis_results_root=REPO_ROOT / "synthesis/results/ablation_re2_v2",
        results_root=REPO_ROOT / "results/ablation_re2_v2",
        max_gaps_override=None,   # RE2 uses len(targets["shown"])
        random_format_flag="regex",
    ),
    "harfbuzz": TargetSpec(
        name="harfbuzz",
        input_format="binary",
        coverage_binary=REPO_ROOT / "dataset/targets/src/harfbuzz/build/coverage/seed_replay",
        source_roots=(REPO_ROOT / "dataset/targets/src/harfbuzz/upstream/src",),
        fixtures_dir=REPO_ROOT / "dataset/fixtures/harfbuzz_ab/harfbuzz",
        prep_dataset_root=REPO_ROOT / "dataset/fixtures/_ablation_hb_dataset",
        synthesis_results_root=REPO_ROOT / "synthesis/results/ablation_harfbuzz",
        results_root=REPO_ROOT / "results/ablation_harfbuzz",
        max_gaps_override=30,   # HB hardcoded to 30 shown gaps
        random_format_flag=None,
    ),
}
