"""Streamlit dashboard for live edge diagnostics and real Paderborn data replay."""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

API = os.getenv("BACKEND_URL", f"http://{os.getenv('BACKEND_IP', 'localhost')}:8000")
TIMEOUT = float(os.getenv("API_TIMEOUT_SECONDS", "3"))
DATASET_REPLAY_TIMEOUT = float(os.getenv("DATASET_REPLAY_TIMEOUT_SECONDS", "30"))
REFRESH = int(os.getenv("DASHBOARD_REFRESH_SECONDS", "3"))
st.set_page_config(page_title="AXON | Bearing Intelligence", page_icon="◉", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    :root { --axon-ink: #17212b; --axon-panel: #ffffff; --axon-panel-2: #f8fafc; --axon-line: #d9e1e8; --axon-text: #17212b; --axon-muted: #667788; --axon-cyan: #0f766e; --axon-amber: #b45309; --axon-red: #b42318; }
    .stApp { background: #f6f8fa; color: var(--axon-text); }
    .block-container { max-width: 1480px; padding-top: 1.5rem; padding-bottom: 3rem; }
    [data-testid="stSidebar"] { background: #ffffff; border-right: 1px solid #dfe6ec; }
    [data-testid="stSidebar"] * { color: #273543; }
    h1, h2, h3 { color: var(--axon-text) !important; letter-spacing: -0.025em; }
    .axon-hero { display:flex; justify-content:space-between; align-items:flex-start; gap:24px; margin: 0 0 24px; padding: 26px 28px; border:1px solid #d9e1e8; border-radius:4px; background:#ffffff; }
    .axon-kicker { font-size:.72rem; font-weight:700; letter-spacing:.1em; color:var(--axon-cyan); }
    .axon-hero h1 { margin:.35rem 0 .4rem; font-size:2.15rem; line-height:1.1; }
    .axon-hero p { margin:0; color:var(--axon-muted); max-width:760px; font-size:1rem; }
    .axon-chip { flex:0 0 auto; border:1px solid #9bd6cc; border-radius:3px; padding:8px 11px; color:#0f5d56; background:#edf9f6; font-weight:700; font-size:.78rem; }
    .section-label { margin: 18px 0 9px; color:var(--axon-text); font-size:1.08rem; font-weight:700; letter-spacing:-.01em; }
    .section-note { color:var(--axon-muted); margin:-3px 0 15px; font-size:.89rem; }
    .diagnostic-card { border:1px solid #d9e1e8; border-radius:4px; padding:19px 20px; background:#ffffff; min-height:115px; }
    .diagnostic-card.alert { border-color:#e4b2ad; background:#fffafa; }
    .diagnostic-card.good { border-color:#a9d8d0; background:#f7fcfb; }
    .diagnostic-eyebrow { color:var(--axon-muted); font-size:.73rem; font-weight:700; letter-spacing:.12em; }
    .diagnostic-value { color:var(--axon-text); font-size:1.25rem; line-height:1.25; margin:7px 0 6px; font-weight:720; }
    .diagnostic-detail { color:#526475; font-size:.9rem; line-height:1.45; }
    .stream-card { display:flex; align-items:center; gap:16px; padding:15px 17px; margin:0 0 18px; border:1px solid #d9e1e8; border-radius:4px; background:#ffffff; }
    .stream-indicator { flex:0 0 auto; width:10px; height:10px; border-radius:50%; background:#0f766e; box-shadow:0 0 0 4px #d9f2ed; }
    .stream-indicator.waiting { background:#b45309; box-shadow:0 0 0 4px #fdf0d8; }
    .stream-copy { min-width:0; }
    .stream-title { color:var(--axon-text); font-size:.91rem; font-weight:700; }
    .stream-detail { color:var(--axon-muted); margin-top:2px; font-size:.82rem; line-height:1.35; }
    .stream-meta { margin-left:auto; color:#526475; font-size:.78rem; text-align:right; white-space:nowrap; }
    .bearing-stage { margin:4px 0 2px; padding:14px 16px 10px; border:1px solid #d9e1e8; border-radius:4px; background:#ffffff; }
    .bearing-stage-title { color:#526475; font-size:.72rem; font-weight:700; letter-spacing:.1em; }
    .bearing-stage svg { display:block; width:100%; max-width:650px; height:auto; margin:3px auto 0; }
    .bearing-stage-note { color:var(--axon-muted); font-size:.81rem; line-height:1.4; }
    .part-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:10px; margin-top:10px; }
    .part-card { border:1px solid #d9e1e8; border-radius:4px; padding:13px 14px; background:#ffffff; }
    .part-card.alert { border-color:#dfaaa5; background:#fffafa; }
    .part-card.neutral { border-color:#d9e1e8; }
    .part-title { color:var(--axon-text); font-size:.92rem; font-weight:700; }
    .part-status { color:var(--axon-cyan); font-size:.72rem; font-weight:700; letter-spacing:.06em; margin-top:5px; }
    .part-card.alert .part-status { color:var(--axon-red); }
    .part-detail { color:var(--axon-muted); font-size:.8rem; margin-top:5px; line-height:1.32; }
    [data-testid="stMetric"] { background:#ffffff; border:1px solid #d9e1e8; border-radius:4px; padding:14px 16px; }
    [data-testid="stMetricLabel"] { color:var(--axon-muted) !important; }
    [data-testid="stMetricValue"] { color:var(--axon-text) !important; }
    [data-testid="stFileUploader"] { border:1px dashed #b8c5d0; border-radius:4px; padding:8px; background:#ffffff; }
    .stButton > button { border-radius:4px; border:1px solid #0f766e; background:#0f766e; color:#ffffff; font-weight:700; transition:background-color .16s ease; }
    .stButton > button:hover { background:#0b625b; color:#ffffff; }
    [data-testid="stDataFrame"] { border:1px solid #d9e1e8; border-radius:4px; overflow:hidden; }
    [data-testid="stAlert"] { border-radius:4px; }
    header[data-testid="stHeader"] { background:#ffffff !important; border-bottom:1px solid #dfe6ec; }
    [data-testid="stToolbar"], [data-testid="stToolbar"] * { color:#526475 !important; }
    .stTextInput div[data-baseweb="input"] > div { background:#ffffff !important; border:1px solid #b8c5d0; border-radius:4px; }
    .stTextInput div[data-baseweb="input"] input { color:var(--axon-text) !important; background:transparent !important; }
    [data-testid="stFileUploaderDropzone"] { background:#ffffff !important; border:1px dashed #b8c5d0 !important; border-radius:4px !important; }
    [data-testid="stFileUploaderDropzone"] * { color:var(--axon-text) !important; }
    [data-testid="stFileUploaderDropzone"] button { background:#f2f5f7 !important; border:1px solid #b8c5d0 !important; border-radius:4px !important; color:var(--axon-text) !important; }
    [data-testid="stAlert"] * { color:#314354 !important; }
    [data-testid="stExpander"] { border:1px solid #d9e1e8; border-radius:4px; background:#ffffff; }
    [data-testid="stExpander"] summary { color:#314354; font-weight:650; }
    [data-testid="stSlider"] [data-baseweb="slider"] [role="slider"] { background:#0f766e !important; border-color:#0f766e !important; box-shadow:0 0 0 1px #0f766e !important; }
    [data-testid="stSlider"] [data-baseweb="slider"] div[role="progressbar"] { background:#0f766e !important; }
    .replay-input { margin:4px 0 18px; padding:13px 15px; border:1px solid #b7ddd6; border-radius:4px; background:#f7fcfb; }
    .replay-input-label { color:#0f766e; font-size:.71rem; font-weight:700; letter-spacing:.11em; }
    .replay-input-value { color:var(--axon-text); margin-top:4px; font-size:.94rem; font-weight:650; }
    .replay-input-detail { color:#526475; margin-top:3px; font-size:.82rem; }
    .integration-strip { display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:1px; margin:0 0 18px; border:1px solid #d9e1e8; border-radius:4px; overflow:hidden; background:#d9e1e8; }
    .integration-step { min-width:0; padding:13px 15px; background:#ffffff; }
    .integration-step-label { color:#0f766e; font-size:.68rem; font-weight:700; letter-spacing:.1em; }
    .integration-step-value { margin-top:5px; color:var(--axon-text); font-size:.87rem; font-weight:650; line-height:1.3; }
    @media (max-width: 760px) { .axon-hero { padding:22px; display:block; } .axon-chip { display:inline-block; margin-top:16px; } .part-grid, .integration-strip { grid-template-columns:1fr; } }
</style>
""", unsafe_allow_html=True)


def hero(status_text="EDGE MODEL ONLINE"):
    st.markdown(f"""<div class='axon-hero'>
      <div><div class='axon-kicker'>AXON · EDGE DIAGNOSTICS</div><h1>Bearing diagnostics</h1>
      <p>Local vibration analysis, model decisions, and maintenance evidence in one workspace.</p></div>
      <div class='axon-chip'>{status_text}</div>
    </div>""", unsafe_allow_html=True)


def section_title(title, note):
    st.markdown(f"<div class='section-label'>{title}</div><div class='section-note'>{note}</div>", unsafe_allow_html=True)


def fetch(path, *, timeout=TIMEOUT, **params):
    try:
        response = requests.get(f"{API}{path}", params=params, timeout=timeout)
        response.raise_for_status()
        return response.json(), None
    except requests.RequestException as exc:
        return None, str(exc)


def post_json(path, payload, *, timeout=TIMEOUT):
    try:
        response = requests.post(f"{API}{path}", json=payload, timeout=timeout)
        response.raise_for_status()
        return response.json(), None
    except requests.RequestException as exc:
        return None, str(exc)


COMPONENT_LABELS = {
    "bearing-drive-end": "Drive-end bearing",
    "bearing-non-drive-end": "Non-drive-end bearing",
}


def component_label(diagnostic):
    component = (diagnostic or {}).get("component_id", "bearing-drive-end")
    return COMPONENT_LABELS.get(component, component.replace("-", " ").replace("_", " ").title())


def diagnostic_source(diagnostic):
    """Describe the recorded path without assuming a hardware device is online."""
    if not diagnostic:
        return "Waiting for a LoRa diagnostic", "No diagnostic has arrived from the Raspberry Pi for this asset yet.", "waiting"
    evidence = (diagnostic or {}).get("evidence") or []
    source = next((item.get("source") for item in evidence if item.get("source")), None)
    if source == "lora_protocol_loopback":
        return "Paderborn runner · software radio replay", "Local model → binary LoRa codec → durable gateway → API. Physical radio and ADC are not used in this replay.", "waiting"
    if source == "lora_uart":
        if diagnostic.get("simulated"):
            return "Recorded data over LoRa", "A historical vibration recording was classified on the edge and sent over the radio.", "waiting"
        return "LoRa gateway result", "Received from the Raspberry Pi through the point-to-point LoRa gateway.", "live"
    if source == "manual_csv_replay":
        return "Manual CSV replay", "A saved vibration window was submitted from this dashboard.", "waiting"
    if source == "paderborn_dataset_replay":
        return "Paderborn dataset replay", "A historical, fact-sheet-labelled Paderborn vibration window was classified locally. It is not a live Raspberry Pi event.", "waiting"
    return "Recorded diagnostic", "This older result has no source tag; new edge events are tagged automatically.", "waiting"


def pretty_label(value):
    return str(value or "Not available").replace("_", " ").replace("-", " ").title()


def bearing_schematic(diagnostic):
    """Show the real reported component with a compact 3D-style bearing view."""
    diagnostic = diagnostic or {}
    location = diagnostic.get("fault_location")
    status = diagnostic.get("status")
    suspected = status in {"FAULT_SUSPECTED", "FAULT_CONFIRMED"}
    confirmed = status == "FAULT_CONFIRMED"
    label = component_label(diagnostic)
    fault_color, neutral = "#dc2626", "#64748b"

    # Generic Paderborn damage supports an assembly-level service action only.
    # If a later validated model supplies a race-level location, this visual
    # narrows the red highlight to that specific bearing element.
    outer = fault_color if suspected and location == "outer_race" else neutral
    inner = fault_color if suspected and location == "inner_race" else neutral
    ball = fault_color if suspected and location == "rolling_element" else neutral
    assembly_alert = suspected and location is None
    component_color = fault_color if suspected else "#94a3b8"
    housing_fill = "url(#alertHousing)" if assembly_alert else "url(#housing)"
    if assembly_alert:
        scope = "Bearing assembly service region"
    elif location == "outer_race":
        scope = "Outer race"
    elif location == "inner_race":
        scope = "Inner race"
    elif location == "rolling_element":
        scope = "Rolling elements"
    else:
        scope = "No current fault location"
    state = "Technician-confirmed" if confirmed else "Model-predicted / unverified"

    st.markdown(f"""<div class='bearing-stage'>
      <div class='bearing-stage-title'>LIVE COMPONENT MODEL · {label.upper()}</div>
      <svg viewBox='0 0 700 310' role='img' aria-label='3D-style bearing model for {label}'>
        <defs>
          <linearGradient id='motor' x1='0' x2='1'><stop stop-color='#f8fafc'/><stop offset='.5' stop-color='#dbe4ec'/><stop offset='1' stop-color='#b9c7d3'/></linearGradient>
          <linearGradient id='shaft' x1='0' x2='0' y1='0' y2='1'><stop stop-color='#91a4b7'/><stop offset='.5' stop-color='#f8fafc'/><stop offset='1' stop-color='#71849a'/></linearGradient>
          <linearGradient id='housing' x1='0' x2='1'><stop stop-color='#dce5ed'/><stop offset='.5' stop-color='#f8fafc'/><stop offset='1' stop-color='#afc0cd'/></linearGradient>
          <linearGradient id='alertHousing' x1='0' x2='1'><stop stop-color='#fee2e2'/><stop offset='.5' stop-color='#fffafa'/><stop offset='1' stop-color='#fecaca'/></linearGradient>
          <radialGradient id='steel'><stop stop-color='#ffffff'/><stop offset='.56' stop-color='#d4dde6'/><stop offset='1' stop-color='#718399'/></radialGradient>
          <filter id='shadow' x='-20%' y='-20%' width='140%' height='140%'><feDropShadow dx='0' dy='6' stdDeviation='5' flood-color='#64748b' flood-opacity='.23'/></filter>
        </defs>
        <rect x='26' y='101' width='135' height='108' rx='3' fill='url(#motor)' stroke='#64748b' stroke-width='3'/>
        <rect x='39' y='115' width='13' height='80' fill='#c0ceda'/><text x='93' y='159' text-anchor='middle' fill='#334155' font-size='16' font-weight='700'>Motor</text>
        <rect x='157' y='142' width='227' height='24' rx='4' fill='url(#shaft)' stroke='#64748b' stroke-width='2'/>
        <g filter='url(#shadow)'>
          <rect x='382' y='69' width='144' height='170' rx='3' fill='{housing_fill}' stroke='{component_color}' stroke-width='{4 if suspected else 3}'/>
          <ellipse cx='382' cy='154' rx='48' ry='85' fill='#d7e1e9' stroke='{component_color}' stroke-width='{4 if assembly_alert else 3}'/>
          <ellipse cx='526' cy='154' rx='48' ry='85' fill='url(#steel)' stroke='{component_color}' stroke-width='{4 if assembly_alert else 3}'/>
          <ellipse cx='526' cy='154' rx='34' ry='66' fill='#f8fafc' stroke='{outer}' stroke-width='13'/>
          <ellipse cx='526' cy='154' rx='14' ry='30' fill='#ffffff' stroke='{inner}' stroke-width='11'/>
          <ellipse cx='526' cy='103' rx='9' ry='13' fill='{ball}'/><ellipse cx='557' cy='126' rx='9' ry='13' fill='{ball}'/>
          <ellipse cx='557' cy='182' rx='9' ry='13' fill='{ball}'/><ellipse cx='526' cy='205' rx='9' ry='13' fill='{ball}'/>
          <ellipse cx='495' cy='182' rx='9' ry='13' fill='{ball}'/><ellipse cx='495' cy='126' rx='9' ry='13' fill='{ball}'/>
        </g>
        <text x='454' y='27' text-anchor='middle' fill='#64748b' font-size='12' font-weight='700'>SERVICE REGION</text>
        <text x='454' y='48' text-anchor='middle' fill='{component_color}' font-size='14' font-weight='700'>{scope}</text>
        <text x='454' y='272' text-anchor='middle' fill='#526475' font-size='13'>{label} · sensor target</text>
        <text x='26' y='282' fill='#526475' font-size='13'>{state}. Component ID: {diagnostic.get('component_id', 'bearing-drive-end')}.</text>
      </svg>
      <div class='bearing-stage-note'>The 3D view follows the newest saved diagnostic. It highlights only the component or bearing feature supplied by the validated model.</div>
    </div>""", unsafe_allow_html=True)
    if suspected and location is None:
        st.warning(f"The model has flagged the {label.lower()} assembly. It does not have validated evidence to colour an inner race, outer race, or rolling element red.")
    else:
        st.caption("Red is a model hypothesis, not physical confirmation. Grey means no evidence supports that subcomponent.")


def component_status_table(diagnostic):
    """Translate one diagnostic into an explicit per-part operator view."""
    diagnostic = diagnostic or {}
    status, location = diagnostic.get("status"), diagnostic.get("fault_location")
    suspected = status in {"FAULT_SUSPECTED", "FAULT_CONFIRMED"}
    healthy = status == "HEALTHY_WITHIN_VALIDATED_SCOPE"
    label = component_label(diagnostic)
    exact_location = {
        "inner_race": "Inner race",
        "outer_race": "Outer race",
        "rolling_element": "Rolling elements",
    }.get(location)
    rows = [
        (
            label,
            "FAULT SUSPECTED" if suspected else "HEALTHY WITHIN SCOPE" if healthy else "AWAITING DIAGNOSTIC",
            f"Inspect or replace this {label.lower()} assembly" if suspected else "No damage detected by the deployed model" if healthy else "Await the next edge vibration window for this component",
            "alert" if suspected else "neutral",
        ),
        (
            "Inner race",
            "SUSPECTED" if exact_location == "Inner race" else "NOT LOCALIZED",
            "Model identified the inner race" if exact_location == "Inner race" else "No validated inner-race conclusion from this diagnostic",
            "alert" if exact_location == "Inner race" else "neutral",
        ),
        (
            "Outer race",
            "SUSPECTED" if exact_location == "Outer race" else "NOT LOCALIZED",
            "Model identified the outer race" if exact_location == "Outer race" else "No validated outer-race conclusion from this diagnostic",
            "alert" if exact_location == "Outer race" else "neutral",
        ),
        (
            "Rolling elements",
            "SUSPECTED" if exact_location == "Rolling elements" else "NOT LOCALIZED",
            "Model identified the rolling elements" if exact_location == "Rolling elements" else "No validated rolling-element conclusion from this diagnostic",
            "alert" if exact_location == "Rolling elements" else "neutral",
        ),
    ]
    cards = "".join(
        f"<div class='part-card {tone}'><div class='part-title'>{part}</div><div class='part-status'>{part_status}</div><div class='part-detail'>{meaning}</div></div>"
        for part, part_status, meaning, tone in rows
    )
    section_title("Part-by-part status", "The live model identifies the sensor-targeted bearing. Red narrows to a race or rolling element only when the model has validated that conclusion.")
    st.markdown(f"<div class='part-grid'>{cards}</div>", unsafe_allow_html=True)


def paderborn_dataset_replay_panel():
    """Interactive, clearly labelled replay of the locally provided bearing data."""
    status, status_error = fetch("/diagnostics/model-status", timeout=DATASET_REPLAY_TIMEOUT)
    hero("DATASET REPLAY READY" if not status_error and status and status.get("available") else "MODEL CHECK REQUIRED")
    section_title(
        "Paderborn bearing data explorer",
        "Choose a real, official Paderborn recording and one 4,096-sample vibration window. The resulting event is marked as a historical replay, never live Raspberry Pi telemetry.",
    )
    catalog, catalog_error = fetch("/diagnostics/paderborn/catalog")
    if catalog_error:
        st.error(f"Dataset catalogue is unavailable: {catalog_error}")
        return
    if not catalog or not catalog.get("available"):
        st.warning((catalog or {}).get("reason", "No locally prepared Paderborn dataset is available."))
        return
    if status_error or not status or not status.get("available"):
        st.warning("The Paderborn recordings are available, but the deployed bearing model is unavailable. Restore a validated bundle before replaying a window.")
        return

    st.markdown(
        f"<div class='integration-strip'>"
        f"<div class='integration-step'><div class='integration-step-label'>DATA SOURCE</div><div class='integration-step-value'>Local Paderborn MATLAB recordings</div></div>"
        f"<div class='integration-step'><div class='integration-step-label'>MODEL API</div><div class='integration-step-value'>FastAPI · /diagnostics/paderborn/replay</div></div>"
        f"<div class='integration-step'><div class='integration-step-label'>INFERENCE</div><div class='integration-step-value'>{status.get('kind', 'bearing model').replace('_', ' ')} · {len(status.get('input_contract', {}).get('feature_names', []))} features</div></div>"
        f"<div class='integration-step'><div class='integration-step-label'>DEPLOYED BUNDLE</div><div class='integration-step-value'>{status.get('model_version', 'version unavailable')}</div></div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    records = catalog.get("records", [])
    if not records:
        st.info("The local Paderborn manifest contains no explicitly labelled recordings to replay.")
        return
    class_counts = catalog.get("class_counts", {})
    metric_columns = st.columns(4)
    metric_columns[0].metric("Prepared windows", f"{int(catalog.get('prepared_windows', 0)):,}")
    metric_columns[1].metric("Labelled recordings", f"{len(records):,}")
    metric_columns[2].metric("Healthy windows", f"{int(class_counts.get('healthy', 0)):,}")
    metric_columns[3].metric("Documented-damage windows", f"{int(sum(count for label, count in class_counts.items() if label != 'healthy')):,}")

    source = catalog.get("source", {})
    st.caption(f"Local source: Paderborn Bearing DataCenter · licence: {source.get('license_id', 'not recorded')}")
    st.caption("The fact-sheet label describes the bearing’s documented experimental condition. The model decision below is produced independently from the selected vibration window.")

    classes = sorted({record.get("fault_class") for record in records if record.get("fault_class")})
    filters, selection = st.columns((.8, 1.2), gap="large")
    with filters:
        selected_class = st.selectbox("Documented condition", classes, format_func=pretty_label, key="paderborn_fault_class")
        filtered_records = [record for record in records if record.get("fault_class") == selected_class]
        bearing_options = sorted({record.get("bearing_id") for record in filtered_records if record.get("bearing_id")})
        # Keys include their parent selections.  A bearing selected under the
        # damaged class (for example KA04) must never remain visible after the
        # operator changes the condition to Healthy.
        selected_bearing = st.selectbox(
            "Physical bearing",
            bearing_options,
            key=f"paderborn_bearing__{selected_class}",
        )
    with selection:
        bearing_records = [record for record in filtered_records if record.get("bearing_id") == selected_bearing]
        recording_labels = {
            record["recording_id"]: f"{record['recording_id']} · {record.get('operating_condition', 'condition unavailable')}"
            for record in bearing_records
        }
        selected_recording_id = st.selectbox(
            "Recording / operating condition",
            list(recording_labels),
            format_func=recording_labels.get,
            key=f"paderborn_recording__{selected_class}__{selected_bearing}",
        )
        selected_record = next(record for record in bearing_records if record["recording_id"] == selected_recording_id)
        window_index = st.slider(
            "Vibration segment",
            min_value=0,
            max_value=max(0, int(selected_record.get("windows", 1)) - 1),
            value=0,
            help="Each segment is one 4,096-sample vibration input to the model. Adjacent segments overlap by 2,048 samples.",
            key=f"paderborn_window_index__{selected_recording_id}",
        )

    generation = catalog.get("window_generation") or {}
    window_samples = int(generation.get("window_samples") or 4_096)
    window_hop = window_samples - int(generation.get("window_overlap") or 0)
    replay_start = window_index * window_hop
    replay_end = replay_start + window_samples - 1
    st.markdown(
        f"<div class='replay-input'><div class='replay-input-label'>ACTIVE MODEL INPUT</div>"
        f"<div class='replay-input-value'>{selected_recording_id} · segment {window_index + 1} of {int(selected_record.get('windows') or 1)}</div>"
        f"<div class='replay-input-detail'>Physical bearing {selected_record.get('bearing_id')} · source samples {replay_start:,}–{replay_end:,}</div></div>",
        unsafe_allow_html=True,
    )
    documented_detail = selected_record.get("damage_type") or "No damage description is documented for this selected healthy bearing."
    st.markdown(
        f"<div class='diagnostic-card'><div class='diagnostic-eyebrow'>DOCUMENTED EXPERIMENT LABEL</div>"
        f"<div class='diagnostic-value'>{pretty_label(selected_record.get('fault_class'))}</div>"
        f"<div class='diagnostic-detail'>Bearing {selected_record.get('bearing_id')} · {selected_record.get('operating_condition')} · {documented_detail}<br/>"
        f"Label evidence: {selected_record.get('label_source') or 'not recorded'}</div></div>",
        unsafe_allow_html=True,
    )
    recording_number = selected_recording_id.rsplit("_", 1)[-1]
    recording_number = recording_number if recording_number.isdigit() else "not encoded"
    sampling_frequency = float(selected_record.get("sampling_frequency_hz") or 0)
    with st.expander("What this recording selection means", expanded=True):
        st.markdown(
            f"**`{selected_recording_id}`** selects one real local Paderborn MATLAB recording; it is not a model label. "
            f"It is recording **{recording_number}** for physical bearing **{selected_record.get('bearing_id')}** "
            f"under the official operating-condition identifier **{selected_record.get('operating_condition')}**."
        )
        details = pd.DataFrame(
            [
                ("Documented experimental condition", pretty_label(selected_record.get("fault_class"))),
                ("Documented damage description", documented_detail),
                ("Fact-sheet evidence", selected_record.get("label_source") or "not recorded"),
                ("Selected replay window", f"{window_index} of {int(selected_record.get('windows', 1)) - 1}"),
                ("Samples sent to the model", f"{replay_start:,}–{replay_end:,} ({window_samples:,} samples at {sampling_frequency / 1_000:g} kHz)"),
            ],
            columns=["Field", "Value"],
        )
        st.dataframe(details, use_container_width=True, hide_index=True)
        st.caption("Changing the recording or window always changes the underlying vibration data. The top model class can still remain the same when multiple windows belong to the same documented bearing condition.")
    st.caption("The supplied dataset supports a healthy-vs-documented-bearing-damage classifier. It does not validate a race-level localisation for every recording, so this screen will not manufacture one.")

    if "paderborn_asset_id" not in st.session_state:
        st.session_state["paderborn_asset_id"] = os.getenv("AXON_ASSET_ID", "demo-motor-001")
    asset_id = st.text_input("Demo asset ID", key="paderborn_asset_id", help="This identifies the replay event in the bearing-diagnosis workspace.")
    st.caption("Preview updates automatically when you change the bearing, operating condition, recording, or window. It is read-only until you explicitly save it below.")
    with st.spinner("Loading the selected recorded vibration window and running the deployed bearing model..."):
        result, error = post_json(
            "/diagnostics/paderborn/replay",
            {
                "asset_id": asset_id,
                "recording_id": selected_recording_id,
                "window_index": window_index,
                "component_id": "bearing-drive-end",
                "save_result": False,
            },
            timeout=DATASET_REPLAY_TIMEOUT,
        )
    if error:
        st.error(f"Dataset preview could not run: {error}")
        return
    diagnostic = result.get("diagnostic", {})
    replay_dataset = result.get("dataset", {})
    waveform = result.get("waveform", {})
    features = result.get("features", {})
    section_title("Live dataset preview", "The documented fact-sheet condition and the model result remain separate so you can evaluate the classifier honestly during the demo.")
    expected = replay_dataset.get("fault_class")
    predicted = diagnostic.get("fault_class")
    agreement = expected == predicted
    dataset_bearing = replay_dataset.get("bearing_id") or selected_record.get("bearing_id") or "Unavailable"
    replay_window = int(replay_dataset.get("window_index", window_index))
    replay_total = int(replay_dataset.get("windows") or selected_record.get("windows") or 1)
    source_start = int(replay_dataset.get("window_start_sample", replay_start))
    source_end = int(replay_dataset.get("window_end_sample", replay_end))
    result_columns = st.columns(4)
    result_columns[0].metric("Physical bearing", dataset_bearing)
    result_columns[1].metric("Recording run", recording_number)
    result_columns[2].metric("Vibration window", f"{replay_window + 1} / {replay_total}")
    display_confidence = min(float(diagnostic.get("confidence", 0)), 0.8678) if diagnostic.get("confidence") is not None else None
    result_columns[3].metric("Operational confidence", f"{display_confidence:.1%}" if display_confidence is not None else "Unavailable")
    st.caption("Operational confidence is conservatively capped at 86.78% for uncalibrated Random Forest votes. The raw score remains in the technical record.")
    st.info(f"""**Dataset label:** {pretty_label(expected)}

**Documented damage:** {replay_dataset.get('damage_type') or 'No documented damage'}

**Model decision:** {pretty_label(predicted)}

**Model input:** `{replay_dataset.get('recording_id', selected_recording_id)}` · samples {source_start:,}–{source_end:,} at {sampling_frequency / 1_000:g} kHz""")
    if features:
        signal_columns = st.columns(3)
        signal_columns[0].metric("Signal RMS", f"{features.get('rms', 0):.4g}")
        signal_columns[1].metric("Peak amplitude", f"{features.get('peak_amplitude', 0):.4g}")
        signal_columns[2].metric("Dominant frequency", f"{features.get('dominant_frequency_hz', 0):.4g} Hz")
        st.caption("These values are recalculated from the selected 4,096-sample dataset segment, so they should change when you select a different recording or segment.")
    if agreement:
        st.success("The classifier’s top label matches this recording’s documented experimental condition.")
    else:
        st.warning("The classifier’s top label does not match this recording’s documented experimental condition. This is a useful honest failure case, not a live fault alert.")

    model_view, signal_view = st.columns((1, 1), gap="large")
    with model_view:
        st.markdown("#### Model-based component view")
        bearing_schematic(diagnostic)
    with signal_view:
        st.markdown("#### Recorded vibration waveform")
        st.caption("Downsampled for display; inference used all 4,096 samples from the selected recorded window.")
        if waveform.get("sample_indices") and waveform.get("amplitudes"):
            waveform_frame = pd.DataFrame({"sample": waveform["sample_indices"], "amplitude": waveform["amplitudes"]}).set_index("sample")
            st.line_chart(waveform_frame, height=340)
    if features:
        with st.expander("Feature values used by the Random Forest", expanded=False):
            feature_frame = pd.DataFrame({"feature": list(features), "value": list(features.values())})
            st.dataframe(feature_frame, use_container_width=True, hide_index=True)
        st.caption("These engineered time- and frequency-domain features are calculated from the selected real vibration window before Random Forest classification.")
    if st.button("Save this preview to Bearing diagnosis history", use_container_width=True):
        with st.spinner("Saving this labelled historical replay to the diagnostic history..."):
            saved_result, save_error = post_json(
                "/diagnostics/paderborn/replay",
                {
                    "asset_id": asset_id,
                    "recording_id": selected_recording_id,
                    "window_index": window_index,
                    "component_id": "bearing-drive-end",
                    "save_result": True,
                },
                timeout=DATASET_REPLAY_TIMEOUT,
            )
        if save_error:
            st.error(f"Could not save the historical replay: {save_error}")
        elif saved_result.get("saved"):
            st.session_state["bearing_asset_id"] = asset_id
            st.success("Saved as a historical Paderborn replay. Open Bearing diagnosis to inspect this same event in the component-localization workspace.")
    with st.expander("Replay provenance", expanded=False):
        st.json({"dataset": replay_dataset, "diagnostic": diagnostic})


def bearing_diagnosis_panel():
    status, status_error = fetch("/diagnostics/model-status", timeout=DATASET_REPLAY_TIMEOUT)
    hero("API MODEL READY" if not status_error and status and status.get("available") else "MODEL CHECK REQUIRED")
    section_title("Bearing diagnostic workspace", "The Raspberry Pi edge path is primary: local sensor window → Pi inference → LoRa summary → gateway diagnostic. This view refreshes automatically.")
    input_column, contract_column = st.columns((1.25, .75), gap="large")
    with input_column:
        if "bearing_asset_id" not in st.session_state:
            st.session_state["bearing_asset_id"] = os.getenv("AXON_ASSET_ID", "demo-motor-001")
        known_assets, _ = fetch("/diagnostics/assets")
        if known_assets:
            selected = st.selectbox("Received assets", ["Enter asset ID below"] + known_assets)
            if selected != "Enter asset ID below":
                st.session_state["bearing_asset_id"] = selected
        asset_id = st.text_input("Asset ID", key="bearing_asset_id", help="Must match the asset_id sent by the Raspberry Pi edge publisher or the Paderborn dataset replay.")
    with contract_column:
        if status and status.get("available"):
            contract = status["input_contract"]
            st.markdown("<div class='diagnostic-card'><div class='diagnostic-eyebrow'>RASPBERRY PI INPUT CONTRACT</div>"
                        f"<div class='diagnostic-value'>{contract['sampling_frequency_hz']:,.0f} Hz vibration</div>"
                        f"<div class='diagnostic-detail'>{contract['window_samples']:,} samples per edge window · {len(contract['feature_names'])} engineered features</div></div>", unsafe_allow_html=True)
        else:
            st.markdown("<div class='diagnostic-card'><div class='diagnostic-eyebrow'>INPUT CONTRACT</div><div class='diagnostic-value'>Unavailable</div><div class='diagnostic-detail'>Deploy a validated bundle before uploading data.</div></div>", unsafe_allow_html=True)
    if status_error:
        st.error(f"Diagnostic API unavailable: {status_error}"); return
    if not status or not status.get("available"):
        st.warning(f"Bearing model unavailable: {(status or {}).get('reason', 'no validated bundle is deployed')}")
        st.caption("No bearing fault label, location, cost, or repair instruction is inferred while the model is unavailable.")
    if status and status.get("available"):
        st.success(f"API model: {status['model_version']} · {status['kind'].replace('_', ' ')}")
    with st.expander("Manual CSV replay (optional)", expanded=False):
        st.caption("This is a troubleshooting and demo fallback. The normal Raspberry Pi route sends a compact diagnostic summary over LoRa, so no file is needed during live operation.")
        upload = st.file_uploader("Recorded vibration window CSV", type=["csv"], help="Use only a recorded window with the active model's sample count and sampling frequency.")
        if upload is not None:
            window = pd.read_csv(upload).select_dtypes(include="number")
            if window.empty:
                st.error("The upload has no numeric vibration column.")
            elif st.button("Replay recorded window", type="primary", use_container_width=True, disabled=not (status or {}).get("available")):
                contract = status["input_contract"]
                with st.spinner("Running local diagnostic replay..."):
                    result, error = post_json("/diagnostics/infer", {"asset_id": asset_id, "component_id": "bearing-drive-end", "signal": window.iloc[:, 0].tolist(), "sampling_frequency_hz": contract["sampling_frequency_hz"]})
                if error:
                    st.error(error)
                else:
                    st.success(f"Replay record {result['event_id']} created. The live result below will refresh automatically.")

    @st.fragment(run_every=f"{REFRESH}s")
    def live_diagnostic_panel(selected_asset):
        history, history_error = fetch("/diagnostics/history", asset_id=selected_asset, limit=20)
        ready, ready_error = fetch("/ready")
        if history_error:
            st.info("No diagnostic history is currently available.")
            return
        latest = history[0] if history else None
        source_title, source_detail, source_tone = diagnostic_source(latest)
        transport_state = "LoRa gateway API ready" if not ready_error and ready and ready.get("transport") == "lora_gateway_http" else "LoRa gateway API unavailable"
        event_time = latest.get("timestamp", "Awaiting event") if latest else "Awaiting edge event"
        st.markdown(f"""<div class='stream-card'>
          <div class='stream-indicator {source_tone}'></div>
          <div class='stream-copy'><div class='stream-title'>{source_title}</div><div class='stream-detail'>{source_detail}</div></div>
          <div class='stream-meta'>{transport_state}<br/>{event_time}</div>
        </div>""", unsafe_allow_html=True)
        section_title(f"Latest diagnostic · auto-refreshes every {REFRESH}s", "The bearing model and decision panel are rebuilt from the newest saved edge result for this asset.")
        left, right = st.columns((1.18, .82), gap="large")
        with left:
            st.markdown("### Component localization")
            bearing_schematic(latest)
            component_status_table(latest)
        with right:
            st.markdown("### Diagnostic decision")
            if latest:
                suspected = latest.get("status") in {"FAULT_SUSPECTED", "FAULT_CONFIRMED"}
                affected = component_label(latest)
                if suspected:
                    location = {"inner_race": "inner race", "outer_race": "outer race", "rolling_element": "rolling elements"}.get(latest.get("fault_location"))
                    detail = f"Validated model evidence points to the {location}." if location else f"Inspect or replace the {affected.lower()} assembly. The model does not claim an unvalidated race-level location."
                    st.markdown(f"<div class='diagnostic-card alert'><div class='diagnostic-eyebrow'>AFFECTED COMPONENT · {latest.get('component_id')}</div><div class='diagnostic-value'>{affected}</div><div class='diagnostic-detail'>{detail}</div></div>", unsafe_allow_html=True)
                elif latest.get("status") == "HEALTHY_WITHIN_VALIDATED_SCOPE":
                    st.markdown(f"<div class='diagnostic-card good'><div class='diagnostic-eyebrow'>MODEL DECISION · {latest.get('component_id')}</div><div class='diagnostic-value'>{affected}: healthy within scope</div><div class='diagnostic-detail'>No bearing damage was detected by the deployed model for this edge window.</div></div>", unsafe_allow_html=True)
                else:
                    st.markdown("<div class='diagnostic-card'><div class='diagnostic-eyebrow'>MODEL DECISION</div><div class='diagnostic-value'>No actionable localization</div><div class='diagnostic-detail'>The result does not support a component-level service action.</div></div>", unsafe_allow_html=True)
                metric_left, metric_right = st.columns(2)
                display_confidence = min(float(latest.get("confidence", 0)), 0.8678) if latest.get("confidence") is not None else None
                metric_left.metric("Operational confidence", f"{display_confidence:.1%}" if display_confidence is not None else "Unavailable")
                st.caption("Capped at 86.78% for this uncalibrated model; raw tree-vote probability is available in the technical record.")
                short_status = {"FAULT_SUSPECTED": "Fault", "FAULT_CONFIRMED": "Verified", "HEALTHY_WITHIN_VALIDATED_SCOPE": "Healthy", "DIAGNOSIS_UNCERTAIN": "Uncertain", "SENSOR_DATA_INVALID": "Invalid sensor data", "MODEL_UNAVAILABLE": "Model unavailable"}.get(latest.get("status"), pretty_label(latest.get("status")))
                metric_right.metric("Status", short_status)
                if latest.get("status") in {"MODEL_UNAVAILABLE", "SENSOR_DATA_INVALID", "DIAGNOSIS_UNCERTAIN"}:
                    st.warning(f"{short_status}: no component replacement decision is supported by this event.")
                st.markdown(f"**Detected bearing**  \n`{affected}`")
                st.markdown(f"**Race-level location**  \n`{latest.get('fault_location') or 'Not localized'}`")
                with st.expander("Technical diagnostic record"):
                    st.json({key: latest.get(key) for key in ("timestamp", "asset_id", "component_id", "status", "fault_class", "fault_location", "confidence", "confidence_calibrated", "model_version", "evidence", "dataset_context", "missing_information")})
            else:
                st.info("Waiting for a Raspberry Pi edge diagnostic for this asset. Manual CSV replay is optional and is available above.")
        with st.expander("Repair intelligence and cost", expanded=False):
            documents, document_error = fetch("/repair-documents/search", query="bearing")
            if document_error or documents.get("status") == "unavailable":
                st.warning("No applicable authorized repair documentation is configured.")
            else:
                st.dataframe(pd.DataFrame(documents["documents"]), use_container_width=True, hide_index=True)
            st.caption("Prices, compatible parts, and repair procedures remain unavailable until an authorized catalogue and equipment-specific documentation are configured.")
        if history:
            section_title("Diagnostic history", "Each result remains auditable by asset, timestamp, component, model status, and confidence.")
            history_frame = pd.DataFrame(history)
            visible_columns = [column for column in ("timestamp", "status", "component_id", "fault_class", "confidence", "model_version") if column in history_frame]
            st.dataframe(history_frame[visible_columns], use_container_width=True, hide_index=True)

    live_diagnostic_panel(asset_id)


st.sidebar.markdown("### AXON")
st.sidebar.caption("Local edge diagnostics")
view = st.sidebar.radio("Workspace", ("Paderborn data replay", "Bearing diagnosis"), key="workspace")
if view == "Bearing diagnosis":
    st.sidebar.success("Live path: Pi → LoRa → gateway")
    st.sidebar.caption("Use Paderborn data replay to interact with the provided historical recordings.")
    bearing_diagnosis_panel()
else:
    st.sidebar.success("Source: Paderborn Bearing DataCenter recordings")
    st.sidebar.caption("Historical laboratory data · explicit fact-sheet labels")
    paderborn_dataset_replay_panel()
