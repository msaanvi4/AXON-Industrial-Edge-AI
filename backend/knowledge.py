"""Local, provenance-required repair-document catalogue access."""
from __future__ import annotations

import json
from pathlib import Path


def search_documents(catalogue_path: Path, query: str, equipment_model: str | None = None) -> list[dict]:
    if not catalogue_path.is_file(): return []
    records = json.loads(catalogue_path.read_text(encoding="utf-8"))
    terms = query.lower().split()
    return [record for record in records if (not equipment_model or equipment_model in record.get("equipment_models", [])) and all(term in json.dumps(record).lower() for term in terms)]
