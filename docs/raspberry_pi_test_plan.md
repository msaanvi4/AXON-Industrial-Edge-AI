# Raspberry Pi validation plan

Profiles are configuration targets, not hardware measurements: Pi 3 = 4 Cortex-
A53-class cores/1 GiB, Pi 4 = 4 Cortex-A72-class cores/4 GiB, Pi 5 = 4 Cortex-
A76-class cores/4 GiB. Each records a four-visible-CPU quota, memory budget,
storage budget, and a test-container-only no-swap policy in `config/edge_profiles`.
CPU quotas limit scheduling capacity only; they do not emulate ARM instructions
or clock speed.

Run locally after installing edge requirements:

```sh
./scripts/edge_test.sh audit
./scripts/edge_test.sh benchmark --profile pi3
./scripts/edge_test.sh benchmark --profile pi4
./scripts/edge_test.sh benchmark --profile pi5
./scripts/edge_test.sh stress --profile pi4
```

On Windows, use `./scripts/edge_test.ps1 audit -PythonExe C:\path\to\python.exe`
when `python` is a Microsoft Store alias; `PYTHON_EXECUTABLE` can set that
interpreter once for repeated runs.

For Docker resource enforcement, choose a profile and run:

```sh
docker compose --env-file config/edge_profiles/pi4.env -f docker-compose.edge-test.yml up --build
```

That compose file declares Docker CPU, CPU-set, PID, and memory limits for the
edge container. Verify enforcement with `docker stats`; Compose validation is
not evidence that a Windows host or daemon applied Linux cgroups.

ARM64 smoke validation remains required:

```sh
docker buildx build --platform linux/arm64 -f Dockerfile.edge-test -t axon-edge:arm64 --load .
docker run --rm --network host -e BACKEND_URL=http://127.0.0.1:8000 axon-edge:arm64
```

On a real Pi, record `vcgencmd measure_temp`, `vcgencmd measure_clock arm`,
`docker stats`, process RSS, LoRa ACK/retry counts, and an external power meter
where possible. QEMU can validate compatibility but not Pi latency, heat, or
power. Never fill the host disk; use an isolated Docker volume for low-storage
testing.
