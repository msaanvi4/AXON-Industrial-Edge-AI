"""Self-contained HTML renderings for certificates and evidence reports.

No external assets: the pages must render from a file, from an email
attachment or from an air-gapped verifier. The browser's print-to-PDF produces
the customer-facing document, which avoids a heavy PDF dependency in the
backend.
"""
from __future__ import annotations

import html
import json
from typing import Any

from backend.certificates import qr_data_url

VERDICT_LABELS = {
    "VERIFIED": "Repair verified",
    "NOT_VERIFIED": "Repair not verified",
    "INCONCLUSIVE": "Inconclusive",
    "CANNOT_VERIFY": "Cannot verify",
    "NOT_APPLICABLE": "No repair required",
}

VERDICT_TONES = {
    "VERIFIED": "#1f9d55",
    "NOT_VERIFIED": "#c53030",
    "INCONCLUSIVE": "#b7791f",
    "CANNOT_VERIFY": "#8a6d3b",
    "NOT_APPLICABLE": "#4a5568",
}

STYLES = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; background: #eef1f5; color: #1a202c;
  font-family: 'Segoe UI', system-ui, -apple-system, Helvetica, Arial, sans-serif; }
.sheet { max-width: 900px; margin: 24px auto; background: #fff; padding: 40px 48px;
  border-radius: 10px; box-shadow: 0 8px 30px rgba(20,30,60,.12); }
.brand { display: flex; justify-content: space-between; align-items: flex-start;
  border-bottom: 3px solid #1a202c; padding-bottom: 14px; margin-bottom: 22px; }
h1 { font-size: 22px; margin: 0; letter-spacing: .06em; }
.sub { color: #627d98; font-size: 12px; margin-top: 4px; letter-spacing: .08em;
  text-transform: uppercase; }
.badge { display: inline-block; padding: 8px 16px; border-radius: 6px; color: #fff;
  font-size: 18px; font-weight: 700; letter-spacing: .04em; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 28px; margin-bottom: 22px; }
.field { border-bottom: 1px dotted #cbd5e0; padding: 7px 0; }
.k { font-size: 10.5px; text-transform: uppercase; letter-spacing: .09em; color: #718096; }
.v { font-size: 14px; color: #1a202c; word-break: break-word; }
table { width: 100%; border-collapse: collapse; margin: 10px 0 22px; font-size: 13px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #e2e8f0; vertical-align: top; }
th { background: #f7fafc; font-size: 11px; text-transform: uppercase; letter-spacing: .07em;
  color: #4a5568; }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .1em; color: #2d3748;
  margin: 26px 0 6px; border-left: 3px solid #1a202c; padding-left: 9px; }
ul { margin: 6px 0 0; padding-left: 20px; font-size: 13px; color: #2d3748; }
.sig { background: #f7fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px 16px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11.5px;
  word-break: break-all; color: #2d3748; }
.notice { background: #fffaf0; border-left: 4px solid #dd6b20; padding: 10px 14px;
  font-size: 12.5px; color: #744210; margin: 14px 0; border-radius: 0 4px 4px 0; }
.disclaimer { font-size: 11.5px; color: #718096; line-height: 1.6; margin-top: 20px;
  border-top: 1px solid #e2e8f0; padding-top: 14px; }
.ok { color: #22543d; font-weight: 600; }
.bad { color: #9b2c2c; font-weight: 600; }
.qr { float: right; text-align: center; font-size: 10px; color: #718096; }
.qr img { width: 96px; height: 96px; display: block; }
footer { margin-top: 26px; font-size: 11px; color: #a0aec0; text-align: center; }
@media print {
  body { background: #fff; }
  .sheet { box-shadow: none; margin: 0; max-width: none; padding: 20px; }
  .noprint { display: none !important; }
}
"""


def _esc(value: Any) -> str:
    if value is None:
        return "—"
    return html.escape(str(value))


def _percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _field(key: str, value: Any) -> str:
    return f'<div class="field"><div class="k">{_esc(key)}</div><div class="v">{_esc(value)}</div></div>'


def _evidence_rows(label: str, side: dict) -> str:
    probabilities = side.get("probabilities") or {}
    rendered = ", ".join(f"{key}={_percent(value)}" for key, value in sorted(probabilities.items()))
    return (
        f"<tr><th>{_esc(label)}</th>"
        f"<td>{_esc(side.get('status'))}</td>"
        f"<td>{_esc(side.get('fault_class')) or '—'}</td>"
        f"<td>{_percent(side.get('confidence'))}</td>"
        f"<td>{_esc(rendered)}</td>"
        f"<td>{_esc(side.get('captured_at'))}</td>"
        f"<td>{_esc(side.get('source'))}</td></tr>"
    )


def _replay_disclosure(payload: dict) -> str:
    sides = [payload.get("before") or {}, payload.get("after") or {}]
    replayed = [side for side in sides if side.get("simulated")]
    if not replayed:
        return ""
    labels = ", ".join(_esc(side.get("event_id")) for side in replayed)
    return (
        '<div class="notice"><strong>Evidence source disclosure.</strong> '
        'At least one reading on this certificate was produced by replaying a packaged '
        'historical laboratory recording, not by a live field capture. Replayed events: '
        f'{labels}. The Paderborn University bearing dataset windows are published under '
        'CC BY-NC 4.0 and are labelled in the evidence record.</div>'
    )


def _calibration_notice(payload: dict) -> str:
    contract = payload.get("input_contract") or {}
    if contract.get("confidence_calibrated"):
        return ""
    return (
        '<div class="notice"><strong>Uncalibrated confidence.</strong> The deployed model '
        'has no calibration artifact for this deployment, so confidence values are raw '
        'model scores rather than probabilities of correctness. The conservative '
        'verification policy was applied.</div>'
    )


def certificate_html(certificate: dict, signature_valid: bool, key: dict,
                     qr_url: str | None = None) -> str:
    payload = certificate.get("payload") or {}
    verdict = payload.get("verdict")
    tone = VERDICT_TONES.get(verdict, "#4a5568")
    asset = payload.get("asset") or {}
    component = payload.get("component") or {}
    before = payload.get("before") or {}
    after = payload.get("after") or {}
    policy = payload.get("policy") or {}
    contract = payload.get("input_contract") or {}
    comparability = payload.get("comparability") or {}
    signature = certificate.get("signature") or ""
    key_id = certificate.get("key_id") or (key or {}).get("key_id")

    qr = ""
    if qr_url:
        data = qr_data_url(qr_url)
        if data:
            qr = (f'<div class="qr"><img src="{data}" alt="verification QR code"/>'
                  f'<div>scan to re-verify</div></div>')

    comparability_rows = "".join(
        f"<tr><td>{_esc(name.replace('_', ' '))}</td>"
        f"<td class=\"{'ok' if ok else 'bad'}\">{'pass' if ok else 'fail'}</td></tr>"
        for name, ok in sorted(comparability.items()) if name != "comparable")

    reasons = "".join(f"<li><strong>{_esc(reason.get('code'))}</strong> — "
                      f"{_esc(reason.get('detail'))}</li>"
                      for reason in payload.get("reasons") or [])

    verification_state = (
        f'<span class="ok">signature verified against key {_esc(key_id)}</span>'
        if signature_valid else
        '<span class="bad">signature verification FAILED — this document must not be trusted</span>')

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>AXON Verify certificate {_esc(payload.get('certificate_id'))}</title>
<style>{STYLES}</style></head>
<body><div class="sheet">
  <div class="brand">
    <div>
      <h1>AXON VERIFY</h1>
      <div class="sub">Repair verification record · {_esc(payload.get('issuer'))}</div>
    </div>
    <div style="text-align:right">
      {qr}
      <div class="badge" style="background:{tone}">{_esc(VERDICT_LABELS.get(verdict, verdict))}</div>
      <div class="sub" style="margin-top:6px">{_esc(payload.get('certificate_id'))}</div>
    </div>
  </div>

  <div class="grid">
    {_field('Issued at', payload.get('issued_at'))}
    {_field('Certificate version', payload.get('certificate_version'))}
    {_field('Work order', payload.get('work_order_id'))}
    {_field('Repair session', payload.get('repair_id'))}
    {_field('Asset', asset.get('name'))}
    {_field('Asset type', asset.get('asset_type'))}
    {_field('Manufacturer / model', f"{asset.get('manufacturer') or '—'} {asset.get('model') or ''}".strip())}
    {_field('Serial number', asset.get('serial'))}
    {_field('Component', component.get('name'))}
    {_field('Component position', component.get('position'))}
    {_field('Issuing technician', payload.get('technician'))}
    {_field('Policy version', policy.get('policy_version'))}
  </div>

  <h2>Scope of this certificate</h2>
  <p style="font-size:13px;color:#2d3748;line-height:1.6">{_esc(payload.get('scope'))}</p>

  <h2>Evidence</h2>
  <table>
    <thead><tr><th>Phase</th><th>Model status</th><th>Class</th><th>Confidence</th>
      <th>Class probabilities</th><th>Captured</th><th>Source</th></tr></thead>
    <tbody>
      {_evidence_rows('Before repair', before)}
      {_evidence_rows('After repair', after)}
    </tbody>
  </table>

  <h2>Verification reasons</h2>
  <ul>{reasons or '<li>No reasons recorded.</li>'}</ul>

  <h2>Model, calibration and input contract</h2>
  <div class="grid">
    {_field('Model version', contract.get('model_version'))}
    {_field('Calibration version', contract.get('calibration_version') or 'not deployed')}
    {_field('Confidence calibrated', 'yes' if contract.get('confidence_calibrated') else 'no')}
    {_field('Sampling frequency', contract.get('sampling_frequency_hz'))}
    {_field('Window samples', contract.get('window_samples'))}
    {_field('Sensor type', contract.get('sensor_type'))}
    {_field('Required before confidence', policy.get('minimum_before_confidence'))}
    {_field('Required after confidence', policy.get('minimum_after_confidence'))}
    {_field('Required fault-probability drop', policy.get('minimum_damage_drop'))}
    {_field('Maximum after-repair fault probability', policy.get('maximum_after_damage_probability'))}
  </div>

  {_calibration_notice(payload)}
  {_replay_disclosure(payload)}

  <h2>Comparability checks</h2>
  <table><tbody>{comparability_rows or '<tr><td>Not recorded</td><td>—</td></tr>'}</tbody></table>

  <h2>Evidence hashes and signature</h2>
  <div class="sig">
    <div>before event: {_esc(before.get('event_id'))}</div>
    <div>before evidence: {_esc(before.get('evidence_sha256'))}</div>
    <div>before reading: {_esc(before.get('reading_sha256'))}</div>
    <div>after event: {_esc(after.get('event_id'))}</div>
    <div>after evidence: {_esc(after.get('evidence_sha256'))}</div>
    <div>after reading: {_esc(after.get('reading_sha256'))}</div>
    <div style="margin-top:8px">key id: {_esc(key_id)}</div>
    <div>algorithm: Ed25519</div>
    <div>signature: {_esc(signature[:96])}{'…' if len(signature) > 96 else ''}</div>
    <div style="margin-top:8px">status: {verification_state}</div>
  </div>

  <div class="disclaimer">{_esc(payload.get('disclaimer'))}<br/><br/>
    Verification confirms only that this record has not been altered since it was signed.
    It does not confirm that the underlying maintenance work was performed correctly, that
    the equipment is safe to operate, or that the component will continue to operate
    within specification. Operation and maintenance decisions remain the responsibility of
    the equipment owner and qualified personnel.</div>

  <footer>AXON Verify · tamper-evident repair evidence · verify at /verify/{_esc(certificate.get('id'))}</footer>
</div></body></html>"""


def repair_html(certificate: dict) -> str:
    return certificate_html(certificate, True, {})


def compact_summary(payload: dict) -> str:
    """Plain-text summary used in API responses and logs."""
    return json.dumps({
        "certificate_id": payload.get("certificate_id"),
        "verdict": payload.get("verdict"),
        "issuer": payload.get("issuer"),
        "asset": (payload.get("asset") or {}).get("name"),
        "issued_at": payload.get("issued_at"),
    }, sort_keys=True)
