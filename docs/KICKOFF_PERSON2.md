# Kickoff: Person 2 (Backend and data, Laptop 2)

Your first hour. Tick each step before moving on.

## 1. Get the code
    git clone https://github.com/Yuvraj0967/axon.git
    cd axon
    git checkout -b feature/backend

## 2. Network (P2-1)
- Join the phone hotspot or shared Wi-Fi. Find your IP: `ipconfig` (Windows), `ip a` or `ifconfig` (macOS/Linux).
- **Send the Laptop 2 IP to Person 1 and Person 3 straight away.** They need it.
- Allow inbound port 8000 in your firewall (8086 and 5432 are optional).
- From their laptops they should be able to `ping <your IP>`.

## 3. Start the data stack (P2-2)
    cd backend
    docker compose up -d
    docker compose ps          # influxdb and postgres should both be running
Give InfluxDB and PostgreSQL 15 to 20 seconds to finish starting before the next step.

## 4. Run the API (P2-3, P2-4)
    python -m venv .venv
    # Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
    pip install -r requirements.txt
    uvicorn main:app --host 0.0.0.0 --port 8000
Open http://localhost:8000/docs. If uvicorn crashes on start with a connection error, the containers were not ready yet: wait a few seconds and run it again.

## 5. Test without Laptop 1 (P2-5)
In a second terminal: `python fake_edge.py`

| Check | Open | Expected |
|---|---|---|
| Telemetry stored | /readings?machine_id=M1&minutes=2 | rows with temp, vibration, current |
| Alerts stored | /alerts?machine_id=M1 | some anomaly rows |
| Health | /health?machine_id=M1 | JSON with score and status |
| Command | POST /command in the Swagger page | message appears on factory/M1/command |
| History | /commands?machine_id=M1 | the command you just sent |
| Remote | http://<LAPTOP2_IP>:8000/docs from Laptop 3 | Swagger loads |

`/health` imports `../shared/health.py` (Person 1's file, included in your folder).
InfluxDB UI: http://localhost:8086 (admin / adminpass123, demo only).

## 6. Push your work
    git add backend
    git commit -m "Backend: ingest, storage and REST API"
    git push -u origin feature/backend
Open a pull request into main.

## Message to post in the group chat
> Laptop 2 IP is <IP>. The API is http://<IP>:8000 (docs at /docs). The LoRa gateway forwards validated radio summaries here.
