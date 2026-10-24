# Testing

Run unit and component tests:

`python -m pytest`

Run artifact training manually:

`python -m ml.training.train --generate-synthetic`

The automated suite covers health status boundaries, LoRa frame CRC and acknowledgement behavior, in-memory backend API handlers, command queueing behavior, and artifact load/inference. Docker integration is not claimed as verified until Docker is available locally:

`cd backend; docker compose up -d`

Then run `python backend/fake_edge.py --scenario fault --count 5` and verify `/readings` and `/alerts` from `http://localhost:8000/docs`.

Synthetic-dataset success is not a measure of real plant accuracy.

Verified locally on 2026-10-17: `python -m pytest` completed with 4 passed tests. The synthetic evaluation reported precision 0.983, recall 0.433, and F1 0.601; these values are recorded only as a synthetic calibration result, not as a real-machine performance claim.
