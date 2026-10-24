"""Evidence ingestion: one function that turns a vibration window into a record.

Every transport (upload/CSV, REST, MQTT, LoRa replay) funnels through
``diagnose_signal`` so the certificate always points at the same model version,
the same calibration state and the same input contract, no matter how the bytes
arrived.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from backend.calibration import apply_temperature, load_calibration
from ml.bearing.bundle import BearingBundleError, load_bearing_bundle
from shared.diagnosis import diagnosis_from_probabilities, unavailable_diagnosis

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class InferenceOutcome:
    """The result of one inference attempt, including its failure modes."""

    diagnostic: dict
    model_version: str | None
    calibration_version: str | None
    calibrated: bool
    input_contract: dict | None
    available: bool
    reason: str | None = None

    @property
    def probabilities(self) -> dict[str, float]:
        return self.diagnostic.get("probabilities") or {}


def default_bundle_loader() -> Callable:
    return lambda: load_bearing_bundle(Path(os.getenv(
        "AXON_BEARING_MODEL_DIR", PROJECT_ROOT / "ml" / "artifacts" / "bearing" / "active")))


def model_status(bundle_loader: Callable | None = None) -> dict:
    """Describe model + calibration availability without raising."""
    loader = bundle_loader or default_bundle_loader()
    from backend.calibration import calibrated_state
    state = calibrated_state()
    try:
        bundle = loader()
    except BearingBundleError as exc:
        return {
            "available": False,
            "status": "MODEL_UNAVAILABLE",
            "reason": str(exc),
            "model_version": None,
            "input_contract": None,
            **state,
        }
    return {
        "available": True,
        "model_version": bundle.metadata["model_version"],
        "kind": bundle.metadata["kind"],
        "classes": bundle.metadata.get("classes"),
        "input_contract": bundle.metadata["input_contract"],
        **state,
    }


def diagnose_signal(
    asset_id: str,
    component_id: str,
    signal,
    sampling_frequency_hz: float,
    *,
    source: str = "upload",
    evidence_extra: list[dict] | None = None,
    bundle_loader: Callable | None = None,
    captured_at: str | None = None,
    simulated: bool = False,
) -> InferenceOutcome:
    """Run the validated bearing model over one window.

    Failure modes are preserved rather than hidden:

    * ``MODEL_UNAVAILABLE``  — the bundle cannot be loaded (checksum/version).
    * ``SENSOR_DATA_INVALID`` — the window violates the input contract.
    * ``DIAGNOSIS_UNCERTAIN`` — the model does not reach the decision threshold.
    """
    loader = bundle_loader or default_bundle_loader()
    calibration = load_calibration()
    calibration_version = calibration.version if calibration else None
    calibrated = calibration is not None

    evidence_base = [{
        "kind": "vibration_window",
        "source": source,
        "transport": {"upload": "http", "csv": "http", "rest": "http",
                      "mqtt": "mqtt", "lora": "lora", "replay": "local_replay"}
        .get(source, "http"),
        "sampling_frequency_hz": float(sampling_frequency_hz),
        "samples": int(len(signal)),
    }]
    if evidence_extra:
        evidence_base = evidence_base + list(evidence_extra)

    try:
        bundle = loader()
    except BearingBundleError as exc:
        diagnostic = unavailable_diagnosis(asset_id, component_id, str(exc))
        diagnostic["probabilities"] = {}
        diagnostic["calibration_version"] = None
        return InferenceOutcome(diagnostic, None, None, False, None, False, str(exc))

    contract = bundle.metadata["input_contract"]
    model_version = bundle.metadata["model_version"]

    try:
        raw = bundle.probabilities(signal, sampling_frequency_hz)
    except ValueError as exc:
        diagnostic = unavailable_diagnosis(
            asset_id, component_id, str(exc), status="SENSOR_DATA_INVALID")
        diagnostic["probabilities"] = {}
        diagnostic["model_version"] = model_version
        diagnostic["calibration_version"] = calibration_version
        diagnostic["input_contract"] = contract
        return InferenceOutcome(
            diagnostic, model_version, calibration_version, calibrated, contract,
            True, str(exc))

    if calibration is not None:
        ordered = calibration.classes or sorted(raw)
        scaled = apply_temperature(raw, calibration.temperature, ordered)
    else:
        scaled = dict(raw)

    minimum = float(os.getenv("AXON_MIN_CLASS_PROBABILITY", "0.70"))
    diagnostic = diagnosis_from_probabilities(
        asset_id, component_id, scaled, model_version,
        minimum_probability=minimum, evidence=evidence_base,
    )
    diagnostic["probabilities"] = {k: round(v, 6) for k, v in scaled.items()}
    diagnostic["raw_probabilities"] = {k: round(v, 6) for k, v in raw.items()}
    diagnostic["confidence_calibrated"] = calibrated
    diagnostic["calibration_version"] = calibration_version
    diagnostic["input_contract"] = contract
    if captured_at:
        diagnostic["timestamp"] = captured_at
    if simulated:
        diagnostic["simulated"] = True
    if calibration is None:
        diagnostic.setdefault("missing_information", []).append(
            "No calibration artifact is deployed: confidence is a raw model "
            "score, not a probability of correctness.")

    return InferenceOutcome(
        diagnostic, model_version, calibration_version, calibrated, contract, True, None)


def contract_summary(outcome: InferenceOutcome) -> dict:
    """The input contract a certificate records."""
    contract = outcome.input_contract or {}
    return {
        "sensor_type": contract.get("sensor_type"),
        "sampling_frequency_hz": contract.get("sampling_frequency_hz"),
        "window_samples": contract.get("window_samples"),
        "feature_count": len(contract.get("feature_names") or []),
        "model_version": outcome.model_version,
        "calibration_version": outcome.calibration_version,
        "confidence_calibrated": outcome.calibrated,
    }


def is_replay_evidence(diagnostic: dict) -> bool:
    """True when the evidence is a historical dataset replay, not a live capture."""
    for item in diagnostic.get("evidence") or []:
        if item.get("historical_replay") or item.get("source") in (
                "paderborn_dataset_replay", "demo_dataset_replay"):
            return True
    return bool(diagnostic.get("simulated"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_event_id() -> str:
    return str(uuid4())


def is_finite_signal(signal) -> bool:
    return all(math.isfinite(float(value)) for value in signal)
