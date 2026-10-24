"""Probability calibration for the bearing classifier.

Facts, hypotheses and unknowns are kept separate here on purpose:

* **Fact** — the shipped bundle returns raw Random-Forest vote fractions. Those
  are not probabilities of anything until they are calibrated.
* **Hypothesis** — temperature scaling fitted on a held-out, group-disjoint
  validation set will make the scores usable as confidences.
* **Unknown** — whether any specific calibration artifact exists in this
  deployment. ``load_calibration`` returns ``None`` when no artifact is present,
  and every caller must fall back to ``confidence_calibrated = False``.

Nothing in this module invents a calibration result. It either loads a real
artifact produced by ``fit_temperature_scaling`` on real held-out predictions,
or it reports that calibration is unavailable.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

CALIBRATION_SCHEMA = "axon-calibration-v1"


class CalibrationError(RuntimeError):
    pass


@dataclass
class Calibration:
    """A fitted temperature-scaling artifact."""

    version: str
    temperature: float
    classes: list[str]
    method: str = "temperature_scaling"
    fitted_at: str = ""
    validation_set: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "calibration_schema": CALIBRATION_SCHEMA,
            "calibration_version": self.version,
            "method": self.method,
            "temperature": self.temperature,
            "classes": list(self.classes),
            "fitted_at": self.fitted_at,
            "validation_set": dict(self.validation_set),
            "metrics": dict(self.metrics),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Calibration":
        if payload.get("calibration_schema") != CALIBRATION_SCHEMA:
            raise CalibrationError("unsupported calibration schema")
        temperature = float(payload.get("temperature", 0.0))
        if not math.isfinite(temperature) or temperature <= 0:
            raise CalibrationError("calibration temperature must be positive and finite")
        classes = list(payload.get("classes") or [])
        if not classes:
            raise CalibrationError("calibration artifact has no class order")
        return cls(
            version=str(payload.get("calibration_version") or "calibration-unversioned"),
            temperature=temperature,
            classes=classes,
            method=str(payload.get("method") or "temperature_scaling"),
            fitted_at=str(payload.get("fitted_at") or ""),
            validation_set=dict(payload.get("validation_set") or {}),
            metrics={k: float(v) for k, v in (payload.get("metrics") or {}).items()
                     if isinstance(v, (int, float))},
        )


# --------------------------------------------------------------------- scoring


def _logits(probabilities: dict[str, float], classes: Sequence[str]) -> list[float]:
    """Turn normalised probabilities into unnormalised log-space scores."""
    eps = 1e-12
    return [math.log(max(float(probabilities.get(c, 0.0)), eps)) for c in classes]


def apply_temperature(probabilities: dict[str, float], temperature: float,
                      classes: Sequence[str] | None = None) -> dict[str, float]:
    """Rescale raw class probabilities with a fitted temperature."""
    order = list(classes or sorted(probabilities))
    if temperature <= 0:
        raise CalibrationError("temperature must be positive")
    scores = [value / temperature for value in _logits(probabilities, order)]
    peak = max(scores)
    exps = [math.exp(value - peak) for value in scores]
    total = sum(exps)
    return {label: value / total for label, value in zip(order, exps)}


def _nll(probabilities: Sequence[dict[str, float]], labels: Sequence[str],
         classes: Sequence[str], temperature: float) -> float:
    total = 0.0
    for row, label in zip(probabilities, labels):
        scaled = apply_temperature(row, temperature, classes)
        total -= math.log(max(scaled.get(label, 0.0), 1e-12))
    return total / max(len(labels), 1)


def expected_calibration_error(probabilities: Sequence[dict[str, float]],
                               labels: Sequence[str], classes: Sequence[str],
                               bins: int = 10) -> float:
    """Standard ECE over `bins` equal-width confidence buckets."""
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for row, label in zip(probabilities, labels):
        scaled = apply_temperature(row, 1.0, classes)
        top = max(scaled, key=scaled.get)
        confidence = scaled[top]
        index = min(bins - 1, int(confidence * bins))
        buckets[index].append((confidence, top == label))
    total = len(probabilities)
    if not total:
        return 1.0
    ece = 0.0
    for bucket in buckets:
        if not bucket:
            continue
        accuracy = sum(1 for _, correct in bucket if correct) / len(bucket)
        confidence = sum(value for value, _ in bucket) / len(bucket)
        ece += (len(bucket) / total) * abs(accuracy - confidence)
    return ece


def _brier(probabilities: Sequence[dict[str, float]], labels: Sequence[str],
           classes: Sequence[str]) -> float:
    total = 0.0
    for row, label in zip(probabilities, labels):
        scaled = apply_temperature(row, 1.0, classes)
        total += sum((scaled.get(c, 0.0) - (1.0 if c == label else 0.0)) ** 2
                     for c in classes)
    return total / max(len(labels), 1)


def fit_temperature_scaling(probabilities: Sequence[dict[str, float]],
                            labels: Sequence[str],
                            classes: Sequence[str] | None = None,
                            version: str | None = None,
                            validation_set: dict[str, Any] | None = None) -> Calibration:
    """Fit temperature scaling by minimising NLL on held-out predictions.

    Golden-section search over ``T`` in ``[1/16, 16]`` — no SciPy dependency,
    deterministic, and the objective is unimodal in ``T`` for this family.
    """
    if not probabilities or len(probabilities) != len(labels):
        raise CalibrationError("need paired predictions and labels")
    order = list(classes or sorted(probabilities[0]))
    for row in probabilities:
        missing = [c for c in order if c not in row]
        if missing:
            raise CalibrationError(f"prediction row missing classes: {missing}")

    low, high = 1.0 / 16.0, 16.0
    phi = (math.sqrt(5.0) - 1.0) / 2.0

    def objective(temperature: float) -> float:
        return _nll(probabilities, labels, order, temperature)

    left = high - phi * (high - low)
    right = low + phi * (high - low)
    f_left, f_right = objective(left), objective(right)
    for _ in range(80):
        if f_left <= f_right:
            high, right, f_right = right, left, f_left
            left = high - phi * (high - low)
            f_left = objective(left)
        else:
            low, left, f_left = left, right, f_right
            right = low + phi * (high - low)
            f_right = objective(right)
        if high - low < 1e-6:
            break
    temperature = round((low + high) / 2.0, 6)

    raw = [apply_temperature(row, 1.0, order) for row in probabilities]
    scaled = [apply_temperature(row, temperature, order) for row in probabilities]
    return Calibration(
        version=version or f"temp-scaling-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        temperature=temperature,
        classes=order,
        fitted_at=datetime.now(timezone.utc).isoformat(),
        validation_set=dict(validation_set or {}),
        metrics={
            "nll_before": round(_nll(probabilities, labels, order, 1.0), 6),
            "nll_after": round(_nll(probabilities, labels, order, temperature), 6),
            "ece_before": round(expected_calibration_error(raw, labels, order), 6),
            "ece_after": round(expected_calibration_error(scaled, labels, order), 6),
            "brier_before": round(_brier(probabilities, labels, order), 6),
            "brier_after": round(_brier(scaled, labels, order), 6),
            "windows": float(len(labels)),
        },
    )


# --------------------------------------------------------------------- loading


def default_calibration_path() -> Path:
    root = Path(os.getenv(
        "AXON_BEARING_MODEL_DIR",
        Path(__file__).resolve().parents[1] / "ml" / "artifacts" / "bearing" / "active",
    ))
    return Path(os.getenv("AXON_CALIBRATION_FILE", root / "calibration.json"))


def load_calibration(path: str | Path | None = None) -> Calibration | None:
    """Load the calibration artifact, or return ``None`` when absent.

    Absence is a first-class answer: the product then reports
    ``confidence_calibrated = false`` and applies the conservative policy.
    """
    candidate = Path(path) if path is not None else default_calibration_path()
    if not candidate.is_file():
        return None
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        return Calibration.from_dict(payload)
    except (OSError, json.JSONDecodeError, CalibrationError, ValueError, TypeError):
        return None


def save_calibration(calibration: Calibration, path: str | Path | None = None) -> Path:
    candidate = Path(path) if path is not None else default_calibration_path()
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(json.dumps(calibration.as_dict(), indent=2, sort_keys=True),
                         encoding="utf-8")
    return candidate


def calibrated_state() -> dict:
    """Report the calibration state of the deployment without guessing."""
    calibration = load_calibration()
    if calibration is None:
        return {
            "confidence_calibrated": False,
            "calibration_version": None,
            "reason": "no calibration artifact is present for this deployment",
            "artifact_path": str(default_calibration_path()),
        }
    return {
        "confidence_calibrated": True,
        "calibration_version": calibration.version,
        "temperature": calibration.temperature,
        "validation_set": calibration.validation_set,
        "metrics": calibration.metrics,
        "artifact_path": str(default_calibration_path()),
    }
