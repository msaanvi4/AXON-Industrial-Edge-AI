"""Capture/replay -> local bearing model -> durable outbox -> LoRa -> API.

The capture provider must use a hardware-clocked ADC. This module never claims
Python GPIO timing is a 64 kS/s ADC driver. See docs/DEMO.md for working replay.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time

import requests

from edge.bearing_inference import diagnose_payload
from edge.diagnostic_journal import BoundedDiagnosticJournal
from edge.lora_gateway import GatewaySpool, accept_frame
from edge.lora_transport import LoraOutbox, TransparentUartLora, send_pending_once
from ml.bearing.bundle import BearingBundleError, load_bearing_bundle
from shared.diagnosis import unavailable_diagnosis

ROOT = Path(__file__).resolve().parents[1]


class LoopbackRadio:
    """Exercise the real wire codec, durable gateway and ACK without a radio."""
    def __init__(self, spool):
        self.spool, self.ack = spool, b""

    def send(self, frame):
        self.ack = accept_frame(frame, self.spool, source="lora_protocol_loopback")

    def receive(self, size):
        return self.ack[:size]

    def close(self):
        pass


def capture_window(command: list[str], timeout: float = 10) -> dict:
    """Run an operator-supplied hardware driver (no shell) returning one JSON window."""
    result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=timeout)
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict) or payload.get("hardware_clocked") is not True:
        raise ValueError("capture provider must confirm hardware_clocked=true")
    if not payload.get("sensor_id") or not payload.get("units"):
        raise ValueError("capture provider must supply sensor_id and calibrated units")
    # Numeric sample-rate and window-length validation is enforced by the model.
    payload["source"] = "hardware_capture_provider"
    payload["capture_transport"] = "external_adc_driver"
    payload["historical_replay"] = False
    return payload


def process_window(payload: dict, bundle, outbox: LoraOutbox, *, model_error=None) -> tuple[dict, str]:
    diagnostic = diagnose_payload(payload, bundle, model_error)
    diagnostic["simulated"] = bool(payload.get("historical_replay", False))
    return diagnostic, outbox.queue(diagnostic)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("paderborn", "capture"), default="paderborn")
    parser.add_argument("--recording", action="append", help="Repeat to alternate recordings in the demo")
    parser.add_argument("--window-index", type=int, default=0)
    parser.add_argument("--capture-command", nargs=argparse.REMAINDER, help="Hardware driver executable and arguments; put this option last")
    parser.add_argument("--transport", choices=("lora", "loopback"), default="lora")
    parser.add_argument("--asset-id", default=os.getenv("AXON_ASSET_ID", "demo-motor-001"))
    parser.add_argument("--component-id", default="bearing-drive-end")
    parser.add_argument("--model-dir", type=Path, default=Path(os.getenv("AXON_BEARING_MODEL_DIR", ROOT / "ml/artifacts/bearing/active")))
    parser.add_argument("--port", default="/dev/serial0")
    parser.add_argument("--baudrate", type=int, default=9600)
    parser.add_argument("--api-url", default=os.getenv("BACKEND_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--outbox", type=Path, default=ROOT / "edge/runtime/bearing-outbox-v2.sqlite")
    parser.add_argument("--spool", type=Path, default=ROOT / "edge/runtime/bearing-gateway-v2.sqlite")
    parser.add_argument("--journal", type=Path, default=ROOT / "edge/runtime/bearing-events.jsonl")
    parser.add_argument("--count", type=int, default=0, help="0 runs until Ctrl+C")
    parser.add_argument("--interval", type=float, default=3)
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args()
    if args.count < 0 or args.interval < 0 or args.attempts < 1:
        parser.error("count/interval must be nonnegative; attempts must be positive")
    if args.source == "capture" and not args.capture_command:
        parser.error("capture requires --capture-command for your selected sensor/ADC driver")
    # Documented healthy/damaged recordings make the continuous integration
    # demonstration visibly exercise both machine states. They are explicitly
    # historical replay examples, not an unseen-bearing accuracy demonstration.
    recordings = args.recording or ["N09_M07_F10_K001_1", "N09_M07_F10_KA04_1"]
    bundle, model_error = None, None
    try:
        bundle = load_bearing_bundle(args.model_dir)
    except BearingBundleError as exc:
        model_error = str(exc)
    outbox = LoraOutbox(args.outbox)
    journal = BoundedDiagnosticJournal(args.journal)
    spool = GatewaySpool(args.spool) if args.transport == "loopback" else None
    radio = LoopbackRadio(spool) if spool else None
    print(f"AXON runner: source={args.source}, transport={args.transport}, asset={args.asset_id}", flush=True)
    index = 0
    try:
        while args.count == 0 or index < args.count:
            payload = {"asset_id": args.asset_id, "component_id": args.component_id,
                       "historical_replay": args.source == "paderborn" or args.transport == "loopback"}
            recording = None
            try:
                if args.source == "paderborn":
                    from backend.paderborn_replay import read_window
                    recording = recordings[index % len(recordings)]
                    record, signal, frequency = read_window(recording, args.window_index)
                    payload.update(signal=signal, sampling_frequency_hz=frequency,
                                   source="paderborn_dataset_replay", capture_transport="local_mat_file")
                else:
                    payload.update(capture_window(args.capture_command))
                    payload.update(asset_id=args.asset_id, component_id=args.component_id,
                                   historical_replay=args.transport == "loopback")
                diagnostic, event_id = process_window(payload, bundle, outbox, model_error=model_error)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                diagnostic = unavailable_diagnosis(args.asset_id, args.component_id, str(exc), status="SENSOR_DATA_INVALID")
                diagnostic["simulated"] = bool(payload["historical_replay"])
                event_id = outbox.queue(diagnostic)
            journal.append(diagnostic)
            # Queue BEFORE opening the UART: an unplugged radio cannot lose the event.
            delivery = "pending_radio"
            try:
                if radio is None:
                    radio = TransparentUartLora(args.port, args.baudrate)
                for _ in range(args.attempts):
                    send_pending_once(outbox, radio)
                    if outbox.is_acknowledged(event_id):
                        delivery = "gateway_persisted"
                        break
                if spool:
                    spool.deliver_pending(args.api_url)
                    delivery = "api_delivered" if outbox.is_acknowledged(event_id) else delivery
            except requests.RequestException as exc:
                delivery = f"gateway_persisted_api_pending: {exc}"
            except (OSError, RuntimeError) as exc:
                delivery = f"pending_radio: {exc}"
                if radio:
                    radio.close()
                radio = None
            print(json.dumps({"event_id": event_id, "recording": recording,
                              "asset_id": args.asset_id, "status": diagnostic["status"],
                              "fault_class": diagnostic["fault_class"], "confidence": diagnostic["confidence"],
                              "simulated": diagnostic["simulated"], "delivery": delivery}), flush=True)
            index += 1
            if args.count == 0 or index < args.count:
                time.sleep(args.interval)
    finally:
        if radio:
            radio.close()
        outbox.close()
        if spool:
            spool.close()


if __name__ == "__main__":
    main()
