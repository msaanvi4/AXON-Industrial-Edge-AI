import os, sys
import requests

action = sys.argv[1] if len(sys.argv) > 1 else "slow_down"    # slow_down | shutdown | resume
api = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
response = requests.post(f"{api}/command", json={"machine_id": "M1", "action": action, "issued_by": "test"}, timeout=5)
response.raise_for_status()
print(response.json())
