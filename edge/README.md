# Raspberry Pi bearing runner

Install from the repository root with python -m pip install -r edge/requirements.txt.

- python -m edge.bearing_runner --transport loopback: real Paderborn replay through local inference, binary LoRa codec, durable gateway, ACK and API. Explicit software replay, no physical radio.
- python -m edge.bearing_runner --transport lora --port /dev/serial0: the same recorded windows sent over a real transparent-UART radio.
- python -m edge.lora_gateway --port COM5: receiver on a Windows USB-UART adapter. Linux normally uses /dev/serial0 or /dev/ttyUSB0.
- python -m edge.bearing_runner --source capture --transport lora --capture-command /path/to/adc-driver --one-window: external hardware capture provider; see docs/DEMO.md.

The sender commits a diagnosis to SQLite before opening the UART. Missing ACKs retain the event, including MODEL_UNAVAILABLE and SENSOR_DATA_INVALID. The gateway commits before ACK and retries API delivery. An ACK confirms gateway persistence, not dashboard display. Names and model version are sent in v2; no asset registry is hardcoded. Both peers must use v2.

Legacy unsent v1 outbox rows are preserved and cause an explicit migration message. Choose a fresh --outbox filename for v2 and retain the old journal for inspection. Previously pending v1 gateway HTTP deliveries are drained.

The old simulator.py is a separate HTTP synthetic-telemetry example. It does not execute this pipeline. Machine command downlink is unavailable.
