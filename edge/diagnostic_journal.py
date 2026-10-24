"""Bounded local persistence for edge diagnostic events."""
from __future__ import annotations

import json
from pathlib import Path


class BoundedDiagnosticJournal:
    def __init__(self, path: Path, maximum_events: int = 1_000):
        self.path, self.maximum_events = Path(path), maximum_events

    def append(self, event: dict) -> None:
        events = self.read(); events.append(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(json.dumps(item, separators=(",", ":")) for item in events[-self.maximum_events:]) + "\n", encoding="utf-8")

    def read(self) -> list[dict]:
        if not self.path.is_file(): return []
        result = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try: result.append(json.loads(line))
            except json.JSONDecodeError: continue
        return result[-self.maximum_events:]
