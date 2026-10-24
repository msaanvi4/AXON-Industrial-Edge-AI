"""Prepare locally acquired Paderborn MAT recordings without relabelling them."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
from typing import Any
import zipfile

import numpy as np

from ml.bearing.signal import segment_signal
from ml.bearing.taxonomy import TAXONOMY_VERSION, validate_label

SCHEMA_VERSION = "axon-paderborn-prepared-v2"
NAME_PATTERN = re.compile(r"(?P<condition>N\d+_M\d+_F\d+)_(?P<bearing>[A-Za-z]+\d+)_(?P<recording>\d+)\.mat$", re.I)


def _arrays(value: Any, prefix: str = ""):
    if isinstance(value, np.ndarray) and value.dtype.kind in "fiu" and value.ndim in (1, 2) and value.size:
        yield prefix, np.asarray(value).reshape(-1)
    elif isinstance(value, dict):
        for key, child in value.items():
            if not str(key).startswith("__"):
                yield from _arrays(child, f"{prefix}.{key}".strip("."))
    elif hasattr(value, "_fieldnames"):
        for key in value._fieldnames:
            yield from _arrays(getattr(value, key), f"{prefix}.{key}".strip("."))
    elif isinstance(value, np.ndarray) and value.dtype == object:
        for index, child in enumerate(value.flat):
            yield from _arrays(child, f"{prefix}[{index}]")


def _load_mat_signal(source: Path | io.BytesIO, candidates: list[str]) -> tuple[str, np.ndarray]:
    try:
        from scipy.io import loadmat
    except ImportError as exc:
        raise RuntimeError("Paderborn MAT preparation requires scipy; install scipy in the ML environment") from exc
    loaded = loadmat(source, squeeze_me=True, struct_as_record=False)
    requested = {candidate.lower() for candidate in candidates}
    # Paderborn recordings wrap their named channels in root.Y[*].Data.  Read
    # the channel's `Name` metadata, rather than treating the generic `Data`
    # field as a signal identity.
    matches: list[tuple[str, np.ndarray]] = []
    for root_key, root in loaded.items():
        channels = getattr(root, "Y", None)
        if isinstance(channels, np.ndarray):
            for index, channel in enumerate(channels.flat):
                name, values = getattr(channel, "Name", None), getattr(channel, "Data", None)
                if isinstance(name, str) and name.lower() in requested and isinstance(values, np.ndarray):
                    matches.append((f"{root_key}.Y[{index}].Data ({name})", np.asarray(values).reshape(-1)))
    if not matches:
        arrays = list(_arrays(loaded))
        matches = [(key, values) for key, values in arrays if key.lower().split(".")[-1] in requested]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one configured signal channel, found {len(matches)} candidates")
    key, signal = matches[0]
    if not np.isfinite(signal).all():
        raise ValueError("signal contains non-finite values")
    return key, signal.astype(np.float64)


def prepare(config_path: Path) -> dict:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    source_path = Path(config["dataset_dir"])
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    window_samples, overlap = int(config["window_samples"]), int(config["window_overlap"])
    sampling_frequency = float(config["sampling_frequency_hz"])
    label_map = config.get("label_map", {})
    if config.get("label_map_file"):
        label_map_path = Path(config["label_map_file"])
        label_map = json.loads(label_map_path.read_text(encoding="utf-8"))
    recording_limit = int(config.get("max_recordings_per_bearing_condition", 0))
    if recording_limit < 0:
        raise ValueError("max_recordings_per_bearing_condition must be zero or positive")
    if not source_path.exists():
        raise FileNotFoundError(f"Paderborn source does not exist: {source_path}")
    is_archive = source_path.is_file() and zipfile.is_zipfile(source_path)
    if source_path.is_file() and not is_archive:
        raise ValueError("dataset_dir must be a directory or a ZIP archive containing Paderborn MAT files")

    records, excluded, windows, labels, groups = [], [], [], [], []
    selected_per_condition: dict[tuple[str, str], int] = {}
    def _recording_sort_key(name: str) -> tuple[str, str, int, str]:
        match = NAME_PATTERN.search(Path(name).name)
        if not match:
            return ("", "", -1, name)
        return (match.group("bearing"), match.group("condition"), int(match.group("recording")), name)

    if is_archive:
        archive_context = zipfile.ZipFile(source_path)
        source_entries = [item for item in archive_context.infolist() if item.filename.lower().endswith(".mat")]
        source_names = sorted((item.filename for item in source_entries), key=_recording_sort_key)
        entry_lookup = {item.filename: item for item in source_entries}
        read_source = lambda name: archive_context.read(entry_lookup[name])
        source_description = {"path": str(source_path), "format": "zip", "size_bytes": source_path.stat().st_size}
    else:
        archive_context = None
        source_names = sorted((str(path) for path in source_path.rglob("*.mat")), key=_recording_sort_key)
        read_source = lambda name: Path(name).read_bytes()
        source_description = {"path": str(source_path), "format": "directory"}

    try:
        for source_name in source_names:
            path = Path(source_name)
            match = NAME_PATTERN.search(path.name)
            bearing_code = match.group("bearing") if match else None
            metadata = label_map.get(bearing_code or "")
            base = {"source_file": f"{source_path}!{source_name}" if is_archive else str(path), "recording_id": path.stem, "bearing_id": bearing_code}
            if is_archive:
                base.update(source_archive=str(source_path), source_member=source_name)
            if not metadata:
                excluded.append(base | {"reason": "missing_explicit_label_mapping"})
                continue
            condition = match.group("condition") if match else "unknown"
            limit_key = (bearing_code or "unknown", condition)
            if recording_limit and selected_per_condition.get(limit_key, 0) >= recording_limit:
                excluded.append(base | {"reason": f"recording_limit_per_bearing_condition={recording_limit}"})
                continue
            payload = read_source(source_name)
            base["sha256"] = hashlib.sha256(payload).hexdigest()
            try:
                definition = validate_label(metadata["fault_class"], metadata.get("fault_location"))
                channel, signal = _load_mat_signal(io.BytesIO(payload), list(config["sensor_key_candidates"]))
                recording_windows = segment_signal(signal, sampling_frequency, window_samples, overlap)
            except (ValueError, KeyError, RuntimeError, TypeError, OSError) as exc:
                excluded.append(base | {"reason": str(exc)})
                continue
            selected_per_condition[limit_key] = selected_per_condition.get(limit_key, 0) + 1
            record = base | {"source_dataset": "Paderborn Bearing DataCenter", "experiment_id": condition, "operating_condition": condition, "sensor_type": channel, "sampling_frequency_hz": sampling_frequency, "fault_class": definition.fault_class, "fault_location": definition.fault_location, "damage_type": metadata.get("damage_type"), "label_source": metadata.get("label_source", "official_fact_sheet"), "label_confidence": metadata.get("label_confidence", "documented"), "license_id": config["license_id"], "windows": len(recording_windows)}
            records.append(record)
            # All recordings/windows from one physical bearing remain one group.
            windows.extend(recording_windows)
            labels.extend([definition.fault_class] * len(recording_windows))
            groups.extend([bearing_code] * len(recording_windows))
    finally:
        if archive_context is not None:
            archive_context.close()
    if windows:
        np.savez_compressed(output_dir / "windows.npz", signals=np.asarray(windows), labels=np.asarray(labels), groups=np.asarray(groups), sampling_frequency_hz=sampling_frequency)
    class_counts = {label: labels.count(label) for label in sorted(set(labels))}
    bearings_per_class = {
        label: len({record["bearing_id"] for record in records if record["fault_class"] == label})
        for label in class_counts
    }
    blockers: list[str] = []
    if len(class_counts) < 2:
        blockers.append("at least two documented fault classes are required")
    inadequate = {label: count for label, count in bearings_per_class.items() if count < 3}
    if inadequate:
        blockers.append("unseen-bearing evaluation requires at least three physical bearings per class: " + ", ".join(f"{label}={count}" for label, count in sorted(inadequate.items())))
    manifest = {"schema_version": SCHEMA_VERSION, "taxonomy_version": TAXONOMY_VERSION, "source": source_description | {"url": config["source_url"], "license_id": config["license_id"]}, "window_generation": {"window_samples": window_samples, "window_overlap": overlap}, "recording_limit_per_bearing_condition": recording_limit or None, "split_group": "physical_bearing_id", "records": records, "excluded": excluded, "class_counts": class_counts, "physical_bearings_per_class": bearings_per_class, "prepared_windows": len(windows), "training_ready": bool(windows) and not blockers, "training_blockers": blockers}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare locally acquired Paderborn bearing recordings")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.config), indent=2))


if __name__ == "__main__":
    main()
