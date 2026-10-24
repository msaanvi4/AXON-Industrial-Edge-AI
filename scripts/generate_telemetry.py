"""Send configurable telemetry through the gateway API using Axon's model preprocessing.

This is a load and failure-injection tool, not a second application.  Valid
events retain Axon's frozen telemetry contract and use the production artifact
for alert decisions; deliberately malformed events test ingestion rejection.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
import sys
import time

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ml.inference.predictor import load_predictor

DEFAULTS = {"temp": 75.0, "vibration": 0.3, "current": 4.5}


def observation(mode: str, sequence: int, rng: random.Random) -> dict[str, float]:
    """Create a legacy-contract reading with a controlled fault shape."""
    values = {
        "temp": 75 + rng.gauss(0, 0.7),
        "vibration": 0.3 + rng.gauss(0, 0.025),
        "current": 4.5 + rng.gauss(0, 0.12),
    }
    if mode == "drift":
        values["temp"] += min(20, sequence * 0.03)
        values["vibration"] += min(0.3, sequence * 0.0005)
    elif mode in {"spike", "sustained"}:
        values["temp"] += 25
        values["vibration"] *= 3
        values["current"] *= 1.4
    return values


def telemetry_payload(machine_id: str, values: dict[str, float], timestamp: datetime, mode: str) -> dict:
    payload = {"machine_id": machine_id, "ts": timestamp.isoformat()}
    payload.update({name: round(values[name], 5) for name in DEFAULTS})
    if mode == "missing":
        payload.pop("current")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--devices", type=int, default=1)
    parser.add_argument("--messages-per-second", type=float, default=5)
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument("--mode", choices=("normal", "drift", "spike", "sustained", "missing", "duplicate", "out_of_order", "delayed", "bursty"), default="normal")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--payload-padding-bytes", type=int, default=0)
    parser.add_argument("--artifact", type=Path, default=PROJECT_ROOT / "ml" / "artifacts" / "latest")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "test-results" / "edge" / "telemetry.json")
    args = parser.parse_args()
    if args.devices < 1 or args.messages_per_second <= 0 or args.duration <= 0 or args.payload_padding_bytes < 0:
        raise ValueError("devices, rate, duration, and payload padding must be positive")

    predictor = load_predictor(args.artifact)
    if tuple(predictor.features) != tuple(DEFAULTS):
        raise ValueError("the telemetry contract supports temp, vibration, current; use ml/artifacts/latest")
    rng = random.Random(args.seed)
    started = time.monotonic()
    interval = 1 / args.messages_per_second
    counts = {"published": 0, "publish_errors": 0, "duplicates": 0, "malformed": 0, "alerts": 0}
    sequence = 0
    try:
        while time.monotonic() - started < args.duration:
            for device in range(args.devices):
                machine_id = f"M{device + 1}"
                timestamp = datetime.now(timezone.utc)
                if args.mode == "out_of_order":
                    timestamp -= timedelta(seconds=30)
                elif args.mode == "delayed":
                    timestamp -= timedelta(seconds=5)
                values = observation(args.mode, sequence, rng)
                payload = telemetry_payload(machine_id, values, timestamp, args.mode)
                if args.payload_padding_bytes:
                    payload["padding"] = "x" * args.payload_padding_bytes
                response = requests.post(f"{args.api_url.rstrip('/')}/ingest/telemetry", json=payload, timeout=3)
                if response.ok:
                    counts["published"] += 1
                else:
                    counts["publish_errors"] += 1
                if args.mode == "missing" or args.payload_padding_bytes:
                    counts["malformed"] += 1
                else:
                    decision = predictor.predict_one(values)
                    if decision["is_anomaly"]:
                        alert = {"machine_id": machine_id, "ts": timestamp.isoformat(), "type": "anomaly", "score": decision["anomaly_score"]}
                        requests.post(f"{args.api_url.rstrip('/')}/ingest/alert", json=alert, timeout=3).raise_for_status()
                        counts["alerts"] += 1
                if args.mode == "duplicate":
                    requests.post(f"{args.api_url.rstrip('/')}/ingest/telemetry", json=payload, timeout=3).raise_for_status()
                    counts["duplicates"] += 1
            sequence += 1
            if args.mode == "bursty":
                time.sleep(max(interval * 5, 0.01))
            else:
                time.sleep(max(interval, 0.001))
    except KeyboardInterrupt:
        pass
    elapsed = time.monotonic() - started
    result = {"measurement_kind": "synthetic HTTP telemetry generation", "api_url": args.api_url, "mode": args.mode, "seed": args.seed, "devices": args.devices, "requested_messages_per_second": args.messages_per_second, "elapsed_seconds": round(elapsed, 3), "counts": counts, "limitations": ["LoRa radio impairment is not injected by this host-only tool.", "Malformed events intentionally exercise backend validation and do not receive an inference decision."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
