"""Probe whether the LLM has memorised the upstream test suites (plan §1.10).

Three probes per (target, model) pair:
  - verbatim completion (first 3 lines -> let LLM finish, measure BLEU/edit-dist)
  - metadata recall (list tests in an upstream file without the code)
  - no-source prediction (predict coverage without source context)

Seed=123, decile-stratified sampling of 10 probe tests, drawn from the
held-out set (the Phase 2 held-out split also uses seed=42 — here we use 123
to avoid any correlation with Phase 2's random pick).

Contamination risk level:
  HIGH:   BLEU > 0.75 for > 50% of probes
  MEDIUM: BLEU > 0.75 for 20–50% of probes OR metadata_recall > 0.5
  LOW:    otherwise
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.dataset_schema import ContaminationReport, Test
from core.logging_config import get_logger

logger = get_logger("utcf.phase1.contam")

SEED = 123
N_PROBES = 10
BLEU_THRESHOLD = 0.75


def _load_tests(dataset_root: Path, target: str) -> list[Test]:
    path = dataset_root / target / "tests.json"
    if not path.is_file():
        raise FileNotFoundError(f"tests.json missing; run extract_tests.py first: {path}")
    return [Test.model_validate(t) for t in json.loads(path.read_text())]


def _stratified_sample(tests: list[Test], n: int, coverage_by_test: dict[str, float]) -> list[Test]:
    rng = random.Random(SEED)
    if not tests:
        return []
    deciles: dict[int, list[Test]] = defaultdict(list)
    for t in tests:
        pct = coverage_by_test.get(t.test_name, 0.0)
        bucket = min(int(pct // 10), 9)
        deciles[bucket].append(t)

    ordered_buckets = [deciles.get(i, []) for i in range(10)]
    picked: list[Test] = []
    # Round-robin across buckets, drawing one per bucket until we hit n.
    idx = 0
    safety = 1000
    while len(picked) < n and safety > 0:
        bucket = ordered_buckets[idx % 10]
        if bucket:
            choice = rng.choice(bucket)
            if choice not in picked:
                picked.append(choice)
        idx += 1
        safety -= 1

    # If we still don't have n (very small dataset), fill from the pool.
    if len(picked) < n:
        pool = [t for t in tests if t not in picked]
        rng.shuffle(pool)
        picked.extend(pool[: n - len(picked)])
    return picked[:n]


def _bleu(ref: str, hyp: str) -> float:
    try:
        from nltk.translate.bleu_score import SmoothingFunction, sentence_bleu
    except ImportError:
        logger.warning("nltk not installed; BLEU returns 0")
        return 0.0
    ref_tokens = ref.split()
    hyp_tokens = hyp.split()
    if not ref_tokens or not hyp_tokens:
        return 0.0
    smooth = SmoothingFunction().method1
    return float(sentence_bleu([ref_tokens], hyp_tokens, smoothing_function=smooth))


def _normalised_edit(ref: str, hyp: str) -> float:
    try:
        import Levenshtein
    except ImportError:
        logger.warning("Levenshtein not installed; edit distance returns 1.0")
        return 1.0
    if not ref:
        return 0.0 if not hyp else 1.0
    dist = Levenshtein.distance(ref, hyp)
    return dist / max(len(ref), 1)


def _first_n_lines(text: str, n: int) -> str:
    return "\n".join(text.splitlines()[:n])


def _classify(
    bleu_scores: list[float],
    metadata_recall: float,
) -> str:
    if not bleu_scores:
        return "LOW"
    high = sum(1 for b in bleu_scores if b > BLEU_THRESHOLD)
    frac = high / len(bleu_scores)
    if frac > 0.5:
        return "HIGH"
    if 0.2 <= frac <= 0.5 or metadata_recall > 0.5:
        return "MEDIUM"
    return "LOW"


def run_probe(
    target: str,
    model: str,
    *,
    dataset_root: Path,
    llm_client,
    max_tokens: int = 400,
) -> ContaminationReport:
    tests = _load_tests(dataset_root, target)

    coverage_by_test: dict[str, float] = {}
    tests_dir = dataset_root / target / "tests"
    if tests_dir.is_dir():
        for idx, t in enumerate(tests):
            cov_path = tests_dir / f"test_{idx:04d}" / "coverage.json"
            if cov_path.is_file():
                data = json.loads(cov_path.read_text())
                total = data.get("total_lines_in_source", 0)
                if total:
                    coverage_by_test[t.test_name] = 100.0 * data.get("total_lines_covered", 0) / total

    probes = _stratified_sample(tests, N_PROBES, coverage_by_test)

    bleu_scores: list[float] = []
    edit_distances: list[float] = []
    exact_matches = 0

    for probe in probes:
        prompt_prefix = _first_n_lines(probe.test_code, 3)
        resp = llm_client.complete(
            messages=[
                {"role": "system", "content": "You are completing C/C++ code verbatim."},
                {
                    "role": "user",
                    "content": (
                        "Complete this C/C++ test function exactly as it appears in the "
                        f"file {probe.upstream_file}. Respond with the code only, no prose.\n\n"
                        f"```cpp\n{prompt_prefix}\n```"
                    ),
                },
            ],
            model=model,
            temperature=0.0,
            top_p=1.0,
            max_tokens=max_tokens,
        )
        completion = resp.content
        bleu = _bleu(probe.test_code, completion)
        edit = _normalised_edit(probe.test_code, completion)
        bleu_scores.append(bleu)
        edit_distances.append(edit)
        if probe.test_code.strip() == completion.strip():
            exact_matches += 1

    # Probe 2: metadata recall.
    upstream_files = sorted({t.upstream_file for t in tests})
    metadata_recall = 0.0
    metadata_precision = 0.0
    if upstream_files:
        sample_file = upstream_files[0]
        expected = {t.test_name for t in tests if t.upstream_file == sample_file}
        resp = llm_client.complete(
            messages=[
                {"role": "system", "content": "You list known test function names."},
                {
                    "role": "user",
                    "content": (
                        f"List the names of all test functions defined in the file "
                        f"{sample_file} of the project upstream. Return one name per line, "
                        "no prose."
                    ),
                },
            ],
            model=model,
            temperature=0.0,
            top_p=1.0,
            max_tokens=512,
        )
        predicted = {line.strip() for line in resp.content.splitlines() if line.strip()}
        tp = len(predicted & expected)
        metadata_recall = tp / len(expected) if expected else 0.0
        metadata_precision = tp / len(predicted) if predicted else 0.0

    risk = _classify(bleu_scores, metadata_recall)

    report = ContaminationReport(
        target=target,
        model=model,
        verbatim_bleu_scores=bleu_scores,
        verbatim_exact_match_rate=exact_matches / max(len(probes), 1),
        verbatim_normalized_edit_distance=edit_distances,
        metadata_recall=metadata_recall,
        metadata_precision=metadata_precision,
        no_source_prediction_accuracy=0.0,  # wired later alongside Phase 2
        contamination_risk_level=risk,
        probe_test_names=[p.test_name for p in probes],
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--dataset-root", type=Path, default=REPO_ROOT / "dataset" / "dataset")
    args = parser.parse_args()

    from core.llm_client import LLMClient
    client = LLMClient()

    report = run_probe(args.target, args.model, dataset_root=args.dataset_root, llm_client=client)
    out = args.dataset_root / args.target / f"contamination_report.{args.model}.json"
    out.write_text(report.model_dump_json(indent=2))
    print(f"wrote {out} risk={report.contamination_risk_level}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
