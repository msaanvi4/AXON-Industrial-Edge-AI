#!/usr/bin/env bash
# Optional: create one GitHub issue per task. Needs the GitHub CLI: gh auth login
# Run once from the repo root after pushing.
set -e
gh issue create --title "P1-1 Create repo, folders and docs/contract.md" --body "Owner: Person 1. Estimate: 1.0 h. See docs/TASKS.md."
gh issue create --title "P1-2 Machine simulator + local anomaly model" --body "Owner: Person 1. Estimate: 3.0 h. See docs/TASKS.md."
gh issue create --title "P1-3 Command listener + fault injection" --body "Owner: Person 1. Estimate: 1.0 h. See docs/TASKS.md."
gh issue create --title "P1-4 shared/health.py: health score and trend, with test" --body "Owner: Person 1. Estimate: 2.0 h. See docs/TASKS.md."
gh issue create --title "P2-1 Network setup, Laptop 2 IP, firewall ports" --body "Owner: Person 2. Estimate: 1.0 h. See docs/TASKS.md."
gh issue create --title "P2-2 Docker stack: Mosquitto, InfluxDB, PostgreSQL" --body "Owner: Person 2. Estimate: 1.0 h. See docs/TASKS.md."
gh issue create --title "P2-3 FastAPI LoRa-gateway ingest into InfluxDB and PostgreSQL" --body "Owner: Person 2. Estimate: 2.5 h. See docs/TASKS.md."
gh issue create --title "P2-4 REST endpoints incl. /health and /commands" --body "Owner: Person 2. Estimate: 1.5 h. See docs/TASKS.md."
gh issue create --title "P2-5 Fake publisher and API test checklist" --body "Owner: Person 2. Estimate: 1.0 h. See docs/TASKS.md."
gh issue create --title "P3-1 Mock data and dashboard skeleton" --body "Owner: Person 3. Estimate: 1.5 h. See docs/TASKS.md."
gh issue create --title "P3-2 Live charts and alert feed" --body "Owner: Person 3. Estimate: 1.5 h. See docs/TASKS.md."
gh issue create --title "P3-3 Health panel, operator controls, command history" --body "Owner: Person 3. Estimate: 2.0 h. See docs/TASKS.md."
gh issue create --title "P3-4 README, demo lead, backup recording, deck update" --body "Owner: Person 3. Estimate: 2.0 h. See docs/TASKS.md."
