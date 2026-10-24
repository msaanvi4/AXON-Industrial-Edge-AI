"""Local Raspberry Pi bearing inference used before sending a LoRa summary."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path: sys.path.insert(0, str(PROJECT_ROOT))

from ml.bearing.bundle import BearingBundleError, load_bearing_bundle
from shared.diagnosis import diagnosis_from_probabilities, unavailable_diagnosis


def diagnose_payload(payload: dict, bundle=None, model_error: str | None = None) -> dict:
    asset_id, component_id = payload.get("asset_id", "unknown-asset"), payload.get("component_id", "unknown-component")
    if model_error or bundle is None:
        return unavailable_diagnosis(asset_id, component_id, model_error or "No bearing bundle loaded")
    try:
        probabilities = bundle.probabilities(payload["signal"], payload["sampling_frequency_hz"])
        result = diagnosis_from_probabilities(asset_id, component_id, probabilities, bundle.metadata["model_version"], evidence=[{"kind": "vibration_window", "source": payload.get("source", "edge_window"), "transport": payload.get("capture_transport", "provided_window"), "sampling_frequency_hz": payload["sampling_frequency_hz"], "samples": len(payload["signal"])}])
        result["simulated"] = bool(payload.get("historical_replay", False))
        return result
    except (KeyError, TypeError, ValueError) as exc:
        return unavailable_diagnosis(asset_id, component_id, str(exc), status="SENSOR_DATA_INVALID")
