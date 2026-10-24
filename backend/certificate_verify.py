"""Offline certificate verifier for AXON Verify.

Verifies a signed certificate without any network access, a database or an API
key. This is the tool an auditor, a customer or a CI pipeline uses to check that
a certificate has not been altered.

Usage::

    python -m backend.certificate_verify certificate.json
    python -m backend.certificate_verify certificate.json --keyring keys.json
    cat certificate.json | python -m backend.certificate_verify -

The input file holds ``{"payload": {...}, "signature": "...", "key": {...}}`` as
returned by ``GET /v1/public/certificates/{id}``. A keyring file is the object
returned by ``GET /v1/public/keys``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.certificates import verify_certificate

EXIT_VALID = 0
EXIT_INVALID = 1
EXIT_UNUSABLE = 2


def _load(stream) -> dict:
    raw = stream.read() if hasattr(stream, "read") else Path(stream).read_text(encoding="utf-8")
    return json.loads(raw)


def _resolve_key(record: dict, keyring: dict | None) -> tuple[str | None, str | None]:
    """Return ``(key_id, public_key_pem)`` from the record or a keyring."""
    key = record.get("key") or {}
    key_id = key.get("key_id") or record.get("key_id")
    pem = key.get("public_key_pem")
    if pem and key_id:
        return key_id, pem
    if keyring:
        for entry in keyring.get("keys", []):
            if key_id is None or entry.get("key_id") == key_id:
                return entry.get("key_id"), entry.get("public_key_pem")
    return key_id, pem


def verify_payload(record: dict, keyring: dict | None = None) -> dict:
    payload = record.get("payload")
    signature = record.get("signature")
    if not isinstance(payload, dict) or not signature:
        return {"valid": False, "reason": "input must contain a 'payload' object and a 'signature'"}
    key_id, pem = _resolve_key(record, keyring)
    if not pem:
        return {"valid": False, "reason": "no public key: pass the certificate's key or a --keyring"}
    if not verify_certificate(payload, signature, pem):
        return {"valid": False, "key_id": key_id,
                "reason": "the signature does not match the payload"}
    return {
        "valid": True,
        "key_id": key_id,
        "certificate_id": payload.get("certificate_id"),
        "issuer": payload.get("issuer"),
        "verdict": payload.get("verdict"),
        "issued_at": payload.get("issued_at"),
        "asset": (payload.get("asset") or {}).get("name"),
        "component": (payload.get("component") or {}).get("name"),
        "model_version": (payload.get("input_contract") or {}).get("model_version"),
        "confidence_calibrated": (payload.get("input_contract") or {}).get("confidence_calibrated"),
        "replayed_evidence": any(side.get("simulated") for side in
                                 (payload.get("before"), payload.get("after"))
                                 if isinstance(side, dict)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.certificate_verify",
        description="Verify an AXON Verify certificate signature offline.")
    parser.add_argument("certificate",
                        help="certificate JSON file, or '-' for stdin")
    parser.add_argument("--keyring", help="keyring JSON exported from GET /v1/public/keys")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    try:
        record = _load(sys.stdin if args.certificate == "-" else args.certificate)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read certificate: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE

    keyring = None
    if args.keyring:
        try:
            keyring = _load(args.keyring)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"cannot read keyring: {exc}", file=sys.stderr)
            return EXIT_UNUSABLE

    result = verify_payload(record, keyring)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif result["valid"]:
        print("VALID")
        for label, key in (("certificate", "certificate_id"), ("issuer", "issuer"),
                           ("verdict", "verdict"), ("asset", "asset"),
                           ("component", "component"), ("issued at", "issued_at"),
                           ("signed by key", "key_id"), ("model", "model_version")):
            if result.get(key):
                print(f"  {label:16s} {result[key]}")
        if result.get("replayed_evidence"):
            print("  note             at least one reading is replayed historical evidence, "
                  "not a live capture")
        if result.get("confidence_calibrated") is False:
            print("  note             confidence is NOT calibrated; raw model scores")
    else:
        print(f"INVALID: {result['reason']}")

    return EXIT_VALID if result["valid"] else EXIT_INVALID


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
