"""HTTP edge simulator for Axon's machine M1; LoRa hardware uses lora_gateway.py."""

import os
import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
import sys

import requests

# Enable both `python edge/simulator.py` from the repository root and
# `python simulator.py` from the edge directory.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from ml.inference.predictor import ArtifactLoadError, load_predictor

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
MID = os.getenv("MACHINE_ID", "M1")
PUBLISH_INTERVAL = float(os.getenv("PUBLISH_INTERVAL", "1"))
state = {"running": True, "speed": 1.0, "fault": False}


def normal_sample(speed):
    """Return a realistic [temperature, vibration, current] sample."""
    return [
        70 + 5 * speed + random.gauss(0, 1),
        0.3 * speed + random.gauss(0, 0.03),
        4.5 * speed + random.gauss(0, 0.2),
    ]


def apply_fault(sample):
    """Apply the simulated bearing fault to a normal sample."""
    temp, vibration, current = sample
    return temp + 25, vibration * 3, current * 1.4


def command_handler(command):
    """Update simulator state from a backend command."""
    action = command.get("action")
    if action == "slow_down":
        state["speed"] = 0.6
    elif action == "shutdown":
        state["running"] = False
    elif action == "resume":
        state.update(running=True, speed=1.0, fault=False)
    else:
        print("IGNORING UNKNOWN ACTION:", action)
        return
    print("COMMAND RECEIVED:", command)


def toggle_fault_from_stdin():
    """Allow a local operator to toggle the fault with ``f`` + Enter."""
    while True:
        try:
            if input().strip().lower() == "f":
                state["fault"] = not state["fault"]
                print("FAULT:", state["fault"])
        except (EOFError, KeyboardInterrupt):
            return


def build_model():
    """Load the validated, offline-trained edge model artifact."""
    artifact_dir = Path(
        os.getenv("AXON_MODEL_DIR", Path(__file__).resolve().parents[1] / "ml" / "artifacts" / "latest")
    )
    try:
        model = load_predictor(artifact_dir)
    except ArtifactLoadError as exc:
        raise RuntimeError(
            f"Cannot start edge inference: {exc}. Run `python -m ml.training.train` first."
        ) from exc
    print(f"loaded anomaly model {model.metadata['model_version']} from {artifact_dir}")
    return model


def is_anomaly(model, features):
    """Return the persisted, calibrated decision and anomaly score."""
    result = model.predict_one({"temp": features[0][0], "vibration": features[0][1], "current": features[0][2]})
    return result["is_anomaly"], result["anomaly_score"]


def publish_reading(model):
    """Post one telemetry reading and alert through the gateway API."""
    sample = normal_sample(state["speed"])
    if state["fault"]:
        sample = apply_fault(sample)
    temp, vibration, current = sample
    timestamp = datetime.now(timezone.utc).isoformat()
    telemetry = {
        "machine_id": MID,
        "ts": timestamp,
        "temp": round(temp, 2),
        "vibration": round(vibration, 3),
        "current": round(current, 2),
    }
    requests.post(f"{BACKEND_URL.rstrip('/')}/ingest/telemetry", json=telemetry, timeout=3).raise_for_status()

    features = [[temp, vibration, current]]
    anomaly, score = is_anomaly(model, features)
    if anomaly:
        alert = {
            "machine_id": MID,
            "ts": timestamp,
            "type": "anomaly",
            "score": round(score, 2),
        }
        requests.post(f"{BACKEND_URL.rstrip('/')}/ingest/alert", json=alert, timeout=3).raise_for_status()
        return telemetry, alert
    return telemetry, None


def poll_commands(seen):
    try:
        commands = requests.get(f"{BACKEND_URL.rstrip('/')}/commands", params={"machine_id": MID}, timeout=3).json()
        for command in reversed(commands):
            token = command.get("created")
            if token not in seen:
                command_handler(command); seen.add(token)
    except requests.RequestException as exc:
        print("COMMAND POLL UNAVAILABLE:", exc)


def main():
    model = build_model()
    threading.Thread(target=toggle_fault_from_stdin, daemon=True).start()
    seen = set()
    print(f"simulator running for {MID}; API={BACKEND_URL}; type f to toggle a fault")

    try:
        while True:
            poll_commands(seen)
            if state["running"]:
                publish_reading(model)
            time.sleep(PUBLISH_INTERVAL)
    except KeyboardInterrupt:
        print("stopping simulator")


if __name__ == "__main__":
    main()
