"""Transport-neutral evidence acceptance.

REST, MQTT and the LoRa gateway all end up here, so the rules are identical no
matter how the bytes arrived:

* the organisation, site and device in the transport envelope must agree,
* the asset and component must already exist inside that organisation,
* an ``event_id`` is accepted exactly once per organisation,
* the record is written together with its audit entry.
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.ingestion import diagnose_signal
from backend.verify_store import VerifyStore, canonical_json, sha256_digest, utcnow
from backend.verify_models import DiagnosticIngestRequest

DIAGNOSTIC_STATUSES = {
    "HEALTHY_WITHIN_VALIDATED_SCOPE", "FAULT_SUSPECTED", "DIAGNOSIS_UNCERTAIN",
    "INSUFFICIENT_EVIDENCE", "OUT_OF_DISTRIBUTION", "UNSUPPORTED_FAULT",
    "SENSOR_DATA_INVALID", "MODEL_UNAVAILABLE",
}

TOPIC_KINDS = ("diagnostic", "window", "status", "command")


class IngestRejection(ValueError):
    """Raised when an inbound event must not be stored."""

    def __init__(self, reason: str, code: str = "rejected"):
        super().__init__(reason)
        self.code = code


@dataclass(frozen=True)
class TopicAddress:
    prefix: str
    org_id: str
    site_id: str
    device_id: str
    kind: str


def parse_topic(topic: str, prefix: str = "axon/v1") -> TopicAddress | None:
    """Parse ``{prefix}/{org_id}/{site_id}/{device_id}/{kind}``.

    Returns ``None`` for anything that is not a well-formed AXON topic, so a
    wildcard subscription can safely ignore foreign traffic.
    """
    parts = topic.split("/")
    head = prefix.split("/")
    if len(parts) != len(head) + 4:
        return None
    if parts[:len(head)] != head:
        return None
    org_id, site_id, device_id, kind = parts[len(head):]
    if not org_id or not site_id or not device_id:
        return None
    if kind not in TOPIC_KINDS:
        return None
    return TopicAddress(prefix, org_id, site_id, device_id, kind)


def address_for(org_id: str, site_id: str, device_id: str, kind: str,
                prefix: str = "axon/v1") -> str:
    return f"{prefix}/{org_id}/{site_id}/{device_id}/{kind}"


def _assert_device(store: VerifyStore, address: TopicAddress) -> dict:
    """Bind the topic to a registered device, or refuse the event."""
    device = store.find_device_by_key(address.device_id)
    if device is None:
        raise IngestRejection(
            f"device '{address.device_id}' is not registered", "unknown_device")
    if device["org_id"] != address.org_id:
        raise IngestRejection(
            f"device '{address.device_id}' does not belong to organisation "
            f"'{address.org_id}'", "tenant_mismatch")
    if device.get("site_id") and address.site_id not in ("-", device["site_id"]):
        raise IngestRejection(
            f"device '{address.device_id}' is bound to site '{device['site_id']}', "
            f"not '{address.site_id}'", "site_mismatch")
    return device


def _assert_target(store: VerifyStore, org_id: str, asset_id: str,
                   component_id: str) -> None:
    if store.get_asset(org_id, asset_id) is None:
        raise IngestRejection(f"asset '{asset_id}' does not exist in this organisation",
                              "unknown_asset")
    if store.get_component(org_id, component_id) is None:
        raise IngestRejection(
            f"component '{component_id}' does not exist on any asset in this organisation",
            "unknown_component")


def accept_diagnostic_event(store: VerifyStore, payload: DiagnosticIngestRequest,
                            *, org_id: str, actor: str, transport: str,
                            device_id: str | None = None,
                            site_id: str | None = None) -> dict:
    """Validate and persist a pre-computed diagnostic event."""
    if payload.status not in DIAGNOSTIC_STATUSES:
        raise IngestRejection(f"unsupported diagnostic status '{payload.status}'",
                              "invalid_status")
    for label, value in payload.probabilities.items():
        if not 0.0 <= value <= 1.0:
            raise IngestRejection(f"probability for '{label}' is outside [0, 1]",
                                  "invalid_probability")
    if payload.probabilities and abs(sum(payload.probabilities.values()) - 1.0) > 0.02:
        raise IngestRejection("probabilities must sum to 1.0", "invalid_probability")

    _assert_target(store, org_id, payload.asset_id, payload.component_id)
    if payload.event_id and store.find_diagnostic_by_event(org_id, payload.event_id):
        raise IngestRejection("event_id has already been recorded", "duplicate_event")

    reading = store.create_reading(
        org_id, asset_id=payload.asset_id, component_id=payload.component_id,
        device_id=device_id, source=transport, captured_at=payload.captured_at or utcnow(),
        sampling_frequency_hz=payload.sampling_frequency_hz or 0.0,
        window_samples=payload.window_samples or 0,
        operating_condition_id=payload.operating_condition_id,
        signal=None,
        sha256=payload.reading_sha256 or sha256_digest(canonical_json({
            "event_id": payload.event_id, "asset_id": payload.asset_id,
            "component_id": payload.component_id, "transport": transport,
        })))
    confidence = payload.confidence
    if confidence is None and payload.probabilities:
        confidence = max(payload.probabilities.values())
    diagnostic = store.create_diagnostic(
        org_id, reading_id=reading["id"], event_id=payload.event_id,
        model_version=payload.model_version, calibration_version=payload.calibration_version,
        status=payload.status, fault_class=payload.fault_class,
        fault_location=payload.fault_location, confidence=confidence,
        confidence_calibrated=payload.confidence_calibrated,
        probabilities=payload.probabilities, evidence=payload.evidence, source=transport,
        simulated=payload.simulated)
    store.audit(org_id, actor, "diagnostic.ingested", "diagnostic", diagnostic["id"],
                {"event_id": payload.event_id, "status": payload.status,
                 "transport": transport, "site_id": site_id,
                 "device_id": device_id, "confidence_calibrated": payload.confidence_calibrated})
    if device_id and site_id:
        device = store.find_device_by_key(device_id)
        if device:
            store.touch_device(org_id, device["id"])
    return {"reading": reading, "diagnostic": diagnostic, "duplicate": False}


def accept_window_event(store: VerifyStore, *, org_id: str, asset_id: str,
                        component_id: str, signal: list[float],
                        sampling_frequency_hz: float, actor: str, transport: str,
                        device_id: str | None = None, site_id: str | None = None,
                        event_id: str | None = None,
                        operating_condition_id: str | None = None,
                        captured_at: str | None = None) -> dict:
    """Score an inbound raw window and persist the resulting evidence."""
    _assert_target(store, org_id, asset_id, component_id)
    if event_id and store.find_diagnostic_by_event(org_id, event_id):
        raise IngestRejection("event_id has already been recorded", "duplicate_event")
    if len(signal) < 16:
        raise IngestRejection("a window needs at least 16 samples", "window_too_short")

    outcome = diagnose_signal(asset_id, component_id, signal, sampling_frequency_hz,
                              source=transport, captured_at=captured_at)
    if event_id:
        outcome.diagnostic["event_id"] = event_id

    digest_input = canonical_json({
        "signal": [round(float(v), 9) for v in signal], "fs": float(sampling_frequency_hz),
        "asset_id": asset_id, "component_id": component_id,
        "captured_at": captured_at or utcnow(),
    })
    reading = store.create_reading(
        org_id, asset_id=asset_id, component_id=component_id, device_id=device_id,
        source=transport, captured_at=captured_at or utcnow(),
        sampling_frequency_hz=sampling_frequency_hz, window_samples=len(signal),
        operating_condition_id=operating_condition_id, signal=signal,
        sha256=sha256_digest(digest_input))
    diagnostic = store.create_diagnostic(
        org_id, reading_id=reading["id"], event_id=outcome.diagnostic["event_id"],
        model_version=outcome.model_version, calibration_version=outcome.calibration_version,
        status=outcome.diagnostic["status"],
        fault_class=outcome.diagnostic.get("fault_class"),
        fault_location=outcome.diagnostic.get("fault_location"),
        confidence=outcome.diagnostic.get("confidence"),
        confidence_calibrated=outcome.calibrated,
        probabilities=outcome.diagnostic.get("probabilities") or {},
        evidence=outcome.diagnostic.get("evidence") or [],
        missing_information=outcome.diagnostic.get("missing_information") or [],
        source=transport, simulated=bool(outcome.diagnostic.get("simulated")))
    store.audit(org_id, actor, "window.ingested", "diagnostic", diagnostic["id"],
                {"status": diagnostic["status"], "transport": transport,
                 "device_id": device_id, "site_id": site_id,
                 "window_samples": len(signal)})
    if device_id and site_id:
        device = store.find_device_by_key(device_id)
        if device:
            store.touch_device(org_id, device["id"])
    return {"reading": reading, "diagnostic": diagnostic,
            "model_version": outcome.model_version, "available": outcome.available}


def handle_transport_event(store: VerifyStore, address: TopicAddress,
                           payload: dict, *, actor: str = "mqtt") -> dict:
    """Dispatch one already-parsed transport payload to the right acceptor."""
    device = _assert_device(store, address)

    if address.kind == "status":
        store.touch_device(address.org_id, device["id"])
        store.audit(address.org_id, actor, "device.status", "device", device["id"],
                    {"site_id": address.site_id, "payload": payload})
        return {"accepted": True, "kind": "status", "device_id": device["device_key"]}

    if address.kind == "command":
        store.audit(address.org_id, actor, "device.command_ignored", "device", device["id"],
                    {"topic_kind": "command",
                     "note": "this deployment does not act on device commands"})
        return {"accepted": False, "kind": "command",
                "reason": "commands are not accepted on the ingest path"}

    if address.kind == "diagnostic":
        event = DiagnosticIngestRequest.model_validate(
            payload | {"device_id": device["id"]})
        result = accept_diagnostic_event(
            store, event, org_id=address.org_id, actor=actor, transport="mqtt",
            device_id=device["device_key"], site_id=address.site_id)
        return {"accepted": True, "kind": "diagnostic",
                "event_id": result["diagnostic"]["event_id"]}

    # window: the device sent raw samples; the backend scores them.
    asset_id = str(payload.get("asset_id") or "")
    component_id = str(payload.get("component_id") or "")
    signal = payload.get("signal")
    if not asset_id or not component_id or not isinstance(signal, list):
        raise IngestRejection(
            "a window payload needs 'asset_id', 'component_id' and a 'signal' array",
            "invalid_window")
    result = accept_window_event(
        store, org_id=address.org_id, asset_id=asset_id, component_id=component_id,
        signal=[float(value) for value in signal],
        sampling_frequency_hz=float(payload.get("sampling_frequency_hz") or 0.0),
        actor=actor, transport="mqtt", device_id=device["device_key"],
        site_id=address.site_id, event_id=payload.get("event_id"),
        operating_condition_id=payload.get("operating_condition_id"),
        captured_at=payload.get("captured_at"))
    return {"accepted": True, "kind": "window",
            "event_id": result["diagnostic"]["event_id"]}
