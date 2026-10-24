# Axon Task Board

About 7 hours per person. Tick a box when the task's "done when" is true.

## Person 1

- [ ] **P1-1** Create repo, folders and docs/contract.md (1.0h)
- [ ] **P1-2** Machine simulator + local anomaly model (3.0h)
- [ ] **P1-3** Command listener + fault injection (1.0h)
- [ ] **P1-4** shared/health.py: health score and trend, with test (2.0h)

## Person 2

- [ ] **P2-1** Network setup, Laptop 2 IP, firewall ports (1.0h)
- [ ] **P2-2** Docker stack: Mosquitto, InfluxDB, PostgreSQL (1.0h)
- [x] **P2-3** FastAPI LoRa-gateway ingest into InfluxDB and PostgreSQL
- [ ] **P2-4** REST endpoints incl. /health and /commands (1.5h)
- [ ] **P2-5** Fake publisher and API test checklist (1.0h)

## Person 3

- [ ] **P3-1** Mock data and dashboard skeleton (1.5h)
- [ ] **P3-2** Live charts and alert feed (1.5h)
- [ ] **P3-3** Health panel, operator controls, command history (2.0h)
- [ ] **P3-4** README, demo lead, backup recording, deck update (2.0h)

## Integration (all three)

- [ ] Stage 1: backend reachable from other laptops (lead: Person 2)
- [ ] Stage 2: live telemetry on the broker (lead: Person 1)
- [ ] Stage 3: storage and REST API verified (lead: Person 2)
- [ ] Stage 4: dashboard shows live charts and health (lead: Person 3)
- [ ] Stage 5: fault produces alerts and critical status (lead: Person 3)
- [ ] Stage 6: dashboard buttons control the simulator (lead: Person 1)
- [ ] Demo rehearsed 3 times, backup recording saved
