import json
from pathlib import Path
import subprocess
import sys

from scripts.generate_telemetry import observation, telemetry_payload

ROOT = Path(__file__).resolve().parents[2]


def test_profiles_are_explicit_and_fit_requested_budgets():
    expected = {"pi3": 1024, "pi4": 4096, "pi5": 4096}
    for name, memory_mb in expected.items():
        profile = json.loads((ROOT / "config" / "edge_profiles" / f"{name}.json").read_text())
        assert profile["cpu_cores"] == 4
        assert profile["memory_mb"] == memory_mb
        assert profile["architecture"].startswith("arm64")


def test_generator_preserves_contract_except_explicit_missing_case():
    values = observation("normal", 1, __import__("random").Random(4))
    valid = telemetry_payload("M1", values, __import__("datetime").datetime.now(__import__("datetime").timezone.utc), "normal")
    missing = telemetry_payload("M1", values, __import__("datetime").datetime.now(__import__("datetime").timezone.utc), "missing")
    assert set(valid) == {"machine_id", "ts", "temp", "vibration", "current"}
    assert "current" not in missing


def test_benchmark_loads_actual_artifact():
    # Keep test artifacts under the ignored workspace directory so this also
    # works in restricted Windows sandboxes without a writable user temp path.
    output = ROOT / "test-results" / "edge" / "pytest-benchmark.json"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "benchmark_inference.py"), "--iterations", "8", "--warmup", "1", "--output", str(output)], check=True, cwd=ROOT)
    report = json.loads(output.read_text())
    assert report["model_version"]
    assert report["features"] == ["temp", "vibration", "current"]
    assert report["throughput_predictions_per_second"] > 0
