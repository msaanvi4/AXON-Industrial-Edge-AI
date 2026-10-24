"""SQLite-backed multi-tenant store for AXON Verify.

Every tenant-scoped row carries ``org_id`` and every read/write method takes an
explicit ``org_id``.  Cross-tenant access is refused by construction rather than
by relying on the caller.  A single shared connection with an RLock keeps the
demo and the test suite deterministic without requiring Docker.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

# Tables that must never be read or written without an org scope.
TENANT_TABLES = {
    "customers", "sites", "assets", "components", "devices",
    "readings", "diagnostics", "work_orders", "repair_sessions",
    "certificates", "audit_events", "memberships", "api_keys",
    "sessions", "events_seen",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_digest(value: str) -> str:
    """A prefixed digest, matching the format used in certificates."""
    return "sha256:" + sha256_text(value)


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS orgs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    plan TEXT NOT NULL DEFAULT 'pilot',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memberships (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (org_id, user_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL,
    org_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    scopes TEXT NOT NULL DEFAULT 'read,write',
    created_at TEXT NOT NULL,
    last_used TEXT,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sites (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    name TEXT NOT NULL,
    address TEXT,
    timezone TEXT DEFAULT 'UTC',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    site_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    name TEXT NOT NULL,
    asset_type TEXT NOT NULL DEFAULT 'rotating_equipment',
    manufacturer TEXT,
    model TEXT,
    serial TEXT,
    qr_token TEXT NOT NULL UNIQUE,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS components (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'bearing',
    position TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    site_id TEXT,
    device_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'mqtt_gateway',
    status TEXT NOT NULL DEFAULT 'pending',
    cert_fingerprint TEXT,
    last_seen TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS readings (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    component_id TEXT NOT NULL,
    device_id TEXT,
    source TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    sampling_frequency_hz REAL NOT NULL,
    window_samples INTEGER NOT NULL,
    operating_condition_id TEXT,
    signal_json TEXT,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS diagnostics (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    reading_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    model_version TEXT,
    calibration_version TEXT,
    status TEXT NOT NULL,
    fault_class TEXT,
    fault_location TEXT,
    confidence REAL,
    confidence_calibrated INTEGER NOT NULL DEFAULT 0,
    probabilities TEXT NOT NULL DEFAULT '{}',
    evidence TEXT NOT NULL DEFAULT '[]',
    missing_information TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (org_id, event_id)
);

CREATE TABLE IF NOT EXISTS work_orders (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    customer_id TEXT,
    asset_id TEXT NOT NULL,
    component_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    technician_id TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS repair_sessions (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    work_order_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    component_id TEXT NOT NULL,
    operating_condition_id TEXT,
    before_diagnostic_id TEXT,
    after_diagnostic_id TEXT,
    status TEXT NOT NULL DEFAULT 'capturing',
    verdict TEXT,
    reasons TEXT NOT NULL DEFAULT '[]',
    policy_version TEXT,
    created_at TEXT NOT NULL,
    verified_at TEXT
);

CREATE TABLE IF NOT EXISTS certificates (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    repair_id TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    signature TEXT NOT NULL,
    key_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events_seen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (org_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_assets_org ON assets (org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_readings_org ON readings (org_id, asset_id);
CREATE INDEX IF NOT EXISTS idx_diag_org ON diagnostics (org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_wo_org ON work_orders (org_id, status);
CREATE INDEX IF NOT EXISTS idx_repair_org ON repair_sessions (org_id, work_order_id);
CREATE INDEX IF NOT EXISTS idx_audit_org ON audit_events (org_id, created_at DESC);
"""


class TenantIsolationError(RuntimeError):
    """Raised when an operation is attempted without a tenant scope."""


class VerifyStore:
    """Multi-tenant persistence for AXON Verify."""

    def __init__(self, path: str = ":memory:"):
        self.path = path
        self._lock = threading.RLock()
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _require_org(org_id: str | None) -> str:
        if not org_id:
            raise TenantIsolationError("operation requires an org scope")
        return org_id

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    def _row(self, sql: str, params: tuple = ()) -> dict | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    @staticmethod
    def _json(value: Any) -> str:
        return canonical_json(value)

    @staticmethod
    def _load(value: str | None, default: Any) -> Any:
        if value is None:
            return default
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default

    # ------------------------------------------------------------------- meta

    def get_meta(self, key: str) -> str | None:
        row = self._row("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    # ------------------------------------------------------------ orgs / users

    def create_org(self, name: str, plan: str = "pilot") -> dict:
        slug = "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-")
        slug = slug[:48] or "org"
        existing = self._row("SELECT id FROM orgs WHERE slug=?", (slug,))
        if existing:
            slug = f"{slug}-{secrets.token_hex(2)}"
        org = {"id": new_id("org"), "name": name, "slug": slug, "plan": plan, "created_at": utcnow()}
        self._execute(
            "INSERT INTO orgs (id, name, slug, plan, created_at) VALUES (?,?,?,?,?)",
            (org["id"], org["name"], org["slug"], org["plan"], org["created_at"]),
        )
        return org

    def get_org(self, org_id: str) -> dict | None:
        return self._row("SELECT * FROM orgs WHERE id=?", (org_id,))

    def create_user(self, email: str, name: str, password_hash: str) -> dict:
        user = {
            "id": new_id("usr"),
            "email": email.strip().lower(),
            "name": name,
            "password_hash": password_hash,
            "created_at": utcnow(),
        }
        self._execute(
            "INSERT INTO users (id, email, name, password_hash, created_at) VALUES (?,?,?,?,?)",
            (user["id"], user["email"], user["name"], user["password_hash"], user["created_at"]),
        )
        return user

    def get_user(self, user_id: str) -> dict | None:
        return self._row("SELECT id, email, name, created_at FROM users WHERE id=?", (user_id,))

    def get_user_by_email(self, email: str) -> dict | None:
        return self._row("SELECT * FROM users WHERE email=?", (email.strip().lower(),))

    def add_membership(self, org_id: str, user_id: str, role: str) -> dict:
        self._require_org(org_id)
        membership = {
            "id": new_id("mem"), "org_id": org_id, "user_id": user_id,
            "role": role, "created_at": utcnow(),
        }
        self._execute(
            "INSERT INTO memberships (id, org_id, user_id, role, created_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(org_id, user_id) DO UPDATE SET role=excluded.role",
            (membership["id"], org_id, user_id, role, membership["created_at"]),
        )
        return membership

    def get_membership(self, org_id: str, user_id: str) -> dict | None:
        self._require_org(org_id)
        return self._row(
            "SELECT * FROM memberships WHERE org_id=? AND user_id=?", (org_id, user_id)
        )

    def list_members(self, org_id: str) -> list[dict]:
        self._require_org(org_id)
        return self._rows(
            "SELECT m.id, m.role, m.created_at, u.id AS user_id, u.email, u.name "
            "FROM memberships m JOIN users u ON u.id = m.user_id "
            "WHERE m.org_id=? ORDER BY m.created_at",
            (org_id,),
        )

    def orgs_for_user(self, user_id: str) -> list[dict]:
        return self._rows(
            "SELECT o.id, o.name, o.slug, o.plan, m.role FROM orgs o "
            "JOIN memberships m ON m.org_id = o.id WHERE m.user_id=? ORDER BY o.created_at",
            (user_id,),
        )

    # --------------------------------------------------------------- sessions

    def create_session(self, token_hash: str, user_id: str, org_id: str, expires_at: str) -> dict:
        session = {
            "id": new_id("ses"), "token_hash": token_hash, "user_id": user_id,
            "org_id": org_id, "expires_at": expires_at, "created_at": utcnow(),
        }
        self._execute(
            "INSERT INTO sessions (id, token_hash, user_id, org_id, expires_at, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (session["id"], token_hash, user_id, org_id, expires_at, session["created_at"]),
        )
        return session

    def get_session(self, token_hash: str) -> dict | None:
        return self._row("SELECT * FROM sessions WHERE token_hash=?", (token_hash,))

    def revoke_session(self, token_hash: str) -> None:
        self._execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    def revoke_user_sessions(self, user_id: str) -> None:
        self._execute("DELETE FROM sessions WHERE user_id=?", (user_id,))

    # --------------------------------------------------------------- api keys

    def create_api_key(self, org_id: str, name: str, key_hash: str, scopes: str) -> dict:
        self._require_org(org_id)
        key = {
            "id": new_id("key"), "org_id": org_id, "name": name, "key_hash": key_hash,
            "scopes": scopes, "created_at": utcnow(), "last_used": None, "revoked_at": None,
        }
        self._execute(
            "INSERT INTO api_keys (id, org_id, name, key_hash, scopes, created_at) VALUES (?,?,?,?,?,?)",
            (key["id"], org_id, name, key_hash, scopes, key["created_at"]),
        )
        return key

    def get_api_key(self, key_hash: str) -> dict | None:
        return self._row(
            "SELECT * FROM api_keys WHERE key_hash=? AND revoked_at IS NULL", (key_hash,)
        )

    def touch_api_key(self, key_id: str) -> None:
        self._execute("UPDATE api_keys SET last_used=? WHERE id=?", (utcnow(), key_id))

    def list_api_keys(self, org_id: str) -> list[dict]:
        self._require_org(org_id)
        return self._rows(
            "SELECT id, name, scopes, created_at, last_used FROM api_keys "
            "WHERE org_id=? AND revoked_at IS NULL ORDER BY created_at",
            (org_id,),
        )

    def revoke_api_key(self, org_id: str, key_id: str) -> bool:
        self._require_org(org_id)
        cursor = self._execute(
            "UPDATE api_keys SET revoked_at=? WHERE id=? AND org_id=?",
            (utcnow(), key_id, org_id),
        )
        return cursor.rowcount > 0

    # ------------------------------------------------- customers / sites / assets

    def create_customer(self, org_id: str, name: str) -> dict:
        self._require_org(org_id)
        customer = {"id": new_id("cus"), "org_id": org_id, "name": name, "created_at": utcnow()}
        self._execute(
            "INSERT INTO customers (id, org_id, name, created_at) VALUES (?,?,?,?)",
            (customer["id"], org_id, name, customer["created_at"]),
        )
        return customer

    def list_customers(self, org_id: str) -> list[dict]:
        self._require_org(org_id)
        return self._rows("SELECT * FROM customers WHERE org_id=? ORDER BY name", (org_id,))

    def get_customer(self, org_id: str, customer_id: str) -> dict | None:
        self._require_org(org_id)
        return self._row("SELECT * FROM customers WHERE id=? AND org_id=?", (customer_id, org_id))

    def create_site(self, org_id: str, customer_id: str, name: str, address: str | None = None) -> dict:
        self._require_org(org_id)
        site = {
            "id": new_id("sit"), "org_id": org_id, "customer_id": customer_id, "name": name,
            "address": address, "timezone": "UTC", "created_at": utcnow(),
        }
        self._execute(
            "INSERT INTO sites (id, org_id, customer_id, name, address, timezone, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (site["id"], org_id, customer_id, name, address, "UTC", site["created_at"]),
        )
        return site

    def list_sites(self, org_id: str, customer_id: str | None = None) -> list[dict]:
        self._require_org(org_id)
        if customer_id:
            return self._rows(
                "SELECT * FROM sites WHERE org_id=? AND customer_id=? ORDER BY name",
                (org_id, customer_id),
            )
        return self._rows("SELECT * FROM sites WHERE org_id=? ORDER BY name", (org_id,))

    def get_site(self, org_id: str, site_id: str) -> dict | None:
        self._require_org(org_id)
        return self._row("SELECT * FROM sites WHERE id=? AND org_id=?", (site_id, org_id))

    def create_asset(self, org_id: str, **asset) -> dict:
        self._require_org(org_id)
        record = {
            "id": new_id("ast"),
            "org_id": org_id,
            "site_id": asset["site_id"],
            "customer_id": asset["customer_id"],
            "name": asset["name"],
            "asset_type": asset.get("asset_type", "rotating_equipment"),
            "manufacturer": asset.get("manufacturer"),
            "model": asset.get("model"),
            "serial": asset.get("serial"),
            "qr_token": asset.get("qr_token") or secrets.token_urlsafe(16),
            "metadata": self._json(asset.get("metadata") or {}),
            "created_at": utcnow(),
        }
        self._execute(
            "INSERT INTO assets (id, org_id, site_id, customer_id, name, asset_type, "
            "manufacturer, model, serial, qr_token, metadata, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (record["id"], org_id, record["site_id"], record["customer_id"], record["name"],
             record["asset_type"], record["manufacturer"], record["model"], record["serial"],
             record["qr_token"], record["metadata"], record["created_at"]),
        )
        record["metadata"] = self._load(record["metadata"], {})
        return record

    def list_assets(self, org_id: str) -> list[dict]:
        self._require_org(org_id)
        rows = self._rows("SELECT * FROM assets WHERE org_id=? ORDER BY created_at DESC", (org_id,))
        for row in rows:
            row["metadata"] = self._load(row["metadata"], {})
        return rows

    def get_asset(self, org_id: str, asset_id: str) -> dict | None:
        self._require_org(org_id)
        row = self._row("SELECT * FROM assets WHERE id=? AND org_id=?", (asset_id, org_id))
        if row:
            row["metadata"] = self._load(row["metadata"], {})
        return row

    def get_asset_by_qr(self, qr_token: str, org_id: str) -> dict | None:
        self._require_org(org_id)
        row = self._row("SELECT * FROM assets WHERE qr_token=? AND org_id=?", (qr_token, org_id))
        if row:
            row["metadata"] = self._load(row["metadata"], {})
        return row

    def create_component(self, org_id: str, asset_id: str, name: str, kind: str = "bearing",
                         position: str | None = None) -> dict:
        self._require_org(org_id)
        component = {
            "id": new_id("cmp"), "org_id": org_id, "asset_id": asset_id, "name": name,
            "kind": kind, "position": position, "created_at": utcnow(),
        }
        self._execute(
            "INSERT INTO components (id, org_id, asset_id, name, kind, position, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (component["id"], org_id, asset_id, name, kind, position, component["created_at"]),
        )
        return component

    def list_components(self, org_id: str, asset_id: str) -> list[dict]:
        self._require_org(org_id)
        return self._rows(
            "SELECT * FROM components WHERE org_id=? AND asset_id=? ORDER BY created_at",
            (org_id, asset_id),
        )

    def get_component(self, org_id: str, component_id: str) -> dict | None:
        self._require_org(org_id)
        return self._row(
            "SELECT * FROM components WHERE id=? AND org_id=?", (component_id, org_id))

    # ---------------------------------------------------------------- devices

    def create_device(self, org_id: str, name: str, device_key: str, kind: str = "mqtt_gateway",
                      site_id: str | None = None, cert_fingerprint: str | None = None) -> dict:
        self._require_org(org_id)
        device = {
            "id": new_id("dev"), "org_id": org_id, "site_id": site_id, "device_key": device_key,
            "name": name, "kind": kind, "status": "active", "cert_fingerprint": cert_fingerprint,
            "last_seen": None, "created_at": utcnow(),
        }
        self._execute(
            "INSERT INTO devices (id, org_id, site_id, device_key, name, kind, status, "
            "cert_fingerprint, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (device["id"], org_id, site_id, device_key, name, kind, "active",
             cert_fingerprint, device["created_at"]),
        )
        return device

    def list_devices(self, org_id: str) -> list[dict]:
        self._require_org(org_id)
        return self._rows("SELECT * FROM devices WHERE org_id=? ORDER BY created_at", (org_id,))

    def get_device(self, org_id: str, device_id: str) -> dict | None:
        self._require_org(org_id)
        return self._row("SELECT * FROM devices WHERE id=? AND org_id=?", (device_id, org_id))

    def find_device_by_key(self, device_key: str) -> dict | None:
        """Device lookup by MQTT identity (not tenant scoped by input)."""
        return self._row("SELECT * FROM devices WHERE device_key=?", (device_key,))

    def touch_device(self, org_id: str, device_id: str) -> None:
        self._require_org(org_id)
        self._execute(
            "UPDATE devices SET last_seen=? WHERE id=? AND org_id=?",
            (utcnow(), device_id, org_id),
        )

    def last_device_seen(self, org_id: str) -> dict | None:
        self._require_org(org_id)
        return self._row(
            "SELECT device_key, name, kind, last_seen FROM devices "
            "WHERE org_id=? AND last_seen IS NOT NULL ORDER BY last_seen DESC LIMIT 1",
            (org_id,),
        )

    # --------------------------------------------------------------- readings

    def create_reading(self, org_id: str, **reading) -> dict:
        self._require_org(org_id)
        signal = reading.get("signal") or []
        record = {
            "id": new_id("rdg"),
            "org_id": org_id,
            "asset_id": reading["asset_id"],
            "component_id": reading["component_id"],
            "device_id": reading.get("device_id"),
            "source": reading.get("source", "upload"),
            "captured_at": reading.get("captured_at") or utcnow(),
            "sampling_frequency_hz": float(reading["sampling_frequency_hz"]),
            "window_samples": int(reading["window_samples"]),
            "operating_condition_id": reading.get("operating_condition_id"),
            "signal_json": self._json(signal) if signal else None,
            "sha256": reading["sha256"],
            "created_at": utcnow(),
        }
        self._execute(
            "INSERT INTO readings (id, org_id, asset_id, component_id, device_id, source, "
            "captured_at, sampling_frequency_hz, window_samples, operating_condition_id, "
            "signal_json, sha256, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (record["id"], org_id, record["asset_id"], record["component_id"], record["device_id"],
             record["source"], record["captured_at"], record["sampling_frequency_hz"],
             record["window_samples"], record["operating_condition_id"], record["signal_json"],
             record["sha256"], record["created_at"]),
        )
        return record

    def get_reading(self, org_id: str, reading_id: str) -> dict | None:
        self._require_org(org_id)
        row = self._row("SELECT * FROM readings WHERE id=? AND org_id=?", (reading_id, org_id))
        if row:
            row["signal"] = self._load(row.pop("signal_json", None), [])
        return row

    def list_readings(self, org_id: str, asset_id: str | None = None, limit: int = 50) -> list[dict]:
        self._require_org(org_id)
        if asset_id:
            rows = self._rows(
                "SELECT * FROM readings WHERE org_id=? AND asset_id=? "
                "ORDER BY created_at DESC LIMIT ?", (org_id, asset_id, limit))
        else:
            rows = self._rows(
                "SELECT * FROM readings WHERE org_id=? ORDER BY created_at DESC LIMIT ?",
                (org_id, limit))
        for row in rows:
            row["signal"] = self._load(row.pop("signal_json", None), [])
        return rows

    # ------------------------------------------------------------ diagnostics

    def create_diagnostic(self, org_id: str, **diag) -> dict:
        self._require_org(org_id)
        record = {
            "id": new_id("dia"),
            "org_id": org_id,
            "reading_id": diag["reading_id"],
            "event_id": diag["event_id"],
            "model_version": diag.get("model_version"),
            "calibration_version": diag.get("calibration_version"),
            "status": diag["status"],
            "fault_class": diag.get("fault_class"),
            "fault_location": diag.get("fault_location"),
            "confidence": diag.get("confidence"),
            "confidence_calibrated": 1 if diag.get("confidence_calibrated") else 0,
            "probabilities": self._json(diag.get("probabilities") or {}),
            "evidence": self._json(diag.get("evidence") or []),
            "missing_information": self._json(diag.get("missing_information") or []),
            "source": diag.get("source", "api"),
            "simulated": 1 if diag.get("simulated") else 0,
            "created_at": diag.get("created_at") or utcnow(),
        }
        self._execute(
            "INSERT INTO diagnostics (id, org_id, reading_id, event_id, model_version, "
            "calibration_version, status, fault_class, fault_location, confidence, "
            "confidence_calibrated, probabilities, evidence, missing_information, source, "
            "simulated, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (record["id"], org_id, record["reading_id"], record["event_id"], record["model_version"],
             record["calibration_version"], record["status"], record["fault_class"],
             record["fault_location"], record["confidence"], record["confidence_calibrated"],
             record["probabilities"], record["evidence"], record["missing_information"],
             record["source"], record["simulated"], record["created_at"]),
        )
        return self.get_diagnostic(org_id, record["id"])

    def get_diagnostic(self, org_id: str, diagnostic_id: str) -> dict | None:
        self._require_org(org_id)
        row = self._row("SELECT * FROM diagnostics WHERE id=? AND org_id=?", (diagnostic_id, org_id))
        return self._hydrate_diag(row) if row else None

    def find_diagnostic_by_event(self, org_id: str, event_id: str) -> dict | None:
        self._require_org(org_id)
        row = self._row(
            "SELECT * FROM diagnostics WHERE org_id=? AND event_id=?", (org_id, event_id))
        return self._hydrate_diag(row) if row else None

    def list_diagnostics(self, org_id: str, asset_id: str | None = None,
                         limit: int = 50) -> list[dict]:
        self._require_org(org_id)
        if asset_id:
            rows = self._rows(
                "SELECT d.* FROM diagnostics d JOIN readings r ON r.id = d.reading_id "
                "WHERE d.org_id=? AND r.asset_id=? ORDER BY d.created_at DESC LIMIT ?",
                (org_id, asset_id, limit))
        else:
            rows = self._rows(
                "SELECT * FROM diagnostics WHERE org_id=? ORDER BY created_at DESC LIMIT ?",
                (org_id, limit))
        return [self._hydrate_diag(row) for row in rows]

    @staticmethod
    def _hydrate_diag(row: dict) -> dict:
        row["probabilities"] = VerifyStore._load(row.get("probabilities"), {})
        row["evidence"] = VerifyStore._load(row.get("evidence"), [])
        row["missing_information"] = VerifyStore._load(row.get("missing_information"), [])
        row["confidence_calibrated"] = bool(row.get("confidence_calibrated"))
        row["simulated"] = bool(row.get("simulated"))
        return row

    # ------------------------------------------------------------ work orders

    def create_work_order(self, org_id: str, **order) -> dict:
        self._require_org(org_id)
        record = {
            "id": new_id("wo"),
            "org_id": org_id,
            "customer_id": order.get("customer_id"),
            "asset_id": order["asset_id"],
            "component_id": order["component_id"],
            "title": order["title"],
            "status": order.get("status", "open"),
            "technician_id": order.get("technician_id"),
            "notes": order.get("notes"),
            "created_at": utcnow(),
            "closed_at": None,
        }
        self._execute(
            "INSERT INTO work_orders (id, org_id, customer_id, asset_id, component_id, title, "
            "status, technician_id, notes, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (record["id"], org_id, record["customer_id"], record["asset_id"],
             record["component_id"], record["title"], record["status"],
             record["technician_id"], record["notes"], record["created_at"]),
        )
        return record

    def get_work_order(self, org_id: str, work_order_id: str) -> dict | None:
        self._require_org(org_id)
        return self._row(
            "SELECT * FROM work_orders WHERE id=? AND org_id=?", (work_order_id, org_id))

    def list_work_orders(self, org_id: str, status: str | None = None) -> list[dict]:
        self._require_org(org_id)
        if status:
            return self._rows(
                "SELECT * FROM work_orders WHERE org_id=? AND status=? ORDER BY created_at DESC",
                (org_id, status))
        return self._rows(
            "SELECT * FROM work_orders WHERE org_id=? ORDER BY created_at DESC", (org_id,))

    def update_work_order(self, org_id: str, work_order_id: str, **fields) -> dict | None:
        self._require_org(org_id)
        allowed = {"status", "notes", "technician_id", "closed_at", "title"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return self.get_work_order(org_id, work_order_id)
        assignments = ", ".join(f"{k}=?" for k in updates)
        self._execute(
            f"UPDATE work_orders SET {assignments} WHERE id=? AND org_id=?",
            (*updates.values(), work_order_id, org_id),
        )
        return self.get_work_order(org_id, work_order_id)

    # -------------------------------------------------------- repair sessions

    def create_repair(self, org_id: str, work_order_id: str, asset_id: str,
                      component_id: str, operating_condition_id: str | None = None) -> dict:
        self._require_org(org_id)
        record = {
            "id": new_id("rpr"), "org_id": org_id, "work_order_id": work_order_id,
            "asset_id": asset_id, "component_id": component_id,
            "operating_condition_id": operating_condition_id,
            "before_diagnostic_id": None, "after_diagnostic_id": None,
            "status": "awaiting_before", "verdict": None, "reasons": [],
            "policy_version": None, "created_at": utcnow(), "verified_at": None,
        }
        self._execute(
            "INSERT INTO repair_sessions (id, org_id, work_order_id, asset_id, component_id, "
            "operating_condition_id, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (record["id"], org_id, work_order_id, asset_id, component_id,
             operating_condition_id, record["status"], record["created_at"]),
        )
        return record

    def get_repair(self, org_id: str, repair_id: str) -> dict | None:
        self._require_org(org_id)
        row = self._row("SELECT * FROM repair_sessions WHERE id=? AND org_id=?",
                        (repair_id, org_id))
        if row:
            row["reasons"] = self._load(row.get("reasons"), [])
        return row

    def list_repairs(self, org_id: str, work_order_id: str | None = None) -> list[dict]:
        self._require_org(org_id)
        if work_order_id:
            rows = self._rows(
                "SELECT * FROM repair_sessions WHERE org_id=? AND work_order_id=? "
                "ORDER BY created_at DESC", (org_id, work_order_id))
        else:
            rows = self._rows(
                "SELECT * FROM repair_sessions WHERE org_id=? ORDER BY created_at DESC",
                (org_id,))
        for row in rows:
            row["reasons"] = self._load(row.get("reasons"), [])
        return rows

    def update_repair(self, org_id: str, repair_id: str, **fields) -> dict | None:
        self._require_org(org_id)
        allowed = {"before_diagnostic_id", "after_diagnostic_id", "status", "verdict",
                   "reasons", "policy_version", "verified_at", "operating_condition_id"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if "reasons" in updates:
            updates["reasons"] = self._json(updates["reasons"])
        if not updates:
            return self.get_repair(org_id, repair_id)
        assignments = ", ".join(f"{k}=?" for k in updates)
        self._execute(
            f"UPDATE repair_sessions SET {assignments} WHERE id=? AND org_id=?",
            (*updates.values(), repair_id, org_id),
        )
        return self.get_repair(org_id, repair_id)

    # ----------------------------------------------------------- certificates

    def create_certificate(self, org_id: str, repair_id: str, payload: dict,
                           signature: str, key_id: str) -> dict:
        self._require_org(org_id)
        record = {
            "id": new_id("cert"), "org_id": org_id, "repair_id": repair_id,
            "payload": self._json(payload), "signature": signature, "key_id": key_id,
            "created_at": utcnow(),
        }
        self._execute(
            "INSERT INTO certificates (id, org_id, repair_id, payload, signature, key_id, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (record["id"], org_id, repair_id, record["payload"], signature, key_id,
             record["created_at"]),
        )
        return record

    def get_certificate(self, org_id: str, certificate_id: str) -> dict | None:
        self._require_org(org_id)
        row = self._row("SELECT * FROM certificates WHERE id=? AND org_id=?",
                        (certificate_id, org_id))
        if row:
            row["payload"] = self._load(row["payload"], {})
        return row

    def get_certificate_by_repair(self, org_id: str, repair_id: str) -> dict | None:
        self._require_org(org_id)
        row = self._row("SELECT * FROM certificates WHERE repair_id=? AND org_id=?",
                        (repair_id, org_id))
        if row:
            row["payload"] = self._load(row["payload"], {})
        return row

    def find_certificate(self, certificate_id: str) -> dict | None:
        """Public lookup used by the verifier (still returns only published fields)."""
        row = self._row("SELECT * FROM certificates WHERE id=?", (certificate_id,))
        if row:
            row["payload"] = self._load(row["payload"], {})
        return row

    def list_certificates(self, org_id: str, limit: int = 50) -> list[dict]:
        self._require_org(org_id)
        rows = self._rows(
            "SELECT * FROM certificates WHERE org_id=? ORDER BY created_at DESC LIMIT ?",
            (org_id, limit))
        for row in rows:
            row["payload"] = self._load(row["payload"], {})
        return rows

    # ----------------------------------------------------------------- audit

    def audit(self, org_id: str, actor: str, action: str, entity_type: str | None = None,
              entity_id: str | None = None, metadata: dict | None = None) -> dict:
        self._require_org(org_id)
        record = {
            "id": new_id("aud"), "org_id": org_id, "actor": actor, "action": action,
            "entity_type": entity_type, "entity_id": entity_id,
            "metadata": self._json(metadata or {}), "created_at": utcnow(),
        }
        self._execute(
            "INSERT INTO audit_events (id, org_id, actor, action, entity_type, entity_id, "
            "metadata, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (record["id"], org_id, actor, action, entity_type, entity_id, record["metadata"],
             record["created_at"]),
        )
        return record

    def list_audit(self, org_id: str, limit: int = 100) -> list[dict]:
        self._require_org(org_id)
        rows = self._rows(
            "SELECT * FROM audit_events WHERE org_id=? ORDER BY created_at DESC LIMIT ?",
            (org_id, limit))
        for row in rows:
            row["metadata"] = self._load(row["metadata"], {})
        return rows

    # ------------------------------------------------------- idempotency gate

    def seen_event(self, org_id: str, event_id: str) -> bool:
        self._require_org(org_id)
        row = self._row(
            "SELECT 1 FROM events_seen WHERE org_id=? AND event_id=?", (org_id, event_id))
        return row is not None

    def record_event(self, org_id: str, event_id: str, source: str) -> bool:
        """Return True when the event is new, False when it is a duplicate."""
        self._require_org(org_id)
        try:
            self._execute(
                "INSERT INTO events_seen (org_id, event_id, source, created_at) VALUES (?,?,?,?)",
                (org_id, event_id, source, utcnow()))
            return True
        except sqlite3.IntegrityError:
            return False

    # -------------------------------------------------------------- stats

    def stats(self, org_id: str) -> dict:
        self._require_org(org_id)
        def count(sql: str) -> int:
            row = self._row(sql, (org_id,))
            return int(list(row.values())[0]) if row else 0
        return {
            "assets": count("SELECT COUNT(*) FROM assets WHERE org_id=?"),
            "readings": count("SELECT COUNT(*) FROM readings WHERE org_id=?"),
            "diagnostics": count("SELECT COUNT(*) FROM diagnostics WHERE org_id=?"),
            "work_orders": count("SELECT COUNT(*) FROM work_orders WHERE org_id=?"),
            "open_work_orders": count(
                "SELECT COUNT(*) FROM work_orders WHERE org_id=? AND status IN "
                "('open','awaiting_verification','capturing')"),
            "verified_repairs": count(
                "SELECT COUNT(*) FROM repair_sessions WHERE org_id=? AND verdict='VERIFIED'"),
            "not_verified": count(
                "SELECT COUNT(*) FROM repair_sessions WHERE org_id=? AND verdict='NOT_VERIFIED'"),
            "inconclusive": count(
                "SELECT COUNT(*) FROM repair_sessions WHERE org_id=? AND verdict IN "
                "('INCONCLUSIVE','CANNOT_VERIFY')"),
            "certificates": count("SELECT COUNT(*) FROM certificates WHERE org_id=?"),
            "devices": count("SELECT COUNT(*) FROM devices WHERE org_id=?"),
            "pending_verification": count(
                "SELECT COUNT(*) FROM repair_sessions WHERE org_id=? AND verdict IS NULL"),
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
