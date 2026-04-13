"""Coverage-over-time curves with 95% CI (plan §analysis)."""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.dataset_schema import CampaignResult


def curves(result: CampaignResult) -> dict[int, dict]:
    """Return {elapsed_s: {mean, ci_low, ci_high, n}} across trials."""
    by_time: dict[int, list[int]] = defaultdict(list)
    for trial in result.trials:
        for snap in trial.snapshots:
            by_time[snap.elapsed_s].append(snap.edges_covered)

    summary: dict[int, dict] = {}
    for t, values in sorted(by_time.items()):
        n = len(values)
        mean = sum(values) / n
        if n > 1:
            var = sum((v - mean) ** 2 for v in values) / (n - 1)
            sem = math.sqrt(var / n)
            ci = 1.96 * sem
        else:
            ci = 0.0
        summary[t] = {"mean": mean, "ci_low": mean - ci, "ci_high": mean + ci, "n": n}
    return summary


def plot(curves_by_config: dict[str, dict], *, out_path: Path, title: str) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError as err:
        raise RuntimeError("matplotlib not installed; coverage plot unavailable") from err

    fig, ax = plt.subplots(figsize=(8, 5))
    for name, summary in curves_by_config.items():
        xs = sorted(summary.keys())
        means = [summary[t]["mean"] for t in xs]
        lows = [summary[t]["ci_low"] for t in xs]
        highs = [summary[t]["ci_high"] for t in xs]
        ax.plot(xs, means, label=name)
        ax.fill_between(xs, lows, highs, alpha=0.2)
    ax.set_xlabel("elapsed_s")
    ax.set_ylabel("edges_covered")
    ax.set_title(title)
    ax.legend()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight", dpi=150)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--results-root", type=Path, default=REPO_ROOT / "synthesis" / "results")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "analysis" / "figures")
    args = parser.parse_args()

    camp_dir = args.results_root / "campaigns" / args.target
    if not camp_dir.is_dir():
        print(f"no campaigns for {args.target}", file=sys.stderr)
        return 1

    curves_by_config: dict[str, dict] = {}
    for path in sorted(camp_dir.glob("*.json")):
        cr = CampaignResult.model_validate_json(path.read_text())
        curves_by_config[cr.config_name] = curves(cr)

    out_json = args.out / f"coverage_{args.target}.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(curves_by_config, indent=2))

    out_png = args.out / f"coverage_{args.target}.png"
    try:
        plot(curves_by_config, out_path=out_png, title=f"{args.target}: coverage over time")
        print(f"wrote {out_json} and {out_png}")
    except RuntimeError as exc:
        print(f"wrote {out_json}; plot skipped: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
