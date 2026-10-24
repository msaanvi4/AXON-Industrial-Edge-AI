"""Train, calibrate, and persist an Axon Isolation Forest artifact."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import IsolationForest
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ml.data.dataset import chronological_split, load_csv
from ml.data.synthetic import FEATURES, write_synthetic_csv


def train(dataset_path, output_dir, contamination=0.02, seed=42, features=FEATURES):
    features = tuple(features)
    data = load_csv(dataset_path, features=features)
    train_data, validation_data, test_data = chronological_split(data)
    normal_train = train_data[train_data.get("label", 0) == 0]
    if len(normal_train) < 50:
        raise ValueError("at least 50 known-normal training records are required")
    pipeline = Pipeline(
        [("scaler", StandardScaler()), ("detector", IsolationForest(contamination=contamination, random_state=seed))]
    )
    pipeline.fit(normal_train.loc[:, features].to_numpy())
    calibration = validation_data[validation_data.get("label", 0) == 0]
    if calibration.empty:
        raise ValueError("validation partition has no known-normal records for calibration")
    # Inverted decision_function: larger means more anomalous. The 99th percentile targets 1% normal alerts.
    calibration_scores = -pipeline.decision_function(calibration.loc[:, features].to_numpy())
    threshold = float(np.quantile(calibration_scores, 0.99))
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, output_dir / "model.joblib")
    dataset_bytes = Path(dataset_path).read_bytes()
    metadata = {
        "model_name": "IsolationForest",
        "model_version": "axon-iforest-v1",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "features": list(features),
        "threshold": threshold,
        "scoring_convention": "anomaly_score = -decision_function; larger values are more anomalous",
        "calibration_method": "99th percentile of known-normal chronological validation records",
        "dataset": {"path": str(dataset_path), "sha256": hashlib.sha256(dataset_bytes).hexdigest(), "synthetic": "synthetic" in str(dataset_path).lower()},
        "random_seed": seed,
        "contamination": contamination,
        "split_rows": {"train": len(train_data), "validation": len(validation_data), "test": len(test_data)},
        "dependencies": {"python": platform.python_version(), "scikit_learn": sklearn.__version__},
    }
    (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return metadata


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("ml/data/synthetic_machine_data.csv"))
    parser.add_argument("--output", type=Path, default=Path("ml/artifacts/latest"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--contamination", type=float, default=0.02)
    parser.add_argument("--features", default=",".join(FEATURES), help="comma-separated numeric feature columns")
    parser.add_argument("--generate-synthetic", action="store_true")
    args = parser.parse_args()
    if args.generate_synthetic or not args.dataset.exists():
        args.dataset.parent.mkdir(parents=True, exist_ok=True)
        write_synthetic_csv(args.dataset, seed=args.seed)
    metadata = train(args.dataset, args.output, args.contamination, args.seed, tuple(args.features.split(",")))
    print(json.dumps({"model_version": metadata["model_version"], "threshold": metadata["threshold"]}, indent=2))


if __name__ == "__main__":
    main()
