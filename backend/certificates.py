"""Signed repair certificates for AXON Verify.

A certificate is a canonical JSON payload plus an Ed25519 signature.  The
payload binds the verdict to the exact evidence that produced it: model and
calibration versions, input contract, before/after event hashes, comparability
checks and the policy that was applied.

The certificate is tamper-evident evidence, not a legal certification.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

CERTIFICATE_VERSION = "axv-certificate-v1"

DEFAULT_SCOPE = (
    "Bearing vibration screening within the validated model input contract "
    "(4,096 samples at 64 kHz, healthy versus documented bearing damage). "
    "This certificate records evidence for one component on one asset; it is not "
    "a legal certification, an inspection sign-off, or a guarantee of future operation."
)


class CertificateError(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    """Deterministic bytes for signing (sorted keys, compact separators)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def normalize_for_signing(value: Any) -> Any:
    """Render every number as a fixed-precision string.

    Python writes ``0.0`` and JavaScript writes ``0`` for the same double, so a
    signed payload containing floats cannot be re-serialised identically in two
    languages. Converting numbers to ``%.6f`` strings before signing makes the
    digest portable: the Python CLI, the server and the browser all produce the
    same bytes.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return f"{float(value):.6f}"
    if isinstance(value, dict):
        return {key: normalize_for_signing(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_for_signing(item) for item in value]
    return value


def signing_bytes(payload: Any) -> bytes:
    return canonical_json(normalize_for_signing(payload))


def evidence_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(signing_bytes(value)).hexdigest()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"))


class Signer:
    """Ed25519 signing identity with a stable, published key id."""

    def __init__(self, private_key: Ed25519PrivateKey, key_id: str):
        self.private_key = private_key
        self.key_id = key_id

    # ------------------------------------------------------------- lifecycle

    @classmethod
    def generate(cls, key_id: str | None = None) -> "Signer":
        return cls(Ed25519PrivateKey.generate(), key_id or "axv-key-1")

    @classmethod
    def from_seed(cls, seed_hex: str, key_id: str | None = None) -> "Signer":
        seed = bytes.fromhex(seed_hex)
        if len(seed) != 32:
            raise CertificateError("signing seed must be 32 bytes")
        return cls(Ed25519PrivateKey.from_private_bytes(seed), key_id or "axv-key-1")

    @classmethod
    def from_pem(cls, pem: bytes, key_id: str | None = None) -> "Signer":
        key = serialization.load_pem_private_key(pem, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise CertificateError("signing key must be Ed25519")
        return cls(key, key_id or "axv-key-1")

    @classmethod
    def resolve(cls, store=None) -> "Signer":
        """Load the signing key from the environment or the store.

        Order: ``AXON_SIGNING_KEY`` (hex seed) > ``AXON_SIGNING_KEY_FILE`` (PEM) >
        persisted store seed > freshly generated and persisted.
        """
        seed = os.getenv("AXON_SIGNING_KEY")
        key_id = os.getenv("AXON_SIGNING_KEY_ID", "axv-key-1")
        if seed:
            return cls.from_seed(seed.strip(), key_id)

        pem_path = os.getenv("AXON_SIGNING_KEY_FILE")
        if pem_path and os.path.isfile(pem_path):
            with open(pem_path, "rb") as handle:
                return cls.from_pem(handle.read(), key_id)

        if store is not None:
            persisted = store.get_meta("verify.signing.seed")
            persisted_id = store.get_meta("verify.signing.key_id") or key_id
            if persisted:
                signer = cls.from_seed(persisted, persisted_id)
                return signer
            signer = cls.generate(key_id)
            store.set_meta("verify.signing.seed", signer.seed_hex())
            store.set_meta("verify.signing.key_id", signer.key_id)
            return signer

        return cls.generate(key_id)

    def seed_hex(self) -> str:
        raw = self.private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return raw.hex()

    # ------------------------------------------------------------- signatures

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self.private_key.public_key()

    def public_pem(self) -> str:
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")

    def sign(self, payload: dict) -> str:
        signature = self.private_key.sign(signing_bytes(payload))
        return _b64(signature)

    def keyring_entry(self) -> dict:
        return {
            "key_id": self.key_id,
            "algorithm": "Ed25519",
            "public_key_pem": self.public_pem(),
        }


def verify_certificate(payload: dict, signature_b64: str, public_key_pem: str) -> bool:
    try:
        key = serialization.load_pem_public_key(public_key_pem.encode("ascii"))
        if not isinstance(key, Ed25519PublicKey):
            return False
        key.verify(_unb64(signature_b64), signing_bytes(payload))
        return True
    except (InvalidSignature, ValueError, TypeError, KeyError):
        return False


def build_payload(*, org_name: str, repair: dict, asset: dict, component: dict | None,
                  before: dict, after: dict, result: dict, policy: dict,
                  input_contract: dict, certificate_id: str,
                  issued_at: str, technician: str | None = None) -> dict:
    """Assemble the signed certificate payload.

    Only non-PII, publication-safe fields are included: the certificate is
    designed to be shown to a customer who has no account in the system.
    """
    scope = os.getenv("AXON_CERTIFICATE_SCOPE", DEFAULT_SCOPE)
    return {
        "certificate_version": CERTIFICATE_VERSION,
        "certificate_id": certificate_id,
        "repair_id": repair.get("id"),
        "work_order_id": repair.get("work_order_id"),
        "issuer": org_name,
        "issued_at": issued_at,
        "verdict": result["verdict"],
        "scope": scope,
        "technician": technician,
        "asset": {
            "name": asset.get("name"),
            "asset_type": asset.get("asset_type"),
            "manufacturer": asset.get("manufacturer"),
            "model": asset.get("model"),
            "serial": asset.get("serial"),
        },
        "component": {
            "name": (component or {}).get("name") or repair.get("component_id"),
            "kind": (component or {}).get("kind") or "bearing",
            "position": (component or {}).get("position"),
        },
        "input_contract": input_contract,
        "policy": policy,
        "comparability": result["comparability"],
        "reasons": result["reasons"],
        "before": {
            "event_id": (result["before"] or {}).get("event_id"),
            "status": (result["before"] or {}).get("status"),
            "fault_class": (result["before"] or {}).get("fault_class"),
            "confidence": (result["before"] or {}).get("confidence"),
            "probabilities": (result["before"] or {}).get("probabilities"),
            "model_version": (result["before"] or {}).get("model_version"),
            "calibration_version": (result["before"] or {}).get("calibration_version"),
            "captured_at": (result["before"] or {}).get("captured_at"),
            "source": (result["before"] or {}).get("source"),
            "simulated": (result["before"] or {}).get("simulated"),
            "evidence_sha256": evidence_hash(result["before"] or {}),
            "reading_sha256": (before or {}).get("sha256"),
        },
        "after": {
            "event_id": (result["after"] or {}).get("event_id"),
            "status": (result["after"] or {}).get("status"),
            "fault_class": (result["after"] or {}).get("fault_class"),
            "confidence": (result["after"] or {}).get("confidence"),
            "probabilities": (result["after"] or {}).get("probabilities"),
            "model_version": (result["after"] or {}).get("model_version"),
            "calibration_version": (result["after"] or {}).get("calibration_version"),
            "captured_at": (result["after"] or {}).get("captured_at"),
            "source": (result["after"] or {}).get("source"),
            "simulated": (result["after"] or {}).get("simulated"),
            "evidence_sha256": evidence_hash(result["after"] or {}),
            "reading_sha256": (after or {}).get("sha256"),
        },
        "disclaimer": (
            "Tamper-evident evidence record. Verification confirms that the payload has "
            "not been altered since it was signed by the issuing key."
        ),
    }


def qr_data_url(content: str) -> str | None:
    """Render a QR code as a data URL; returns None when qrcode is unavailable."""
    try:
        import io

        import qrcode
    except ImportError:
        return None
    image = qrcode.make(content, box_size=6, border=2)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
