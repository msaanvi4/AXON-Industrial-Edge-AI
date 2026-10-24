from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4
import json
import sqlite3

import pytest
import requests

from edge.bearing_runner import LoopbackRadio, capture_window, process_window
from edge.lora_gateway import GatewaySpool, accept_frame
from edge.lora_transport import LoraOutbox, TransparentUartLora, send_pending_once
from shared.lora_protocol import (ACK_SIZE, LoraProtocolError, ack_matches,
    decode_diagnostic, encode_ack, encode_diagnostic, gateway_diagnostic)


def sample_diagnostic(**overrides):
    return {"event_id": str(uuid4()), "asset_id": "cryo-pump-22",
            "component_id": "bearing-stage-7", "fault_class": "other_bearing_damage",
            "status": "FAULT_SUSPECTED", "confidence": .91, "model_version": "rf-hash-123",
            "timestamp": datetime.now(timezone.utc).isoformat(), **overrides}


def test_arbitrary_identifiers_and_model_version_round_trip():
    diagnostic = sample_diagnostic()
    packet = decode_diagnostic(encode_diagnostic(diagnostic, 42))
    expanded = gateway_diagnostic(packet)
    for key in ("event_id", "asset_id", "component_id", "fault_class", "status", "model_version"):
        assert expanded[key] == diagnostic[key]
    assert expanded["confidence"] == .91


@pytest.mark.parametrize("status,fault,confidence", [
    ("MODEL_UNAVAILABLE", None, None), ("SENSOR_DATA_INVALID", None, None),
    ("DIAGNOSIS_UNCERTAIN", "other_bearing_damage", .69),
    ("DIAGNOSIS_UNCERTAIN", "healthy", .60),
    ("HEALTHY_WITHIN_VALIDATED_SCOPE", "healthy", .99)])
def test_status_is_not_reconstructed_from_fault_class(status, fault, confidence):
    packet = decode_diagnostic(encode_diagnostic(sample_diagnostic(
        status=status, fault_class=fault, confidence=confidence), 1))
    record = gateway_diagnostic(packet)
    assert record["status"] == status
    assert record["fault_class"] == fault
    assert record["confidence"] == confidence


def test_crc_rejects_corrupt_frame_and_stale_ack():
    packet = decode_diagnostic(encode_diagnostic(sample_diagnostic(), 42))
    ack = encode_ack(packet)
    assert len(ack) == ACK_SIZE and ack_matches(ack, packet)
    assert not ack_matches(ack, replace(packet, event_id=str(uuid4())))
    assert not ack_matches(ack, replace(packet, sequence=43))
    frame = bytearray(encode_diagnostic(sample_diagnostic(), 42))
    frame[-3] ^= 1
    with pytest.raises(LoraProtocolError, match="CRC"):
        decode_diagnostic(bytes(frame))


def test_reset_or_wrapped_sequence_does_not_drop_new_events(tmp_path):
    spool = GatewaySpool(tmp_path / "gateway.sqlite")
    one, two = sample_diagnostic(), sample_diagnostic()
    for record in (one, two):
        frame = encode_diagnostic(record, 1)
        assert ack_matches(accept_frame(frame, spool), decode_diagnostic(frame))
    assert spool.connection.execute("SELECT count(*) FROM received_v2").fetchone()[0] == 2
    accept_frame(encode_diagnostic(one, 1), spool)
    assert spool.connection.execute("SELECT count(*) FROM received_v2").fetchone()[0] == 2
    with pytest.raises(LoraProtocolError, match="collision"):
        accept_frame(encode_diagnostic(one | {"confidence": .80}, 1), spool)


def test_outbox_only_clears_the_specific_acknowledged_event(tmp_path):
    outbox = LoraOutbox(tmp_path / "outbox.sqlite")
    spool = GatewaySpool(tmp_path / "gateway.sqlite")
    first = outbox.queue(sample_diagnostic())
    second = outbox.queue(sample_diagnostic())
    radio = LoopbackRadio(spool)
    assert send_pending_once(outbox, radio)
    assert outbox.is_acknowledged(first) and not outbox.is_acknowledged(second)
    assert send_pending_once(outbox, radio)
    assert outbox.is_acknowledged(second)


def test_no_ack_keeps_durable_frame_across_restart(tmp_path):
    path = tmp_path / "outbox.sqlite"
    outbox = LoraOutbox(path)
    event_id = outbox.queue(sample_diagnostic())
    class Radio:
        def send(self, frame): pass
        def receive(self, size): return b""
    assert not send_pending_once(outbox, Radio())
    outbox.close()
    reopened = LoraOutbox(path)
    assert reopened.next_pending()[0] == event_id


def test_gateway_keeps_event_when_api_offline_then_retries(monkeypatch, tmp_path):
    spool = GatewaySpool(tmp_path / "gateway.sqlite")
    accept_frame(encode_diagnostic(sample_diagnostic(), 1), spool)
    def offline(*args, **kwargs): raise requests.ConnectionError("offline")
    monkeypatch.setattr(requests, "post", offline)
    with pytest.raises(requests.ConnectionError):
        spool.deliver_pending("http://localhost:8000")
    assert spool.connection.execute("SELECT delivered FROM received_v2").fetchone()[0] == 0
    class Response:
        def raise_for_status(self): pass
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: Response())
    assert spool.deliver_pending("http://localhost:8000") == 1
    assert spool.deliver_pending("http://localhost:8000") == 0


def test_runner_queues_model_failure_and_gateway_preserves_it(tmp_path):
    outbox = LoraOutbox(tmp_path / "outbox.sqlite")
    payload = {"asset_id": "M1", "component_id": "custom-bearing", "historical_replay": True}
    record, event_id = process_window(payload, None, outbox)
    assert record["status"] == "MODEL_UNAVAILABLE"
    packet = decode_diagnostic(outbox.next_pending()[1])
    assert packet.event_id == event_id and packet.simulated
    assert gateway_diagnostic(packet)["status"] == "MODEL_UNAVAILABLE"


def test_capture_provider_must_declare_hardware_clock(monkeypatch):
    class Result:
        stdout = json.dumps({"signal": [0] * 4096, "sampling_frequency_hz": 64000})
    monkeypatch.setattr("edge.bearing_runner.subprocess.run", lambda *a, **kw: Result())
    with pytest.raises(ValueError, match="hardware_clocked"):
        capture_window(["driver"])


def test_uart_accepts_fragmented_frames_and_recovers_after_noise():
    frame = encode_diagnostic(sample_diagnostic(), 1)
    class Serial:
        timeout = .1
        in_waiting = 1
        def __init__(self): self.data = bytearray(b"noise" + frame + frame)
        def read(self, count):
            data = bytes(self.data[:3])
            del self.data[:3]
            return data
    radio = TransparentUartLora.__new__(TransparentUartLora)
    radio.serial, radio._buffer = Serial(), bytearray()
    assert radio.receive_frame() == frame
    assert radio.receive_frame() == frame
