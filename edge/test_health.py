"""Deterministic smoke test for the shared health model."""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
from health import compute_health


def main():
    random.seed(42)
    normal = [
        {"temp": 73 + random.gauss(0, 1), "vibration": 0.3 + random.gauss(0, 0.03)}
        for _ in range(60)
    ]
    rising = [
        {"temp": 73 + i * 0.25, "vibration": 0.3 + i * 0.002}
        for i in range(60)
    ]
    faulty = normal[:40] + [
        {"temp": 98 + random.gauss(0, 1), "vibration": 0.9} for _ in range(20)
    ]

    results = {
        "normal": compute_health(normal, 0),
        "rising": compute_health(rising, 2),
        "faulty": compute_health(faulty, 10),
    }
    assert results["normal"]["status"] == "healthy"
    assert 90 <= results["normal"]["score"] <= 100
    assert results["rising"]["status"] in {"healthy", "warning"}
    assert results["rising"]["minutes_to_limit"] is not None
    assert results["faulty"]["status"] == "critical"
    assert results["faulty"]["score"] < 50

    for name, result in results.items():
        print(f"{name}: {result}")
    print("health model checks passed")


if __name__ == "__main__":
    main()
