"""Shared ablation orchestrator.

`AblationRunner` consolidates ~85% of the logic that used to live in
`scripts/run_ablation_re2.py` and `scripts/run_ablation_harfbuzz.py`.
Per-target paths come from `core.targets.TARGETS`; per-model tuning
comes from `core.config.MODEL_DEFAULTS`; the variant design comes from
`core.variants.STANDARD_VARIANTS`.

Adding a third target is a `TargetSpec` entry + a ~30-line wrapper that
names its models and `SONNET_ONLY_VARIANTS`. No new phase code.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analysis.metrics import METRICS, Metric  # noqa: E402
from core.config import SOURCE_TOKEN_BUDGET_ALL_MODELS, defaults  # noqa: E402
from core.logging_config import get_logger  # noqa: E402
from core.prompt_strategies import (  # noqa: E402
    DEFAULT_STRATEGY_NAME,
    STRATEGIES,
    PromptStrategy,
    resolve_strategies,
)
from core.targets import TargetSpec  # noqa: E402
from core.variants import VARIANTS_BY_NAME, VariantSpec  # noqa: E402

PY = sys.executable
TARGET_SEEDS = 150           # research default (load-bearing); --num-seeds may override per-run
SAMPLES_PER_CALL = 1
MAX_ATTEMPTS_DEFAULT = 300
MAX_ATTEMPTS_CAPPED = 100    # for models that hit the UF output cap on binary
SUBPROCESS_TIMEOUT = 45
CONSEC_FAIL_WINDOW = 20      # early-exit: no seeds in last N batches (legacy default)

# Opt-in aggressive abandon policy (experiment5 cost protection). DEFAULT
# (env unset) is byte-identical to the legacy behaviour: no-gain window
# == CONSEC_FAIL_WINDOW (20), yield-ceiling guard OFF. Setting
# UTCF_ABANDON_NOGAIN switches on the aggressive policy (tighter window
# + a yield-ceiling guard that abandons, after a warmup, when the
# realised unique-seed rate provably cannot reach the target within
# MAX_ATTEMPTS — the true "lost cause" signal, e.g. cot_strict's
# diversity collapse). UTCF_ABANDON_WARMUP tunes the warmup (default 30).
ABANDON_NOGAIN_ENV = "UTCF_ABANDON_NOGAIN"
ABANDON_WARMUP_ENV = "UTCF_ABANDON_WARMUP"
DEFAULT_ABANDON_WARMUP = 30


def abandon_policy() -> tuple[int, bool, int | None]:
    """Resolve (no_gain_window, yield_ceiling_enabled, warmup) from env.

    Returns the legacy tuple ``(CONSEC_FAIL_WINDOW, False, None)`` when
    ``UTCF_ABANDON_NOGAIN`` is unset/blank/invalid — so every prior and
    non-opt-in experiment keeps byte-identical abort behaviour (the
    experiment2_1 baselines stay comparable). When set to a valid int it
    enables the aggressive policy.
    """
    raw = os.environ.get(ABANDON_NOGAIN_ENV)
    if raw is None or not raw.strip():
        return CONSEC_FAIL_WINDOW, False, None
    try:
        window = int(raw)
    except ValueError:
        return CONSEC_FAIL_WINDOW, False, None
    if window < 1:
        return CONSEC_FAIL_WINDOW, False, None
    try:
        warmup = int(os.environ.get(ABANDON_WARMUP_ENV, DEFAULT_ABANDON_WARMUP))
    except ValueError:
        warmup = DEFAULT_ABANDON_WARMUP
    return window, True, max(1, warmup)

LITELLM_URL = "https://api.ai.it.ufl.edu"
CLAUDE_KEY_PATH = REPO_ROOT / "secrets/claude_key"
CLAUDE_MODELS = frozenset({"claude-sonnet-4-6", "claude-haiku-4-5-20251001"})


class AblationRunner:
    """Drive the prep/random/synthesis/M1/M2 pipeline for one target."""

    def __init__(
        self,
        target: TargetSpec,
        variants: list[VariantSpec],
        models: list[str],
        *,
        sonnet_only_variants: set[str] | None = None,
        free_only: bool = True,
        orig_dataset_root: Path | None = None,
        strategies: list[PromptStrategy] | None = None,
    ) -> None:
        self.target = target
        self.variants = variants
        self.models = list(models)
        self.sonnet_only_variants = sonnet_only_variants or set()
        self.free_only = free_only
        self.orig_dataset_root = orig_dataset_root or REPO_ROOT / "dataset/data"
        if strategies is None:
            strategies = [STRATEGIES[DEFAULT_STRATEGY_NAME]]
        if not strategies:
            raise ValueError("AblationRunner.strategies must be non-empty")
        self.strategies = list(strategies)
        # Per-run seed target (150 = research default). --num-seeds overrides.
        self.num_seeds = TARGET_SEEDS
        self.logger = get_logger(f"utcf.ablation.{target.name}")

    # ── per-model tuning helpers ─────────────────────────────────────────

    def _inputs_per_call(self, model: str, variant: VariantSpec) -> int:
        """Compute inputs-per-call given model × target.input_format × variant.

        The UF LiteLLM proxy caps responses at 2048 chars. Binary targets
        (base64 blobs) hit that cap with 3 blobs/call for large-output
        models; text targets only hit it when the variant adds verbose
        gap reasoning.
        """
        d = defaults(model)
        if d.output_capped_on_binary:
            if self.target.input_format == "binary":
                return 1
            if variant.include_gaps:
                return 1
        return d.inputs_per_call

    def _max_attempts(self, model: str) -> int:
        d = defaults(model)
        if d.output_capped_on_binary and self.target.input_format == "binary":
            return MAX_ATTEMPTS_CAPPED
        return MAX_ATTEMPTS_DEFAULT

    def _env_for_model(
        self, model: str, *,
        cell_key: str | None = None,
    ) -> dict[str, str]:
        env = os.environ.copy()
        if defaults(model).provider == "anthropic":
            env["UTCF_ANTHROPIC_KEY_PATH"] = str(CLAUDE_KEY_PATH)
            env.pop("UTCF_LITELLM_URL", None)
        else:
            env["UTCF_LITELLM_URL"] = LITELLM_URL
            env.pop("UTCF_ANTHROPIC_KEY_PATH", None)
        if cell_key is not None:
            env["UTCF_BUDGET_CELL_KEY"] = cell_key
        return env

    def _count_seeds(self, seeds_dir: Path) -> int:
        if not seeds_dir.is_dir():
            return 0
        return sum(1 for p in seeds_dir.iterdir()
                   if p.is_file() and p.suffix == ".bin")

    def _subsample_seeds(
        self, seeds_dir: Path, target_count: int, rng_seed: int = 42,
    ) -> None:
        seed_files = sorted(p for p in seeds_dir.iterdir()
                            if p.is_file() and p.suffix == ".bin")
        if len(seed_files) <= target_count:
            return
        rng = random.Random(rng_seed)
        keep = set(p.name for p in rng.sample(seed_files, k=target_count))
        for p in seed_files:
            if p.name not in keep:
                p.unlink()
        self.logger.info("subsampled seeds", extra={
            "seeds_dir": str(seeds_dir), "kept": target_count,
            "removed": len(seed_files) - target_count,
        })

    def _cell_skipped_by_policy(
        self, model: str, variant: VariantSpec,
    ) -> str | None:
        if self.free_only and model in CLAUDE_MODELS:
            return "free-only mode"
        if (not self.free_only and model == "claude-sonnet-4-6"
                and variant.name not in self.sonnet_only_variants):
            return "sonnet reserved for targeted variants"
        return None

    # ── Phase: prep ──────────────────────────────────────────────────────

    def phase_prep(self) -> None:
        if not self.target.m2_targets_path.exists():
            raise FileNotFoundError(
                f"M2 targets not found at {self.target.m2_targets_path}. "
                f"Run: python -m analysis.scripts.freeze_target_branches "
                f"--target {self.target.name}"
            )
        targets = json.loads(self.target.m2_targets_path.read_text())
        shown = targets["shown"]

        target_dir = self.target.prep_dataset_root / self.target.name
        target_dir.mkdir(parents=True, exist_ok=True)

        for fn in ("tests.json", "metadata.json"):
            src = self.orig_dataset_root / self.target.name / fn
            dst = target_dir / fn
            if src.is_file() and not dst.is_file():
                shutil.copy2(src, dst)

        new_gaps = {
            "total_upstream_tests": targets.get("n_all_candidates", 0),
            "union_coverage_pct": 0.0,
            "gap_branches": [
                {
                    "file": s["file"],
                    "line": s["line"],
                    "code_context": s["code_context"],
                    "condition_description": s["condition_description"],
                    "uncovered_side": s.get("uncovered_side", "unknown"),
                    "reachability_score": None,
                }
                for s in shown
            ],
            "gap_functions": [],
            "per_test_unique_coverage": {},
            "coverage_overlap_matrix": {},
        }
        (target_dir / "coverage_gaps.json").write_text(
            json.dumps(new_gaps, indent=2),
        )
        self.logger.info("prep done", extra={
            "dataset_root": str(self.target.prep_dataset_root),
            "n_gaps_in_prompt": len(shown),
        })

    # ── Phase: synthesis ─────────────────────────────────────────────────

    def _run_synthesis_batch(
        self, variant: VariantSpec, model: str, sample_offset: int,
        *, strategy: PromptStrategy,
    ) -> int:
        d = defaults(model)
        max_gaps = self.target.max_gaps_override
        if max_gaps is None:
            targets = json.loads(self.target.m2_targets_path.read_text())
            max_gaps = len(targets["shown"])

        cmd = [
            PY, "-m", "synthesis.scripts.generate_ablation_inputs",
            "--target", self.target.name,
            "--model", model,
            "--cell", variant.name,
            "--dataset-root", str(self.target.prep_dataset_root),
            "--results-root", str(self.target.synthesis_results_root),
            "--samples", str(SAMPLES_PER_CALL),
            "--num-inputs", str(self._inputs_per_call(model, variant)),
            "--max-gaps", str(max_gaps),
            "--source-token-budget", str(SOURCE_TOKEN_BUDGET_ALL_MODELS),
            "--input-format", self.target.input_format,
            "--max-tokens", str(d.synthesis_max_tokens),
            "--run-id", str(sample_offset),
            "--strategy", strategy.name,
        ]
        if variant.include_tests:
            cmd.append("--include-tests")
        if variant.include_gaps:
            cmd.append("--include-gaps")
        if variant.include_source:
            cmd.append("--include-source")

        cell_key = f"{self.target.name}/{strategy.name}/{variant.name}/{model}"
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                env=self._env_for_model(model, cell_key=cell_key),
                timeout=SUBPROCESS_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"synthesis timed out for {variant.name}/{model} "
                f"(offset={sample_offset})"
            ) from exc
        if r.returncode != 0:
            raise RuntimeError(
                f"synthesis failed for {variant.name}/{model} "
                f"(offset={sample_offset}):\n{r.stderr[-2000:]}"
            )
        return self._count_seeds(
            self.target.cell_seeds_dir(variant.name, model, strategy=strategy.name)
        )

    def phase_synthesis(
        self, *, skip_existing: bool = False, attempt_offset: int = 0,
    ) -> None:
        for strategy in self.strategies:
            for variant in self.variants:
                for model in self.models:
                    reason = self._cell_skipped_by_policy(model, variant)
                    if reason:
                        self.logger.info(f"skip synthesis ({reason})", extra={
                            "variant": variant.name, "model": model,
                            "strategy": strategy.name,
                        })
                        continue

                    seeds_dir = self.target.cell_seeds_dir(
                        variant.name, model, strategy=strategy.name,
                    )
                    if skip_existing and self._count_seeds(seeds_dir) >= self.num_seeds:
                        self.logger.info(
                            "skip synthesis (already has enough seeds)",
                            extra={"variant": variant.name, "model": model,
                                   "strategy": strategy.name,
                                   "n_seeds": self._count_seeds(seeds_dir)},
                        )
                        self._subsample_seeds(seeds_dir, self.num_seeds)
                        continue

                    self._run_cell(
                        variant, model, strategy=strategy,
                        attempt_offset=attempt_offset,
                    )

    def _run_cell(
        self, variant: VariantSpec, model: str, *,
        attempt_offset: int, strategy: PromptStrategy,
    ) -> None:
        seeds_dir = self.target.cell_seeds_dir(
            variant.name, model, strategy=strategy.name,
        )
        seeds_dir.mkdir(parents=True, exist_ok=True)
        max_attempts = self._max_attempts(model)
        n_workers = defaults(model).worker_count

        attempt_counter = 0
        lock = threading.Lock()  # noqa: F841 — attempt_counter only touched by main
        recent_gains: list[bool] = []
        last_recorded_seeds = self._count_seeds(seeds_dir)

        # Abandon policy. window/yield-ceiling default to the LEGACY
        # behaviour unless UTCF_ABANDON_NOGAIN is set (see abandon_policy).
        nogain_window, yield_ceiling_on, warmup = abandon_policy()
        completed_attempts = 0
        n_no_gain = 0
        abandon_reason = "attempts_exhausted"  # overwritten below

        def _write_synthesis_stats(final_seeds: int, reason: str) -> None:
            """Always-on, behaviour-neutral per-cell yield artifact.

            Written into ``seeds_dir`` as ``_synthesis_stats.json``; it is
            invisible to every consumer (``_count_seeds`` /
            ``_subsample_seeds`` / ``measure_gap_coverage`` all filter to
            ``*.bin``), so this changes no seed count, subsample, metric,
            or cache. Dollars are intentionally NOT computed here
            (invariant 7 — the single pricing source is cost_audit /
            estimate_cost); we persist the orchestration facts
            (attempts, no-gain rate, calls/seed) so $ is derivable
            downstream without duplicating the pricing table.
            """
            stats = {
                "target": self.target.name,
                "model": model,
                "strategy": strategy.name,
                "cell": variant.name,
                "num_seeds_target": self.num_seeds,
                "final_seeds": final_seeds,
                "filled": final_seeds >= self.num_seeds,
                "abandoned": final_seeds < self.num_seeds,
                "reason": reason,
                "n_attempts_dispatched": attempt_counter,
                "n_attempts_completed": completed_attempts,
                "n_no_gain": n_no_gain,
                "no_gain_rate": (
                    n_no_gain / completed_attempts if completed_attempts else 0.0
                ),
                "strategy_calls_per_seed": strategy.n_calls_per_seed,
                "abandon_policy": {
                    "nogain_window": nogain_window,
                    "yield_ceiling_enabled": yield_ceiling_on,
                    "warmup": warmup,
                    "max_attempts": max_attempts,
                },
            }
            try:
                (seeds_dir / "_synthesis_stats.json").write_text(
                    json.dumps(stats, indent=2)
                )
            except OSError as exc:  # never let a stats write break a run
                self.logger.warning(
                    "synthesis_stats write failed", extra={
                        "variant": variant.name, "model": model,
                        "strategy": strategy.name, "error": str(exc)[:200],
                    },
                )

        def _submit_next(executor, futures):
            nonlocal attempt_counter
            attempt_counter += 1
            a = attempt_counter
            self.logger.info("synthesis batch", extra={
                "variant": variant.name, "model": model,
                "strategy": strategy.name, "attempt": a,
                "current_seeds": self._count_seeds(seeds_dir),
                "target": self.num_seeds,
            })
            fut = executor.submit(
                self._run_synthesis_batch, variant, model,
                a + attempt_offset, strategy=strategy,
            )
            futures[fut] = a

        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            futures: dict = {}
            for _ in range(min(n_workers, max_attempts)):
                _submit_next(executor, futures)

            while futures:
                done_fut = next(as_completed(futures))
                done_attempt = futures.pop(done_fut)
                try:
                    done_fut.result()
                except RuntimeError as e:
                    self.logger.error("synthesis batch failed", extra={
                        "variant": variant.name, "model": model,
                        "strategy": strategy.name,
                        "attempt": done_attempt, "error": str(e)[:500],
                    })

                current = self._count_seeds(seeds_dir)
                completed_attempts += 1
                gained = current > last_recorded_seeds
                if not gained:
                    n_no_gain += 1
                last_recorded_seeds = current
                recent_gains.append(gained)
                if len(recent_gains) > nogain_window:
                    recent_gains.pop(0)
                if (len(recent_gains) == nogain_window
                        and not any(recent_gains)):
                    self.logger.warning(
                        "early exit: no seeds in last %d attempts",
                        nogain_window,
                        extra={"variant": variant.name, "model": model,
                               "strategy": strategy.name,
                               "n_seeds": current, "n_attempts": attempt_counter},
                    )
                    abandon_reason = "nogain_window"
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

                # Opt-in yield-ceiling guard: after a warmup, if the
                # realised unique-seed acquisition rate provably cannot
                # reach the target within MAX_ATTEMPTS, this method is a
                # lost cause (e.g. cot_strict diversity collapse) — stop
                # bleeding budget. Disabled by default (env unset), so the
                # legacy path never reaches this branch.
                if (yield_ceiling_on and current < self.num_seeds
                        and attempt_counter >= (warmup or 0)):
                    rate = current / attempt_counter if attempt_counter else 0.0
                    projected = (
                        self.num_seeds / rate if rate > 0 else float("inf")
                    )
                    if projected > max_attempts:
                        self.logger.warning(
                            "early exit: yield-ceiling — projected %s attempts "
                            "to reach %d (> max %d) at rate %.3f",
                            ("inf" if projected == float("inf")
                             else f"{projected:.0f}"),
                            self.num_seeds, max_attempts, rate,
                            extra={"variant": variant.name, "model": model,
                                   "strategy": strategy.name,
                                   "n_seeds": current,
                                   "n_attempts": attempt_counter},
                        )
                        abandon_reason = "yield_ceiling"
                        executor.shutdown(wait=False, cancel_futures=True)
                        break

                if current >= self.num_seeds:
                    abandon_reason = "filled"
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

                if attempt_counter < max_attempts:
                    _submit_next(executor, futures)

        final_count = self._count_seeds(seeds_dir)
        if final_count < self.num_seeds:
            self.logger.warning(
                "synthesis capped: cell skipped (too many parse failures)",
                extra={"variant": variant.name, "model": model,
                       "strategy": strategy.name,
                       "n_seeds": final_count, "n_attempts": attempt_counter,
                       "max_attempts": max_attempts,
                       "abandon_reason": abandon_reason},
            )
            _write_synthesis_stats(final_count, abandon_reason)
            return

        self._subsample_seeds(seeds_dir, self.num_seeds)
        final_count = self._count_seeds(seeds_dir)
        self.logger.info("synthesis done", extra={
            "variant": variant.name, "model": model,
            "strategy": strategy.name,
            "n_seeds": final_count, "n_attempts": attempt_counter,
        })
        _write_synthesis_stats(final_count, "filled")
        assert final_count == self.num_seeds, (
            f"Expected {self.num_seeds} seeds, got {final_count}"
        )

    # ── Phase: random anchor ─────────────────────────────────────────────

    def phase_random(self, *, skip_existing: bool = False) -> None:
        random_dir = self.target.random_seeds_dir
        if skip_existing and self._count_seeds(random_dir) >= self.num_seeds:
            self.logger.info("skip random (exists)")
            self._subsample_seeds(random_dir, self.num_seeds)
            return
        random_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            PY, "-m", "synthesis.scripts.generate_random_inputs",
            "--target", self.target.name,
            "--count", str(self.num_seeds),
            "--seed", "42",
            "--results-root", str(self.target.synthesis_results_root),
        ]
        if self.target.random_format_flag:
            cmd += ["--input-format", self.target.random_format_flag]

        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"random anchor failed: {r.stderr[-2000:]}")
        self._subsample_seeds(random_dir, self.num_seeds)
        self.logger.info("random anchor done", extra={
            "dir": str(random_dir),
            "n_seeds": self._count_seeds(random_dir),
        })

    # ── Phase: metrics (M1, M2, future) ──────────────────────────────────

    def _metric_cell_out_dir(
        self, metric: Metric, variant: str, model: str,
        *, strategy: str = DEFAULT_STRATEGY_NAME,
    ) -> Path:
        safe_model = model.replace("/", "_")
        base = self.target.results_root
        if strategy != DEFAULT_STRATEGY_NAME:
            base = base / strategy
        return base / metric.results_subdir / variant / safe_model

    def phase_metric(self, metric: Metric, *, skip_existing: bool = False) -> None:
        for strategy in self.strategies:
            for variant in self.variants:
                for model in self.models:
                    seeds_dir = self.target.cell_seeds_dir(
                        variant.name, model, strategy=strategy.name,
                    )
                    out_dir = self._metric_cell_out_dir(
                        metric, variant.name, model, strategy=strategy.name,
                    )
                    if not seeds_dir.is_dir() or self._count_seeds(seeds_dir) == 0:
                        self.logger.warning(f"{metric.name} skip: no seeds", extra={
                            "variant": variant.name, "model": model,
                            "strategy": strategy.name,
                        })
                        continue
                    if skip_existing and (out_dir / "summary.json").is_file():
                        continue
                    self.logger.info(f"{metric.name} start", extra={
                        "variant": variant.name, "model": model,
                        "strategy": strategy.name,
                    })
                    metric.compute_cell(seeds_dir, self.target, out_dir)

        # Random anchor is strategy-independent: only computed once under
        # the legacy (no-strategy) path.
        random_dir = self.target.random_seeds_dir
        if self._count_seeds(random_dir) > 0:
            out_dir = self.target.results_root / metric.results_subdir / "random"
            if not (skip_existing and (out_dir / "summary.json").is_file()):
                metric.compute_cell(random_dir, self.target, out_dir)

    # Back-compat shims (phase_m1/phase_m2 still referenced by CLI dispatch).
    def phase_m1(self, *, skip_existing: bool = False) -> None:
        m = next(m for m in METRICS if m.name == "m1")
        self.phase_metric(m, skip_existing=skip_existing)

    def phase_m2(self, *, skip_existing: bool = False) -> None:
        m = next(m for m in METRICS if m.name == "m2")
        self.phase_metric(m, skip_existing=skip_existing)

    # ── CLI helpers ──────────────────────────────────────────────────────

    def _print_strategy_list(self) -> None:
        """Print one line per registered strategy to stdout.

        Columns: name, calls/seed, supports_tool_use, short description.
        Uses the strategy's `description` attribute if present; otherwise
        falls back to the class name.
        """
        for name in sorted(STRATEGIES):
            s = STRATEGIES[name]
            desc = getattr(s, "description", None) or type(s).__name__
            print(
                f"{s.name:<14} calls/seed={s.n_calls_per_seed}  "
                f"supports_tool_use={s.supports_tool_use}  {desc}"
            )

    def _resolve_variants(self, csv: str | None) -> list[VariantSpec]:
        """Filter `self.variants` by a CSV of variant names.

        Unknown names raise ValueError; empty filter preserves the full
        set. The order follows the original `self.variants` ordering
        (not the CLI argument order) so the matrix log stays stable.
        """
        if not csv:
            return list(self.variants)
        requested = {n.strip() for n in csv.split(",") if n.strip()}
        # Known = the standard 5-entry registry (VARIANTS_BY_NAME) — a
        # typo is a hard error regardless of whether the wrapper
        # restricted the runtime set to a subset.
        known = set(VARIANTS_BY_NAME.keys())
        unknown = requested - known
        if unknown:
            known_sorted = ", ".join(sorted(known))
            raise ValueError(
                f"unknown variant(s): {sorted(unknown)}. Known: {known_sorted}"
            )
        filtered = [v for v in self.variants if v.name in requested]
        if not filtered:
            raise ValueError(
                "--variants filter removed all configured variants "
                f"(configured={[v.name for v in self.variants]}, requested={sorted(requested)})"
            )
        return filtered

    def _compat_matrix(
        self, strategies: list[PromptStrategy], models: list[str],
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """Return (viable, incompatible) pairs for the cross product.

        A pair is incompatible iff the strategy requires tool-use and
        the model's `ModelDefaults.supports_tool_use` is False.
        """
        viable: list[tuple[str, str]] = []
        incompatible: list[tuple[str, str]] = []
        for s in strategies:
            for m in models:
                if s.supports_tool_use and not defaults(m).supports_tool_use:
                    incompatible.append((s.name, m))
                else:
                    viable.append((s.name, m))
        return viable, incompatible

    def _print_run_banner(
        self, variants: list[VariantSpec], models: list[str],
        strategies: list[PromptStrategy], phase: str, skip_existing: bool,
    ) -> None:
        phase_list = {
            "all": ["prep", "synthesis", "random", *[m.name for m in METRICS]],
            "prep": ["prep"], "synthesis": ["synthesis"], "random": ["random"],
        }.get(phase, [phase])
        n_cells = len(variants) * len(models) * len(strategies)
        est_calls = sum(
            s.n_calls_per_seed for s in strategies
        ) * len(variants) * len(models) * self.num_seeds
        print("=== Ablation run ===")
        print(f"Target:       {self.target.name}")
        print(f"Variants ({len(variants)}): "
              f"{', '.join(v.name for v in variants)}")
        print(f"Models ({len(models)}):   {', '.join(models)}")
        print(f"Strategies ({len(strategies)}): "
              f"{', '.join(s.name for s in strategies)}")
        print(f"Seeds/cell:   {self.num_seeds}")
        print(f"Phases:       {', '.join(phase_list)}")
        print(f"Skip-existing: {skip_existing}")
        print(f"Total cells:  {n_cells}   "
              f"({len(variants)} variants x {len(models)} models "
              f"x {len(strategies)} strategies)")
        print(f"Estimated LLM calls (upper bound): {est_calls}")

    # ── CLI ──────────────────────────────────────────────────────────────

    def main(self, argv: list[str] | None = None) -> int:
        metric_names = [m.name for m in METRICS]
        # Short-circuit: --list-strategies prints and exits before full parse
        # so the test + user path don't need to supply any other args.
        if argv is None:
            argv = sys.argv[1:]
        if "--list-strategies" in argv:
            self._print_strategy_list()
            return 0

        parser = argparse.ArgumentParser(
            description=f"Ablation orchestrator for {self.target.name}",
        )
        parser.add_argument(
            "--phase",
            choices=["prep", "synthesis", "random", "all", *metric_names],
            default="all",
        )
        parser.add_argument("--skip-existing", action="store_true")
        parser.add_argument(
            "--only-models", nargs="+", default=None, metavar="MODEL",
            help="restrict synthesis/metrics to these models",
        )
        parser.add_argument(
            "--attempt-offset", type=int, default=0, metavar="N",
            help="add N to every attempt's run_id to avoid cache collisions on restart",
        )
        parser.add_argument(
            "--strategy", default=None, metavar="NAME[,NAME...]",
            help=(
                "comma-separated prompt strategies to run "
                f"(default: {DEFAULT_STRATEGY_NAME}). "
                f"Known: {','.join(sorted(STRATEGIES))}"
            ),
        )
        parser.add_argument(
            "--variants", default=None, metavar="NAME[,NAME...]",
            help=(
                "comma-separated variant names to run (default: all "
                "configured). Unknown names cause a hard error."
            ),
        )
        parser.add_argument(
            "--num-seeds", type=int, default=TARGET_SEEDS, metavar="N",
            help=(
                f"seeds/cell target (default: {TARGET_SEEDS} — research "
                "default; do not override for publishable runs)."
            ),
        )
        parser.add_argument(
            "--list-strategies", action="store_true",
            help="print registered prompt strategies and exit",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help=(
                "print the strategy x variant x model matrix that would "
                "run and exit 0 without executing any phase"
            ),
        )
        args = parser.parse_args(argv)

        # (--list-strategies handled above; still supported here for
        # completeness if someone combines flags.)
        if args.list_strategies:
            self._print_strategy_list()
            return 0

        # --- Preflight: resolve strategies -----------------------------
        if args.strategy is not None:
            names = [n.strip() for n in args.strategy.split(",") if n.strip()]
            try:
                self.strategies = resolve_strategies(names)
            except ValueError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 1
            self.logger.info("strategy filter applied", extra={
                "strategies": [s.name for s in self.strategies],
            })

        # --- Preflight: variant filter ---------------------------------
        try:
            resolved_variants = self._resolve_variants(args.variants)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2

        # --- Preflight: num_seeds + warn if non-default ----------------
        if args.num_seeds < 1:
            print("ERROR: --num-seeds must be >= 1", file=sys.stderr)
            return 2
        self.num_seeds = args.num_seeds
        if self.num_seeds != TARGET_SEEDS:
            print(
                f"WARNING: --num-seeds={self.num_seeds} overrides the "
                f"{TARGET_SEEDS}-seed research default. Do not use for "
                "publishable runs.",
                file=sys.stderr,
            )

        # --- Preflight: model filter -----------------------------------
        if args.only_models:
            invalid = set(args.only_models) - set(self.models)
            if invalid:
                print(
                    f"ERROR: unknown models: {invalid}. Valid: {self.models}",
                    file=sys.stderr,
                )
                return 1
            self.models = list(args.only_models)
            self.logger.info("model filter applied", extra={"models": self.models})

        # --- Preflight: strategy/model compatibility matrix ------------
        viable, incompatible = self._compat_matrix(self.strategies, self.models)
        if incompatible:
            incompat_str = ", ".join(f"({s},{m})" for s, m in incompatible)
            if not viable:
                print(
                    "ERROR: no viable (strategy, model) cells to run. "
                    f"Incompatible: {incompat_str}. "
                    "Requested tool-use strategy on model(s) whose "
                    "ModelDefaults.supports_tool_use is False.",
                    file=sys.stderr,
                )
                return 2
            print(
                f"WARN: skipping incompatible cells: {incompat_str} "
                "(supports_tool_use=False on those models).",
                file=sys.stderr,
            )
            # Filter strategies/models down to those that appear in `viable`.
            viable_set = set(viable)
            self.strategies = [
                s for s in self.strategies
                if any((s.name, m) in viable_set for m in self.models)
            ]
            self.models = [
                m for m in self.models
                if any((s.name, m) in viable_set for s in self.strategies)
            ]

        # --- Preflight: banner + dry-run --------------------------------
        self.variants = resolved_variants
        self._print_run_banner(
            resolved_variants, self.models, self.strategies,
            args.phase, args.skip_existing,
        )
        if args.dry_run:
            return 0

        # --- Execute phases --------------------------------------------
        if args.phase in ("prep", "all"):
            self.phase_prep()
        if args.phase in ("random", "all"):
            self.phase_random(skip_existing=args.skip_existing)
        if args.phase in ("synthesis", "all"):
            self.phase_synthesis(
                skip_existing=args.skip_existing,
                attempt_offset=args.attempt_offset,
            )
        for metric in METRICS:
            if args.phase in (metric.name, "all"):
                self.phase_metric(metric, skip_existing=args.skip_existing)

        print(f"phase={args.phase} done")
        return 0
