from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.main import MemoryStore, create_app
from backend.paderborn_replay import catalog, read_window
from edge.bearing_runner import LoopbackRadio, process_window
from edge.lora_gateway import GatewaySpool
from edge.lora_transport import LoraOutbox, send_pending_once
from ml.bearing.bundle import BearingBundleError, load_bearing_bundle

ROOT = Path(__file__).resolve().parents[1]


def test_packaged_real_data_runs_without_private_dataset(monkeypatch):
    monkeypatch.setenv("AXON_PADERBORN_PREPARED_DIR", str(ROOT / "demo/paderborn"))
    bundle = load_bearing_bundle(ROOT / "ml/artifacts/bearing/active")
    assert catalog()["prepared_windows"] == 8
    for recording in catalog()["records"]:
        _, first, frequency = read_window(recording["recording_id"], 0)
        _, second, _ = read_window(recording["recording_id"], 1)
        assert len(first) == 4096 and frequency == 64000
        assert not np.array_equal(first, second)
        assert set(bundle.probabilities(first, frequency)) == {"healthy", "other_bearing_damage"}


def test_real_runner_to_gateway_to_api_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("AXON_PADERBORN_PREPARED_DIR", str(ROOT / "demo/paderborn"))
    bundle = load_bearing_bundle(ROOT / "ml/artifacts/bearing/active")
    client = TestClient(create_app(store=MemoryStore()))
    outbox, spool = LoraOutbox(tmp_path / "outbox.db"), GatewaySpool(tmp_path / "gateway.db")
    for recording in ("N09_M07_F10_K001_1", "N09_M07_F10_KA04_1"):
        record, signal, rate = read_window(recording, 0)
        result, event_id = process_window({"asset_id": "cryo-pump-22", "component_id": "stage-7-bearing",
            "signal": signal, "sampling_frequency_hz": rate, "historical_replay": True}, bundle, outbox)
        assert result["fault_class"] == record["fault_class"]
        assert send_pending_once(outbox, LoopbackRadio(spool))
        assert outbox.is_acknowledged(event_id)
    # Simulate an HTTP response lost AFTER server acceptance. The gateway retries.
    import requests
    calls = []
    def post(url, json, timeout):
        response = client.post("/ingest/diagnostic", json=json)
        calls.append(json["event_id"])
        if len(calls) == 1:
            raise requests.ConnectionError("response lost")
        return response
    monkeypatch.setattr("edge.lora_gateway.requests.post", post)
    with pytest.raises(requests.ConnectionError):
        spool.deliver_pending("http://testserver")
    spool.deliver_pending("http://testserver")
    assert calls[0] == calls[1]
    history = client.get("/diagnostics/history", params={"asset_id": "cryo-pump-22"}).json()
    assert len(history) == 2
    assert {item["fault_class"] for item in history} == {"healthy", "other_bearing_damage"}
    assert all(item["simulated"] and item["model_version"] == bundle.metadata["model_version"] for item in history)
    assert client.get("/machines").json() == ["cryo-pump-22"]
    assert client.get("/diagnostics/assets").json() == ["cryo-pump-22"]


def test_bundle_rejects_library_version_drift(monkeypatch):
    monkeypatch.setattr("ml.bearing.bundle.sklearn.__version__", "1.5.2")
    with pytest.raises(BearingBundleError, match="requires scikit-learn"):
        load_bearing_bundle(ROOT / "ml/artifacts/bearing/active")
