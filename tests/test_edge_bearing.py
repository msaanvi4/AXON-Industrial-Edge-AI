from pathlib import Path

from edge.bearing_inference import diagnose_payload
from edge.diagnostic_journal import BoundedDiagnosticJournal


def test_edge_bearing_service_returns_unavailable_without_a_bundle():
    result = diagnose_payload({"asset_id": "A1", "component_id": "bearing-drive-end", "signal": [0.0] * 64, "sampling_frequency_hz": 64000}, model_error="artifact missing")
    assert result["status"] == "MODEL_UNAVAILABLE" and result["fault_class"] is None


def test_bounded_diagnostic_journal_keeps_latest_events():
    path = Path("test-results/edge-bearing-journal.jsonl")
    journal = BoundedDiagnosticJournal(path, maximum_events=2)
    journal.append({"event_id": "one"}); journal.append({"event_id": "two"}); journal.append({"event_id": "three"})
    assert [event["event_id"] for event in journal.read()] == ["two", "three"]
