"""AXON LoRa v2. Upgrade BOTH peers. CRC detects corruption, not forgery."""
from __future__ import annotations
import binascii
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
import struct
from uuid import UUID, uuid4
from shared.diagnosis import unavailable_diagnosis

MAGIC = b"AX"
PROTOCOL_VERSION = 2
HEADER = struct.Struct("<2sBH")
BODY = struct.Struct("<16sHQBBBBBBB")  # UUID, seq, epoch ms, fault/conf/status/flags, lengths
MAX_FRAME_SIZE = HEADER.size + BODY.size + 128 * 3 + 2
ACK_BODY = struct.Struct("<2sB16sH")
ACK_SIZE = ACK_BODY.size + 2
FAULT_IDS = {None: 255, "healthy": 0, "inner_race_damage": 1, "outer_race_damage": 2,
             "rolling_element_damage": 3, "other_bearing_damage": 4, "unknown": 254}
STATUS_IDS = {"HEALTHY_WITHIN_VALIDATED_SCOPE": 0, "FAULT_SUSPECTED": 1,
              "DIAGNOSIS_UNCERTAIN": 2, "INSUFFICIENT_EVIDENCE": 3,
              "OUT_OF_DISTRIBUTION": 4, "UNSUPPORTED_FAULT": 5,
              "SENSOR_DATA_INVALID": 6, "MODEL_UNAVAILABLE": 7,
              "AWAITING_TECHNICIAN_CONFIRMATION": 8, "FAULT_CONFIRMED": 9}


class LoraProtocolError(ValueError):
    pass


def crc16(payload: bytes) -> int:
    return binascii.crc_hqx(payload, 0xFFFF)


def _checked(payload: bytes) -> bytes:
    if len(payload) < 2 or crc16(payload[:-2]) != struct.unpack("<H", payload[-2:])[0]:
        raise LoraProtocolError("LoRa frame CRC mismatch")
    return payload[:-2]


@dataclass(frozen=True)
class LoraDiagnostic:
    event_id: str
    asset_id: str
    component_id: str
    fault_class: str | None
    confidence_percent: int | None
    sequence: int
    timestamp_ms: int
    status: str
    model_version: str | None
    simulated: bool


def _identifier(value, name: str) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise LoraProtocolError(f"{name} must be 1-128 letters, digits, underscores or hyphens")
    return value.encode("ascii")


def encode_diagnostic(diagnostic: dict, sequence: int) -> bytes:
    asset = _identifier(diagnostic.get("asset_id"), "asset_id")
    component = _identifier(diagnostic.get("component_id"), "component_id")
    model = str(diagnostic.get("model_version") or "").encode("utf-8")
    if len(model) > 128:
        raise LoraProtocolError("model_version exceeds 128 bytes")
    raw_confidence = diagnostic.get("confidence")
    if raw_confidence is not None and (not math.isfinite(float(raw_confidence)) or not 0 <= float(raw_confidence) <= 1):
        raise LoraProtocolError("confidence must be finite and between zero and one")
    confidence = 255 if raw_confidence is None else round(float(raw_confidence) * 100)
    fault = diagnostic.get("fault_class")
    status = diagnostic.get("status")
    if status is None:
        status = ("INSUFFICIENT_EVIDENCE" if fault is None else
                  "DIAGNOSIS_UNCERTAIN" if raw_confidence is None or float(raw_confidence) < .70 else
                  "HEALTHY_WITHIN_VALIDATED_SCOPE" if fault == "healthy" else "FAULT_SUSPECTED")
    try:
        event_id = UUID(str(diagnostic.get("event_id") or uuid4()))
        timestamp = diagnostic.get("timestamp")
        when = datetime.fromisoformat(timestamp.replace("Z", "+00:00")) if isinstance(timestamp, str) else datetime.now(timezone.utc)
        if when.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        body = BODY.pack(event_id.bytes, sequence & 0xFFFF, round(when.timestamp() * 1000),
                         FAULT_IDS[fault], confidence, STATUS_IDS[status], int(bool(diagnostic.get("simulated"))),
                         len(asset), len(component), len(model)) + asset + component + model
    except (KeyError, ValueError, TypeError, OverflowError, struct.error) as exc:
        raise LoraProtocolError(f"invalid diagnostic: {exc}") from exc
    packet = HEADER.pack(MAGIC, PROTOCOL_VERSION, len(body)) + body
    return packet + struct.pack("<H", crc16(packet))


def decode_diagnostic(frame: bytes) -> LoraDiagnostic:
    if not HEADER.size + BODY.size + 2 <= len(frame) <= MAX_FRAME_SIZE:
        raise LoraProtocolError("invalid v2 diagnostic frame length")
    payload = _checked(frame)
    magic, version, length = HEADER.unpack_from(payload)
    if magic != MAGIC or version != PROTOCOL_VERSION or length != len(payload) - HEADER.size:
        raise LoraProtocolError("invalid framing or protocol version; upgrade both peers to v2")
    event, seq, timestamp, fault, confidence, status, flags, na, nc, nm = BODY.unpack_from(payload, HEADER.size)
    strings = payload[HEADER.size + BODY.size:]
    if len(strings) != na + nc + nm or max(na, nc, nm) > 128 or not (confidence <= 100 or confidence == 255) or flags > 1:
        raise LoraProtocolError("invalid diagnostic lengths or values")
    try:
        datetime.fromtimestamp(timestamp / 1000, timezone.utc)
        asset, component = strings[:na].decode("ascii"), strings[na:na+nc].decode("ascii")
        _identifier(asset, "asset_id"); _identifier(component, "component_id")
        return LoraDiagnostic(str(UUID(bytes=event)), asset, component,
                              {v: k for k, v in FAULT_IDS.items()}[fault],
                              None if confidence == 255 else confidence, seq, timestamp,
                              {v: k for k, v in STATUS_IDS.items()}[status],
                              strings[na+nc:].decode("utf-8") or None, bool(flags))
    except (KeyError, UnicodeError, ValueError, OverflowError, OSError) as exc:
        raise LoraProtocolError("unsupported diagnostic field") from exc


def encode_ack(packet: LoraDiagnostic) -> bytes:
    body = ACK_BODY.pack(b"AK", PROTOCOL_VERSION, UUID(packet.event_id).bytes, packet.sequence)
    return body + struct.pack("<H", crc16(body))


def ack_matches(frame: bytes, packet: LoraDiagnostic) -> bool:
    if len(frame) != ACK_SIZE:
        return False
    try:
        magic, version, event, sequence = ACK_BODY.unpack(_checked(frame))
        return magic == b"AK" and version == PROTOCOL_VERSION and event == UUID(packet.event_id).bytes and sequence == packet.sequence
    except (LoraProtocolError, ValueError, struct.error):
        return False


def gateway_diagnostic(packet: LoraDiagnostic) -> dict:
    result = unavailable_diagnosis(packet.asset_id, packet.component_id,
        "Radio summary: full probabilities and capture details remain in the Pi journal.", status=packet.status)
    result.update(event_id=packet.event_id,
                  timestamp=datetime.fromtimestamp(packet.timestamp_ms / 1000, timezone.utc).isoformat(),
                  fault_class=packet.fault_class,
                  fault_location={"inner_race_damage": "inner_race", "outer_race_damage": "outer_race", "rolling_element_damage": "rolling_element"}.get(packet.fault_class),
                  confidence=None if packet.confidence_percent is None else packet.confidence_percent / 100,
                  model_version=packet.model_version, simulated=packet.simulated,
                  requires_technician_confirmation=packet.status != "HEALTHY_WITHIN_VALIDATED_SCOPE",
                  evidence=[{"kind": "lora_diagnostic_summary", "source": "lora_uart", "transport": "lora_p2p",
                             "historical_replay": packet.simulated, "sequence": packet.sequence, "protocol_version": 2}])
    return result
