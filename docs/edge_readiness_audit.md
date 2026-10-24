# Axon edge-readiness audit

## Verified architecture

The deployable edge component is `edge/simulator.py`.  It loads the serialized
Isolation Forest once at startup from `ml/artifacts/latest`, turns `temp`,
`vibration`, and `current` into the feature order recorded in `metadata.json`,
and sends compact bearing diagnostics through LoRa. `backend/main.py`
accepts gateway HTTP ingest, validates the contract, writes telemetry to InfluxDB
and alerts/commands to PostgreSQL, then serves the API used by the Streamlit
dashboard.  Training stays offline in `ml/training/train.py`.

The current `latest/model.joblib` is 1,416,832 bytes (metadata 827 bytes).
Its feature order and calibrated threshold are artifact metadata, not recreated
per event. Measured load and warm-inference figures are recorded separately in
`docs/edge_benchmark_results.md` with host provenance.

The effective path is: sensor sample -> edge feature map -> Isolation Forest ->
LoRa gateway diagnostic -> backend validation/storage -> API/dashboard. The
current backend does not infer; it trusts edge-generated alerts.  The remote
database and dashboard are not required for minimal edge inference, but they
are required for the full current deployment.

## Edge hotspots and risks

- The model uses `joblib`, NumPy, and scikit-learn. They must be installed from
  ARM64-compatible packages and tested by loading the exact artifact on ARM64.
- `influxdb-client`, `psycopg2`, FastAPI, and Streamlit increase memory and
  should remain server-side for the minimal profile.
- LoRa retransmission and the backend's synchronous Influx write can delay data
  processing when the network or storage is slow.
- The existing client has no bounded local telemetry queue, replay journal,
 de-duplication, stale-message policy, or log-rotation policy. Radio outage
  recovery therefore remains an application gap.
- Invalid LoRa diagnostic frames are rejected by the gateway. This avoids a false normal
  reading, but there is no stored `unverified` machine state or dashboard
  indicator. A production edge deployment needs that explicit state before a
  failed detector can be distinguished from a healthy machine.

## Required work before field deployment

1. Execute the ARM64 smoke path with the exact model artifact.
2. Add bounded buffering, telemetry freshness/de-duplication, and an explicit
   degraded/unverified status to the frozen contract and dashboard.
3. Measure resource use on a physical Pi under representative sensors and
   network loss; do not derive Pi power or thermal behavior from x86 results.
4. Establish log retention and a safe isolated low-disk test volume.
