"""Regenerate every committed machine-readable audit result."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from lab_log_audit.reproduce import reproduce  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=REPOSITORY_ROOT / "data" / "raw")
    parser.add_argument("--manifest", type=Path, default=REPOSITORY_ROOT / "data" / "manifest.json")
    parser.add_argument("--results-dir", type=Path, default=REPOSITORY_ROOT / "results")
    parser.add_argument("--derived-dir", type=Path, default=REPOSITORY_ROOT / "data" / "derived")
    args = parser.parse_args()

    print("Verifying dataset archives...")
    metrics = reproduce(args.manifest, args.data_dir, args.results_dir, args.derived_dir)
    actions = metrics["chemspeed"]["actions"]
    batch = metrics["batch_distillation"]
    recoveries = batch["original_window"]
    null = batch["background_null"]["windows"][0]
    print("Dataset verified")
    print(f"Actions parsed: {actions['total']}")
    print(
        "Labelled recoveries matched: "
        f"{recoveries['matched_actions']} / {recoveries['total_actions']}"
    )
    print("Metrics regenerated")
    print("Sensitivity analysis regenerated")
    print(
        "Background null regenerated: observed "
        f"{null['observed_matched']} / {null['analysed_recoveries']} "
        f"= {null['observed_fraction']:.2%} vs random-anchor background "
        f"{null['expected_fraction']:.2%} "
        f"(ratio {null['ratio_observed_over_expected']:.2f}x, "
        f"empirical p = {null['empirical_p_value']:.4f})"
    )
    print("Figure regenerated")
    print(f"Results written to {args.results_dir}")
    print("Reproduction successful")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

