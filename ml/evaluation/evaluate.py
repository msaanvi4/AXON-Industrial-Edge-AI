"""Evaluate a trained artifact on labeled CSV data without recalibrating it."""
import argparse
import json
from pathlib import Path

from sklearn.metrics import confusion_matrix, precision_recall_fscore_support

from ml.data.dataset import load_csv
from ml.inference.predictor import load_predictor


def evaluate(dataset_path, artifact_dir):
    predictor = load_predictor(artifact_dir)
    data = load_csv(dataset_path, features=predictor.features)
    if "label" not in data:
        raise ValueError("labeled evaluation requires a label column")
    scores = -predictor.pipeline.decision_function(data.loc[:, predictor.features].to_numpy())
    predicted = scores >= predictor.metadata["threshold"]
    precision, recall, f1, _ = precision_recall_fscore_support(data["label"], predicted, average="binary", zero_division=0)
    tn, fp, fn, tp = confusion_matrix(data["label"], predicted, labels=[0, 1]).ravel()
    note = "Synthetic-data metrics do not establish real-world fault performance." if predictor.metadata["dataset"].get("synthetic") else "This legacy anomaly-model evaluation does not establish bearing fault-location performance."
    return {"model_version": predictor.metadata["model_version"], "rows": len(data), "precision": precision, "recall": recall, "f1": f1, "confusion_matrix": {"tn":int(tn), "fp":int(fp), "fn":int(fn), "tp":int(tp)}, "note": note}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("ml/data/synthetic_machine_data.csv"))
    parser.add_argument("--artifact", type=Path, default=Path("ml/artifacts/latest"))
    parser.add_argument("--output", type=Path, default=Path("ml/artifacts/latest/evaluation.json"))
    args = parser.parse_args()
    report = evaluate(args.dataset, args.artifact)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__": main()
