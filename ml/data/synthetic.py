"""Deterministic synthetic machine telemetry for development and evaluation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import random

import numpy as np
import pandas as pd


FEATURES = ("temp", "vibration", "current")


def generate_dataset(rows=3600, seed=42, machine_id="M1"):
    """Generate correlated operating episodes with labeled normal and fault rows.

    This development dataset is synthetic and must not be interpreted as plant data.
    """
    if rows < 100:
        raise ValueError("rows must be at least 100")
    rng = random.Random(seed)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    records = []
    for index in range(rows):
        # A repeating 100-s episode keeps normal and fault periods in every
        # chronological split while retaining contiguous fault sequences.
        phase = (index % 100) // 10
        speed = (0.6, 0.8, 1.0, 1.2)[(index // 45) % 4]
        scenario = "normal"
        label = 0
        temp_offset = vibration_factor = current_offset = 0.0
        if phase == 6:
            scenario, label, temp_offset = "elevated_temperature", 1, 10 + (index % 10) * 0.5
        elif phase == 7:
            scenario, label, vibration_factor = "excessive_vibration", 1, 0.32
        elif phase == 8:
            scenario, label, current_offset = "abnormal_current", 1, 2.4
        elif phase == 9:
            scenario, label = "combined_fault", 1
            temp_offset, vibration_factor, current_offset = 18, 0.38, 1.8
        records.append(
            {
                "ts": (start + timedelta(seconds=index)).isoformat(),
                "machine_id": machine_id,
                "speed": speed,
                "operating_state": "degraded" if label else "running",
                "scenario": scenario,
                "label": label,
                "temp": round(70 + 5 * speed + temp_offset + rng.gauss(0, 1), 3),
                "vibration": round(0.3 * speed + vibration_factor + rng.gauss(0, 0.025), 4),
                "current": round(4.5 * speed + current_offset + rng.gauss(0, 0.18), 3),
            }
        )
    return pd.DataFrame.from_records(records)


def write_synthetic_csv(path, rows=3600, seed=42):
    data = generate_dataset(rows=rows, seed=seed)
    data.to_csv(path, index=False)
    return data
