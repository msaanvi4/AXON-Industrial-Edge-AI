import json
import importlib.util
from pathlib import Path

import numpy as np
import pytest

from ml.bearing.bundle import load_bearing_bundle
from ml.bearing.signal import engineered_features, segment_signal
from ml.bearing.training import train_random_forest
from ml.training.train_cnn import train_cnn
from ml.data.paderborn import prepare
from shared.diagnosis import diagnosis_from_probabilities


ROOT = Path(__file__).resolve().parents[1]


def _prepared_fixture(path: Path):
    rng = np.random.default_rng(4)
    signals, labels, groups = [], [], []
    samples = np.arange(4096) / 64000
    for label, frequency in (("healthy", 5), ("other_bearing_damage", 17)):
        for group in range(4):
            test_frequency = 500 if label == "healthy" else 3000
            signals.append(np.sin(2 * np.pi * test_frequency * samples) + rng.normal(0, 0.03, len(samples)))
            labels.append(label); groups.append(f"{label}-bearing-{group}")
    np.savez_compressed(path, signals=np.asarray(signals), labels=np.asarray(labels), groups=np.asarray(groups), sampling_frequency_hz=64000.0)


def test_signal_features_and_windows_are_finite():
    signal = np.sin(np.arange(128) / 4)
    windows = segment_signal(signal, 64, 64, 32)
    values = engineered_features(windows[0], 64)
    assert len(windows) == 3 and all(np.isfinite(value) for value in values.values())


def test_random_forest_bundle_uses_grouped_recordings():
    output = ROOT / "test-results" / "bearing-rf-test"; output.mkdir(parents=True, exist_ok=True)
    prepared = output / "prepared.npz"; _prepared_fixture(prepared)
    metadata = train_random_forest(prepared, output / "bundle", trees=20)
    for fold in metadata["split"]["folds"]:
        assert set(fold["train_groups"]).isdisjoint(fold["held_out_groups"])
    bundle = load_bearing_bundle(output / "bundle")
    probabilities = bundle.probabilities(np.sin(np.arange(4096) / 40), 64000)
    result = diagnosis_from_probabilities("demo-asset", "bearing-drive-end", probabilities, metadata["model_version"])
    assert result["fault_class"] in {"healthy", "other_bearing_damage"}
    with pytest.raises(ValueError, match="sampling frequency"):
        bundle.probabilities(np.zeros(4096), 32000)


def test_paderborn_preparation_excludes_unmapped_files_without_guessing():
    output = ROOT / "test-results" / "paderborn-empty"; raw = output / "raw"; raw.mkdir(parents=True, exist_ok=True)
    config = {"dataset_dir": str(raw), "output_dir": str(output / "prepared"), "license_id": "CC-BY-NC-4.0", "source_url": "https://example.invalid", "sampling_frequency_hz": 64000, "window_samples": 64, "window_overlap": 32, "sensor_key_candidates": ["vibration"], "label_map": {}}
    config_path = output / "config.json"; config_path.write_text(json.dumps(config))
    manifest = prepare(config_path)
    assert manifest["prepared_windows"] == 0 and not manifest["training_ready"]


def test_cnn_path_is_honest_about_missing_optional_tensorflow():
    if importlib.util.find_spec("tensorflow") is None:
        with pytest.raises(RuntimeError, match="TensorFlow"):
            train_cnn(Path("not-used.npz"), ROOT / "test-results" / "cnn")
