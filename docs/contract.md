# AXON LoRa v2 and REST contract

This version supersedes the original 13-byte protocol. Upgrade Pi and gateway together.

## Binary diagnostic frame

All integers are little endian. The frame is:

- Header <2sBH>: AX magic, version 2, body byte length.
- Fixed body <16sHQBBBBBBB>: event UUID (16 bytes), sequence (uint16), UTC epoch milliseconds (uint64), fault, confidence, status, flags, asset-name length, component-name length, model-version length.
- Variable body: asset ID, component ID, model version as UTF-8 bytes, each at most 128 bytes. Asset/component names accept letters, digits, underscores and hyphens.
- CRC-16/CCITT-FALSE (uint16) covers header and body.

No raw vibration samples travel over LoRa. The length is variable, with 40 bytes of framing/fields/CRC plus the three names. Confidence 255 means unavailable; flags bit 0 marks historical/simulated data. Explicit fault/status enum IDs are in shared/lora_protocol.py. Confidence below the decision threshold preserves DIAGNOSIS_UNCERTAIN; null-fault errors are supported.

ACK <2sB16sHH>: AK magic, version 2, event UUID, sequence, CRC. Event UUID is the deduplication identity; sequence wrap or outbox recreation cannot suppress a different event. A conflicting reuse of an event UUID is rejected without ACK. Exact retries are ACKed after confirming durable receipt. The API is idempotent on event_id so lost HTTP responses can be retried.

CRC provides corruption detection only. Configure paired transparent UART modules appropriately. RF performance and hardware compatibility require on-device tests.

## REST

- POST /ingest/diagnostic: gateway diagnostic record, idempotent on event_id.
- GET /diagnostics/history?asset_id=demo-motor-001: latest diagnoses.
- GET /diagnostics/assets and /machines: discovered asset identities.
- GET /diagnostics/model-status: API bundle availability and input contract.
- POST /diagnostics/paderborn/replay: historical-window inference; save_result defaults false.
- POST /diagnostics/infer: manually supplied vibration window.
- POST /command: HTTP 501; actuator downlink is not implemented.

Legacy HTTP-only synthetic telemetry endpoints /ingest/telemetry, /ingest/alert, /readings, /alerts and /health remain separate from the bearing model. Their temperature/current/aggregate-vibration fields cannot replace the bearing model's 4,096-sample vibration window.
