# Backend and data

    docker compose up -d
From the repository root:

    python -m pip install -r backend/requirements.txt
    python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

For the presentation use scripts/start_demo.ps1, which explicitly selects
memory mode and checks the packaged bearing bundle. The legacy fake_edge.py
feeds synthetic HTTP telemetry only; use edge.bearing_runner for bearing data.

`/health` imports `../shared/health.py` (Person 1).
InfluxDB UI: http://localhost:8086 (admin / adminpass123, demo only).
