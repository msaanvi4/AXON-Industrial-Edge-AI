"""AXON Verify REST API: organisations, evidence, verification, certificates.

Everything tenant scoped is gated by a :class:`~backend.auth.Principal` bound to
exactly one organisation. The store refuses unscoped access, so a handler
cannot accidentally read across tenants.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Annotated, Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from backend.auth import (
    AuthError,
    Principal,
    generate_api_key,
    hash_api_key,
    hash_password,
    issue_token,
    new_secret,
    read_token,
    verify_password,
)
from backend.calibration import calibrated_state
from backend.certificates import (
    Signer,
    build_payload,
    qr_data_url,
    verify_certificate,
)
from backend.ingestion import (
    InferenceOutcome,
    contract_summary,
    diagnose_signal,
    model_status,
)
from backend.ingest_service import IngestRejection, accept_diagnostic_event
from backend.paderborn_replay import (
    PaderbornReplayError,
    catalog as paderborn_catalog,
    read_window as read_paderborn_window,
)
from backend.report import certificate_html
from backend.verify_models import (
    ApiKeyCreateRequest,
    AssetCreateRequest,
    CustomerCreateRequest,
    DeviceCreateRequest,
    DiagnosticIngestRequest,
    LoginRequest,
    MemberInviteRequest,
    MemberRoleRequest,
    PhaseCaptureRequest,
    ReadingUploadRequest,
    RegisterRequest,
    RepairCreateRequest,
    ReplayCaptureRequest,
    SiteCreateRequest,
    SwitchOrgRequest,
    WorkOrderCreateRequest,
)
from backend.verify_store import VerifyStore, canonical_json, sha256_digest, sha256_text, utcnow
from backend.verification import (
    POLICY_VERSION,
    VERIFIED,
    default_policy,
    verify_repair,
)

verify_router = APIRouter(prefix="/v1")


class AuthFailed(HTTPException):
    def __init__(self, detail: str = "authentication required"):
        super().__init__(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


# ----------------------------------------------------------------- plumbing


def get_store(request: Request) -> VerifyStore:
    store = getattr(request.app.state, "verify_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="the verification store is not configured")
    return store


def get_signer(request: Request) -> Signer:
    return request.app.state.verify_signer


def _pbkdf2_iterations() -> int:
    return int(os.getenv("AXON_PBKDF2_ITERATIONS", "390000"))


async def current_principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
    store: VerifyStore = Depends(get_store),
) -> Principal:
    """Authenticate a caller as exactly one organisation."""
    raw_key = x_api_key
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        candidate = authorization[7:].strip()
        if candidate.startswith("axv1_"):
            raw_key = candidate
        else:
            token = candidate
    if raw_key:
        record = store.get_api_key(hash_api_key(raw_key))
        if record is None:
            raise AuthFailed("unknown or revoked API key")
        store.touch_api_key(record["id"])
        return Principal(
            user_id=record["id"], org_id=record["org_id"], role="api_key",
            email=f"api-key:{record['name']}", name=record["name"], auth_type="api_key")
    if not token:
        raise AuthFailed()
    payload = read_token(request.app.state.verify_token_secret, token)
    if payload is None:
        raise AuthFailed("session token is invalid or expired")
    session = store.get_session(sha256_text(token))
    if session is None:
        raise AuthFailed("session has been revoked")
    if session["org_id"] != payload.get("org"):
        raise AuthFailed("session does not match the token organisation")
    user = store.get_user(payload["sub"])
    if user is None:
        raise AuthFailed("account no longer exists")
    membership = store.get_membership(payload["org"], user["id"])
    if membership is None:
        raise AuthFailed("account is not a member of this organisation")
    return Principal(
        user_id=user["id"], org_id=payload["org"], role=membership["role"],
        email=user["email"], name=user["name"], auth_type="session")


def require(permission: str) -> Callable:
    """Dependency factory enforcing a permission for the caller's role."""

    async def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.can(permission):
            raise HTTPException(
                status_code=403,
                detail=f"role '{principal.role}' does not have the '{permission}' permission",
            )
        return principal

    return dependency


def _issue_session(request: Request, store: VerifyStore, user_id: str, org_id: str) -> dict:
    token = issue_token(request.app.state.verify_token_secret, user_id, org_id)
    store.create_session(
        sha256_text(token), user_id, org_id,
        (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat())
    return {"access_token": token, "token_type": "bearer", "expires_in": 60 * 60 * 12,
            "org_id": org_id}


def _require_asset(store: VerifyStore, org_id: str, asset_id: str) -> dict:
    asset = store.get_asset(org_id, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="asset not found in this organisation")
    return asset


def _require_component(store: VerifyStore, org_id: str, component_id: str) -> dict:
    row = store.get_component(org_id, component_id)
    if row is None:
        raise HTTPException(status_code=404, detail="component not found in this organisation")
    return row


def _require_repair(store: VerifyStore, org_id: str, repair_id: str) -> dict:
    repair = store.get_repair(org_id, repair_id)
    if repair is None:
        raise HTTPException(status_code=404, detail="repair session not found")
    return repair


def evidence_dict(diagnostic: dict, reading: dict) -> dict:
    """Join a diagnostic with its reading for the policy engine."""
    return {
        "event_id": diagnostic.get("event_id"),
        "reading_id": reading.get("id"),
        "asset_id": reading.get("asset_id"),
        "component_id": reading.get("component_id"),
        "status": diagnostic.get("status"),
        "fault_class": diagnostic.get("fault_class"),
        "fault_location": diagnostic.get("fault_location"),
        "confidence": diagnostic.get("confidence"),
        "confidence_calibrated": diagnostic.get("confidence_calibrated"),
        "probabilities": diagnostic.get("probabilities") or {},
        "model_version": diagnostic.get("model_version"),
        "calibration_version": diagnostic.get("calibration_version"),
        "captured_at": reading.get("captured_at"),
        "sampling_frequency_hz": reading.get("sampling_frequency_hz"),
        "window_samples": reading.get("window_samples"),
        "operating_condition_id": reading.get("operating_condition_id"),
        "sha256": reading.get("sha256"),
        "source": reading.get("source"),
        "simulated": bool(diagnostic.get("simulated")),
        "created_at": diagnostic.get("created_at"),
    }


def _persist_outcome(store: VerifyStore, org_id: str, asset_id: str, component_id: str,
                     outcome: InferenceOutcome, *, source: str,
                     sampling_frequency_hz: float, window_samples: int,
                     signal: list[float] | None, captured_at: str | None,
                     operating_condition_id: str | None,
                     device_id: str | None) -> tuple[dict, dict]:
    """Write the reading and its diagnostic as one evidence pair."""
    digest_input = canonical_json({
        "signal": [round(float(v), 9) for v in (signal or [])],
        "fs": float(sampling_frequency_hz),
        "asset_id": asset_id,
        "component_id": component_id,
        "captured_at": captured_at or utcnow(),
    })
    reading = store.create_reading(
        org_id,
        asset_id=asset_id,
        component_id=component_id,
        device_id=device_id,
        source=source,
        captured_at=captured_at or utcnow(),
        sampling_frequency_hz=sampling_frequency_hz,
        window_samples=window_samples,
        operating_condition_id=operating_condition_id,
        signal=signal,
        sha256=sha256_digest(digest_input),
    )
    diagnostic = store.create_diagnostic(
        org_id,
        reading_id=reading["id"],
        event_id=outcome.diagnostic["event_id"],
        model_version=outcome.model_version,
        calibration_version=outcome.calibration_version,
        status=outcome.diagnostic["status"],
        fault_class=outcome.diagnostic.get("fault_class"),
        fault_location=outcome.diagnostic.get("fault_location"),
        confidence=outcome.diagnostic.get("confidence"),
        confidence_calibrated=outcome.calibrated,
        probabilities=outcome.diagnostic.get("probabilities") or {},
        evidence=outcome.diagnostic.get("evidence") or [],
        missing_information=outcome.diagnostic.get("missing_information") or [],
        source=source,
        simulated=bool(outcome.diagnostic.get("simulated")),
    )
    return reading, diagnostic


def _repair_view(store: VerifyStore, org_id: str, repair: dict) -> dict:
    """Full repair state including the joined before/after evidence."""
    view = dict(repair)
    for phase in ("before", "after"):
        diagnostic_id = repair.get(f"{phase}_diagnostic_id")
        view[phase] = None
        if not diagnostic_id:
            continue
        diagnostic = store.get_diagnostic(org_id, diagnostic_id)
        if diagnostic is None:
            continue
        reading = store.get_reading(org_id, diagnostic["reading_id"])
        view[phase] = {"diagnostic": diagnostic, "reading": reading,
                       "evidence": evidence_dict(diagnostic, reading or {})}
    certificate = store.get_certificate_by_repair(org_id, repair["id"])
    view["certificate"] = {"id": certificate["id"], "key_id": certificate["key_id"],
                           "created_at": certificate["created_at"]} if certificate else None
    work_order = store.get_work_order(org_id, repair["work_order_id"])
    view["work_order"] = work_order
    return view


def _run_policy(repair: dict, before: dict, after: dict) -> dict:
    calibrated = bool(before.get("confidence_calibrated")) and bool(
        after.get("confidence_calibrated"))
    return verify_repair(before, after, default_policy(calibrated))


# ------------------------------------------------------------------- identity


@verify_router.post("/auth/register")
def register(payload: RegisterRequest, request: Request, response: Response,
             store: VerifyStore = Depends(get_store)):
    if store.get_user_by_email(payload.email):
        raise HTTPException(status_code=409, detail="an account with this email already exists")
    try:
        password_hash = hash_password(payload.password, iterations=_pbkdf2_iterations())
    except AuthError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    org = store.create_org(payload.org_name)
    user = store.create_user(payload.email, payload.name, password_hash)
    store.add_membership(org["id"], user["id"], "owner")
    session = _issue_session(request, store, user["id"], org["id"])
    store.audit(org["id"], user["email"], "org.created", "org", org["id"],
                {"name": org["name"], "slug": org["slug"]})
    response.status_code = 201
    return {
        "access_token": session["access_token"], "token_type": "bearer",
        "expires_in": session["expires_in"], "org": org,
        "user": {"id": user["id"], "email": user["email"], "name": user["name"]},
        "role": "owner",
    }


@verify_router.post("/auth/login")
def login(payload: LoginRequest, request: Request, store: VerifyStore = Depends(get_store)):
    user = store.get_user_by_email(payload.email)
    if user is None or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid email or password")
    orgs = store.orgs_for_user(user["id"])
    if not orgs:
        raise HTTPException(status_code=403, detail="this account is not a member of an organisation")
    org_id = payload.org_id or orgs[0]["id"]
    if org_id not in {org["id"] for org in orgs}:
        raise HTTPException(status_code=403, detail="this account is not a member of that organisation")
    session = _issue_session(request, store, user["id"], org_id)
    role = next(org["role"] for org in orgs if org["id"] == org_id)
    return {
        "access_token": session["access_token"], "token_type": "bearer",
        "expires_in": session["expires_in"], "org_id": org_id, "role": role,
        "user": {"id": user["id"], "email": user["email"], "name": user["name"]},
        "organisations": orgs,
    }


@verify_router.post("/auth/switch-org")
def switch_org(payload: SwitchOrgRequest, request: Request,
               principal: Principal = Depends(current_principal),
               store: VerifyStore = Depends(get_store)):
    if store.get_membership(payload.org_id, principal.user_id) is None:
        raise HTTPException(status_code=403, detail="this account is not a member of that organisation")
    return _issue_session(request, store, principal.user_id, payload.org_id)


@verify_router.post("/auth/logout")
def logout(request: Request, principal: Principal = Depends(current_principal),
           store: VerifyStore = Depends(get_store)):
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        store.revoke_session(sha256_text(authorization[7:].strip()))
    return {"status": "signed_out"}


@verify_router.get("/me")
def me(principal: Principal = Depends(current_principal), store: VerifyStore = Depends(get_store)):
    user = store.get_user(principal.user_id) or {}
    return {
        "user": {"id": principal.user_id, "email": principal.email, "name": principal.name},
        "organisation": store.get_org(principal.org_id),
        "role": principal.role,
        "permissions": sorted(principal.permissions),
        "auth_type": principal.auth_type,
        "organisations": store.orgs_for_user(principal.user_id),
    }


# --------------------------------------------------------------------- team


@verify_router.get("/team")
def list_team(principal: Principal = Depends(require("read")),
              store: VerifyStore = Depends(get_store)):
    return {"members": store.list_members(principal.org_id)}


@verify_router.post("/team", status_code=201)
def invite_member(payload: MemberInviteRequest, principal: Principal = Depends(require("manage")),
                  store: VerifyStore = Depends(get_store)):
    email = payload.email.strip().lower()
    user = store.get_user_by_email(email)
    created = user is None
    if user is None:
        password = payload.password or generate_api_key(principal.org_id)[-16:]
        try:
            user = store.create_user(email, payload.name, hash_password(
                password, iterations=_pbkdf2_iterations()))
        except AuthError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    store.add_membership(principal.org_id, user["id"], payload.role)
    store.audit(principal.org_id, principal.actor, "member.invited", "user", user["id"],
                {"email": email, "role": payload.role, "account_created": created})
    return {"member": {"user_id": user["id"], "email": email, "role": payload.role},
            "account_created": created,
            "note": "This deployment has no email delivery; share the temporary password out of band."
            if created else "Existing account was granted access."}


@verify_router.patch("/team/{user_id}")
def update_member(user_id: str, payload: MemberRoleRequest,
                  principal: Principal = Depends(require("manage")),
                  store: VerifyStore = Depends(get_store)):
    membership = store.get_membership(principal.org_id, user_id)
    if membership is None:
        raise HTTPException(status_code=404, detail="member not found in this organisation")
    if membership["role"] == "owner" and payload.role != "owner":
        owners = [m for m in store.list_members(principal.org_id) if m["role"] == "owner"]
        if len(owners) <= 1:
            raise HTTPException(status_code=409, detail="an organisation must keep one owner")
    store.add_membership(principal.org_id, user_id, payload.role)
    store.audit(principal.org_id, principal.actor, "member.role_changed", "user", user_id,
                {"from": membership["role"], "to": payload.role})
    return {"user_id": user_id, "role": payload.role}


@verify_router.get("/audit")
def audit_log(limit: int = 100, principal: Principal = Depends(require("audit")),
              store: VerifyStore = Depends(get_store)):
    return {"events": store.list_audit(principal.org_id, limit=min(limit, 500))}


# ----------------------------------------------------------------- API keys


@verify_router.get("/api-keys")
def list_api_keys(principal: Principal = Depends(require("manage")),
                  store: VerifyStore = Depends(get_store)):
    return {"keys": store.list_api_keys(principal.org_id)}


@verify_router.post("/api-keys", status_code=201)
def create_api_key(payload: ApiKeyCreateRequest, principal: Principal = Depends(require("manage")),
                   store: VerifyStore = Depends(get_store)):
    raw = generate_api_key(principal.org_id)
    key = store.create_api_key(principal.org_id, payload.name, hash_api_key(raw), "read,write")
    store.audit(principal.org_id, principal.actor, "api_key.created", "api_key", key["id"],
                {"name": payload.name})
    return {"id": key["id"], "name": key["name"], "key": raw,
            "warning": "This secret is shown once and cannot be retrieved again."}


@verify_router.delete("/api-keys/{key_id}")
def revoke_api_key(key_id: str, principal: Principal = Depends(require("manage")),
                   store: VerifyStore = Depends(get_store)):
    if not store.revoke_api_key(principal.org_id, key_id):
        raise HTTPException(status_code=404, detail="API key not found")
    store.audit(principal.org_id, principal.actor, "api_key.revoked", "api_key", key_id)
    return {"status": "revoked", "id": key_id}


# ------------------------------------------------------------------ registry


@verify_router.get("/customers")
def list_customers(principal: Principal = Depends(require("read")),
                   store: VerifyStore = Depends(get_store)):
    return {"customers": store.list_customers(principal.org_id)}


@verify_router.post("/customers", status_code=201)
def create_customer(payload: CustomerCreateRequest, principal: Principal = Depends(require("write")),
                    store: VerifyStore = Depends(get_store)):
    customer = store.create_customer(principal.org_id, payload.name)
    store.audit(principal.org_id, principal.actor, "customer.created", "customer",
                customer["id"], {"name": payload.name})
    return customer


@verify_router.get("/sites")
def list_sites(customer_id: str | None = None, principal: Principal = Depends(require("read")),
               store: VerifyStore = Depends(get_store)):
    return {"sites": store.list_sites(principal.org_id, customer_id)}


@verify_router.post("/sites", status_code=201)
def create_site(payload: SiteCreateRequest, principal: Principal = Depends(require("write")),
                store: VerifyStore = Depends(get_store)):
    if store.get_customer(principal.org_id, payload.customer_id) is None:
        raise HTTPException(status_code=404, detail="customer not found")
    site = store.create_site(principal.org_id, payload.customer_id, payload.name, payload.address)
    store.audit(principal.org_id, principal.actor, "site.created", "site", site["id"],
                {"name": payload.name})
    return site


@verify_router.get("/assets")
def list_assets(principal: Principal = Depends(require("read")),
                store: VerifyStore = Depends(get_store)):
    assets = store.list_assets(principal.org_id)
    for asset in assets:
        asset["components"] = store.list_components(principal.org_id, asset["id"])
    return {"assets": assets}


@verify_router.post("/assets", status_code=201)
def create_asset(payload: AssetCreateRequest, principal: Principal = Depends(require("write")),
                 store: VerifyStore = Depends(get_store)):
    if store.get_site(principal.org_id, payload.site_id) is None:
        raise HTTPException(status_code=404, detail="site not found")
    if store.get_customer(principal.org_id, payload.customer_id) is None:
        raise HTTPException(status_code=404, detail="customer not found")
    asset = store.create_asset(
        principal.org_id, site_id=payload.site_id, customer_id=payload.customer_id,
        name=payload.name, asset_type=payload.asset_type, manufacturer=payload.manufacturer,
        model=payload.model, serial=payload.serial)
    component = store.create_component(
        principal.org_id, asset["id"], payload.component_name, "bearing",
        payload.component_position)
    store.audit(principal.org_id, principal.actor, "asset.created", "asset", asset["id"],
                {"name": payload.name, "site_id": payload.site_id})
    return {**asset, "components": [component]}


@verify_router.get("/assets/{asset_id}")
def get_asset(asset_id: str, principal: Principal = Depends(require("read")),
              store: VerifyStore = Depends(get_store)):
    asset = _require_asset(store, principal.org_id, asset_id)
    asset["components"] = store.list_components(principal.org_id, asset_id)
    asset["work_orders"] = store.list_work_orders(principal.org_id)
    asset["work_orders"] = [w for w in asset["work_orders"] if w["asset_id"] == asset_id]
    return asset


@verify_router.post("/assets/{asset_id}/components", status_code=201)
def add_component(asset_id: str, payload: dict, principal: Principal = Depends(require("write")),
                  store: VerifyStore = Depends(get_store)):
    _require_asset(store, principal.org_id, asset_id)
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="component name is required")
    component = store.create_component(
        principal.org_id, asset_id, name, str(payload.get("kind") or "bearing"),
        payload.get("position"))
    return component


@verify_router.get("/assets/{asset_id}/timeline")
def asset_timeline(asset_id: str, limit: int = 100, principal: Principal = Depends(require("read")),
                   store: VerifyStore = Depends(get_store)):
    _require_asset(store, principal.org_id, asset_id)
    events = []
    for diagnostic in store.list_diagnostics(principal.org_id, asset_id, min(limit, 300)):
        reading = store.get_reading(principal.org_id, diagnostic["reading_id"]) or {}
        events.append({
            "kind": "diagnostic",
            "at": diagnostic["created_at"],
            "event_id": diagnostic["event_id"],
            "status": diagnostic["status"],
            "fault_class": diagnostic.get("fault_class"),
            "confidence": diagnostic.get("confidence"),
            "confidence_calibrated": diagnostic.get("confidence_calibrated"),
            "model_version": diagnostic.get("model_version"),
            "calibration_version": diagnostic.get("calibration_version"),
            "source": reading.get("source"),
            "simulated": bool(diagnostic.get("simulated")),
        })
    for repair in store.list_repairs(principal.org_id):
        if repair["asset_id"] != asset_id:
            continue
        events.append({"kind": "repair", "at": repair["created_at"], "repair_id": repair["id"],
                       "verdict": repair.get("verdict"), "status": repair.get("status")})
    events.sort(key=lambda item: item["at"], reverse=True)
    return {"asset_id": asset_id, "events": events[:limit]}


# ------------------------------------------------------------------ devices


@verify_router.get("/devices")
def list_devices(principal: Principal = Depends(require("read")),
                 store: VerifyStore = Depends(get_store)):
    return {"devices": store.list_devices(principal.org_id)}


@verify_router.post("/devices", status_code=201)
def create_device(payload: DeviceCreateRequest, principal: Principal = Depends(require("manage")),
                  store: VerifyStore = Depends(get_store)):
    if store.find_device_by_key(payload.device_key):
        raise HTTPException(status_code=409, detail="a device with this key already exists")
    if payload.site_id and store.get_site(principal.org_id, payload.site_id) is None:
        raise HTTPException(status_code=404, detail="site not found")
    device = store.create_device(
        principal.org_id, payload.name, payload.device_key, payload.kind,
        payload.site_id, payload.cert_fingerprint)
    store.audit(principal.org_id, principal.actor, "device.registered", "device", device["id"],
                {"device_key": payload.device_key, "kind": payload.kind})
    return device


# ------------------------------------------------------------------ evidence


@verify_router.get("/model-status")
def model_status_endpoint(principal: Principal = Depends(require("read"))):
    return model_status()


@verify_router.get("/calibration")
def calibration_status(principal: Principal = Depends(require("read"))):
    return calibrated_state()


@verify_router.get("/paderborn/catalog")
def replay_catalog(principal: Principal = Depends(require("read"))):
    try:
        return paderborn_catalog()
    except PaderbornReplayError as exc:
        return {"available": False, "reason": str(exc), "records": []}


@verify_router.post("/readings", status_code=201)
def upload_reading(payload: ReadingUploadRequest, principal: Principal = Depends(require("write")),
                   store: VerifyStore = Depends(get_store)):
    _require_asset(store, principal.org_id, payload.asset_id)
    _require_component(store, principal.org_id, payload.component_id)
    if payload.event_id and store.find_diagnostic_by_event(principal.org_id, payload.event_id):
        raise HTTPException(status_code=409, detail="event_id has already been recorded")
    outcome = diagnose_signal(
        payload.asset_id, payload.component_id, payload.signal, payload.sampling_frequency_hz,
        source=payload.source, captured_at=payload.captured_at)
    reading, diagnostic = _persist_outcome(
        store, principal.org_id, payload.asset_id, payload.component_id, outcome,
        source=payload.source, sampling_frequency_hz=payload.sampling_frequency_hz,
        window_samples=len(payload.signal), signal=list(payload.signal),
        captured_at=payload.captured_at,
        operating_condition_id=payload.operating_condition_id, device_id=None)
    store.audit(principal.org_id, principal.actor, "reading.ingested", "reading", reading["id"],
                {"source": payload.source, "status": diagnostic["status"],
                 "model_version": outcome.model_version})
    return {"reading": reading, "diagnostic": diagnostic,
            "contract": contract_summary(outcome)}


@verify_router.post("/readings/replay", status_code=201)
def replay_reading(payload: ReplayCaptureRequest, principal: Principal = Depends(require("write")),
                   store: VerifyStore = Depends(get_store)):
    """Score a packaged historical window into an organisation's evidence store."""
    _require_asset(store, principal.org_id, payload.asset_id)
    _require_component(store, principal.org_id, payload.component_id)
    return _replay_for_target(payload, payload.asset_id, payload.component_id, principal, store)


def _replay_for_target(payload: ReplayCaptureRequest, asset_id: str, component_id: str,
                       principal: Principal, store: VerifyStore) -> dict:
    try:
        record, signal, sampling_frequency = read_paderborn_window(
            payload.recording_id, payload.window_index)
    except PaderbornReplayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    outcome = diagnose_signal(
        asset_id, component_id, signal, sampling_frequency, source="replay",
        captured_at=payload.captured_at, simulated=True,
        evidence_extra=[{
            "kind": "vibration_window", "source": "paderborn_dataset_replay",
            "transport": "local_replay", "historical_replay": True,
            "recording_id": record["recording_id"], "bearing_id": record["bearing_id"],
            "operating_condition": record["operating_condition"],
            "documented_fault_class": record["fault_class"],
            "documented_fault_location": record["fault_location"],
            "damage_type": record["damage_type"], "label_source": record["label_source"],
            "license_id": record["license_id"],
        }])
    reading, diagnostic = _persist_outcome(
        store, principal.org_id, asset_id, component_id, outcome, source="replay",
        sampling_frequency_hz=sampling_frequency, window_samples=int(signal.size),
        signal=None, captured_at=payload.captured_at,
        operating_condition_id=payload.operating_condition_id or record["operating_condition"],
        device_id=None)
    return {"reading": reading, "diagnostic": diagnostic, "dataset": record,
            "contract": contract_summary(outcome)}


@verify_router.get("/readings")
def list_readings(asset_id: str | None = None, limit: int = 50,
                  principal: Principal = Depends(require("read")),
                  store: VerifyStore = Depends(get_store)):
    readings = store.list_readings(principal.org_id, asset_id, min(limit, 500))
    return {"readings": [{k: v for k, v in row.items() if k != "signal"} for row in readings]}


@verify_router.get("/diagnostics")
def list_diagnostics(asset_id: str | None = None, limit: int = 50,
                     principal: Principal = Depends(require("read")),
                     store: VerifyStore = Depends(get_store)):
    return {"diagnostics": store.list_diagnostics(principal.org_id, asset_id, min(limit, 500))}


@verify_router.post("/ingest/diagnostic", status_code=201)
def ingest_diagnostic(payload: DiagnosticIngestRequest,
                      principal: Principal = Depends(require("write")),
                      store: VerifyStore = Depends(get_store)):
    """Accept a pre-computed diagnostic from an authorised transport.

    The record is validated against the diagnostic contract and de-duplicated by
    ``event_id``. This is the HTTP twin of the MQTT ingest worker; both run the
    same acceptance rules in :mod:`backend.ingest_service`.
    """
    try:
        result = accept_diagnostic_event(
            store, payload, org_id=principal.org_id, actor=principal.actor,
            transport="rest", device_id=None)
    except IngestRejection as exc:
        # An asset or component that does not exist inside this organisation is
        # a 404: the caller must not learn anything about other tenants.
        if exc.code in ("unknown_asset", "unknown_component"):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        status = 409 if exc.code == "duplicate_event" else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {"status": "accepted", "diagnostic": result["diagnostic"],
            "reading": result["reading"]}


# ------------------------------------------------------------- work orders


@verify_router.get("/work-orders")
def list_work_orders(status: str | None = None, principal: Principal = Depends(require("read")),
                     store: VerifyStore = Depends(get_store)):
    orders = store.list_work_orders(principal.org_id, status)
    for order in orders:
        order["repairs"] = store.list_repairs(principal.org_id, order["id"])
        asset = store.get_asset(principal.org_id, order["asset_id"])
        order["asset_name"] = asset["name"] if asset else None
    return {"work_orders": orders}


@verify_router.post("/work-orders", status_code=201)
def create_work_order(payload: WorkOrderCreateRequest,
                      principal: Principal = Depends(require("write")),
                      store: VerifyStore = Depends(get_store)):
    asset = _require_asset(store, principal.org_id, payload.asset_id)
    _require_component(store, principal.org_id, payload.component_id)
    order = store.create_work_order(
        principal.org_id, asset_id=payload.asset_id, component_id=payload.component_id,
        title=payload.title, notes=payload.notes,
        customer_id=payload.customer_id or asset.get("customer_id"),
        technician_id=principal.user_id)
    store.audit(principal.org_id, principal.actor, "work_order.created", "work_order",
                order["id"], {"title": payload.title, "asset_id": payload.asset_id})
    return order


@verify_router.get("/work-orders/{work_order_id}")
def get_work_order(work_order_id: str, principal: Principal = Depends(require("read")),
                   store: VerifyStore = Depends(get_store)):
    order = store.get_work_order(principal.org_id, work_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="work order not found")
    order["repairs"] = [_repair_view(store, principal.org_id, repair)
                        for repair in store.list_repairs(principal.org_id, work_order_id)]
    asset = store.get_asset(principal.org_id, order["asset_id"])
    order["asset"] = asset
    order["asset_name"] = asset["name"] if asset else None
    components = store.list_components(principal.org_id, order["asset_id"])
    order["component"] = next((c for c in components if c["id"] == order["component_id"]), None)
    return order


@verify_router.patch("/work-orders/{work_order_id}")
def update_work_order(work_order_id: str, payload: dict,
                      principal: Principal = Depends(require("write")),
                      store: VerifyStore = Depends(get_store)):
    if store.get_work_order(principal.org_id, work_order_id) is None:
        raise HTTPException(status_code=404, detail="work order not found")
    allowed = {k: v for k, v in payload.items() if k in {"status", "notes"} and v is not None}
    order = store.update_work_order(principal.org_id, work_order_id, **allowed)
    store.audit(principal.org_id, principal.actor, "work_order.updated", "work_order",
                work_order_id, allowed)
    return order


# ------------------------------------------------------------ repair flow


@verify_router.post("/work-orders/{work_order_id}/repairs", status_code=201)
def create_repair(work_order_id: str, payload: RepairCreateRequest,
                  principal: Principal = Depends(require("write")),
                  store: VerifyStore = Depends(get_store)):
    order = store.get_work_order(principal.org_id, work_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="work order not found")
    repair = store.create_repair(principal.org_id, work_order_id, order["asset_id"],
                                 order["component_id"], payload.operating_condition_id)
    store.audit(principal.org_id, principal.actor, "repair.created", "repair", repair["id"],
                {"work_order_id": work_order_id})
    return repair


@verify_router.get("/repairs/{repair_id}")
def get_repair(repair_id: str, principal: Principal = Depends(require("read")),
               store: VerifyStore = Depends(get_store)):
    repair = _require_repair(store, principal.org_id, repair_id)
    return _repair_view(store, principal.org_id, repair)


@verify_router.post("/repairs/{repair_id}/capture")
def capture_phase(repair_id: str, payload: PhaseCaptureRequest,
                  principal: Principal = Depends(require("write")),
                  store: VerifyStore = Depends(get_store)):
    """Capture one side of a repair: a before or an after reading."""
    repair = _require_repair(store, principal.org_id, repair_id)
    if repair.get(f"{payload.phase}_diagnostic_id"):
        raise HTTPException(status_code=409,
                            detail=f"the {payload.phase} reading has already been captured; "
                                   "create a new repair session to recapture")
    asset = _require_asset(store, principal.org_id, repair["asset_id"])
    components = store.list_components(principal.org_id, repair["asset_id"])
    component_id = repair["component_id"] or (
        components[0]["id"] if components else repair["component_id"])

    if payload.source == "upload":
        if not payload.signal or not payload.sampling_frequency_hz:
            raise HTTPException(status_code=422,
                                detail="an uploaded capture needs 'signal' and 'sampling_frequency_hz'")
        outcome = diagnose_signal(
            asset["id"], component_id, payload.signal, payload.sampling_frequency_hz,
            source="upload", captured_at=payload.captured_at)
        reading, diagnostic = _persist_outcome(
            store, principal.org_id, asset["id"], component_id, outcome, source="upload",
            sampling_frequency_hz=payload.sampling_frequency_hz,
            window_samples=len(payload.signal), signal=list(payload.signal),
            captured_at=payload.captured_at,
            operating_condition_id=payload.operating_condition_id or repair.get("operating_condition_id"),
            device_id=None)
    else:
        if not payload.recording_id or payload.window_index is None:
            raise HTTPException(status_code=422,
                                detail="a replayed capture needs 'recording_id' and 'window_index'")
        try:
            record, signal, sampling_frequency = read_paderborn_window(
                payload.recording_id, payload.window_index)
        except PaderbornReplayError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        outcome = diagnose_signal(
            asset["id"], component_id, signal, sampling_frequency, source="replay",
            captured_at=payload.captured_at, simulated=True,
            evidence_extra=[{
                "kind": "vibration_window", "source": "paderborn_dataset_replay",
                "transport": "local_replay", "historical_replay": True,
                "recording_id": record["recording_id"], "bearing_id": record["bearing_id"],
                "operating_condition": record["operating_condition"],
                "documented_fault_class": record["fault_class"],
                "documented_fault_location": record["fault_location"],
                "damage_type": record["damage_type"], "label_source": record["label_source"],
                "license_id": record["license_id"],
            }])
        reading, diagnostic = _persist_outcome(
            store, principal.org_id, asset["id"], component_id, outcome, source="replay",
            sampling_frequency_hz=sampling_frequency, window_samples=int(signal.size),
            signal=None, captured_at=payload.captured_at,
            operating_condition_id=payload.operating_condition_id or repair.get("operating_condition_id")
            or record["operating_condition"],
            device_id=None)

    updates = {f"{payload.phase}_diagnostic_id": diagnostic["id"]}
    updated = store.update_repair(principal.org_id, repair_id, **updates)
    # Recompute the lifecycle state from what is actually captured.
    if updated.get("before_diagnostic_id") and updated.get("after_diagnostic_id"):
        next_status = "awaiting_verification"
    elif payload.phase == "before":
        next_status = "awaiting_after"
    else:
        next_status = "awaiting_before"
    updated = store.update_repair(principal.org_id, repair_id, status=next_status)
    store.audit(principal.org_id, principal.actor, f"repair.{payload.phase}_captured", "repair",
                repair_id, {"reading_id": reading["id"], "status": diagnostic["status"],
                            "source": reading["source"],
                            "simulated": bool(diagnostic.get("simulated")),
                            "repair_status": next_status})
    return {"repair": _repair_view(store, principal.org_id, updated),
            "phase": payload.phase, "diagnostic": diagnostic,
            "contract": contract_summary(outcome)}


@verify_router.post("/repairs/{repair_id}/verify")
def verify_repair_endpoint(repair_id: str, principal: Principal = Depends(require("verify")),
                           store: VerifyStore = Depends(get_store)):
    """Run the verification policy over the captured evidence."""
    repair = _require_repair(store, principal.org_id, repair_id)
    before, after = _load_evidence(store, principal.org_id, repair)
    result = _run_policy(repair, before, after)
    updated = store.update_repair(
        principal.org_id, repair_id, verdict=result["verdict"], reasons=result["reasons"],
        policy_version=result["policy"]["policy_version"], status="verified"
        if result["verdict"] == VERIFIED else "verification_complete",
        verified_at=utcnow())
    store.audit(principal.org_id, principal.actor, "repair.verified", "repair", repair_id,
                {"verdict": result["verdict"],
                 "reasons": [reason["code"] for reason in result["reasons"]],
                 "policy_version": result["policy"]["policy_version"],
                 "confidence_calibrated": result["policy"]["confidence_calibrated"]})
    return {"repair": _repair_view(store, principal.org_id, updated), "result": result}


def _load_evidence(store: VerifyStore, org_id: str, repair: dict) -> tuple[dict, dict]:
    for phase in ("before", "after"):
        if not repair.get(f"{phase}_diagnostic_id"):
            raise HTTPException(status_code=409,
                                detail=f"the {phase} reading has not been captured yet")
    before_diagnostic = store.get_diagnostic(org_id, repair["before_diagnostic_id"])
    after_diagnostic = store.get_diagnostic(org_id, repair["after_diagnostic_id"])
    if before_diagnostic is None or after_diagnostic is None:
        raise HTTPException(status_code=409, detail="the recorded evidence is missing")
    before_reading = store.get_reading(org_id, before_diagnostic["reading_id"]) or {}
    after_reading = store.get_reading(org_id, after_diagnostic["reading_id"]) or {}
    return (evidence_dict(before_diagnostic, before_reading),
            evidence_dict(after_diagnostic, after_reading))


# ----------------------------------------------------------- certificates


@verify_router.post("/repairs/{repair_id}/certificate", status_code=201)
def issue_certificate(repair_id: str, principal: Principal = Depends(require("certify")),
                      store: VerifyStore = Depends(get_store),
                      signer: Signer = Depends(get_signer)):
    repair = _require_repair(store, principal.org_id, repair_id)
    if not repair.get("verdict"):
        raise HTTPException(status_code=409,
                            detail="verify the repair before issuing a certificate")
    if store.get_certificate_by_repair(principal.org_id, repair_id):
        raise HTTPException(status_code=409, detail="a certificate already exists for this repair")

    before, after = _load_evidence(store, principal.org_id, repair)
    result = _run_policy(repair, before, after)
    if result["verdict"] != repair["verdict"]:
        raise HTTPException(status_code=409,
                            detail="the stored verdict no longer matches the recorded evidence")

    org = store.get_org(principal.org_id)
    asset = _require_asset(store, principal.org_id, repair["asset_id"])
    components = store.list_components(principal.org_id, repair["asset_id"])
    component = next((c for c in components if c["id"] == repair["component_id"]), None)
    certificate_id = f"AXV-{repair_id.upper().split('_')[-1][:12]}"
    issued_at = utcnow()
    policy = result["policy"]
    contract = {
        "sensor_type": "vibration",
        "sampling_frequency_hz": before.get("sampling_frequency_hz"),
        "window_samples": before.get("window_samples"),
        "model_version": before.get("model_version"),
        "calibration_version": before.get("calibration_version"),
        "confidence_calibrated": bool(before.get("confidence_calibrated"))
        and bool(after.get("confidence_calibrated")),
    }
    payload = build_payload(
        org_name=org["name"], repair=repair, asset=asset, component=component,
        before=before, after=after, result=result, policy=policy, input_contract=contract,
        certificate_id=certificate_id, issued_at=issued_at, technician=principal.name
        if principal.auth_type == "session" else None)
    signature = signer.sign(payload)
    certificate = store.create_certificate(principal.org_id, repair_id, payload, signature,
                                           signer.key_id)
    store.update_repair(principal.org_id, repair_id, status="certified")
    store.audit(principal.org_id, principal.actor, "certificate.issued", "certificate",
                certificate["id"], {"verdict": payload["verdict"], "key_id": signer.key_id,
                                    "repair_id": repair_id})
    return {"certificate": {**certificate, "payload": payload},
            "signature": signature, "key_id": signer.key_id,
            "verify_url": f"/verify/{certificate['id']}"}


@verify_router.get("/certificates")
def list_certificates(principal: Principal = Depends(require("read")),
                      store: VerifyStore = Depends(get_store)):
    certificates = store.list_certificates(principal.org_id)
    return {"certificates": [
        {"id": c["id"], "repair_id": c["repair_id"], "key_id": c["key_id"],
         "created_at": c["created_at"], "verdict": c["payload"].get("verdict"),
         "asset": c["payload"].get("asset"), "issuer": c["payload"].get("issuer")}
        for c in certificates]}


@verify_router.get("/certificates/{certificate_id}")
def get_certificate(certificate_id: str, principal: Principal = Depends(require("read")),
                    store: VerifyStore = Depends(get_store),
                    signer: Signer = Depends(get_signer)):
    certificate = store.get_certificate(principal.org_id, certificate_id)
    if certificate is None:
        raise HTTPException(status_code=404, detail="certificate not found")
    return {"certificate": certificate,
            "signature_valid": verify_certificate(certificate["payload"],
                                                  certificate["signature"],
                                                  signer.public_pem()),
            "key": signer.keyring_entry()}


# ------------------------------------------------------------ public verifier


@verify_router.get("/public/keys")
def public_keys(signer: Signer = Depends(get_signer)):
    return {"keys": [signer.keyring_entry()],
            "note": "Certificates are verified offline against these published keys."}


@verify_router.get("/public/certificates/{certificate_id}")
def public_certificate(certificate_id: str, store: VerifyStore = Depends(get_store),
                       signer: Signer = Depends(get_signer)):
    """Public, read-only certificate lookup.

    Returns only publication-safe payload fields; no account is required and no
    organisational data beyond the certificate itself is exposed.
    """
    certificate = store.find_certificate(certificate_id)
    if certificate is None:
        raise HTTPException(status_code=404, detail="certificate not found")
    valid = verify_certificate(certificate["payload"], certificate["signature"],
                               signer.public_pem())
    if not valid:
        return JSONResponse(status_code=409, content={
            "certificate_id": certificate_id, "valid": False,
            "reason": "the signature does not match the published verification key"})
    return {"certificate_id": certificate_id, "valid": True,
            "payload": certificate["payload"], "signature": certificate["signature"],
            "key": signer.keyring_entry(), "verified_at": utcnow()}


@verify_router.get("/public/certificates/{certificate_id}/report", response_class=HTMLResponse)
def public_report(certificate_id: str, store: VerifyStore = Depends(get_store),
                  signer: Signer = Depends(get_signer)):
    certificate = store.find_certificate(certificate_id)
    if certificate is None:
        raise HTTPException(status_code=404, detail="certificate not found")
    valid = verify_certificate(certificate["payload"], certificate["signature"],
                               signer.public_pem())
    return HTMLResponse(certificate_html(certificate, valid, signer.keyring_entry(),
                                         qr_url=f"/v1/public/certificates/{certificate_id}"))


@verify_router.get("/stats")
def stats(principal: Principal = Depends(require("read")),
          store: VerifyStore = Depends(get_store)):
    values = store.stats(principal.org_id)
    values["policy_version"] = POLICY_VERSION
    values["confidence_calibrated"] = calibrated_state()["confidence_calibrated"]
    return values


@verify_router.get("/ingest/status")
def ingest_status(principal: Principal = Depends(require("read")),
                  store: VerifyStore = Depends(get_store)):
    """Transport health: what the MQTT worker last delivered for this tenant."""
    last_device = store.last_device_seen(principal.org_id)
    recent = store.list_diagnostics(principal.org_id, limit=5)
    return {
        "org_id": principal.org_id,
        "mqtt": {
            "configured": bool(os.getenv("AXON_MQTT_URL")),
            "broker": os.getenv("AXON_MQTT_URL"),
            "topic_prefix": os.getenv("AXON_MQTT_TOPIC_PREFIX", "axon/v1"),
            "last_device_seen": last_device,
        },
        "lora": {"available": True, "note": "LoRa evidence arrives through the gateway HTTP API"},
        "upload": {"available": True},
        "recent_diagnostics": [
            {"event_id": d["event_id"], "source": d["source"], "status": d["status"],
             "created_at": d["created_at"], "simulated": d["simulated"]} for d in recent],
    }
