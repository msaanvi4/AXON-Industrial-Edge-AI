from pathlib import Path

from ml.data.synthetic import generate_dataset
from ml.inference.predictor import load_predictor
from ml.training.train import train


def test_artifact_training_and_fault_inference():
    output = Path("test-results/ml-artifact")
    output.mkdir(parents=True, exist_ok=True)
    dataset = output / "synthetic.csv"
    generate_dataset(rows=600, seed=7).to_csv(dataset, index=False)
    train(dataset, output / "artifact", seed=7)
    predictor = load_predictor(output / "artifact")
    assert not predictor.predict_one({"temp": 75, "vibration": 0.3, "current": 4.5})["is_anomaly"]
    assert predictor.predict_one({"temp": 100, "vibration": 0.9, "current": 7.0})["is_anomaly"]
