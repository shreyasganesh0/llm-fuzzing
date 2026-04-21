"""Pydantic v2 schemas for all dataset + LLM + result objects.

Every schema uses `extra='forbid'` so typos/drift surface as validation errors.
Schemas intentionally mirror the JSON examples in
docs/research_document_v3.md §5.1 and docs/plan_v3.md §1, §2.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FrozenConfig = ConfigDict(extra="forbid", populate_by_name=True)


# ----------------------------------------------------------------------------
# Test objects (Phase 1 extraction)
# ----------------------------------------------------------------------------


class Test(BaseModel):
    """A single upstream unit test with mandatory provenance."""

    __test__ = False  # pytest collection hint — this is a schema, not a test case

    model_config = FrozenConfig

    test_name: str
    test_code: str
    test_file: str

    upstream_repo: str
    upstream_commit: str
    upstream_file: str
    upstream_line: int = Field(ge=1)

    framework: Literal[
        "googletest", "glib", "custom_c", "ctest", "tcl", "perl_tap"
    ]
    input_data: dict[str, Any] | str | None = None
    called_functions: list[str] = Field(default_factory=list)


# ----------------------------------------------------------------------------
# Coverage objects (Phase 1 measurement)
# ----------------------------------------------------------------------------


class BranchCoverage(BaseModel):
    model_config = FrozenConfig

    true_taken: bool = Field(alias="true")
    false_taken: bool = Field(alias="false")


class FileCoverage(BaseModel):
    model_config = FrozenConfig

    lines_covered: list[int] = Field(default_factory=list)
    lines_not_covered: list[int] = Field(default_factory=list)
    branches: dict[str, BranchCoverage] = Field(default_factory=dict)
    functions_covered: list[str] = Field(default_factory=list)
    functions_not_covered: list[str] = Field(default_factory=list)


class CoverageProfile(BaseModel):
    """Per-test coverage. Matches §5.1.2 schema exactly."""

    model_config = FrozenConfig

    test_name: str
    upstream_file: str
    upstream_line: int = Field(ge=1)
    framework: str

    files: dict[str, FileCoverage] = Field(default_factory=dict)

    total_lines_covered: int = 0
    total_lines_in_source: int = 0
    total_branches_covered: int = 0
    total_branches_in_source: int = 0

    status: Literal["ok", "crash", "timeout", "skipped"] = "ok"
    status_detail: str | None = None


# ----------------------------------------------------------------------------
# Coverage gaps (Phase 1 aggregation)
# ----------------------------------------------------------------------------


class GapBranch(BaseModel):
    model_config = FrozenConfig

    file: str
    line: int = Field(ge=1)
    code_context: str
    condition_description: str
    uncovered_side: str | None = None   # "true" or "false" — the specific branch side to hit
    reachability_score: float | None = None


class GapFunction(BaseModel):
    model_config = FrozenConfig

    file: str
    function: str


class CoverageGapsReport(BaseModel):
    """coverage_gaps.json — field names per §1.6 audit note."""

    model_config = FrozenConfig

    total_upstream_tests: int = Field(ge=0)
    union_coverage_pct: float = Field(ge=0.0, le=100.0)

    gap_branches: list[GapBranch] = Field(default_factory=list)
    gap_functions: list[GapFunction] = Field(default_factory=list)

    per_test_unique_coverage: dict[str, int] = Field(default_factory=dict)
    coverage_overlap_matrix: dict[str, dict[str, float]] = Field(default_factory=dict)


# ----------------------------------------------------------------------------
# Dataset entry (one row of the assembled dataset)
# ----------------------------------------------------------------------------


class DatasetEntry(BaseModel):
    model_config = FrozenConfig

    test: Test
    coverage: CoverageProfile
    source_files: list[str]


# ----------------------------------------------------------------------------
# Contamination probe (Phase 1.10)
# ----------------------------------------------------------------------------


class ContaminationReport(BaseModel):
    model_config = FrozenConfig

    target: str
    model: str

    verbatim_bleu_scores: list[float] = Field(default_factory=list)
    verbatim_exact_match_rate: float = 0.0
    verbatim_normalized_edit_distance: list[float] = Field(default_factory=list)

    metadata_recall: float = Field(ge=0.0, le=1.0, default=0.0)
    metadata_precision: float = Field(ge=0.0, le=1.0, default=0.0)

    no_source_prediction_accuracy: float = Field(ge=0.0, le=1.0, default=0.0)

    contamination_risk_level: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"

    probe_test_names: list[str] = Field(default_factory=list)


# ----------------------------------------------------------------------------
# LLM logging + responses
# ----------------------------------------------------------------------------


class PromptLogEntry(BaseModel):
    """Mandatory per-call log fields (plan §2.3)."""

    model_config = FrozenConfig

    model: str
    temperature: float
    top_p: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float
    prompt_hash: str
    timestamp: str
    generation_wall_clock_s: float

    target: str | None = None
    phase: str | None = None
    experiment_tag: str | None = None
    cached: bool = False


# ----------------------------------------------------------------------------
# Phase 2 predictions
# ----------------------------------------------------------------------------


class BranchPrediction(BaseModel):
    model_config = FrozenConfig

    location: str
    true_taken: bool
    false_taken: bool


class PredictionResult(BaseModel):
    """Parsed output of the primary coverage_prediction.j2 template."""

    model_config = ConfigDict(extra="ignore")

    functions_covered: list[str] = Field(default_factory=list)
    functions_not_covered: list[str] = Field(default_factory=list)
    branches: list[BranchPrediction] = Field(default_factory=list)
    estimated_line_coverage_pct: float = 0.0
    reasoning: str = ""


class PredictionRecord(BaseModel):
    """Wraps a PredictionResult with metadata for downstream evaluation."""

    model_config = FrozenConfig

    target: str
    model: str
    few_shot_count: int = Field(ge=0)
    context_size: Literal["function_only", "file", "multi_file"] = "file"
    prompt_variant: Literal["primary", "rephrase_a", "rephrase_b"] = "primary"

    test_name: str
    prediction: PredictionResult | None = None
    parse_status: Literal["ok", "parse_failure", "dry_run"] = "ok"
    raw_response: str = ""
    log: PromptLogEntry | None = None
    metrics: PredictionMetrics | None = None


# ----------------------------------------------------------------------------
# Prediction evaluation metrics (Phase 2.4)
# ----------------------------------------------------------------------------


class PredictionMetrics(BaseModel):
    model_config = FrozenConfig

    function_precision: float | None = None
    function_recall: float | None = None
    function_f1: float | None = None

    branch_precision: float | None = None
    branch_recall: float | None = None
    branch_f1: float | None = None

    coverage_mae: float | None = None
    spearman_rho: float | None = None
    spearman_p: float | None = None

    gap_closure_rate: float | None = None

    n_predictions: int = 0
    n_parse_failures: int = 0


# ----------------------------------------------------------------------------
# Phase 3 — gap-targeted input synthesis
# ----------------------------------------------------------------------------


class GeneratedInput(BaseModel):
    """One LLM-synthesised seed with full provenance back to the gap it targets."""

    model_config = FrozenConfig

    input_id: str
    content_b64: str  # base64 of raw bytes (libFuzzer-compatible)
    target_gaps: list[str] = Field(default_factory=list)  # ["file:line", ...]
    reasoning: str = ""

    source: Literal["llm", "random", "unittest", "fuzzbench"] = "llm"
    model: str | None = None
    temperature: float | None = None
    sample_index: int | None = None  # which of the 3 samples (plan §Phase 3)

    target: str
    experiment: Literal["exp1", "exp2"] = "exp1"


class SynthesisRecord(BaseModel):
    """One LLM synthesis call + extracted seeds."""

    model_config = FrozenConfig

    target: str
    model: str
    experiment: Literal["exp1", "exp2"] = "exp1"
    sample_index: int = 0

    inputs: list[GeneratedInput] = Field(default_factory=list)
    parse_status: Literal["ok", "parse_failure", "dry_run"] = "ok"
    raw_response: str = ""
    log: PromptLogEntry | None = None


class InputValidation(BaseModel):
    """Result of running a single generated input through the sanitizer build."""

    model_config = FrozenConfig

    input_id: str
    parsed: bool = True
    crashed: bool = False
    crash_signature: str | None = None
    stderr_tail: str = ""
    runtime_ms: float = 0.0


# ----------------------------------------------------------------------------
# Phase 3 — libFuzzer campaigns
# ----------------------------------------------------------------------------


class CampaignConfig(BaseModel):
    """Fuzzing campaign configuration (libFuzzer or AFL++)."""

    model_config = FrozenConfig

    name: Literal[
        "empty", "fuzzbench_seeds", "unittest_seeds",
        "llm_seeds", "combined_seeds", "random_seeds",
        "source_only_llm_seeds", "source_only_combined",
    ]
    target: str
    fuzzer_engine: Literal["libfuzzer", "aflpp"] = "libfuzzer"
    trials: int = Field(ge=1, default=20)
    duration_s: int = Field(ge=1, default=82_800)  # 23 hours
    rss_limit_mb: int = 2048
    timeout_s: int = 25
    max_len: int = 4096
    dictionary: str | None = None
    seed_corpus_dir: str | None = None
    libfuzzer_binary: str = ""
    afl_binary: str = ""
    snapshot_interval_s: int = 900  # 15 minutes


class CoverageSnapshot(BaseModel):
    model_config = FrozenConfig

    elapsed_s: int
    edges_covered: int
    features_covered: int
    corpus_size: int
    execs: int


class TrialResult(BaseModel):
    """One trial out of the 20 per (config, target)."""

    model_config = FrozenConfig

    config_name: str
    target: str
    trial_index: int = Field(ge=0)
    seed: int
    snapshots: list[CoverageSnapshot] = Field(default_factory=list)
    final_edges: int = 0
    final_execs: int = 0
    wall_clock_s: float = 0.0
    crashes: list[str] = Field(default_factory=list)
    status: Literal["ok", "timeout", "error"] = "ok"


class CampaignResult(BaseModel):
    """Aggregate across trials for one (config, target) cell."""

    model_config = FrozenConfig

    config_name: str
    target: str
    trials: list[TrialResult] = Field(default_factory=list)


class PairwiseComparison(BaseModel):
    """Mann-Whitney / Vargha-Delaney pairwise comparison (plan §Phase 3.8)."""

    model_config = FrozenConfig

    target: str
    config_a: str
    config_b: str
    metric: str
    mann_whitney_u: float
    mann_whitney_p: float
    vargha_delaney_a12: float
    effect_label: Literal["negligible", "small", "medium", "large"] = "negligible"
    significant_at_0_05: bool = False
    n_a: int = 0
    n_b: int = 0


class CrashRecord(BaseModel):
    """One deduplicated crash (stack hash + coverage profile, plan §3.6)."""

    model_config = FrozenConfig

    crash_id: str
    target: str
    config_name: str
    stack_hash: str
    coverage_profile_hash: str
    input_b64: str
    first_seen_trial: int
    first_seen_elapsed_s: float
    reproducer_path: str | None = None
    stderr_tail: str = ""


class FailureAnalysisReport(BaseModel):
    """Corpus-pollution + seed-survival evidence (plan §Phase 3.7)."""

    model_config = FrozenConfig

    target: str
    config_name: str
    hurts_vs_baseline: bool = False
    baseline_config: str = "unittest_seeds"
    mean_edges_diff: float = 0.0
    seed_survival_at_1h: float = 0.0
    seed_survival_at_23h: float = 0.0
    notes: str = ""


# ----------------------------------------------------------------------------
# Phase Transfer — LOO cross-target
# ----------------------------------------------------------------------------


class TransferRecord(BaseModel):
    model_config = FrozenConfig

    held_out_target: str
    source_targets: list[str]
    model: str
    mode: Literal["prediction", "synthesis"]
    records: list[PredictionRecord] = Field(default_factory=list)
    synthesis_records: list[SynthesisRecord] = Field(default_factory=list)


class TransferMatrix(BaseModel):
    model_config = FrozenConfig

    metric: str
    rows: list[str] = Field(default_factory=list)
    cols: list[str] = Field(default_factory=list)
    values: list[list[float]] = Field(default_factory=list)
    per_target_detail: dict[str, dict[str, float]] = Field(default_factory=dict)


# ----------------------------------------------------------------------------
# Experiment 2 — source-only synthesis
# ----------------------------------------------------------------------------


class ExperimentComparison(BaseModel):
    """Per-target Exp 1 vs Exp 2 outcome (plan §Experiment 2 + TV7)."""

    model_config = FrozenConfig

    target: str
    exp1_mean_edges: float = 0.0
    exp2_mean_edges: float = 0.0
    exp1_total_tokens: int = 0
    exp2_total_tokens: int = 0
    mann_whitney_p: float = 1.0
    vargha_delaney_a12: float = 0.5
    token_budget_delta_pct: float = 0.0
    outcome: Literal["A_test_conditioned_wins", "B_no_difference", "C_source_only_wins"] = "B_no_difference"


# Forward-reference resolution: PredictionRecord.metrics references
# PredictionMetrics which is declared after it. Rebuild once both are defined.
PredictionRecord.model_rebuild()
