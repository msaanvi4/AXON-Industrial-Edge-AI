"""Leakage-resistant physical-bearing cross-validation and model bundling."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import time

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import balanced_accuracy_score, classification_report, confusion_matrix, f1_score, log_loss
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import Pipeline

from ml.bearing.signal import FEATURE_VERSION, LEGACY_FEATURE_VERSION, feature_names, feature_vector, legacy_feature_names, legacy_feature_vector, validate_signal
from ml.bearing.taxonomy import TAXONOMY_VERSION


def load_prepared(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    data = np.load(path, allow_pickle=False)
    required = {"signals", "labels", "groups", "sampling_frequency_hz"}
    if not required.issubset(data.files):
        raise ValueError("prepared data is missing signals, labels, groups, or sampling_frequency_hz")
    signals, labels, groups = data["signals"], data["labels"].astype(str), data["groups"].astype(str)
    if signals.ndim != 2 or not len(signals) or len(signals) != len(labels) or len(labels) != len(groups):
        raise ValueError("prepared data arrays have incompatible shapes")
    sampling_frequency = float(data["sampling_frequency_hz"])
    for signal in signals:
        validate_signal(signal, sampling_frequency, minimum_samples=signals.shape[1])
    for group in np.unique(groups):
        if len(np.unique(labels[groups == group])) != 1:
            raise ValueError(f"physical bearing {group!r} has conflicting labels")
    return signals, labels, groups, sampling_frequency


def grouped_split(labels: np.ndarray, groups: np.ndarray, *, seed: int = 42) -> dict[str, np.ndarray]:
    """Legacy single split helper. New training uses nested grouped CV below."""
    if len(labels) != len(groups):
        raise ValueError("labels and groups must have the same length")
    group_labels: dict[str, str] = {}
    for group in np.unique(groups):
        labels_for_group = np.unique(labels[groups == group])
        if len(labels_for_group) != 1:
            raise ValueError(f"physical-bearing group {group!r} has conflicting labels")
        group_labels[str(group)] = str(labels_for_group[0])
    by_class: dict[str, list[str]] = {}
    for group, label in group_labels.items():
        by_class.setdefault(label, []).append(group)
    inadequate = {label: len(items) for label, items in by_class.items() if len(items) < 3}
    if inadequate:
        details = ", ".join(f"{label}={count}" for label, count in sorted(inadequate.items()))
        raise ValueError("unseen-bearing evaluation requires at least three physical bearings per class; found " + details)
    rng = np.random.default_rng(seed)
    selected: dict[str, list[str]] = {"train": [], "validation": [], "test": []}
    for label in sorted(by_class):
        candidates = np.asarray(sorted(by_class[label]), dtype=str)
        rng.shuffle(candidates)
        selected["test"].append(candidates[0])
        selected["validation"].append(candidates[1])
        selected["train"].extend(candidates[2:].tolist())
    return {name: np.flatnonzero(np.isin(groups, chosen)) for name, chosen in selected.items()}


def _metrics(true: np.ndarray, predicted: np.ndarray, probabilities: np.ndarray, classes: np.ndarray) -> dict:
    return {
        "accuracy": float(np.mean(true == predicted)),
        "macro_f1": float(f1_score(true, predicted, average="macro", zero_division=0)),
        "balanced_accuracy": float(balanced_accuracy_score(true, predicted)),
        "classification_report": classification_report(true, predicted, labels=classes, output_dict=True, zero_division=0),
        "confusion_matrix": confusion_matrix(true, predicted, labels=classes).tolist(),
        "classes": classes.tolist(),
        "log_loss": float(log_loss(true, probabilities, labels=classes)),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _group_equal_weights(labels: np.ndarray, groups: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """Give each physical bearing equal total weight within its true class."""
    local_labels, local_groups = labels[indices], groups[indices]
    group_sizes = Counter(local_groups.tolist())
    group_class = {group: str(np.unique(local_labels[local_groups == group])[0]) for group in group_sizes}
    groups_per_class = Counter(group_class.values())
    weights = np.asarray([
        1.0 / (groups_per_class[group_class[group]] * group_sizes[group])
        for group in local_groups
    ], dtype=np.float64)
    return weights * (len(weights) / weights.sum())


def _group_balanced_accuracy(true: np.ndarray, predicted: np.ndarray, groups: np.ndarray) -> float:
    per_class: dict[str, list[float]] = {}
    for group in np.unique(groups):
        mask = groups == group
        label = str(np.unique(true[mask])[0])
        per_class.setdefault(label, []).append(float(np.mean(predicted[mask] == true[mask])))
    return float(np.mean([np.mean(scores) for scores in per_class.values()]))


def _candidate_specs() -> list[dict]:
    # Candidate 0 reproduces the previous RF settings. The other two test
    # bearing-balanced training plus modest tree regularization.
    return [
        {"name": "rf_previous_baseline", "trees": 200, "max_features": "sqrt", "min_samples_leaf": 1, "max_samples": None, "group_balanced": False, "class_weight": "balanced_subsample"},
        {"name": "rf_group_balanced_leaf2", "trees": 300, "max_features": "sqrt", "min_samples_leaf": 2, "max_samples": 0.8, "group_balanced": True, "class_weight": None},
        {"name": "rf_group_balanced_leaf4", "trees": 300, "max_features": 0.75, "min_samples_leaf": 4, "max_samples": 0.8, "group_balanced": True, "class_weight": None},
        {"name": "rf_group_balanced_healthy15", "trees": 300, "max_features": "sqrt", "min_samples_leaf": 2, "max_samples": 0.8, "group_balanced": True, "class_weight": {"healthy": 1.5, "other_bearing_damage": 1.0}},
        {"name": "rf_group_balanced_healthy20", "trees": 300, "max_features": "sqrt", "min_samples_leaf": 2, "max_samples": 0.8, "group_balanced": True, "class_weight": {"healthy": 2.0, "other_bearing_damage": 1.0}},
    ]


def _build_model(spec: dict, seed: int) -> Pipeline:
    classifier = RandomForestClassifier(
        n_estimators=spec["trees"], max_features=spec["max_features"],
        min_samples_leaf=spec["min_samples_leaf"], max_samples=spec["max_samples"],
        class_weight=spec["class_weight"], random_state=seed, n_jobs=-1,
    )
    return Pipeline([("classifier", classifier)])


def _fit(model: Pipeline, features: np.ndarray, labels: np.ndarray, groups: np.ndarray, indices: np.ndarray, spec: dict) -> Pipeline:
    fit_params = {}
    if spec["group_balanced"]:
        fit_params["classifier__sample_weight"] = _group_equal_weights(labels, groups, indices)
    model.fit(features[indices], labels[indices], **fit_params)
    return model


def _nested_group_cv(features: np.ndarray, labels: np.ndarray, groups: np.ndarray, *, seed: int) -> tuple[np.ndarray, np.ndarray, dict, dict]:
    group_class: dict[str, str] = {}
    for group in np.unique(groups):
        classes_for_group = np.unique(labels[groups == group])
        if len(classes_for_group) != 1:
            raise ValueError(f"physical bearing {group!r} has conflicting labels")
        group_class[str(group)] = str(classes_for_group[0])
    groups_per_class = Counter(group_class.values())
    outer_count = min(5, min(groups_per_class.values()))
    if outer_count < 3:
        raise ValueError("nested unseen-bearing evaluation requires at least three physical bearings per class")
    candidates = _candidate_specs()
    classes = np.unique(labels)
    oof_probabilities = np.full((len(labels), len(classes)), np.nan, dtype=np.float64)
    oof_predictions = np.empty(len(labels), dtype=object)
    oof_predictions[:] = None
    outer = StratifiedGroupKFold(n_splits=outer_count, shuffle=True, random_state=seed)
    folds, selected_names = [], []

    for fold_number, (outer_train, outer_test) in enumerate(outer.split(features, labels, groups), start=1):
        print(f"Outer fold {fold_number}/{outer_count}: {len(np.unique(groups[outer_train]))} train bearings, {len(np.unique(groups[outer_test]))} held out", flush=True)
        if set(groups[outer_train]) & set(groups[outer_test]):
            raise AssertionError("physical bearing leaked across outer fold")
        inner_group_counts = Counter(group_class[group] for group in np.unique(groups[outer_train]))
        inner_count = min(3, min(inner_group_counts.values()))
        if inner_count < 2:
            raise ValueError("outer training fold has too few physical bearings for inner model selection")
        inner = StratifiedGroupKFold(n_splits=inner_count, shuffle=True, random_state=seed + fold_number)
        candidate_results = []
        for spec in candidates:
            scores = []
            for inner_train_rel, inner_valid_rel in inner.split(features[outer_train], labels[outer_train], groups[outer_train]):
                inner_train, inner_valid = outer_train[inner_train_rel], outer_train[inner_valid_rel]
                if set(groups[inner_train]) & set(groups[inner_valid]):
                    raise AssertionError("physical bearing leaked across inner fold")
                model = _fit(_build_model(spec, seed + fold_number), features, labels, groups, inner_train, spec)
                pred = model.predict(features[inner_valid])
                scores.append(_group_balanced_accuracy(labels[inner_valid], pred, groups[inner_valid]))
            candidate_results.append({"name": spec["name"], "inner_group_balanced_accuracy": float(np.mean(scores)), "inner_fold_scores": scores})
            print(f"  {spec['name']}: inner group-balanced accuracy={np.mean(scores):.4f}", flush=True)
        candidate_results.sort(key=lambda result: (result["inner_group_balanced_accuracy"], result["name"]), reverse=True)
        selected = next(spec for spec in candidates if spec["name"] == candidate_results[0]["name"])
        model = _fit(_build_model(selected, seed + 1000 + fold_number), features, labels, groups, outer_train, selected)
        probs = model.predict_proba(features[outer_test])
        pred = model.predict(features[outer_test])
        oof_probabilities[outer_test] = probs
        oof_predictions[outer_test] = pred
        selected_names.append(selected["name"])
        folds.append({
            "fold": fold_number,
            "train_groups": sorted(np.unique(groups[outer_train]).tolist()),
            "held_out_groups": sorted(np.unique(groups[outer_test]).tolist()),
            "train_windows": int(len(outer_train)),
            "held_out_windows": int(len(outer_test)),
            "selected_model": selected["name"],
            "inner_selection": candidate_results,
            "group_balanced_accuracy": _group_balanced_accuracy(labels[outer_test], pred, groups[outer_test]),
        })
    if np.isnan(oof_probabilities).any() or any(item is None for item in oof_predictions):
        raise AssertionError("cross-validation did not produce exactly one held-out prediction per window")
    selection_counts = Counter(selected_names)
    selection_scores: dict[str, list[float]] = {}
    for fold in folds:
        for result in fold["inner_selection"]:
            selection_scores.setdefault(result["name"], []).append(result["inner_group_balanced_accuracy"])
    final_name = max(candidates, key=lambda spec: (selection_counts[spec["name"]], np.mean(selection_scores[spec["name"]]), spec["name"]))["name"]
    summary = {"strategy": "nested_stratified_group_k_fold", "group": "physical_bearing_id", "outer_folds": outer_count, "inner_folds_max": 3, "folds": folds, "selected_final_model": final_name, "selection_frequency": dict(selection_counts), "mean_inner_group_balanced_accuracy": {name: float(np.mean(values)) for name, values in selection_scores.items()}}
    return oof_predictions.astype(str), oof_probabilities, summary, next(spec for spec in candidates if spec["name"] == final_name)


def _per_bearing_report(true: np.ndarray, pred: np.ndarray, probs: np.ndarray, groups: np.ndarray) -> dict:
    report = {}
    for group in sorted(np.unique(groups)):
        mask = groups == group
        label = str(np.unique(true[mask])[0])
        report[str(group)] = {
            "true_class": label,
            "windows": int(mask.sum()),
            "window_accuracy": float(np.mean(pred[mask] == true[mask])),
            "mean_damage_probability": float(np.mean(probs[mask, 1])),
            "majority_vote_prediction": str(np.unique(pred[mask], return_counts=True)[0][np.argmax(np.unique(pred[mask], return_counts=True)[1])]),
        }
    classes = sorted({value["true_class"] for value in report.values()})
    return {"bearings": report, "mean_window_accuracy_by_class": {label: float(np.mean([v["window_accuracy"] for v in report.values() if v["true_class"] == label])) for label in classes}, "group_balanced_accuracy": _group_balanced_accuracy(true, pred, groups)}


def _fixed_baseline_group_cv(features: np.ndarray, labels: np.ndarray, groups: np.ndarray, *, seed: int) -> dict:
    """Evaluate the original RF settings on the same outer folds as nested CV."""
    group_labels = {str(group): str(np.unique(labels[groups == group])[0]) for group in np.unique(groups)}
    group_counts = Counter(group_labels.values())
    folds = min(5, min(group_counts.values()))
    splitter = StratifiedGroupKFold(n_splits=folds, shuffle=True, random_state=seed)
    classes = np.unique(labels)
    predictions = np.empty(len(labels), dtype=object)
    predictions[:] = None
    probabilities = np.full((len(labels), len(classes)), np.nan, dtype=np.float64)
    baseline = _candidate_specs()[0]
    fold_records = []
    for fold_number, (train, test) in enumerate(splitter.split(features, labels, groups), start=1):
        print(f"Fixed RF baseline fold {fold_number}/{folds}", flush=True)
        if set(groups[train]) & set(groups[test]):
            raise AssertionError("physical bearing leaked across baseline CV fold")
        model = _fit(_build_model(baseline, seed + 2000 + fold_number), features, labels, groups, train, baseline)
        probabilities[test] = model.predict_proba(features[test])
        predictions[test] = model.predict(features[test])
        fold_records.append({"fold": fold_number, "held_out_groups": sorted(np.unique(groups[test]).tolist()), "group_balanced_accuracy": _group_balanced_accuracy(labels[test], predictions[test].astype(str), groups[test])})
    if np.isnan(probabilities).any() or any(value is None for value in predictions):
        raise AssertionError("baseline CV did not produce one prediction per window")
    return _metrics(labels, predictions.astype(str), probabilities, classes) | {
        "per_bearing": _per_bearing_report(labels, predictions.astype(str), probabilities, groups),
        "folds": fold_records,
        "evaluation_unit": "same nested-CV outer physical-bearing folds; previous Random Forest settings",
    }


def _operational_policy(true: np.ndarray, probs: np.ndarray, classes: np.ndarray, threshold: float = 0.70) -> dict:
    predictions = classes[np.argmax(probs, axis=1)]
    confidence = probs.max(axis=1)
    decided = confidence >= threshold
    damaged = true == "other_bearing_damage"
    damage_index = int(np.flatnonzero(classes == "other_bearing_damage")[0])
    damage_recall = float(np.mean((predictions[damaged] == "other_bearing_damage") & decided[damaged])) if damaged.any() else 0.0
    return {"minimum_probability": threshold, "decisive_coverage": float(np.mean(decided)), "uncertain_rate": float(np.mean(~decided)), "accuracy_on_decisive_windows": float(np.mean(predictions[decided] == true[decided])) if decided.any() else None, "damaged_windows_returning_fault_suspected": damage_recall, "note": "Matches AXON's current 0.70 minimum-probability uncertainty policy; probabilities remain uncalibrated."}


def train_random_forest(prepared_path: Path, output_dir: Path, *, seed: int = 42, trees: int | None = None, feature_version: str = LEGACY_FEATURE_VERSION) -> dict:
    started = time.perf_counter()
    signals, labels, groups, sampling_frequency = load_prepared(prepared_path)
    if signals.shape[1] != 4096 or not np.isclose(sampling_frequency, 64000):
        raise ValueError("deployable AXON bearing training requires exactly 4,096 samples at 64,000 Hz")
    unique_groups = np.unique(groups)
    class_group_counts = {label: int(len(np.unique(groups[labels == label]))) for label in np.unique(labels)}
    if len(np.unique(labels)) != 2 or set(np.unique(labels)) != {"healthy", "other_bearing_damage"}:
        raise ValueError("AXON's active edge model requires the documented binary classes healthy/other_bearing_damage")
    if min(class_group_counts.values()) < 3:
        raise ValueError("unseen-bearing evaluation requires at least three physical bearings per class")

    # Compute the existing edge feature contract once; no training-fold
    # statistics or adjacent windows can cross a physical-bearing boundary.
    if feature_version == LEGACY_FEATURE_VERSION:
        selected_feature_names, vectorizer = legacy_feature_names(), legacy_feature_vector
    elif feature_version == FEATURE_VERSION:
        selected_feature_names, vectorizer = feature_names(), feature_vector
    else:
        raise ValueError(f"unsupported feature version: {feature_version}")
    print(f"Extracting {len(signals)} windows with {feature_version}", flush=True)
    features = np.asarray([vectorizer(window, sampling_frequency) for window in signals], dtype=np.float32)
    print("Running nested physical-bearing cross-validation", flush=True)
    predictions, probabilities, cv, selected = _nested_group_cv(features, labels, groups, seed=seed)
    classes = np.unique(labels)
    cv_metrics = _metrics(labels, predictions, probabilities, classes)
    cv_metrics["per_bearing"] = _per_bearing_report(labels, predictions, probabilities, groups)
    cv_metrics["operational_policy_at_0_70"] = _operational_policy(labels, probabilities, classes)
    cv_metrics["evaluation_unit"] = "nested out-of-fold predictions; all windows from each physical bearing held out together"
    cv_metrics["interpretation"] = "Development cross-validation across the supplied Paderborn bearings; this is not independent field validation."
    baseline_cv_metrics = _fixed_baseline_group_cv(features, labels, groups, seed=seed)

    # The previous artifact's old test bearings remain fully unseen to that
    # artifact. Re-evaluate it on these same recordings for a limited baseline.
    previous_bundle_dir = Path(__file__).resolve().parents[1] / "artifacts" / "bearing" / "active"
    previous_comparison = None
    try:
        from ml.bearing.bundle import load_bearing_bundle
        previous = load_bearing_bundle(previous_bundle_dir)
        prior_test_mask = np.isin(groups, ["K002", "K003", "KI04", "KI14"])
        if prior_test_mask.any():
            old_probs = np.asarray([list(previous.probabilities(window, sampling_frequency).values()) for window in signals[prior_test_mask]])
            old_classes = np.asarray(list(previous.model.named_steps["classifier"].classes_))
            old_predictions = old_classes[np.argmax(old_probs, axis=1)]
            # Align columns to the class ordering used by this prepared set.
            old_probs_aligned = np.column_stack([old_probs[:, list(old_classes).index(label)] for label in classes])
            previous_comparison = {
                "bearings": sorted(np.unique(groups[prior_test_mask]).tolist()),
                "previous_model_metrics": _metrics(labels[prior_test_mask], old_predictions, old_probs_aligned, classes),
                "retrained_nested_cv_metrics": _metrics(labels[prior_test_mask], predictions[prior_test_mask], probabilities[prior_test_mask], classes),
                "retrained_nested_cv_by_bearing": {group: cv_metrics["per_bearing"]["bearings"][group] for group in sorted(np.unique(groups[prior_test_mask]))},
                "note": "Matched comparison on the four bearing identities documented as validation/test holdouts for the old artifact. Predictions for the retrained model are outer-fold OOF predictions; this is not field validation.",
            }
    except Exception as exc:
        previous_comparison = {"unavailable_reason": str(exc)}

    if trees is not None:
        selected = selected | {"trees": int(trees)}
    model = _build_model(selected, seed)
    all_indices = np.arange(len(labels))
    print(f"Training final edge model on all {len(unique_groups)} physical bearings", flush=True)
    _fit(model, features, labels, groups, all_indices, selected)
    training_seconds = time.perf_counter() - started

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "model.joblib"
    joblib.dump(model, model_path, compress=3)
    metadata = {
        "bundle_schema_version": "axon-bearing-bundle-v1",
        "kind": "bearing_random_forest",
        "model_version": f"axon-bearing-rf-{_sha256(model_path)[:12]}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "taxonomy_version": TAXONOMY_VERSION,
        "preprocessing_version": feature_version,
        "input_contract": {"sensor_type": "vibration", "sampling_frequency_hz": sampling_frequency, "window_samples": int(signals.shape[1]), "feature_names": list(selected_feature_names)},
        "classes": classes.tolist(),
        "split": {"strategy": cv["strategy"], "group": cv["group"], "outer_folds": cv["outer_folds"], "training_fit_groups": sorted(unique_groups.tolist()), "held_out_cv_windows": int(len(labels)), "folds": cv["folds"]},
        "training": {"seed": seed, "selected_model": selected, "all_bearing_balanced": bool(selected["group_balanced"]), "physical_bearings_per_class": class_group_counts, "windows": int(len(signals)), "duration_seconds": training_seconds, "library_versions": {"python": platform.python_version(), "scikit_learn": sklearn.__version__}},
        "source": {"prepared_path": str(prepared_path), "sha256": _sha256(Path(prepared_path))},
        "artifact": {"filename": model_path.name, "sha256": _sha256(model_path)},
    }
    report = cv_metrics | {"cross_validation": cv, "previous_hyperparameter_baseline_same_folds": baseline_cv_metrics, "previous_model_comparison": previous_comparison, "model_selection": {"candidates": _candidate_specs(), "selection_method": "inner StratifiedGroupKFold, selected independently inside each outer fold; final config chosen by nested-fold selection frequency then mean inner score"}}
    (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    (output_dir / "evaluation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    np.savez_compressed(output_dir / "oof_predictions.npz", labels=labels, groups=groups, predictions=predictions, probabilities=probabilities, classes=classes)
    return metadata | {"evaluation": report}
