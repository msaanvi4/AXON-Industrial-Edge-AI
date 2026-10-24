"""Safety-oriented conversion from model probabilities to diagnostic records."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from ml.bearing.taxonomy import FAULTS

DIAGNOSTIC_STATUSES = {"HEALTHY_WITHIN_VALIDATED_SCOPE", "FAULT_SUSPECTED", "DIAGNOSIS_UNCERTAIN", "INSUFFICIENT_EVIDENCE", "OUT_OF_DISTRIBUTION", "UNSUPPORTED_FAULT", "SENSOR_DATA_INVALID", "MODEL_UNAVAILABLE", "AWAITING_TECHNICIAN_CONFIRMATION", "FAULT_CONFIRMED"}


def unavailable_diagnosis(asset_id: str, component_id: str, reason: str, *, status: str = "MODEL_UNAVAILABLE") -> dict:
    if status not in DIAGNOSTIC_STATUSES: raise ValueError("invalid diagnostic status")
    return {"event_id": str(uuid4()), "timestamp": datetime.now(timezone.utc).isoformat(), "asset_id": asset_id, "component_id": component_id, "fault_class": None, "fault_location": None, "confidence": None, "confidence_calibrated": False, "status": status, "evidence": [], "alternative_hypotheses": [], "missing_information": [reason], "recommended_confirmation_tests": [], "repair_reference_ids": [], "cost_estimate_id": None, "model_version": None, "requires_technician_confirmation": True, "simulated": False}


def diagnosis_from_probabilities(asset_id: str, component_id: str, probabilities: dict[str, float], model_version: str, *, minimum_probability: float = 0.70, evidence: list[dict] | None = None) -> dict:
    if not probabilities: return unavailable_diagnosis(asset_id, component_id, "model returned no class probabilities")
    ranked = sorted(probabilities.items(), key=lambda pair: pair[1], reverse=True)
    fault_class, confidence = ranked[0]
    if fault_class not in FAULTS:
        return unavailable_diagnosis(asset_id, component_id, f"unsupported model class: {fault_class}", status="UNSUPPORTED_FAULT")
    location = FAULTS[fault_class].fault_location
    if confidence < minimum_probability:
        status = "DIAGNOSIS_UNCERTAIN"
    elif fault_class == "healthy":
        status = "HEALTHY_WITHIN_VALIDATED_SCOPE"
    else:
        status = "FAULT_SUSPECTED"
    alternatives = [{"fault_class": label, "fault_location": FAULTS[label].fault_location if label in FAULTS else None, "score": score, "score_kind": "uncalibrated_classifier_probability"} for label, score in ranked[1:3]]
    return {"event_id": str(uuid4()), "timestamp": datetime.now(timezone.utc).isoformat(), "asset_id": asset_id, "component_id": component_id, "fault_class": fault_class, "fault_location": location, "confidence": confidence, "confidence_calibrated": False, "status": status, "evidence": evidence or [], "alternative_hypotheses": alternatives, "missing_information": ["Probability calibration and physical inspection are required before confirmation."], "recommended_confirmation_tests": ["Perform inspection according to equipment-specific authorized documentation."], "repair_reference_ids": [], "cost_estimate_id": None, "model_version": model_version, "requires_technician_confirmation": status != "HEALTHY_WITHIN_VALIDATED_SCOPE", "simulated": False}
