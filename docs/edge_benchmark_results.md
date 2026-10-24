# Edge benchmark results

## Scope and provenance

Verified on 2026-10-17 with `scripts/benchmark_inference.py`, Python 3.12.14,
the real `ml/artifacts/latest` `axon-iforest-v1` Isolation Forest, 100 warm
single-event predictions after 10 warmups, seed-free deterministic observation,
and `psutil` process accounting. Raw machine-readable records are committed in
`test-results/edge/`. The host reported Windows 11, `platform.machine()` AMD64,
and an ARMv8 Qualcomm processor string; the interpreter/container architecture
and Linux cgroup enforcement were not established. No Pi, Linux cgroup, Docker
container, or ARM64 image execution occurred.

| Declared profile | p50 / p95 / p99 ms | Throughput/s | Load ms | RSS after run |
| --- | ---: | ---: | ---: | ---: |
| Pi 3 target | 26.329 / 41.024 / 55.703 | 36.902 | 4561.995 | 211,161,088 B |
| Pi 4 target | 27.102 / 46.734 / 57.510 | 34.351 | 8461.142 | 211,292,160 B |
| Pi 5 target | 31.124 / 47.113 / 51.022 | 29.870 | 7506.680 | 211,636,224 B |

These are **verified host-only inference measurements**, not comparisons of
the three Pi profiles: the benchmark intentionally did not apply resource
limits, so the variation is run-to-run host noise. The 1,416,832-byte
`model.joblib` plus 827-byte metadata was loaded once per process; warm
predictions do not reconstruct the model.

## Dataset and detection evidence

The deployed `latest` artifact was trained on the repository's synthetic
machine dataset and calibrated at threshold `0.037872855632647175`; it is not
representative industrial validation. Bearing diagnosis uses the separate
Paderborn vibration workflow and does not claim real-world performance until a
fact-sheet-labelled, held-out-bearing model bundle is trained. Detection delay
and false actionable alarms per machine-day have not been measured.

## Capability status

- **Verified:** model loading, feature ordering, single-event inference, CPU
  time/RSS collection, profile/Compose configuration parsing, and all tests.
- **Simulated:** only the profile configuration itself. No CPU or memory limit
  was enforced because Docker daemon access was denied.
- **Unverified:** full LoRa-to-dashboard latency, radio loss/queue depth,
  overload recovery, low disk behavior, Docker cgroup enforcement, ARM64
  package compatibility, real Pi thermals, and power.

`docker compose ... config` rendered the Pi 4 limits successfully. `docker
info` could not access `dockerDesktopLinuxEngine`, so image builds and runtime
claims are deliberately withheld.
