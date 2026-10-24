"""CLI for a deployable bearing Random Forest bundle."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ml.bearing.training import train_random_forest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--trees", type=int, default=None, help="override the nested-CV-selected tree count")
    parser.add_argument("--feature-version", choices=["axon-vibration-features-v1", "axon-vibration-features-v2"], default="axon-vibration-features-v1")
    args = parser.parse_args()
    print(json.dumps(train_random_forest(args.prepared, args.output, seed=args.seed, trees=args.trees, feature_version=args.feature_version), indent=2))


if __name__ == "__main__":
    main()
