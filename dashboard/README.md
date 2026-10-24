# Dashboard

Run the dashboard against the local AXON API:

```powershell
python -m pip install -r requirements.txt
$env:BACKEND_URL = "http://127.0.0.1:8000"
streamlit run app.py
```

Two workspaces are available:

The local integration stack is:

`Streamlit UI → FastAPI diagnostic API → deployed scikit-learn Random Forest → local Paderborn MATLAB recording`

For every selected 4,096-sample segment, FastAPI calculates the deployed
model's 16 engineered vibration features and returns the dataset provenance,
waveform preview, and model result. Dataset replay is visibly separate from
live Raspberry Pi/LoRa telemetry.

- **Paderborn data replay** is the interactive demo path. It reads only the
  locally prepared, fact-sheet-labelled Paderborn recordings, lets an operator
  select a bearing, experiment recording, and 4,096-sample window, then shows
  the recorded waveform, the 16 features, the documented experimental label,
  and the deployed model decision. Every resulting event is visibly tagged as
  a historical replay.
- **Model Analysis** is the live path. It shows the deployed Random Forest's
  model specification, grouped-validation metrics, and compact diagnostic events
  delivered from the Raspberry Pi through the LoRa gateway. Manual CSV replay
  remains available only as a troubleshooting fallback.

The dashboard does not show a generic alert feed or machine-control queue:
the supplied Paderborn data does not contain temperature/current telemetry,
and a queued LoRa command is not proof that a physical machine executed it.
