"""Package a few exact local Paderborn windows for a portable, attributed demo."""
from pathlib import Path
import hashlib
import json
import numpy as np
from backend.paderborn_replay import _manifest, read_window


def main():
    root = Path(__file__).resolve().parents[1]
    target = root / "demo/paderborn"
    target.mkdir(parents=True, exist_ok=True)
    manifest, _ = _manifest()
    records, counts = [], {}
    selected = ["N09_M07_F10_K001_1", "N09_M07_F10_KA04_1", "N09_M07_F10_K003_1", "N09_M07_F10_KI04_1"]
    for name in selected:
        original = next(record for record in manifest["records"] if record["recording_id"] == name)
        signals = [read_window(name, index)[1] for index in (0, 1)]
        path = target / f"{name}.npz"
        np.savez_compressed(path, signals=np.asarray(signals))
        record = original | {"windows": 2, "source_file": path.relative_to(root).as_posix(),
                             "packaged_windows": True, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        records.append(record)
        counts[record["fault_class"]] = counts.get(record["fault_class"], 0) + 2
    portable = {"source": manifest["source"], "window_generation": manifest["window_generation"],
                "records": records, "class_counts": counts, "prepared_windows": 8,
                "physical_bearings_per_class": {label: sum(r["fault_class"] == label for r in records) for label in counts},
                "note": "Eight exact historical windows for integration replay only; not the training dataset or a new evaluation."}
    (target / "manifest.json").write_text(json.dumps(portable, indent=2) + "\n", encoding="utf-8")
    print(f"Packaged {len(records)} recordings / 8 exact windows at {target}")


if __name__ == "__main__":
    main()
