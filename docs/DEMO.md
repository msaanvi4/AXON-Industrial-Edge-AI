# Presentation walkthrough

1. From the repository root run .\scripts\start_demo.ps1.
2. Open http://127.0.0.1:8501, then Paderborn data replay.
3. The continuous runner alternates K001 (documented healthy) and KA04 (documented damage) so both states are visible. These identities were included in final fitting; use other bearings for exploratory replay, not as a separate test set.
4. For continuous integration playback run .\.venv\Scripts\python.exe -m edge.bearing_runner --transport loopback --interval 5.
5. Open Bearing diagnosis, asset demo-motor-001. The newest results alternate as the runner processes the two real recordings. Stop playback with Ctrl+C.

The loopback uses the same binary codec, CRC, SQLite outbox, gateway acceptance and ACK functions as the physical radio. The label explicitly says software radio replay. It does not test radio range or ADC timing.

## Model claims for the presentation

The packaged Random Forest classifies healthy versus generic bearing damage from 4,096 vibration samples at 64 kHz and 16 features. The earlier artifact reported 95.30% accuracy and 90.59% damaged-bearing recall on 2,981 windows from only K003 and KI04; these are stored historical results, not the active model's independently verified test performance. The active model was fitted using all 32 available bearing identities after nested grouped cross-validation. Its out-of-fold accuracy is 91.86%, macro F1 0.8678, and equal-per-bearing class-balanced accuracy 87.28%. K002 had zero accuracy in its held-out fold and KI05 damage recall was low. See `ml/artifacts/bearing/active/evaluation.json` and `docs/ML_TRAINING_AUDIT.md`; do not present K003/KI04 as unseen holdouts for the active model.

Dataset facts can identify KA04's documented outer-ring pitting, but this binary model only predicts generic damage. The UI therefore marks the affected bearing assembly. It does not estimate remaining life or identify an exact race.

## Live hardware provider

The Pi needs a hardware-clocked ADC and compatible vibration sensor. An actual sensor/ADC model and calibrated acquisition method have not been supplied, so no device-specific ADC driver or physical 64 kS/s validation is claimed.

Run the edge runner with --source capture and put --capture-command LAST:

~~~sh
python -m edge.bearing_runner --source capture --transport lora --asset-id demo-motor-001 --port /dev/serial0 --capture-command /path/to/adc-driver --one-window
~~~

The driver must print one JSON object to stdout within 10 seconds:
signal: a list of exactly 4096 calibrated samples;
sampling_frequency_hz: 64000;
hardware_clocked: true;
sensor_id: the actual device identifier;
units: calibrated vibration units.

Diagnostics go through local inference and are queued before UART transmission. Invalid samples or missing model produce explicit status events. A different sensor transfer function or mounting can require retraining even at the same sample rate.

Run the gateway using python -m edge.lora_gateway --port COM5 --api-url http://127.0.0.1:8000 (choose the actual serial port). Both radios must use v2. ACK means safely stored at the gateway; the gateway continues HTTP delivery when the API returns.

## Network outage

Pi inference and its SQLite outbox operate locally. Radio failure retains unacknowledged events. Gateway/API disconnection retains accepted events in the gateway spool. Restart the gateway to drain pending events after connection recovery. Memory-mode API history is temporary; use the Docker PostgreSQL mode for server persistence. No machine-control downlink is provided.
