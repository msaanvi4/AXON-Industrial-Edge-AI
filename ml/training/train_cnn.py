"""CLI for the compact optional TensorFlow 1D-CNN bearing classifier."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np

from ml.bearing.training import grouped_split, load_prepared
from ml.bearing.taxonomy import TAXONOMY_VERSION


def train_cnn(prepared: Path, output: Path, *, seed: int = 42, epochs: int = 30, batch_size: int = 32) -> dict:
    try:
        import tensorflow as tf
    except ImportError as exc:
        raise RuntimeError("1D CNN training requires optional TensorFlow. Install ml/requirements-bearing.txt on a supported host.") from exc
    signals, labels, groups, frequency = load_prepared(prepared)
    split = grouped_split(labels, groups, seed=seed)
    classes = np.unique(labels); class_to_index = {label: index for index, label in enumerate(classes)}
    if len(np.unique(labels[split["train"]])) < 2:
        raise ValueError("training partition needs at least two fault classes")
    mean, std = float(signals[split["train"]].mean()), float(signals[split["train"]].std())
    if std == 0: raise ValueError("training signals are degenerate")
    inputs = ((signals - mean) / std)[..., np.newaxis].astype(np.float32)
    targets = np.asarray([class_to_index[label] for label in labels])
    tf.keras.utils.set_random_seed(seed)
    model = tf.keras.Sequential([tf.keras.layers.Input((signals.shape[1], 1)), tf.keras.layers.Conv1D(16, 9, padding="same", activation="relu"), tf.keras.layers.MaxPool1D(4), tf.keras.layers.Conv1D(24, 7, padding="same", activation="relu"), tf.keras.layers.GlobalAveragePooling1D(), tf.keras.layers.Dense(len(classes), activation="softmax")])
    model.compile(optimizer=tf.keras.optimizers.Adam(1e-3), loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    callbacks = [tf.keras.callbacks.EarlyStopping(monitor="val_loss", patience=5, restore_best_weights=True)]
    model.fit(inputs[split["train"]], targets[split["train"]], validation_data=(inputs[split["validation"]], targets[split["validation"]]), epochs=epochs, batch_size=batch_size, verbose=0, callbacks=callbacks)
    probabilities = model.predict(inputs[split["test"]], verbose=0); predictions = classes[np.argmax(probabilities, axis=1)]
    from ml.bearing.training import _metrics, _sha256
    output.mkdir(parents=True, exist_ok=True); model_path = output / "model.keras"; model.save(model_path)
    metadata = {"bundle_schema_version": "axon-bearing-bundle-v1", "kind": "bearing_cnn", "model_version": "axon-bearing-cnn-v1", "created_at": datetime.now(timezone.utc).isoformat(), "taxonomy_version": TAXONOMY_VERSION, "input_contract": {"sensor_type": "vibration", "sampling_frequency_hz": frequency, "window_samples": int(signals.shape[1])}, "classes": classes.tolist(), "normalization": {"mean": mean, "std": std}, "training": {"seed": seed, "epochs_requested": epochs, "batch_size": batch_size, "tensorflow": tf.__version__}, "source": {"prepared_path": str(prepared), "sha256": _sha256(prepared)}, "artifact": {"filename": model_path.name, "sha256": _sha256(model_path)}, "onnx_export": {"status": "not_exported", "reason": "Install and validate tf2onnx separately; no conversion is claimed by this command."}}
    report = _metrics(labels[split["test"]], predictions, probabilities, classes) | {"evaluation_unit": "windows grouped by source recording"}
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8"); (output / "evaluation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return metadata | {"evaluation": report}


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--prepared", type=Path, required=True); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--seed", type=int, default=42); parser.add_argument("--epochs", type=int, default=30); parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args(); print(json.dumps(train_cnn(args.prepared, args.output, seed=args.seed, epochs=args.epochs, batch_size=args.batch_size), indent=2))


if __name__ == "__main__": main()
