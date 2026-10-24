"""Durable event outbox and framed transparent-UART LoRa transport."""
from __future__ import annotations
from dataclasses import replace
import sqlite3
from pathlib import Path
import time
from shared.lora_protocol import (ACK_SIZE, HEADER, MAGIC, PROTOCOL_VERSION, MAX_FRAME_SIZE,
                                  LoraProtocolError, ack_matches, decode_diagnostic, encode_diagnostic)


class LoraOutbox:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("CREATE TABLE IF NOT EXISTS outbox_v2 (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, frame BLOB NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0)")
        self.connection.commit()
        legacy = self.connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='outbox'").fetchone()
        if legacy and self.connection.execute("SELECT COUNT(*) FROM outbox WHERE acknowledged=0").fetchone()[0]:
            raise LoraProtocolError("Unsent v1 frames retained. Use a new --outbox path for v2; inspect the old journal before archiving it.")

    def queue(self, diagnostic: dict) -> str:
        with self.connection:
            next_id = self.connection.execute("SELECT COALESCE(MAX(id),0)+1 FROM outbox_v2").fetchone()[0]
            frame = encode_diagnostic(diagnostic, next_id & 0xFFFF)
            packet = decode_diagnostic(frame)
            existing = self.connection.execute("SELECT frame FROM outbox_v2 WHERE event_id=?", (packet.event_id,)).fetchone()
            if existing:
                previous = decode_diagnostic(existing[0])
                if replace(packet, sequence=previous.sequence) != previous:
                    raise LoraProtocolError("event_id was reused for a different diagnostic")
                return packet.event_id
            self.connection.execute("INSERT INTO outbox_v2 (event_id,frame) VALUES (?,?)", (packet.event_id, frame))
        return packet.event_id

    def next_pending(self):
        return self.connection.execute("SELECT event_id, frame FROM outbox_v2 WHERE acknowledged=0 ORDER BY id LIMIT 1").fetchone()

    def acknowledge(self, event_id: str) -> None:
        with self.connection:
            self.connection.execute("UPDATE outbox_v2 SET acknowledged=1 WHERE event_id=?", (event_id,))

    def is_acknowledged(self, event_id: str) -> bool:
        row = self.connection.execute("SELECT acknowledged FROM outbox_v2 WHERE event_id=?", (event_id,)).fetchone()
        return bool(row and row[0])

    def close(self):
        self.connection.close()


class TransparentUartLora:
    def __init__(self, port: str, baudrate: int = 9600, timeout: float = 3):
        import serial
        self.serial = serial.Serial(port=port, baudrate=baudrate, timeout=timeout, write_timeout=timeout)
        self._buffer = bytearray()

    def send(self, payload: bytes) -> None:
        if self.serial.write(payload) != len(payload):
            raise OSError("LoRa UART write was incomplete")
        self.serial.flush()

    def receive(self, size: int) -> bytes:
        return self.serial.read(size)

    def receive_frame(self) -> bytes:
        """Keep partial reads across timeouts; resynchronize after UART noise."""
        deadline = time.monotonic() + (self.serial.timeout or 3)
        while True:
            while len(self._buffer) >= HEADER.size:
                start = self._buffer.find(MAGIC)
                if start < 0:
                    self._buffer[:] = self._buffer[-1:]
                    break
                del self._buffer[:start]
                if len(self._buffer) < HEADER.size:
                    break
                _, version, length = HEADER.unpack_from(self._buffer)
                total = HEADER.size + length + 2
                if version != PROTOCOL_VERSION or not HEADER.size + 2 < total <= MAX_FRAME_SIZE:
                    del self._buffer[0]
                    continue
                if len(self._buffer) < total:
                    break
                frame = bytes(self._buffer[:total])
                try:
                    decode_diagnostic(frame)
                except LoraProtocolError:
                    del self._buffer[0]
                    continue
                del self._buffer[:total]
                return frame
            if time.monotonic() >= deadline:
                return b""
            self._buffer.extend(self.serial.read(max(1, self.serial.in_waiting)))

    def close(self):
        self.serial.close()


def send_pending_once(outbox: LoraOutbox, radio) -> bool:
    pending = outbox.next_pending()
    if not pending:
        return True
    event_id, frame = pending
    packet = decode_diagnostic(frame)
    radio.send(frame)
    if ack_matches(radio.receive(ACK_SIZE), packet):
        outbox.acknowledge(event_id)
        return True
    return False
