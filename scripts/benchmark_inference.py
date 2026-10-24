"""Measure Axon's actual serialized Isolation Forest inference path.

This deliberately calls ``ml.inference.predictor.load_predictor`` and
``Predictor.predict_one``; it does not substitute a mock scoring function.
Results are host measurements, not Raspberry Pi measurements.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import statistics
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ml.inference.predictor import load_predictor

try:
    import psutil
except ImportError:  # The result explicitly reports missing process telemetry.
    psutil = None


DEFAULT_VALUES = {
    "temp": 75.0,
    "vibration": 0.3,
    "current": 4.5,
    "pressure": 9.3,
    "oil_temperature": 55.0,
    "motor_current": 4.0,
}


def percentile(values: list[float], percentage: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * percentage / 100
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def profile_config(name: str) -> dict:
    path = PROJECT_ROOT / "config" / "edge_profiles" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def host_info() -> dict:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "processor": platform.processor() or "unreported",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("pi3", "pi4", "pi5"), default="pi4")
    parser.add_argument("--artifact", type=Path, default=PROJECT_ROOT / "ml" / "artifacts" / "latest")
    parser.add_argument("--iterations", type=int, default=2_000)
    parser.add_argument("--warmup", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "test-results" / "edge" / "inference.json")
    args = parser.parse_args()
    if args.iterations < 1 or args.warmup < 0 or args.concurrency < 1:
        raise ValueError("iterations, warmup, and concurrency must be positive")

    process = psutil.Process() if psutil else None
    rss_before = process.memory_info().rss if process else None
    load_started = time.perf_counter_ns()
    predictor = load_predictor(args.artifact)
    load_ms = (time.perf_counter_ns() - load_started) / 1_000_000
    rss_after_load = process.memory_info().rss if process else None
    observation = {name: DEFAULT_VALUES.get(name, 1.0) for name in predictor.features}
    for _ in range(args.warmup):
        predictor.predict_one(observation)

    cpu_before = process.cpu_times() if process else None
    started = time.perf_counter_ns()

    def infer_once(_: int) -> float:
        tick = time.perf_counter_ns()
        predictor.predict_one(observation)
        return (time.perf_counter_ns() - tick) / 1_000_000

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        latencies = list(pool.map(infer_once, range(args.iterations)))
    elapsed_s = (time.perf_counter_ns() - started) / 1_000_000_000
    cpu_after = process.cpu_times() if process else None
    rss_peak = process.memory_info().rss if process else None
    cpu_seconds = None if not process else (cpu_after.user + cpu_after.system - cpu_before.user - cpu_before.system)
    report = {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "measurement_kind": "host inference benchmark; CPU/memory limits are not applied by this script",
        "host": host_info(),
        "profile_target": profile_config(args.profile),
        "artifact": str(args.artifact),
        "model_version": predictor.metadata["model_version"],
        "features": list(predictor.features),
        "workload": {"iterations": args.iterations, "warmup": args.warmup, "concurrency": args.concurrency},
        "model_load_ms": round(load_ms, 4),
        "latency_ms": {
            "p50": round(percentile(latencies, 50), 6),
            "p95": round(percentile(latencies, 95), 6),
            "p99": round(percentile(latencies, 99), 6),
            "mean": round(statistics.fmean(latencies), 6),
        },
        "throughput_predictions_per_second": round(args.iterations / elapsed_s, 3),
        "elapsed_seconds": round(elapsed_s, 6),
        "process_cpu_seconds": None if cpu_seconds is None else round(cpu_seconds, 6),
        "process_rss_bytes": {"before_load": rss_before, "after_load": rss_after_load, "after_run": rss_peak},
        "psutil_available": psutil is not None,
        "limitations": ["No ARM instruction-set emulation or cgroup limit is applied by this Python-only benchmark."]
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
