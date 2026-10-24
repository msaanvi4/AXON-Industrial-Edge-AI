from shared.health import compute_health


def test_health_status_thresholds_and_missing_rows():
    assert compute_health([])["status"] == "healthy"
    normal = [{"temp": 73, "vibration": 0.3} for _ in range(10)]
    faulty = [{"temp": 100, "vibration": 0.9} for _ in range(20)]
    assert compute_health(normal)["score"] >= 90
    assert compute_health(faulty, n_recent_alerts=10)["status"] == "critical"
