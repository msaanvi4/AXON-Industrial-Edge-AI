from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from fastapi import HTTPException

from backend.main import Alert, Command, DiagnosticEvent, MemoryStore, Telemetry, create_app


def diagnostic_payload():
    return {
        "event_id": "event-1", "timestamp": datetime.now(timezone.utc).isoformat(), "asset_id": "asset-1",
        "component_id": "bearing-drive-end", "fault_class": None, "fault_location": None, "confidence": None,
        "confidence_calibrated": False, "status": "MODEL_UNAVAILABLE", "evidence": [],
        "alternative_hypotheses": [], "missing_information": ["no model"],
        "recommended_confirmation_tests": [], "repair_reference_ids": [], "cost_estimate_id": None,
        "model_version": None, "requires_technician_confirmation": True, "simulated": False,
    }


def test_http_ingest_and_command_queue_contract():
    store = MemoryStore(); app = create_app(store=store)
    endpoints = {route.path: route.endpoint for route in app.routes if hasattr(route, "endpoint")}
    now = datetime.now(timezone.utc).isoformat()
    endpoints["/ingest/telemetry"](Telemetry(machine_id="M1", ts=now, temp=73, vibration=0.3, current=4.5))
    endpoints["/ingest/alert"](Alert(machine_id="M1", ts=now, type="anomaly", score=0.4))
    assert endpoints["/machines"]() == ["M1"]
    assert len(endpoints["/readings"]("M1", 5)) == 1
    assert len(endpoints["/alerts"]("M1")) == 1
    with pytest.raises(HTTPException) as error:
        endpoints["/command"](Command(machine_id="M1", action="slow_down", issued_by="test"))
    assert error.value.status_code == 501
    assert store.commands("M1") == []
    assert endpoints["/ready"]()["transport"] == "lora_gateway_http"


def test_lora_gateway_diagnostic_is_persisted():
    store = MemoryStore(); app = create_app(store=store)
    endpoints = {route.path: route.endpoint for route in app.routes if hasattr(route, "endpoint")}
    result = endpoints["/ingest/diagnostic"](DiagnosticEvent.model_validate(diagnostic_payload()))
    assert result["status"] == "accepted"
    assert store.diagnostics("asset-1")[0]["event_id"] == "event-1"


def test_diagnostic_and_cost_routes_are_explicit_when_no_bearing_model_is_deployed(monkeypatch):
    import backend.main as backend_main
    from ml.bearing.bundle import BearingBundleError
    def missing_bundle():
        raise BearingBundleError("bundle absent in test")
    monkeypatch.setattr(backend_main, "bearing_bundle", missing_bundle)
    store = MemoryStore(); app = create_app(store=store)
    endpoints = {route.path: route.endpoint for route in app.routes if hasattr(route, "endpoint")}
    from backend.main import CostEstimateRequest, DiagnosticRequest
    result = endpoints["/diagnostics/infer"](DiagnosticRequest(asset_id="demo-asset", component_id="bearing-drive-end", signal=[0.0] * 64, sampling_frequency_hz=64000))
    assert result["status"] == "MODEL_UNAVAILABLE"
    assert endpoints["/diagnostics/history"]("demo-asset", 50)[0]["event_id"] == result["event_id"]
    estimate = endpoints["/cost-estimates"](CostEstimateRequest(line_items=[{"description": "bearing", "quantity": 1}], currency="INR"))
    assert estimate["estimate_status"] == "incomplete" and estimate["total"] is None


def test_paderborn_catalog_reports_a_missing_local_dataset_without_inventing_records(monkeypatch):
    missing_dataset = Path(__file__).with_name("not-a-paderborn-prepared-dataset")
    monkeypatch.setenv("AXON_PADERBORN_PREPARED_DIR", str(missing_dataset))
    app = create_app(store=MemoryStore())
    endpoints = {route.path: route.endpoint for route in app.routes if hasattr(route, "endpoint")}
    result = endpoints["/diagnostics/paderborn/catalog"]()
    assert result["available"] is False
    assert result["records"] == []


def test_paderborn_replay_is_stored_as_a_historical_dataset_event(monkeypatch):
    import backend.main as backend_main
    from backend.main import PaderbornReplayRequest

    record = {
        "recording_id": "N09_M07_F10_K001_1", "experiment_id": "N09_M07_F10", "bearing_id": "K001", "operating_condition": "N09_M07_F10",
        "fault_class": "healthy", "fault_location": None, "damage_type": None,
        "label_source": "official fact sheet", "label_confidence": "documented",
        "sampling_frequency_hz": 64000.0, "windows": 1, "license_id": "CC-BY-NC-4.0",
    }
    signal = np.linspace(-1.0, 1.0, 64)

    class FakeBundle:
        metadata = {"model_version": "test-bearing-rf", "input_contract": {"window_samples": 64, "sampling_frequency_hz": 64000.0}}

        @staticmethod
        def probabilities(values, sampling_frequency_hz):
            assert len(values) == 64 and sampling_frequency_hz == 64000.0
            return {"healthy": 0.9, "other_bearing_damage": 0.1}

    monkeypatch.setattr(backend_main, "read_paderborn_window", lambda recording_id, window_index: (record, signal, 64000.0))
    monkeypatch.setattr(backend_main, "bearing_bundle", lambda: FakeBundle())
    store = MemoryStore()
    app = create_app(store=store)
    endpoints = {route.path: route.endpoint for route in app.routes if hasattr(route, "endpoint")}
    result = endpoints["/diagnostics/paderborn/replay"](
        PaderbornReplayRequest(asset_id="paderborn-demo-001", recording_id="N09_M07_F10_K001_1", window_index=0, save_result=True)
    )
    diagnostic = result["diagnostic"]
    assert diagnostic["simulated"] is True
    assert diagnostic["evidence"][0]["source"] == "paderborn_dataset_replay"
    assert result["dataset"]["recording_id"] == record["recording_id"]
    assert result["dataset"]["experiment_id"] == "N09_M07_F10"
    assert result["saved"] is True
    assert len(result["features"]) == 16
    assert store.diagnostics("paderborn-demo-001")[0]["event_id"] == diagnostic["event_id"]
