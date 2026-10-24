# Kickoff: Person 3 (Dashboard and demo, Laptop 3)

Your first hour. Tick each step before moving on.

## 1. Get the code
    git clone https://github.com/Yuvraj0967/axon.git
    cd axon
    git checkout -b feature/dashboard

## 2. Set up Python
    cd dashboard
    python -m venv .venv
    # Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
    pip install -r requirements.txt

## 3. Develop on mock data (P3-1)
    python make_mock.py
    # macOS/Linux
    USE_MOCK=1 streamlit run app.py
    # Windows (cmd)
    set USE_MOCK=1
    streamlit run app.py
You should see a critical health banner, three charts, an alert row and the control buttons.

## 4. Build out the dashboard (P3-2, P3-3)
- Charts and alert feed (P3-2).
- Health panel, operator controls and command history (P3-3).
- An empty or unreachable backend must not crash the page; it should say it is waiting for data.

## 5. Point at the real backend (when Person 2 shares the IP)
    # macOS/Linux
    BACKEND_IP=<LAPTOP2_IP> streamlit run app.py
    # Windows (cmd)
    set USE_MOCK=0
    set BACKEND_IP=<LAPTOP2_IP>
    streamlit run app.py

## 6. Demo, README, backup (P3-4)
- Write the README with the architecture and a screenshot.
- Run the demo script from your guide three times and keep time.
- Record a 2 to 3 minute screen recording of a good run as a backup.
- Add a final architecture slide and a dashboard screenshot to the Canva deck.

## 7. Push your work
    git add dashboard docs
    git commit -m "Dashboard and demo materials"
    git push -u origin feature/dashboard
Open a pull request into main.

## Message to post in the group chat
> Dashboard is running on mock data. Person 2, please send the Laptop 2 IP and tell me when /readings, /alerts, /health, /commands and /command work, and I will switch to the real API.
