# AXON Industrial Edge AI

Edge intelligence for industrial reliability — bearing fault diagnosis, vibration analytics and connected maintenance.

AXON classifies vibration windows with a packaged Paderborn Random Forest and shows the result, waveform, features and affected bearing assembly in a Streamlit dashboard.

## Presentation startup (Windows, from repository root)

Use Python 3.12 and the project virtual environment:

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt -r edge/requirements.txt -r dashboard/requirements.txt
.\scripts\start_demo.ps1
~~~

Open http://127.0.0.1:8501. The launcher explicitly uses memory storage, so Docker is optional for this demo and diagnostic history disappears when the API stops. Use AXON_STORAGE_MODE=external with the existing Docker Compose services for persistent storage.

The packaged model is under ml/artifacts/bearing/active. Its SHA-256, input contract and training library version are checked. The API and Pi use the same requirements. No retraining or synthetic data generation is required to run it.

## Integrated bearing path

Hardware capture provider or recorded Paderborn window → local model → SQLite outbox → LoRa v2 binary frame → gateway SQLite spool → API diagnostic history → dashboard.

For a repeatable demonstration without radio hardware, run:

~~~powershell
.\.venv\Scripts\python.exe -m edge.bearing_runner --transport loopback --interval 5
~~~

Open **Bearing diagnosis**, asset **demo-motor-001**. This alternates K001 (documented healthy) and KA04 (documented damage) recordings, so the live display exercises both healthy and fault states. It exercises local inference, binary encoding, CRC, durable gateway receipt, ACK and HTTP delivery. The UI labels it as software radio replay and applies a clearly labelled 86.78% operational-confidence ceiling to uncalibrated votes; the raw score remains auditable in the technical record. Ctrl+C stops it. Use `--recording N09_M07_F10_K003_1 --recording N09_M07_F10_KI04_1` to replay examples from the prior model's test identities; they are included in the retrained model's fit and are not held out now.

The **Paderborn data replay** workspace uses the full locally prepared dataset if available, otherwise eight packaged, attributed windows from four recordings. See demo/paderborn/README.md for provenance and noncommercial data licence.

## Hardware boundary

On a Pi 4 use python -m edge.bearing_runner --transport lora --port /dev/serial0. The default source is historical recordings. Live capture requires --source capture and a hardware-specific --capture-command that returns a calibrated, hardware-clocked vibration window. No sensor/ADC model has been specified or physically validated here. Python does not time 64 kS/s acquisition. See docs/DEMO.md for the provider contract.

Run python -m edge.lora_gateway --port COM5 on a Windows USB-UART gateway, or use the appropriate /dev/serial0 device on Linux. Upgrade sender and gateway together: v2 replaces the earlier 13-byte packet. It supports arbitrary asset/component IDs, model identity, diagnostic status and event-unique ACK/deduplication.

## Model evidence

Input: 4,096 vibration samples at 64,000 Hz → 16 features. Output: healthy or other_bearing_damage. No race-level diagnosis, failure-time prediction or new-sensor validation is claimed.

The active model was retrained on 512 recordings from 32 fact-sheet-labelled physical bearings in the supplied Paderborn archive. Nested five-fold grouped cross-validation held out whole bearing identities and produced 91.86% window accuracy, macro F1 0.8678, and 87.28% equal-per-bearing class-balanced accuracy. Healthy recall was 79.98%; damage recall was 94.59%. The previous RF settings scored 86.72% on that same primary metric, so the improvement is modest. K002 is a failed healthy-bearing holdout and KI05 has low damage recall. See ml/artifacts/bearing/active/evaluation.json and dataset_manifest.json for fold-level results, source checksums, and exclusions.

The previous artifact's 95.30% score came from 2,981 windows on only K003 and KI04 and is not comparable to the new cross-validation. The four bearing identities that were validation/test holdouts for the previous artifact score worse under the retrained model's matched out-of-fold predictions, driven by K002. These scores are laboratory cross-validation, not field accuracy. Probabilities remain uncalibrated; retain technician confirmation.

### Retrain from the supplied Paderborn ZIP

Copy config/paderborn.archive.example.json to ml/data/paderborn/local.json, set dataset_dir to the local archive path, then run:

~~~powershell
.\.venv\Scripts\python.exe -m ml.data.prepare --config ml/data/paderborn/local.json
.\.venv\Scripts\python.exe -m ml.training.train_random_forest --prepared ml/data/paderborn/prepared-r4/windows.npz --output ml/artifacts/bearing/candidate --feature-version axon-vibration-features-v1
~~~

The archive preparer reads MAT members directly and does not extract or fabricate recordings. The checked-in fact-sheet label map retains Paderborn's CC-BY-NC-4.0 attribution and noncommercial restriction. The active run used four recordings per bearing/operating-condition pair.

The older edge/simulator.py and ml/artifacts/latest Isolation Forest are legacy synthetic telemetry examples. They POST HTTP telemetry; they are not part of this bearing demo and do not send bearing diagnoses over LoRa. Their health and alert endpoints remain for compatibility. Machine commands return HTTP 501 because no LoRa actuator downlink exists.

## Verification

~~~powershell
$env:AXON_STORAGE_MODE = "memory"
.\.venv\Scripts\python.exe -m pytest
~~~

See docs/contract.md for the v2 wire contract and docs/DEMO.md for the presentation walkthrough.
