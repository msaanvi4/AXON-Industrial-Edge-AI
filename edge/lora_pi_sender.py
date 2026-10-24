"""Persist and transmit a locally inferred Pi diagnostic using transparent-UART LoRa."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from edge.lora_transport import LoraOutbox, TransparentUartLora, send_pending_once


def queue_and_send(diagnostic: dict, port: str, baudrate: int, outbox_path: Path, attempts: int = 3) -> bool:
    """Queue first, then retry radio transmission until the gateway ACKs."""
    outbox_path.parent.mkdir(parents=True, exist_ok=True)
    outbox = LoraOutbox(outbox_path)
    event_id = outbox.queue(diagnostic)
    radio = None
    try:
        radio = TransparentUartLora(port, baudrate)
        for _ in range(attempts):
            send_pending_once(outbox, radio)
            if outbox.is_acknowledged(event_id):
                return True
        return False
    finally:
        if radio is not None:
            radio.close()
        outbox.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Queue a Pi bearing diagnosis for LoRa delivery")
    parser.add_argument("diagnostic_json", type=Path, help="a locally generated diagnosis JSON record")
    parser.add_argument("--port", default=os.getenv("LORA_SERIAL_PORT", "/dev/serial0"))
    parser.add_argument("--baudrate", type=int, default=int(os.getenv("LORA_BAUDRATE", "9600")))
    parser.add_argument("--outbox", type=Path, default=Path(os.getenv("AXON_LORA_OUTBOX_DB", "edge/runtime/lora-outbox.sqlite")))
    args = parser.parse_args()
    diagnostic = json.loads(args.diagnostic_json.read_text(encoding="utf-8"))
    if not queue_and_send(diagnostic, args.port, args.baudrate, args.outbox):
        raise SystemExit("No LoRa ACK received; the diagnostic remains in the SQLite outbox for retry.")


if __name__ == "__main__":
    main()
