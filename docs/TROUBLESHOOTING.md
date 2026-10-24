# Troubleshooting

- LoRa diagnostic not received: confirm both radios have antennas, matching transparent-UART settings, the configured 865–867 MHz channel, and that the Pi outbox/gateway spool paths are writable.
- Dashboard unavailable: open `http://<backend-ip>:8000/docs` from Laptop 3 and confirm the API was started with `--host 0.0.0.0`.
- No data: use `python backend/fake_edge.py --count 3` to isolate broker/backend ingestion from the real simulator.
- Edge will not start: generate `ml/artifacts/latest` with `python -m ml.training.train --generate-synthetic` and install `edge/requirements.txt`.
- Docker services fail: inspect `docker compose logs`, confirm port 8086 is unused, and replace default development passwords in `.env`. If local port 5432 is already occupied, set `POSTGRES_PORT=5433` in `.env`.
