"""Persist LoRa v2 events before ACK; retry API delivery with stable event IDs."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sqlite3
import time
import requests
from edge.lora_transport import TransparentUartLora
from shared.lora_protocol import LoraProtocolError, decode_diagnostic, encode_ack, gateway_diagnostic


class GatewaySpool:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("CREATE TABLE IF NOT EXISTS received_v2 (event_id TEXT PRIMARY KEY, diagnostic TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0)")
        self.connection.commit()

    def store(self, packet, diagnostic: dict) -> bool:
        raw = json.dumps(diagnostic, sort_keys=True)
        with self.connection:
            row = self.connection.execute("SELECT diagnostic FROM received_v2 WHERE event_id=?", (packet.event_id,)).fetchone()
            if row:
                if row[0] != raw:
                    raise LoraProtocolError("event identity collision; refusing ACK")
                return False  # exact duplicate is already durable and may be ACKed
            self.connection.execute("INSERT INTO received_v2 (event_id, diagnostic) VALUES (?,?)", (packet.event_id, raw))
        return True

    def deliver_pending(self, api_url: str) -> int:
        rows = self.connection.execute("SELECT event_id,diagnostic FROM received_v2 WHERE delivered=0 ORDER BY rowid LIMIT 20").fetchall()
        for event_id, raw in rows:
            response = requests.post(f"{api_url.rstrip('/')}/ingest/diagnostic", json=json.loads(raw), timeout=5)
            response.raise_for_status()
            with self.connection:
                self.connection.execute("UPDATE received_v2 SET delivered=1 WHERE event_id=?", (event_id,))
        # Drain an earlier v1 spool without losing its pending API deliveries.
        legacy = self.connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='received'").fetchone()
        if legacy:
            for rowid, raw in self.connection.execute("SELECT rowid,diagnostic FROM received WHERE delivered=0 LIMIT 20").fetchall():
                response = requests.post(f"{api_url.rstrip('/')}/ingest/diagnostic", json=json.loads(raw), timeout=5)
                response.raise_for_status()
                with self.connection:
                    self.connection.execute("UPDATE received SET delivered=1 WHERE rowid=?", (rowid,))
        return len(rows)

    def close(self):
        self.connection.close()


def accept_frame(frame: bytes, spool: GatewaySpool, *, source: str = "lora_uart") -> bytes:
    packet = decode_diagnostic(frame)
    diagnostic = gateway_diagnostic(packet)
    if source == "lora_protocol_loopback":
        diagnostic["simulated"] = True
        diagnostic["evidence"][0].update(source=source, transport="software_loopback")
    spool.store(packet, diagnostic)
    return encode_ack(packet)


def main() -> None:
    parser = argparse.ArgumentParser(description="AXON LoRa v2 gateway (upgrade both peers together)")
    parser.add_argument("--port", default=os.getenv("LORA_SERIAL_PORT", "/dev/serial0"))
    parser.add_argument("--baudrate", type=int, default=9600)
    parser.add_argument("--api-url", default=os.getenv("BACKEND_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--spool", type=Path, default=Path("edge/runtime/lora-gateway.sqlite"))
    args = parser.parse_args()
    radio, spool = TransparentUartLora(args.port, args.baudrate), GatewaySpool(args.spool)
    print(f"LoRa v2 gateway listening on {args.port}", flush=True)
    try:
        while True:
            try:
                frame = radio.receive_frame()
                if frame:
                    radio.send(accept_frame(frame, spool))
                spool.deliver_pending(args.api_url)
            except (LoraProtocolError, requests.RequestException, OSError, sqlite3.Error) as exc:
                print(f"Gateway delivery deferred: {exc}", flush=True)
                time.sleep(.2)
    finally:
        radio.close(); spool.close()


if __name__ == "__main__":
    main()
