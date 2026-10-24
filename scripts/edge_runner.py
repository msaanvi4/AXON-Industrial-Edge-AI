"""Portable command runner behind scripts/edge_test.sh."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "test-results" / "edge"


def profile(name: str) -> dict:
    return json.loads((ROOT / "config" / "edge_profiles" / f"{name}.json").read_text(encoding="utf-8"))


def audit() -> None:
    def command_version(command: list[str]) -> str | None:
        if not shutil.which(command[0]):
            return None
        try:
            return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT, timeout=10).strip()
        except (OSError, subprocess.SubprocessError):
            return "installed but unavailable"
    report = {
        "host": {"system": platform.system(), "release": platform.release(), "machine": platform.machine(), "python": platform.python_version()},
        "tools": {"docker": command_version(["docker", "--version"]), "docker_compose": command_version(["docker", "compose", "version"]), "docker_buildx": command_version(["docker", "buildx", "version"]), "stress_ng": command_version(["stress-ng", "--version"])},
        "profiles": [profile(name) for name in ("pi3", "pi4", "pi5")],
        "conclusion": "Host capability audit only. ARM64 execution and cgroup enforcement require a Linux/Docker runtime and are not inferred from this result.",
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


def run_benchmark(name: str, stress: bool) -> None:
    target = RESULTS / (f"stress-{name}.json" if stress else f"inference-{name}.json")
    command = [sys.executable, str(ROOT / "scripts" / "benchmark_inference.py"), "--profile", name, "--output", str(target)]
    if stress:
        command.extend(["--concurrency", str(profile(name)["cpu_cores"]), "--iterations", "5000"])
    subprocess.run(command, check=True)


def report() -> None:
    available = sorted(path.name for path in RESULTS.glob("*.json")) if RESULTS.exists() else []
    print(json.dumps({"result_directory": str(RESULTS), "files": available, "note": "Read raw JSON for measurement provenance and limitations."}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("audit", "benchmark", "stress", "report"))
    parser.add_argument("--profile", choices=("pi3", "pi4", "pi5"), default="pi4")
    args = parser.parse_args()
    if args.command == "audit":
        audit()
    elif args.command == "benchmark":
        run_benchmark(args.profile, False)
    elif args.command == "stress":
        run_benchmark(args.profile, True)
    else:
        report()


if __name__ == "__main__":
    main()
