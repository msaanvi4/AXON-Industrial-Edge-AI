# Backend and data

    docker compose up -d
From the repository root:

    python -m pip install -r backend/requirements.txt
    python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

For the presentation use scripts/start_demo.ps1, which explicitly selects
memory mode and checks the packaged bearing bundle. The legacy fake_edge.py
feeds synthetic HTTP telemetry only; use edge.bearing_runner for bearing data.

The AXON Verify frontend is served by FastAPI at `/app/`; its authenticated
`/v1` API uses the same active Random Forest bundle as `/diagnostics/infer`.
Vibration uploads and Paderborn replay pass through `backend.ingestion`, which
records the model version, input contract, raw scores, and calibration state.
By default scores are uncalibrated unless a real calibration artifact is
configured. Memory mode is for local demos; external mode persists verification
accounts and evidence to the SQLite database configured by `AXON_VERIFY_DB`.

`/health` imports `../shared/health.py` (Person 1).
InfluxDB UI: http://localhost:8086 (admin / adminpass123, demo only).
