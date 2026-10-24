# Architecture

Axon separates offline ML training from edge inference. `ml/data` creates or validates datasets, `ml/training` fits and calibrates a scikit-learn Isolation Forest, `ml/artifacts/latest` holds the trusted model and metadata, and `ml/inference` validates artifacts and scores each edge observation.

For bearing diagnosis, the Raspberry Pi 4 captures the compatible local vibration window, performs inference locally, and stores the compact diagnostic frame in SQLite before transparent-UART LoRa transmission. The gateway validates CRC-16, de-duplicates `(asset, sequence)`, acknowledges the frame, and forwards the typed record to the API.

The backend validates typed HTTP records from the gateway before storage and exposes contract endpoints. The dashboard never falls back to mock data when a live backend fails. LoRa acknowledgement confirms gateway receipt, not physical repair or actuator execution.

For a distributed deployment, the Pi and gateway communicate over LoRa while the gateway and dashboard use the API. PostgreSQL should remain internal; only the API port 8000 needs LAN access.
