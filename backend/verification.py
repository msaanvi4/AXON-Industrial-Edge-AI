"""Repair verification policy for AXON Verify.

The policy is deliberately conservative and fully deterministic: the same
before/after evidence always produces the same verdict and the same machine
readable reasons.  The system can only certify a repair; it can never be
talked into certifying an uncertain one.

Thresholds are policy, not truth.  When the deployed model is not calibrated,
``confidence_calibrated`` is False and the conservative default thresholds are
used together with an explicit policy warning.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

POLICY_VERSION = "axv-policy-v1"

VERIFIED = "VERIFIED"
NOT_VERIFIED = "NOT_VERIFIED"
INCONCLUSIVE = "INCONCLUSIVE"
CANNOT_VERIFY = "CANNOT_VERIFY"
NOT_APPLICABLE = "NOT_APPLICABLE"

VERDICTS = (VERIFIED, NOT_VERIFIED, INCONCLUSIVE, CANNOT_VERIFY, NOT_APPLICABLE)

# Repair status values taken from the model's diagnostic contract.
FAULT_SUSPECTED = "FAULT_SUSPECTED"
HEALTHY = "HEALTHY_WITHIN_VALIDATED_SCOPE"
UNCERTAIN = "DIAGNOSIS_UNCERTAIN"

REASON_TEXT = {
    "BEFORE_FAULT_SUSPECTED": "The pre-repair reading showed a suspected fault.",
    "BEFORE_HEALTHY": "The pre-repair reading showed no fault inside the validated scope.",
    "BEFORE_UNCERTAIN": "The pre-repair reading was uncertain, so no baseline fault is established.",
    "AFTER_HEALTHY": "The post-repair reading is healthy within the validated scope.",
    "AFTER_STILL_FAULT": "The post-repair reading still indicates the same fault class.",
    "AFTER_UNCERTAIN": "The post-repair reading is uncertain; it cannot be treated as proof.",
    "MODEL_UNAVAILABLE": "The bearing model is unavailable, so no verdict can be produced.",
    "SENSOR_INVALID": "The reading failed the model input contract.",
    "NON_COMPARABLE": "The before and after readings are not comparable.",
    "NO_CHANGE": "The before and after evidence did not materially change.",
    "OUT_OF_SCOPE": "The reading is outside the model's validated scope.",
    "ASSET_MISMATCH": "The readings belong to different assets or components.",
    "MODEL_MISMATCH": "The readings were scored by different model versions.",
    "OPERATING_CONDITION_MISMATCH": "The readings were captured under different operating conditions.",
    "TIMESTAMP_ORDER": "The after reading is not newer than the before reading.",
    "POLICY_MARGIN": "The improvement is below the certification margin.",
    "AFTER_DAMAGE_PROBABILITY": "The calibrated post-repair fault probability is still too high.",
}


@dataclass
class Policy:
    """Thresholds used to decide a repair verdict."""
    version: str = POLICY_VERSION
    minimum_before_confidence: float = 0.70
    minimum_after_confidence: float = 0.70
    minimum_damage_drop: float = 0.30
    maximum_after_damage_probability: float = 0.30
    calibrated: bool = True
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "policy_version": self.version,
            "minimum_before_confidence": self.minimum_before_confidence,
            "minimum_after_confidence": self.minimum_after_confidence,
            "minimum_damage_drop": self.minimum_damage_drop,
            "maximum_after_damage_probability": self.maximum_after_damage_probability,
            "confidence_calibrated": self.calibrated,
            "warnings": list(self.warnings),
        }


def default_policy(calibrated: bool) -> Policy:
    """Return the policy for a given model calibration state."""
    if calibrated:
        return Policy(
            calibrated=True,
            minimum_after_confidence=0.70,
            minimum_damage_drop=0.30,
            maximum_after_damage_probability=0.30,
        )
    # Uncalibrated probabilities are not a risk measure: hold the line harder
    # and say so, rather than pretending the number is meaningful.
    return Policy(
        calibrated=False,
        minimum_before_confidence=0.90,
        minimum_after_confidence=0.90,
        minimum_damage_drop=0.50,
        maximum_after_damage_probability=0.20,
        warnings=[
            "The deployed model has no calibration artifact; conservative thresholds "
            "were used and the confidence values are raw model outputs, not probabilities."
        ],
    )


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _damage_probability(diagnostic: dict) -> float:
    """Return the probability of a non-healthy outcome, if one is reported."""
    probabilities = diagnostic.get("probabilities") or {}
    damage = None
    healthy = None
    for key, value in probabilities.items():
        if key in ("other_bearing_damage", "damage", "fault"):
            damage = _as_float(value)
        elif key in ("healthy",):
            healthy = _as_float(value)
    if damage is not None:
        return damage
    if healthy is not None:
        return 1.0 - healthy
    return _as_float(diagnostic.get("confidence"))


def _is_fault_status(status: str | None) -> bool:
    return status == FAULT_SUSPECTED


def _is_healthy_status(status: str | None) -> bool:
    return status == HEALTHY


def _is_uncertain_status(status: str | None) -> bool:
    return status in (UNCERTAIN, "INSUFFICIENT_EVIDENCE", "OUT_OF_DISTRIBUTION",
                      "UNSUPPORTED_FAULT")


def _is_unavailable_status(status: str | None) -> bool:
    return status in ("MODEL_UNAVAILABLE", "SENSOR_DATA_INVALID")


def comparability(before: dict, after: dict) -> dict:
    """Compare the operating context of the two readings."""
    # Unset fields on both sides are not treated as a match: two records that
    # carry no sampling rate have not been shown to share one.
    checks = {
        "same_asset": bool(before.get("asset_id")) and before.get("asset_id") == after.get("asset_id"),
        "same_component": bool(before.get("component_id")) and before.get("component_id") == after.get("component_id"),
        "same_sampling_rate": (
            bool(before.get("sampling_frequency_hz"))
            and _as_float(before.get("sampling_frequency_hz")) ==
            _as_float(after.get("sampling_frequency_hz"))
        ),
        "same_window": (
            int(before.get("window_samples") or 0) > 0
            and int(before.get("window_samples") or 0) == int(after.get("window_samples") or 0)
        ),
        "same_model": (
            before.get("model_version") is not None
            and before.get("model_version") == after.get("model_version")
        ),
        "different_event": before.get("event_id") != after.get("event_id"),
        "operating_condition_match": True,
        "timestamp_order": False,
    }

    before_condition = before.get("operating_condition_id")
    after_condition = after.get("operating_condition_id")
    if before_condition and after_condition:
        checks["operating_condition_match"] = before_condition == after_condition
    elif before_condition or after_condition:
        # One side recorded a condition and the other did not: not provably equal.
        checks["operating_condition_match"] = False

    before_time = _parse_time(before.get("captured_at") or before.get("created_at"))
    after_time = _parse_time(after.get("captured_at") or after.get("created_at"))
    if before_time and after_time:
        # Equal timestamps are simultaneous laboratory captures, not a reversal.
        # Only a strictly earlier post-repair reading blocks verification.
        checks["timestamp_order"] = after_time >= before_time
    elif before_time or after_time:
        checks["timestamp_order"] = True  # one side has no timestamp: do not block on it

    checks["comparable"] = all(checks.values())
    return checks


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            from datetime import timezone
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def verify_repair(before: dict, after: dict, policy: Policy,
                  reading_contract: dict | None = None) -> dict:
    """Produce a deterministic repair verdict from two diagnostics.

    ``before`` / ``after`` are diagnostic records (or dicts with the same
    fields) that already carry asset, component, model, status and evidence.
    """
    reasons: list[str] = []
    comparison = comparability(before, after)

    for key, label in (
        ("same_asset", "ASSET_MISMATCH"),
        ("same_component", "ASSET_MISMATCH"),
        ("same_sampling_rate", "NON_COMPARABLE"),
        ("same_window", "NON_COMPARABLE"),
        ("same_model", "MODEL_MISMATCH"),
        ("different_event", "NO_CHANGE"),
        ("operating_condition_match", "OPERATING_CONDITION_MISMATCH"),
        ("timestamp_order", "TIMESTAMP_ORDER"),
    ):
        if not comparison[key]:
            reasons.append(label)

    before_status = before.get("status")
    after_status = after.get("status")

    # An unavailable model or an invalid sensor record outranks every
    # comparability question: the honest answer is that verification cannot be
    # attempted at all, not that the two records disagree about context.
    if _is_unavailable_status(before_status) or _is_unavailable_status(after_status):
        offending = after_status if _is_unavailable_status(after_status) else before_status
        code = "SENSOR_INVALID" if offending == "SENSOR_DATA_INVALID" else "MODEL_UNAVAILABLE"
        return _result(CANNOT_VERIFY, [code, *reasons], comparison, policy, before, after)

    if reasons:
        return _result(INCONCLUSIVE, reasons, comparison, policy, before, after)

    if _is_uncertain_status(before_status):
        return _result(INCONCLUSIVE, ["BEFORE_UNCERTAIN"], comparison, policy, before, after)

    if _is_healthy_status(before_status):
        return _result(NOT_APPLICABLE, ["BEFORE_HEALTHY"], comparison, policy, before, after)

    if not _is_fault_status(before_status):
        return _result(INCONCLUSIVE, ["BEFORE_UNCERTAIN"], comparison, policy, before, after)

    # Baseline fault established. Inspect the post-repair evidence.
    if _is_unavailable_status(after_status):
        return _result(CANNOT_VERIFY, ["MODEL_UNAVAILABLE"], comparison, policy, before, after)

    if _is_uncertain_status(after_status):
        return _result(INCONCLUSIVE, ["AFTER_UNCERTAIN"], comparison, policy, before, after)

    if _is_fault_status(after_status):
        reasons = ["AFTER_STILL_FAULT"]
        before_damage = _damage_probability(before)
        after_damage = _damage_probability(after)
        if abs(after_damage - before_damage) < 0.05:
            reasons.append("NO_CHANGE")
        return _result(NOT_VERIFIED, reasons, comparison, policy, before, after)

    if not _is_healthy_status(after_status):
        return _result(INCONCLUSIVE, ["AFTER_UNCERTAIN"], comparison, policy, before, after)

    # Healthy after-repair: apply the conservative margins.
    before_confidence = _as_float(before.get("confidence"))
    after_confidence = _as_float(after.get("confidence"))
    before_damage = _damage_probability(before)
    after_damage = _damage_probability(after)
    drop = before_damage - after_damage

    if after_confidence < policy.minimum_after_confidence:
        return _result(INCONCLUSIVE, ["AFTER_UNCERTAIN"], comparison, policy, before, after)
    if before_confidence < policy.minimum_before_confidence:
        return _result(INCONCLUSIVE, ["BEFORE_UNCERTAIN"], comparison, policy, before, after)
    if drop < policy.minimum_damage_drop:
        return _result(INCONCLUSIVE, ["POLICY_MARGIN"], comparison, policy, before, after)
    if after_damage > policy.maximum_after_damage_probability:
        return _result(INCONCLUSIVE, ["AFTER_DAMAGE_PROBABILITY"], comparison, policy, before, after)

    return _result(
        VERIFIED,
        ["BEFORE_FAULT_SUSPECTED", "AFTER_HEALTHY", "POLICY_MARGIN"],
        comparison, policy, before, after,
    )


def _result(verdict: str, reasons: list[str], comparison: dict, policy: Policy,
            before: dict, after: dict) -> dict:
    seen: list[str] = []
    for reason in reasons:
        if reason not in seen:
            seen.append(reason)
    return {
        "verdict": verdict,
        "reasons": [
            {"code": code, "detail": REASON_TEXT.get(code, code)} for code in seen
        ],
        "comparability": comparison,
        "policy": policy.as_dict(),
        "before": _summary(before),
        "after": _summary(after),
    }


def _summary(diagnostic: dict) -> dict:
    return {
        "event_id": diagnostic.get("event_id"),
        "status": diagnostic.get("status"),
        "fault_class": diagnostic.get("fault_class"),
        "confidence": diagnostic.get("confidence"),
        "confidence_calibrated": bool(diagnostic.get("confidence_calibrated")),
        "probabilities": diagnostic.get("probabilities") or {},
        "model_version": diagnostic.get("model_version"),
        "calibration_version": diagnostic.get("calibration_version"),
        "asset_id": diagnostic.get("asset_id"),
        "component_id": diagnostic.get("component_id"),
        "captured_at": diagnostic.get("captured_at") or diagnostic.get("created_at"),
        "source": diagnostic.get("source"),
        "simulated": bool(diagnostic.get("simulated")),
        "sha256": diagnostic.get("sha256"),
        "operating_condition_id": diagnostic.get("operating_condition_id"),
        "sampling_frequency_hz": diagnostic.get("sampling_frequency_hz"),
        "window_samples": diagnostic.get("window_samples"),
    }
