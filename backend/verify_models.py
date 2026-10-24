"""Request and response schemas for the AXON Verify API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

IDENTIFIER = r"^[A-Za-z0-9._-]+$"
EVENT_ID = r"^[A-Za-z0-9._:-]+$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------- identity


class RegisterRequest(_Strict):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=256)
    name: str = Field(min_length=1, max_length=120)
    org_name: str = Field(min_length=2, max_length=120)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("a valid email address is required")
        return value


class LoginRequest(_Strict):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    org_id: str | None = Field(default=None, max_length=64)


class SwitchOrgRequest(_Strict):
    org_id: str = Field(min_length=1, max_length=64)


class MemberInviteRequest(_Strict):
    email: str = Field(min_length=3, max_length=254)
    name: str = Field(min_length=1, max_length=120)
    role: Literal["admin", "manager", "technician", "viewer", "customer"]
    password: str | None = Field(default=None, min_length=8, max_length=256)


class MemberRoleRequest(_Strict):
    role: Literal["owner", "admin", "manager", "technician", "viewer", "customer"]


class ApiKeyCreateRequest(_Strict):
    name: str = Field(min_length=1, max_length=80)


# ------------------------------------------------------------------- registry


class CustomerCreateRequest(_Strict):
    name: str = Field(min_length=1, max_length=160)


class SiteCreateRequest(_Strict):
    customer_id: str = Field(min_length=1, max_length=64, pattern=IDENTIFIER)
    name: str = Field(min_length=1, max_length=160)
    address: str | None = Field(default=None, max_length=400)


class AssetCreateRequest(_Strict):
    site_id: str = Field(min_length=1, max_length=64, pattern=IDENTIFIER)
    customer_id: str = Field(min_length=1, max_length=64, pattern=IDENTIFIER)
    name: str = Field(min_length=1, max_length=160)
    asset_type: str = Field(default="rotating_equipment", max_length=64)
    manufacturer: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=80)
    serial: str | None = Field(default=None, max_length=80)
    component_name: str = Field(default="drive-end bearing", max_length=80)
    component_position: str | None = Field(default=None, max_length=40)


class DeviceCreateRequest(_Strict):
    name: str = Field(min_length=1, max_length=120)
    device_key: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    site_id: str | None = Field(default=None, max_length=64)
    kind: Literal["mqtt_gateway", "lora_gateway", "manual_upload"] = "mqtt_gateway"
    cert_fingerprint: str | None = Field(default=None, max_length=128)


# ------------------------------------------------------------------- evidence


class ReadingUploadRequest(_Strict):
    """A vibration window submitted over upload, CSV import or REST."""

    asset_id: str = Field(min_length=1, max_length=64, pattern=IDENTIFIER)
    component_id: str = Field(min_length=1, max_length=64, pattern=IDENTIFIER)
    signal: list[float] = Field(min_length=16, max_length=262_144)
    sampling_frequency_hz: float = Field(gt=0, le=1_000_000)
    captured_at: str | None = Field(default=None, max_length=40)
    operating_condition_id: str | None = Field(default=None, max_length=64)
    source: Literal["upload", "csv", "rest"] = "upload"
    event_id: str | None = Field(default=None, max_length=64, pattern=EVENT_ID)

    @field_validator("signal")
    @classmethod
    def _finite(cls, values: list[float]) -> list[float]:
        for value in values:
            if value != value or value in (float("inf"), float("-inf")):
                raise ValueError("signal contains a non-finite sample")
        return values


class ReplayCaptureRequest(_Strict):
    """Replay a packaged historical laboratory window as evidence.

    The result is permanently marked as a dataset replay so it can never be
    mistaken for a live capture.
    """

    asset_id: str = Field(min_length=1, max_length=64, pattern=IDENTIFIER)
    component_id: str = Field(min_length=1, max_length=64, pattern=IDENTIFIER)
    recording_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.-]+$")
    window_index: int = Field(ge=0, le=10_000)
    captured_at: str | None = Field(default=None, max_length=40)
    operating_condition_id: str | None = Field(default=None, max_length=64)


class PhaseCaptureRequest(_Strict):
    phase: Literal["before", "after"]
    source: Literal["upload", "replay"] = "upload"
    signal: list[float] | None = Field(default=None, min_length=16, max_length=262_144)
    sampling_frequency_hz: float | None = Field(default=None, gt=0, le=1_000_000)
    recording_id: str | None = Field(default=None, max_length=160)
    window_index: int | None = Field(default=None, ge=0, le=10_000)
    captured_at: str | None = Field(default=None, max_length=40)
    operating_condition_id: str | None = Field(default=None, max_length=64)

    @field_validator("signal")
    @classmethod
    def _finite(cls, values: list[float] | None) -> list[float] | None:
        if values is None:
            return values
        for value in values:
            if value != value or value in (float("inf"), float("-inf")):
                raise ValueError("signal contains a non-finite sample")
        return values


class WorkOrderCreateRequest(_Strict):
    asset_id: str = Field(min_length=1, max_length=64, pattern=IDENTIFIER)
    component_id: str = Field(min_length=1, max_length=64, pattern=IDENTIFIER)
    title: str = Field(min_length=3, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)
    customer_id: str | None = Field(default=None, max_length=64)


class RepairCreateRequest(_Strict):
    operating_condition_id: str | None = Field(default=None, max_length=64)


class DiagnosticIngestRequest(_Strict):
    """An externally produced diagnostic event (MQTT, LoRa gateway, REST).

    The payload is validated against the diagnostic contract before it is
    accepted, and the transport must be authorised for the organisation in the
    topic.
    """

    event_id: str = Field(min_length=1, max_length=64, pattern=EVENT_ID)
    asset_id: str = Field(min_length=1, max_length=64, pattern=IDENTIFIER)
    component_id: str = Field(min_length=1, max_length=64, pattern=IDENTIFIER)
    status: str = Field(min_length=1, max_length=64)
    fault_class: str | None = Field(default=None, max_length=64)
    fault_location: str | None = Field(default=None, max_length=64)
    confidence: float | None = Field(default=None, ge=0, le=1)
    confidence_calibrated: bool = False
    probabilities: dict[str, float] = Field(default_factory=dict)
    model_version: str | None = Field(default=None, max_length=120)
    calibration_version: str | None = Field(default=None, max_length=120)
    captured_at: str | None = Field(default=None, max_length=40)
    sampling_frequency_hz: float | None = Field(default=None, gt=0, le=1_000_000)
    window_samples: int | None = Field(default=None, ge=1, le=1_000_000)
    operating_condition_id: str | None = Field(default=None, max_length=64)
    device_id: str | None = Field(default=None, max_length=64)
    simulated: bool = False
    evidence: list[dict] = Field(default_factory=list)
    reading_sha256: str | None = Field(default=None, max_length=128)
