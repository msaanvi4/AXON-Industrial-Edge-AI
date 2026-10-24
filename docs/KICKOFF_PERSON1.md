# Kickoff: Person 1 (Edge layer and ML, Laptop 1)

Your first hour. Tick each step before moving on.

## 1. Get the code
    git clone https://github.com/Yuvraj0967/axon.git
    cd axon
    git checkout -b feature/edge

## 2. Set up Python
    cd edge
    python -m venv .venv
    # Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
    pip install -r requirements.txt

## 3. Check the health model (P1-4 starter)
    python test_health.py
Expected: normal about 100 healthy, rising about 70 warning, faulty about 0 critical.

## 4. Configure the paired LoRa radios
Connect the Waveshare SX1262/Ebyte E22 UART radio to the Pi 4, attach its
antenna before power-up, and configure the same 865–867 MHz channel and baud
rate on the matching gateway receiver.

## 5. Run and watch the simulator (P1-2, P1-3)
Terminal A: `python simulator.py`
Terminal B: `python listen.py`
- You should see telemetry every second.
- Type `f` + Enter in terminal A. Alerts should start appearing.
- Terminal C: `python send_command.py slow_down`, then `shutdown`, then `resume`. The simulator should print COMMAND RECEIVED and its behaviour should change.

## 6. Tune the health model (P1-4)
Edit the thresholds in `shared/health.py` until a normal machine scores 90 to 100 and a faulted one falls below 50. Do not change the function name or the returned keys: Person 2 and Person 3 depend on them.

## 7. Push your work
    git add edge shared
    git commit -m "Edge simulator and health model"
    git push -u origin feature/edge
Open a pull request into main and tell Person 2 that `shared/health.py` is ready.

## At integration (gateway/API is running)
    export BACKEND_URL=http://<LAPTOP2_IP>:8000      # Windows: set BACKEND_URL=http://<LAPTOP2_IP>:8000
    python simulator.py

## Message to post in the group chat
> Repo is up: https://github.com/Yuvraj0967/axon. Please clone it, read docs/contract.md (it is frozen) and work only in your own folder on your own branch. Person 2: shared/health.py is the health model for your /health endpoint. I will push the simulator today.
