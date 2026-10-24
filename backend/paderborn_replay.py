"""Read-only replay helpers for locally prepared Paderborn bearing data.

The dashboard uses this module to replay a real, fact-sheet-labelled vibration
window.  It never labels a raw file itself and it never presents the replay as
live Raspberry Pi telemetry.
"""
from __future__ import annotations

import json
import hashlib
import io
import math
import os
from pathlib import Path
import re
from functools import lru_cache
from typing import Any
import zipfile

import numpy as np

from ml.data.paderborn import _load_mat_signal

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREPARED_DIR = PROJECT_ROOT / "ml" / "data" / "paderborn" / "prepared"
CHANNEL_PATTERN = re.compile(r"\(([^()]+)\)$")


class PaderbornReplayError(RuntimeError):
    """Raised when a local Paderborn replay cannot be safely completed."""


def _prepared_dir() -> Path:
    configured = os.getenv("AXON_PADERBORN_PREPARED_DIR")
    if configured:
        return Path(configured).expanduser()  # explicit missing paths remain errors
    return DEFAULT_PREPARED_DIR if (DEFAULT_PREPARED_DIR / "manifest.json").is_file() else PROJECT_ROOT / "demo" / "paderborn"


def _manifest() -> tuple[dict[str, Any], Path]:
    prepared_dir = _prepared_dir()
    path = prepared_dir / "manifest.json"
    if not path.is_file():
        raise PaderbornReplayError(
            f"prepared Paderborn manifest was not found at {path}; prepare the locally acquired dataset first"
        )
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaderbornReplayError(f"prepared Paderborn manifest cannot be read: {exc}") from exc
    if not isinstance(manifest.get("records"), list):
        raise PaderbornReplayError("prepared Paderborn manifest has no valid record list")
    return manifest, prepared_dir


def _record_summary(record: dict[str, Any]) -> dict[str, Any]:
    """Expose only the provenance fields needed for a safe operator replay."""
    return {
        "recording_id": record.get("recording_id"),
        "experiment_id": record.get("experiment_id"),
        "bearing_id": record.get("bearing_id"),
        "operating_condition": record.get("operating_condition"),
        "fault_class": record.get("fault_class"),
        "fault_location": record.get("fault_location"),
        "damage_type": record.get("damage_type"),
        "label_source": record.get("label_source"),
        "label_confidence": record.get("label_confidence"),
        "sampling_frequency_hz": record.get("sampling_frequency_hz"),
        "windows": record.get("windows"),
        "license_id": record.get("license_id"),
    }


def catalog() -> dict[str, Any]:
    """Return the available, explicit-label Paderborn recordings for the UI."""
    manifest, _ = _manifest()
    records = [record for record in manifest["records"] if record.get("recording_id")]
    return {
        "available": True,
        "source": manifest.get("source", {}),
        "sampling_frequency_hz": records[0].get("sampling_frequency_hz") if records else None,
        "window_generation": manifest.get("window_generation", {}),
        "class_counts": manifest.get("class_counts", {}),
        "physical_bearings_per_class": manifest.get("physical_bearings_per_class", {}),
        "prepared_windows": manifest.get("prepared_windows", 0),
        "records": [_record_summary(record) for record in records],
    }


def _source_reference(record: dict[str, Any]) -> tuple[Path, str | None]:
    archive_value = record.get("source_archive")
    member_value = record.get("source_member")
    if archive_value and member_value:
        archive = Path(str(archive_value).replace("\\", "/"))
        if not archive.is_absolute():
            archive = PROJECT_ROOT / archive
        member = str(member_value).replace("\\", "/")
        if ".." in Path(member).parts:
            raise PaderbornReplayError("the selected manifest record has an invalid archive member")
        return archive, member

    source_file = record.get("source_file")
    if not source_file:
        raise PaderbornReplayError("the selected manifest record has no source file")
    # The manifest is portable between Windows and POSIX hosts.
    source_text = str(source_file).replace("\\", "/")
    # Compatibility with manifests produced by the ZIP-aware preparer.
    if "!" in source_text:
        archive_text, member = source_text.split("!", 1)
        archive = Path(archive_text)
        if not archive.is_absolute():
            archive = PROJECT_ROOT / archive
        if ".." in Path(member).parts:
            raise PaderbornReplayError("the selected manifest record has an invalid archive member")
        return archive, member
    source = Path(source_text)
    return (source if source.is_absolute() else PROJECT_ROOT / source), None


def _signal_channel(record: dict[str, Any]) -> str:
    sensor_type = str(record.get("sensor_type") or "")
    match = CHANNEL_PATTERN.search(sensor_type)
    if not match:
        raise PaderbornReplayError("the selected manifest record has no recoverable vibration channel name")
    return match.group(1)


@lru_cache(maxsize=8)
def _load_recording_signal(source_text: str, member: str, channel: str, expected_sha256: str = "") -> np.ndarray:
    """Load recently selected local recordings once, then slice them per window."""
    source = Path(source_text)
    if member:
        with zipfile.ZipFile(source) as archive:
            payload = archive.read(member)
    else:
        payload = source.read_bytes()
    if expected_sha256 and hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise PaderbornReplayError("the selected Paderborn source recording checksum does not match its manifest")
    _, signal = _load_mat_signal(io.BytesIO(payload), [channel])
    values = np.ascontiguousarray(signal, dtype=np.float64)
    values.setflags(write=False)
    return values


def read_window(recording_id: str, window_index: int) -> tuple[dict[str, Any], np.ndarray, float]:
    """Load exactly one recorded vibration window by its manifest identity."""
    manifest, _ = _manifest()
    record = next((item for item in manifest["records"] if item.get("recording_id") == recording_id), None)
    if not record:
        raise PaderbornReplayError("the requested Paderborn recording is not in the prepared manifest")
    total_windows = int(record.get("windows") or 0)
    if not 0 <= window_index < total_windows:
        raise PaderbornReplayError(f"window index must be between 0 and {max(total_windows - 1, 0)} for {recording_id}")

    generation = manifest.get("window_generation") or {}
    window_samples = int(generation.get("window_samples") or 0)
    overlap = int(generation.get("window_overlap") or 0)
    sampling_frequency = float(record.get("sampling_frequency_hz") or 0)
    if window_samples <= 0 or not 0 <= overlap < window_samples or not math.isfinite(sampling_frequency) or sampling_frequency <= 0:
        raise PaderbornReplayError("the prepared manifest has an invalid window-generation contract")

    source, member = _source_reference(record)
    if not source.is_file():
        raise PaderbornReplayError(f"the local source recording is unavailable: {source}")
    if member:
        try:
            with zipfile.ZipFile(source) as archive:
                archive.getinfo(member)
        except (OSError, KeyError, zipfile.BadZipFile) as exc:
            raise PaderbornReplayError(f"the local archive member is unavailable: {member}") from exc
    try:
        if record.get("packaged_windows"):
            if hashlib.sha256(source.read_bytes()).hexdigest() != record.get("sha256"):
                raise PaderbornReplayError("packaged demo window checksum mismatch")
            with np.load(source, allow_pickle=False) as data:
                window = np.asarray(data["signals"][window_index], dtype=np.float64)
            if window.size != window_samples:
                raise PaderbornReplayError("packaged demo window has wrong sample count")
            start = window_index * (window_samples - overlap)
            summary = _record_summary(record)
            summary.update(window_start_sample=start, window_end_sample=start + window_samples - 1)
            return summary, window, sampling_frequency
        signal = _load_recording_signal(str(source.resolve()), member or "", _signal_channel(record), str(record.get("sha256") or ""))
    except (OSError, RuntimeError, ValueError) as exc:
        raise PaderbornReplayError(f"could not load the selected vibration recording: {exc}") from exc

    start = window_index * (window_samples - overlap)
    window = np.asarray(signal[start : start + window_samples], dtype=np.float64)
    if window.size != window_samples:
        raise PaderbornReplayError("the selected source recording no longer matches its prepared window contract")
    summary = _record_summary(record)
    summary["window_start_sample"] = start
    summary["window_end_sample"] = start + window_samples - 1
    return summary, window, sampling_frequency


def waveform_preview(window: np.ndarray, *, maximum_points: int = 640) -> dict[str, list[float] | list[int]]:
    """Downsample only the display payload; model inference keeps every sample."""
    if window.size == 0:
        return {"sample_indices": [], "amplitudes": []}
    step = max(1, math.ceil(window.size / maximum_points))
    indices = np.arange(0, window.size, step, dtype=np.int64)
    return {
        "sample_indices": [int(index) for index in indices],
        "amplitudes": [float(value) for value in window[indices]],
    }
