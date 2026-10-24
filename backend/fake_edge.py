"""Predictable HTTP telemetry sender for backend integration testing, not the real edge model."""
import argparse, json, os, random, time
from datetime import datetime, timezone

import requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine-id", default="M1")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--scenario", choices=("normal", "fault"), default="normal")
    parser.add_argument("--count", type=int, default=0, help="zero publishes forever")
    args = parser.parse_args()
    random.seed(42)
    api = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
    try:
        for index in range(args.count or 2**31):
            fault = args.scenario == "fault"
            reading = {"machine_id": args.machine_id, "ts": datetime.now(timezone.utc).isoformat(), "temp": round(75 + random.gauss(0, 1) + (25 if fault else 0), 2), "vibration": round(.3 + random.gauss(0, .03) + (.6 if fault else 0), 3), "current": round(4.5 + random.gauss(0, .2) + (2 if fault else 0), 2)}
            requests.post(f"{api}/ingest/telemetry", json=reading, timeout=3).raise_for_status()
            if fault: requests.post(f"{api}/ingest/alert", json={"machine_id":args.machine_id,"ts":reading["ts"],"type":"anomaly","score":0.9}, timeout=3).raise_for_status()
            print(json.dumps(reading)); time.sleep(args.interval)
    except KeyboardInterrupt: pass


if __name__ == "__main__": main()
