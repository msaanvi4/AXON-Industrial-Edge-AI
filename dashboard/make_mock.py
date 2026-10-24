import csv, random
from datetime import datetime, timedelta, timezone

t0 = datetime.now(timezone.utc) - timedelta(seconds=100)
with open("mock_readings.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["ts", "temp", "vibration", "current"])
    for i in range(100):
        bump = 1 if i >= 80 else 0            # last 20 rows look faulty
        w.writerow([(t0 + timedelta(seconds=i)).isoformat(),
                    round(73 + random.gauss(0, 1) + bump * 12, 2),
                    round(0.3 + random.gauss(0, 0.03) + bump * 0.5, 3),
                    round(4.8 + random.gauss(0, 0.2) + bump * 1.5, 2)])
