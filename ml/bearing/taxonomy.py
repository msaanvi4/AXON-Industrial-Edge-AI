"""Versioned fault taxonomy for evidence-backed bearing diagnoses."""
from __future__ import annotations

from dataclasses import dataclass

TAXONOMY_VERSION = "axon-bearing-taxonomy-v1"


@dataclass(frozen=True)
class FaultDefinition:
    fault_class: str
    fault_location: str | None
    description: str


FAULTS = {
    "healthy": FaultDefinition("healthy", None, "No fault class is predicted within the model's validated scope."),
    "inner_race_damage": FaultDefinition("inner_race_damage", "inner_race", "Documented damage located at the inner race."),
    "outer_race_damage": FaultDefinition("outer_race_damage", "outer_race", "Documented damage located at the outer race."),
    "rolling_element_damage": FaultDefinition("rolling_element_damage", "rolling_element", "Documented rolling-element damage."),
    "other_bearing_damage": FaultDefinition("other_bearing_damage", None, "Documented bearing damage without a supported specific location."),
    "unknown": FaultDefinition("unknown", None, "The source label is unavailable or unsupported."),
}


def validate_label(fault_class: str, fault_location: str | None = None) -> FaultDefinition:
    if fault_class not in FAULTS:
        raise ValueError(f"unsupported AXON bearing fault class: {fault_class}")
    definition = FAULTS[fault_class]
    if fault_location is not None and definition.fault_location != fault_location:
        raise ValueError("fault location does not match the selected AXON fault class")
    return definition


def public_taxonomy() -> list[dict]:
    return [{"fault_class": item.fault_class, "fault_location": item.fault_location, "description": item.description} for item in FAULTS.values()]
