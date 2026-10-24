import numpy as np

TEMP_LIMIT = 95.0     # degrees at which we consider the machine at risk

def compute_health(readings, n_recent_alerts=0):
    '''readings: list of dicts with temp and vibration, ordered oldest to newest.
    Returns {score, temp_slope, minutes_to_limit, status}.'''
    rows = [r for r in readings if r.get("temp") is not None and r.get("vibration") is not None]
    if len(rows) < 5:
        return {"score": 100, "temp_slope": 0.0, "minutes_to_limit": None, "status": "healthy"}
    temp = np.array([r["temp"] for r in rows], dtype=float)
    vib = np.array([r["vibration"] for r in rows], dtype=float)

    penalty = max(0, temp[-20:].mean() - 76) * 2
    penalty += max(0, vib[-20:].mean() - 0.35) * 100
    penalty += n_recent_alerts * 3
    score = int(max(0, min(100, 100 - penalty)))

    slope = float(np.polyfit(np.arange(len(temp)), temp, 1)[0])    # degrees per sample (~1 s)
    minutes = None
    if slope > 0.05 and temp[-1] < TEMP_LIMIT:    # ignore tiny noise-level slopes
        minutes = round(float((TEMP_LIMIT - temp[-1]) / slope / 60), 1)

    status = "healthy" if score >= 80 else ("warning" if score >= 50 else "critical")
    return {"score": score, "temp_slope": round(slope, 4), "minutes_to_limit": minutes, "status": status}
