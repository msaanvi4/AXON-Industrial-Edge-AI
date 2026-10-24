"""Print new diagnostic records arriving through the LoRa gateway API."""
import os
import time

import requests

api = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
asset = os.getenv("AXON_ASSET_ID", os.getenv("ASSET_ID", "demo-motor-001"))
seen = set()
print(f"watching LoRa-backed diagnostics for {asset} at {api}")
while True:
    response = requests.get(f"{api}/diagnostics/history", params={"asset_id": asset, "limit": 20}, timeout=5)
    response.raise_for_status()
    for event in reversed(response.json()):
        if event["event_id"] not in seen:
            print(event); seen.add(event["event_id"])
    time.sleep(2)
