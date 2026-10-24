/* AXON Verify webapp — no build step, same origin as the API.
   Every view is a thin projection of a documented /v1 endpoint. */
(() => {
  "use strict";

  const state = {
    token: localStorage.getItem("axon.token"),
    me: null,
    route: (location.hash || "#/dashboard").slice(1),
    loading: false,
    cache: {},
  };

  /* ------------------------------------------------------------- helpers */

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const pct = (value, digits = 1) => {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
    return `${(Number(value) * 100).toFixed(digits)}%`;
  };

  const fmtDate = (value) => {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return esc(value);
    return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  };

  const short = (value, size = 12) => value ? String(value).slice(0, size) + "…" : "—";

  const VERDICT = {
    VERIFIED: ["ok", "Repair verified"],
    NOT_VERIFIED: ["bad", "Repair not verified"],
    INCONCLUSIVE: ["warn", "Inconclusive"],
    CANNOT_VERIFY: ["warn", "Cannot verify"],
    NOT_APPLICABLE: ["neutral", "No repair required"],
  };

  const STATUS = {
    HEALTHY_WITHIN_VALIDATED_SCOPE: ["ok", "Healthy in scope"],
    FAULT_SUSPECTED: ["bad", "Fault suspected"],
    DIAGNOSIS_UNCERTAIN: ["warn", "Uncertain"],
    INSUFFICIENT_EVIDENCE: ["warn", "Insufficient evidence"],
    OUT_OF_DISTRIBUTION: ["warn", "Out of distribution"],
    UNSUPPORTED_FAULT: ["warn", "Unsupported fault"],
    SENSOR_DATA_INVALID: ["warn", "Sensor data invalid"],
    MODEL_UNAVAILABLE: ["warn", "Model unavailable"],
  };

  const badge = (map, key, fallback) => {
    const [tone, label] = map[key] || ["neutral", fallback || key];
    return `<span class="badge ${tone} dot">${esc(label)}</span>`;
  };
  const verdictBadge = (v) => v ? badge(VERDICT, v, v) : `<span class="badge neutral">not verified yet</span>`;
  const statusBadge = (s) => s ? badge(STATUS, s, s) : "—";

  function toast(message, tone = "") {
    const node = document.createElement("div");
    node.className = `toast ${tone}`;
    node.textContent = message;
    document.body.appendChild(node);
    setTimeout(() => node.remove(), 4200);
  }

  async function api(path, { method = "GET", body } = {}) {
    const headers = { "Content-Type": "application/json" };
    if (state.token) headers.Authorization = `Bearer ${state.token}`;
    const response = await fetch(`/v1${path}`, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = null;
    try { data = await response.json(); } catch (_) { data = null; }
    if (response.status === 401 && state.token) { signOut(false); throw new Error("session expired"); }
    if (!response.ok) {
      const detail = data && data.detail;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail || response.statusText));
    }
    return data;
  }

  function signOut(notify = true) {
    if (state.token) {
      api("/auth/logout", { method: "POST" }).catch(() => {});
    }
    state.token = null; state.me = null;
    localStorage.removeItem("axon.token");
    if (notify) render();
    else render();
  }

  /* ---------------------------------------------------------------- auth */

  async function loadMe() {
    if (!state.token) { state.me = null; return; }
    try { state.me = await api("/me"); }
    catch (_) { state.token = null; state.me = null; localStorage.removeItem("axon.token"); }
  }

  function authView(mode = "login") {
    return `
      <div class="auth-wrap">
        <div class="auth-card">
          <div class="logo">AXON<span> VERIFY</span></div>
          <div class="tagline">Verified repair evidence</div>
          <h2>${mode === "login" ? "Sign in" : "Create your organisation"}</h2>
          <p class="inline-note" style="margin:6px 0 4px">
            ${mode === "login"
              ? "Access is scoped to one organisation at a time."
              : "The first account becomes the owner and can invite the rest of the team."}
          </p>
          <form id="auth-form">
            ${mode === "register" ? `
              <label>Organisation name</label>
              <input name="org_name" required minlength="2" placeholder="Acme Industrial Services"/>
              <label>Your name</label>
              <input name="name" required placeholder="Dana Owner"/>` : ""}
            <label>Email</label>
            <input name="email" type="email" required placeholder="you@company.com"/>
            <label>Password</label>
            <input name="password" type="password" required minlength="8"
                   placeholder="${mode === "register" ? "at least 8 characters" : "your password"}"/>
            <div class="row-actions">
              <button class="primary" type="submit">${mode === "login" ? "Sign in" : "Create organisation"}</button>
            </div>
          </form>
          <div class="auth-switch">
            ${mode === "login"
              ? `No account yet? <a href="#" data-auth="register">Create an organisation</a>`
              : `Already registered? <a href="#" data-auth="login">Sign in</a>`}
          </div>
          <div class="notice info" style="margin-top:18px">
            <b>Deployment note</b>
            Sessions are signed server-side and revocable. In production this screen sits
            behind your identity provider; the roles and organisation scoping stay the same.
          </div>
        </div>
      </div>`;
  }

  function wireAuth(root, mode) {
    root.querySelectorAll("[data-auth]").forEach((node) =>
      node.addEventListener("click", (event) => {
        event.preventDefault();
        renderAuth(node.dataset.auth);
      }));
    const form = root.querySelector("#auth-form");
    if (!form) return;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const values = Object.fromEntries(new FormData(form).entries());
      const button = form.querySelector("button");
      button.disabled = true; button.innerHTML = `<span class="spinner"></span> working`;
      try {
        const path = mode === "login" ? "/auth/login" : "/auth/register";
        const session = await api(path, { method: "POST", body: values });
        state.token = session.access_token;
        localStorage.setItem("axon.token", state.token);
        await loadMe();
        location.hash = "#/dashboard";
        render();
        toast(mode === "login" ? "Signed in" : "Organisation created", "ok");
      } catch (error) {
        toast(error.message, "bad");
        button.disabled = false;
        button.textContent = mode === "login" ? "Sign in" : "Create organisation";
      }
    });
  }

  function renderAuth(mode) {
    const root = document.getElementById("root");
    root.innerHTML = authView(mode);
    wireAuth(root, mode);
  }

  /* -------------------------------------------------------------- shell */

  const NAV = [
    ["Overview", [
      ["#/dashboard", "Dashboard", "▦"],
      ["#/work-orders", "Work orders", "⚙"],
      ["#/assets", "Assets", "⬢"],
    ]],
    ["Evidence", [
      ["#/import", "Capture & import", "⇪"],
      ["#/certificates", "Certificates", "❑"],
      ["#/devices", "Devices & transports", "⇄"],
    ]],
    ["Organisation", [
      ["#/team", "Team & roles", "♟"],
      ["#/api-keys", "API keys", "🔑"],
      ["#/audit", "Audit log", "≡"],
    ]],
  ];

  function shell(content) {
    const me = state.me || {};
    const org = me.organisation || {};
    const pending = state.cache.stats ? state.cache.stats.pending_verification : null;
    const counts = { "#/work-orders": pending };
    const nav = NAV.map(([group, items]) => `
      <div class="nav-group">${esc(group)}</div>
      ${items.map(([href, label, icon]) => `
        <a class="nav-item ${state.route.startsWith(href) ? "active" : ""}" href="${href}">
          <span aria-hidden="true">${icon}</span>${esc(label)}
          ${counts[href] ? `<span class="count">${counts[href]}</span>` : ""}
        </a>`).join("")}`).join("");
    return `
      <div class="shell">
        <aside class="sidebar noprint">
          <div class="logo">AXON<span> VERIFY</span></div>
          <div class="tagline">Verified repair evidence</div>
          ${nav}
          <div class="sidebar-footer">
            <div class="whoami"><b>${esc(me.user?.name || "")}</b>${esc(org.name || "")} · ${esc(me.role || "")}</div>
            <button class="nav-item" id="sign-out"><span aria-hidden="true">⏻</span> Sign out</button>
          </div>
        </aside>
        <main class="main">${content}</main>
      </div>`;
  }

  /* ----------------------------------------------------------- dashboard */

  async function viewDashboard() {
    const [stats, model, calibration, ingest] = await Promise.all([
      api("/stats"), api("/model-status"), api("/calibration"), api("/ingest/status"),
    ]);
    state.cache.stats = stats;
    const recent = state.cache.workOrders ?? (await api("/work-orders")).work_orders;

    const calibratedNotice = calibration.confidence_calibrated
      ? `<div class="notice ok"><b>Calibrated confidence</b>
           Artifact ${esc(calibration.calibration_version)} fitted on
           ${esc(calibration.validation_set?.windows ?? "—")} held-out windows.
           ECE ${esc(calibration.metrics?.ece_before)} → ${esc(calibration.metrics?.ece_after)}.</div>`
      : `<div class="notice"><b>Uncalibrated confidence</b>
           ${esc(calibration.reason)}. Confidence values shown across this product are raw
           model scores, and the conservative verification policy is applied.</div>`;

    const modelNotice = model.available
      ? `<div class="notice info"><b>Model ${esc(model.model_version)}</b>
           ${esc(model.input_contract?.window_samples)} samples at
           ${esc(model.input_contract?.sampling_frequency_hz)} Hz ·
           ${esc((model.classes || []).join(" vs "))}</div>`
      : `<div class="notice bad"><b>Model unavailable</b>${esc(model.reason)}</div>`;

    return `
      <div class="page-head">
        <div>
          <h1>Dashboard</h1>
          <p>Every verdict on this screen was produced by the policy engine from evidence
             that is stored, hashed and signed. Nothing here is illustrative.</p>
        </div>
        <a class="btn" href="#/work-orders">Open work orders</a>
      </div>

      <div class="grid cols-4" style="margin-bottom:16px">
        ${stat("Open work orders", stats.open_work_orders, `${stats.work_orders} total`)}
        ${stat("Repairs verified", stats.verified_repairs, "policy-verdict VERIFIED")}
        ${stat("Not verified", stats.not_verified, "fault still indicated")}
        ${stat("Inconclusive", stats.inconclusive + stats.pending_verification, "cannot be certified")}
      </div>

      <div class="grid cols-2">
        <div>
          <div class="card">
            <h2>Model & calibration</h2>
            ${modelNotice}${calibratedNotice}
            <dl class="kv">
              <dt>Calibration artifact</dt><dd>${esc(calibration.artifact_path)}</dd>
              <dt>Policy version</dt><dd>${esc(stats.policy_version)}</dd>
              <dt>Confidence calibrated</dt><dd>${calibration.confidence_calibrated ? "yes" : "no"}</dd>
            </dl>
          </div>
          <div class="card">
            <h2>Evidence transports</h2>
            <p class="hint">One evidence record, three transports. All of them land in the same store.</p>
            <table><tbody>
              <tr><td><b>Upload / CSV / REST</b><div class="inline-note">POST /v1/readings</div></td>
                  <td class="num">${esc(ingest.upload?.available ? "available" : "—")}</td></tr>
              <tr><td><b>MQTT</b><div class="inline-note">${esc(ingest.mqtt?.topic_prefix)}/&#123;org&#125;/&#123;site&#125;/&#123;device&#125;/diagnostic</div></td>
                  <td class="num">${ingest.mqtt?.configured
                    ? `last seen ${esc(ingest.mqtt.last_device_seen?.device_id || ingest.mqtt.last_device_seen?.device_key || "never")}`
                    : "not configured"}</td></tr>
              <tr><td><b>LoRa gateway</b><div class="inline-note">gateway HTTP API → /v1/ingest/diagnostic</div></td>
                  <td class="num">available</td></tr>
            </tbody></table>
          </div>
        </div>
        <div class="card">
          <h2>Work requiring attention</h2>
          ${recent.length === 0 ? `<div class="empty">No work orders yet. Create one from the Assets page.</div>` : `
          <table>
            <thead><tr><th>Work order</th><th>Asset</th><th>State</th><th>Verdict</th></tr></thead>
            <tbody>${recent.slice(0, 8).map((order) => {
              const repair = (order.repairs || [])[0];
              return `<tr class="clickable" data-goto="#/work-orders/${esc(order.id)}">
                <td><b>${esc(order.title)}</b><div class="inline-note">${esc(order.status)}</div></td>
                <td>${esc(order.asset_name || short(order.asset_id))}</td>
                <td>${esc(repair?.status || "no session")}</td>
                <td>${verdictBadge(repair?.verdict)}</td></tr>`;
            }).join("")}</tbody>
          </table>`}
        </div>
      </div>`;
  }

  function stat(label, value, sub) {
    return `<div class="stat"><div class="label">${esc(label)}</div>
      <div class="value">${esc(value ?? 0)}</div><div class="sub">${esc(sub || "")}</div></div>`;
  }

  /* ---------------------------------------------------------- work orders */

  async function viewWorkOrders() {
    const { work_orders: orders } = await api("/work-orders");
    const assets = (await api("/assets")).assets;
    state.cache.assets = assets;
    return `
      <div class="page-head">
        <div><h1>Work orders</h1>
          <p>A work order owns exactly one repair-verification session at a time. The session
             captures a before reading and an after reading, then runs the policy.</p></div>
        <button class="primary" data-action="new-work-order">New work order</button>
      </div>
      <div class="card">
        ${orders.length === 0 ? `<div class="empty">No work orders yet.</div>` : `
        <table>
          <thead><tr><th>Title</th><th>Asset</th><th>Status</th><th>Repairs</th><th>Verdict</th></tr></thead>
          <tbody>${orders.map((order) => {
            const repairs = order.repairs || [];
            const verdict = repairs.map((r) => r.verdict).filter(Boolean)[0];
            return `<tr class="clickable" data-goto="#/work-orders/${esc(order.id)}">
              <td><b>${esc(order.title)}</b><div class="inline-note">created ${fmtDate(order.created_at)}</div></td>
              <td>${esc(order.asset_name || short(order.asset_id))}</td>
              <td><span class="badge neutral">${esc(order.status)}</span></td>
              <td>${repairs.length || "—"}</td>
              <td>${verdictBadge(verdict)}</td></tr>`;
          }).join("")}</tbody>
        </table>`}
      </div>`;
  }

  async function viewWorkOrder(id) {
    const order = await api(`/work-orders/${id}`);
    const repair = (order.repairs || [])[0] || null;
    const steps = repairSteps(repair);
    const body = repair
      ? await repairPanel(order, repair)
      : `<div class="card"><div class="empty">No verification session for this work order yet.
           <div style="margin-top:14px"><button class="primary" data-action="start-repair">Start verification session</button></div></div></div>`;
    return `
      <div class="page-head">
        <div>
          <a class="inline-note" href="#/work-orders">← work orders</a>
          <h1>${esc(order.title)}</h1>
          <p>${esc(order.asset_name || order.asset_id)} · ${esc(order.component?.name || order.component_id)}
             · created ${fmtDate(order.created_at)}</p>
        </div>
        <div class="row-actions noprint">
          ${order.status !== "closed" ? `<button data-action="close-work-order">Close work order</button>` : ""}
        </div>
      </div>
      <div class="steps">${steps}</div>
      ${order.notes ? `<div class="card"><h2>Notes</h2><p style="margin:0">${esc(order.notes)}</p></div>` : ""}
      ${body}`;
  }

  function repairSteps(repair) {
    const step = (index, title, detail, cls) =>
      `<div class="step ${cls}"><b>${index}. ${esc(title)}</b>${esc(detail)}</div>`;
    if (!repair) {
      return step(1, "Before reading", "capture the failed state", "active") +
             step(2, "After reading", "capture post-repair", "") +
             step(3, "Verification", "run the policy", "") +
             step(4, "Certificate", "sign and publish", "");
    }
    const before = !!repair.before_diagnostic_id;
    const after = !!repair.after_diagnostic_id;
    const verified = !!repair.verdict;
    const certified = !!repair.certificate;
    return [
      step(1, "Before reading", before ? short(repair.before?.diagnostic?.event_id) : "capture the failed state", before ? "done" : "active"),
      step(2, "After reading", after ? short(repair.after?.diagnostic?.event_id) : "capture post-repair", after ? "done" : (before ? "active" : "")),
      step(3, "Verification", verified ? repair.verdict : "run the policy", verified ? "done" : (after ? "active" : "")),
      step(4, "Certificate", certified ? repair.certificate.id : "sign and publish", certified ? "done" : (verified ? "active" : "")),
    ].join("");
  }

  async function repairPanel(order, repair) {
    const [view, catalog] = await Promise.all([
      api(`/repairs/${repair.id}`),
      api("/paderborn/catalog").catch(() => ({ records: [] })),
    ]);
    state.cache.catalog = catalog;
    const result = view.result || null;
    return `
      <div class="grid cols-2">
        ${phaseCard(view, "before", catalog)}
        ${phaseCard(view, "after", catalog)}
      </div>
      <div class="card">
        <h2>Verification</h2>
        ${view.verdict ? verdictBlock(view, result) : `
          <p class="hint">The policy compares the two readings for comparability, then decides.
             It cannot be persuaded by a label: only the evidence decides.</p>`}
        <div class="row-actions noprint">
          <button class="primary" data-action="verify" ${view.before && view.after && !view.verdict ? "" : "disabled"}>
            Run verification policy</button>
          <button class="success" data-action="certificate"
            ${view.verdict && !view.certificate ? "" : "disabled"}>Issue certificate</button>
        </div>
        ${view.certificate ? `
          <div class="notice ok"><b>Certificate issued</b>
            ${esc(view.certificate.id)} · key ${esc(view.certificate.key_id)}<br/>
            <a href="/verify/${esc(view.certificate.id)}" target="_blank">Public verification page</a> ·
            <a href="/v1/public/certificates/${esc(view.certificate.id)}/report" target="_blank">Printable report</a> ·
            <a href="/v1/public/certificates/${esc(view.certificate.id)}" target="_blank">Raw signed JSON</a>
          </div>` : ""}
      </div>`;
  }

  function verdictBlock(view, result) {
    const payload = result || {};
    const reasons = (payload.reasons || []).map((reason) =>
      `<li><b>${esc(reason.code)}</b> — ${esc(reason.detail)}</li>`).join("");
    const policy = payload.policy || {};
    const comparison = payload.comparability || {};
    const checks = Object.entries(comparison).filter(([k]) => k !== "comparable").map(([key, ok]) =>
      `<span class="badge ${ok ? "ok" : "bad"}">${esc(key.replace(/_/g, " "))}</span>`).join(" ");
    return `
      <div class="verdict ${esc(view.verdict)}">
        <div class="marker"></div>
        <div>
          <div class="title">${esc(VERDICT[view.verdict]?.[1] || view.verdict)}</div>
          <div class="detail">${reasons || "recorded verdict"} · policy ${esc(policy.policy_version || view.policy_version)}</div>
        </div>
      </div>
      ${policy.confidence_calibrated ? "" : `
        <div class="notice"><b>Conservative thresholds applied</b>
          This deployment has no calibration artifact, so the policy required
          ${esc(policy.minimum_before_confidence)} before and ${esc(policy.minimum_after_confidence)}
          after, with a fault-probability drop of at least ${esc(policy.minimum_damage_drop)}.</div>`}
      <ul style="font-size:13.5px;margin:8px 0 14px">${reasons}</ul>
      <div style="display:flex;gap:6px;flex-wrap:wrap">${checks}</div>`;
  }

  function phaseCard(view, phase, catalog) {
    const data = view[phase];
    const label = phase === "before" ? "Before repair" : "After repair";
    if (!data) {
      return `<div class="phase">
        <div class="head"><h3>${esc(label)}</h3><span class="badge neutral">awaiting capture</span></div>
        <p class="inline-note">Capture a ${phase} reading. It can come from an uploaded window,
           a replayed laboratory recording, an MQTT event or the LoRa gateway.</p>
        <div class="row-actions"><button class="primary" data-action="capture" data-phase="${phase}">Capture ${phase} reading</button></div>
      </div>`;
    }
    const diagnostic = data.diagnostic;
    const reading = data.reading;
    const probs = diagnostic.probabilities || {};
    const bars = Object.entries(probs).map(([label2, value]) => `
      <div>
        <div class="inline-note">${esc(label2)} · ${pct(value)}</div>
        <div class="meter ${label2 === "healthy" ? "healthy" : "damage"}"><i style="width:${Math.round(value * 100)}%"></i></div>
      </div>`).join("");
    return `<div class="phase">
      <div class="head"><h3>${esc(label)}</h3>${statusBadge(diagnostic.status)}</div>
      <dl class="kv">
        <dt>Event</dt><dd>${esc(diagnostic.event_id)}</dd>
        <dt>Class</dt><dd>${esc(diagnostic.fault_class || "—")}</dd>
        <dt>Confidence</dt><dd>${pct(diagnostic.confidence)}${diagnostic.confidence_calibrated ? " (calibrated)" : " (raw score)"}</dd>
        <dt>Captured</dt><dd>${fmtDate(reading?.captured_at)}</dd>
        <dt>Transport</dt><dd>${esc(reading?.source)}${diagnostic.simulated ? " · replayed evidence" : ""}</dd>
        <dt>Reading hash</dt><dd>${esc(short(reading?.sha256, 20))}</dd>
        <dt>Model</dt><dd>${esc(diagnostic.model_version || "—")}</dd>
      </dl>
      <div class="grid" style="gap:8px;margin-top:12px">${bars}</div>
      ${diagnostic.simulated ? `<div class="notice" style="margin-bottom:0"><b>Replayed evidence</b>
        Produced by replaying a packaged historical laboratory recording, not a live capture.</div>` : ""}
    </div>`;
  }

  /* --------------------------------------------------------------- assets */

  async function viewAssets() {
    const { assets } = await api("/assets");
    const { sites } = await api("/sites");
    const { customers } = await api("/customers");
    state.cache.sites = sites; state.cache.customers = customers;
    return `
      <div class="page-head">
        <div><h1>Assets</h1><p>Every asset belongs to a site and a customer. Evidence can only be
          attached to an asset inside your organisation.</p></div>
        <button class="primary" data-action="new-asset">Register asset</button>
      </div>
      <div class="card">
        ${assets.length === 0 ? `<div class="empty">No assets registered yet.</div>` : `
        <table>
          <thead><tr><th>Asset</th><th>Site</th><th>Type</th><th>Components</th><th>Serial</th></tr></thead>
          <tbody>${assets.map((asset) => `
            <tr class="clickable" data-goto="#/assets/${esc(asset.id)}">
              <td><b>${esc(asset.name)}</b><div class="inline-note">${esc(asset.manufacturer || "")} ${esc(asset.model || "")}</div></td>
              <td>${esc(sites.find((s) => s.id === asset.site_id)?.name || short(asset.site_id))}</td>
              <td>${esc(asset.asset_type)}</td>
              <td>${(asset.components || []).map((c) => esc(c.name)).join(", ") || "—"}</td>
              <td class="mono">${esc(asset.serial || "—")}</td></tr>`).join("")}</tbody>
        </table>`}
      </div>
      ${customers.length === 0 ? "" : `
      <div class="card"><h2>Customers & sites</h2>
        <table><thead><tr><th>Customer</th><th>Sites</th></tr></thead><tbody>
        ${customers.map((customer) => `<tr><td><b>${esc(customer.name)}</b></td>
          <td>${esc(sites.filter((s) => s.customer_id === customer.id).map((s) => s.name).join(", ") || "—")}</td></tr>`).join("")}
        </tbody></table></div>`}`;
  }

  async function viewAsset(id) {
    const [asset, timeline] = await Promise.all([
      api(`/assets/${id}`), api(`/assets/${id}/timeline`),
    ]);
    const component = (asset.components || [])[0];
    return `
      <div class="page-head">
        <div><a class="inline-note" href="#/assets">← assets</a>
          <h1>${esc(asset.name)}</h1>
          <p>${esc(asset.asset_type)} · ${esc(asset.manufacturer || "—")} ${esc(asset.model || "")}
             · serial ${esc(asset.serial || "—")}</p></div>
        <button class="primary" data-action="new-work-order" data-asset="${esc(asset.id)}"
          data-component="${esc(component?.id || "")}">New work order</button>
      </div>
      <div class="card">
        <h2>Components</h2>
        <table><thead><tr><th>Name</th><th>Kind</th><th>Position</th></tr></thead><tbody>
          ${(asset.components || []).map((c) => `<tr><td>${esc(c.name)}</td><td>${esc(c.kind)}</td>
            <td>${esc(c.position || "—")}</td></tr>`).join("") || `<tr><td colspan="3" class="empty">none</td></tr>`}
        </tbody></table>
      </div>
      <div class="card">
        <h2>Evidence timeline</h2>
        ${timeline.events.length === 0 ? `<div class="empty">No evidence recorded for this asset.</div>` : `
        <table><thead><tr><th>When</th><th>Kind</th><th>Detail</th><th>Source</th></tr></thead><tbody>
          ${timeline.events.map((event) => event.kind === "diagnostic" ? `
            <tr><td class="inline-note">${fmtDate(event.at)}</td>
              <td>${statusBadge(event.status)}</td>
              <td class="mono">${esc(short(event.event_id, 16))} · ${pct(event.confidence)}
                ${event.confidence_calibrated ? "" : "· raw score"}</td>
              <td>${esc(event.source || "—")}${event.simulated ? ` <span class="badge warn">replay</span>` : ""}</td></tr>`
            : `<tr><td class="inline-note">${fmtDate(event.at)}</td>
              <td><span class="badge info">repair session</span></td>
              <td>${verdictBadge(event.verdict)} <span class="inline-note">${esc(event.status)}</span></td><td>—</td></tr>`).join("")}
        </tbody></table>`}
      </div>`;
  }

  /* --------------------------------------------------------------- import */

  async function viewImport() {
    const [catalog, assets] = await Promise.all([api("/paderborn/catalog"), api("/assets")]);
    state.cache.catalog = catalog;
    const windows = (catalog.records || []).flatMap((record) =>
      [0, 1].map((index) => ({ record, index })));
    return `
      <div class="page-head">
        <div><h1>Capture & import</h1>
          <p>Bring evidence in. Uploaded windows are scored live by the deployed model;
             replayed laboratory windows are permanently marked as historical evidence.</p></div>
      </div>
      <div class="notice">
        <b>Evidence honesty</b>
        Replayed windows come from the Paderborn University bearing dataset
        (CC BY-NC 4.0). They are legitimate historical evidence and are labelled as such on
        every certificate that uses them. They are not field captures and must not be
        presented as production readings.
      </div>
      <div class="card">
        <h2>Replay a laboratory window</h2>
        <p class="hint">Pick an asset to attach the evidence to. The window is scored by the
           deployed model, and the documented dataset label is stored alongside it.</p>
        <label>Attach to asset</label>
        <select id="replay-asset">
          ${assets.assets.map((a) => `<option value="${esc(a.id)}">${esc(a.name)}</option>`).join("") ||
            `<option value="">no assets registered</option>`}
        </select>
        <table style="margin-top:14px">
          <thead><tr><th>Recording</th><th>Documented class</th><th>Condition</th><th>Window</th><th></th></tr></thead>
          <tbody>${windows.map(({ record, index }) => `
            <tr>
              <td class="mono">${esc(record.recording_id)}</td>
              <td>${record.fault_class === "healthy"
                    ? `<span class="badge ok">healthy</span>`
                    : `<span class="badge bad">${esc(record.fault_class)}</span>`}</td>
              <td class="mono">${esc(record.operating_condition)}</td>
              <td class="num">${index}</td>
              <td class="num"><button class="sm" data-action="replay" data-recording="${esc(record.recording_id)}"
                 data-index="${index}">Score window</button></td>
            </tr>`).join("") || `<tr><td colspan="5" class="empty">dataset not available in this deployment</td></tr>`}
          </tbody>
        </table>
        <div id="replay-result"></div>
      </div>
      <div class="card">
        <h2>Upload a vibration window</h2>
        <p class="hint">One CSV column of samples. The model requires
           ${esc(state.cache.modelContract?.window_samples || 4096)} samples at
           ${esc(state.cache.modelContract?.sampling_frequency_hz || 64000)} Hz.</p>
        <div class="field-row">
          <div><label>Asset</label>
            <select id="upload-asset">${assets.assets.map((a) => `<option value="${esc(a.id)}">${esc(a.name)}</option>`).join("")}</select></div>
          <div><label>Sampling frequency (Hz)</label>
            <input id="upload-fs" type="number" value="64000"/></div>
        </div>
        <label>CSV file</label>
        <input id="upload-file" type="file" accept=".csv,.txt"/>
        <div class="row-actions"><button class="primary" data-action="upload">Score and store</button></div>
        <div id="upload-result"></div>
      </div>`;
  }

  /* ---------------------------------------------------------- certificates */

  async function viewCertificates() {
    const { certificates } = await api("/certificates");
    return `
      <div class="page-head">
        <div><h1>Certificates</h1><p>Each certificate is canonical JSON signed with Ed25519.
          The public verification page re-checks the signature without an account.</p></div>
      </div>
      <div class="card">
        ${certificates.length === 0 ? `<div class="empty">No certificates issued yet.</div>` : `
        <table>
          <thead><tr><th>Certificate</th><th>Asset</th><th>Verdict</th><th>Issued</th><th></th></tr></thead>
          <tbody>${certificates.map((cert) => `
            <tr>
              <td class="mono">${esc(cert.id)}</td>
              <td>${esc(cert.asset?.name || "—")}</td>
              <td>${verdictBadge(cert.verdict)}</td>
              <td class="inline-note">${fmtDate(cert.created_at)}</td>
              <td class="num">
                <a class="btn sm" href="/verify/${esc(cert.id)}" target="_blank">Verify</a>
                <a class="btn sm" href="/v1/public/certificates/${esc(cert.id)}/report" target="_blank">Report</a>
              </td></tr>`).join("")}</tbody>
        </table>`}
      </div>`;
  }

  /* --------------------------------------------------------------- devices */

  async function viewDevices() {
    const [devices, ingest] = await Promise.all([api("/devices"), api("/ingest/status")]);
    const orgId = state.me?.organisation?.id || "{org_id}";
    return `
      <div class="page-head">
        <div><h1>Devices & transports</h1><p>Devices are registered per organisation. An MQTT
          device may only publish inside its own tenant prefix, and the broker enforces it with ACLs.</p></div>
        <button class="primary" data-action="new-device">Register device</button>
      </div>
      <div class="grid cols-2">
        <div class="card">
          <h2>MQTT topic scheme</h2>
          <pre class="code">${esc(ingest.mqtt?.topic_prefix || "axon/v1")}/<span class="k">{org_id}</span>/<span class="k">{site_id}</span>/<span class="k">{device_id}</span>/<span class="k">diagnostic</span>
${esc(ingest.mqtt?.topic_prefix || "axon/v1")}/<span class="k">{org_id}</span>/<span class="k">{site_id}</span>/<span class="k">{device_id}</span>/<span class="k">window</span>
${esc(ingest.mqtt?.topic_prefix || "axon/v1")}/<span class="k">{org_id}</span>/<span class="k">{site_id}</span>/<span class="k">{device_id}</span>/<span class="k">status</span></pre>
          <dl class="kv">
            <dt>Broker</dt><dd>${esc(ingest.mqtt?.broker || "not configured in this deployment")}</dd>
            <dt>Security</dt><dd>TLS 8883, per-device client certificates, broker-side ACL on org_id</dd>
            <dt>Delivery</dt><dd>QoS 1, de-duplicated by event_id per organisation</dd>
            <dt>Your org id</dt><dd>${esc(orgId)}</dd>
          </dl>
        </div>
        <div class="card">
          <h2>Registered devices</h2>
          ${devices.devices.length === 0 ? `<div class="empty">No devices registered.</div>` : `
          <table><thead><tr><th>Name</th><th>Key</th><th>Kind</th><th>Last seen</th></tr></thead><tbody>
            ${devices.devices.map((d) => `<tr><td><b>${esc(d.name)}</b></td>
              <td class="mono">${esc(d.device_key)}</td><td>${esc(d.kind)}</td>
              <td class="inline-note">${d.last_seen ? fmtDate(d.last_seen) : "never"}</td></tr>`).join("")}
          </tbody></table>`}
          <div class="notice info" style="margin-bottom:0"><b>Transport status</b>
            MQTT ${ingest.mqtt?.configured ? `configured against ${esc(ingest.mqtt.broker)}`
              : "not configured — start the broker and the ingest worker to enable it"}.
            ${ingest.mqtt?.last_device_seen ? `<br/>Last device activity: ${esc(ingest.mqtt.last_device_seen.device_key)} at ${fmtDate(ingest.mqtt.last_device_seen.last_seen)}.` : ""}
          </div>
        </div>
      </div>`;
  }

  /* ---------------------------------------------------------- team & keys */

  async function viewTeam() {
    const { members } = await api("/team");
    return `
      <div class="page-head">
        <div><h1>Team & roles</h1><p>Roles decide what a person can do inside this organisation.
          Certificates can only be issued by owners, admins and managers.</p></div>
        <button class="primary" data-action="invite">Invite member</button>
      </div>
      <div class="card">
        <table><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Can</th></tr></thead><tbody>
          ${members.map((m) => `<tr><td><b>${esc(m.name)}</b></td><td class="mono">${esc(m.email)}</td>
            <td><span class="badge info">${esc(m.role)}</span></td>
            <td class="inline-note">${esc(ROLE_CAPABILITY[m.role] || "—")}</td></tr>`).join("")}
        </tbody></table>
      </div>`;
  }

  const ROLE_CAPABILITY = {
    owner: "everything, including billing and ownership transfer",
    admin: "manage the organisation, invite members, issue certificates",
    manager: "run verification sessions and issue certificates",
    technician: "capture evidence and update work orders",
    viewer: "read-only access",
    customer: "read-only access to their own evidence",
  };

  async function viewApiKeys() {
    const { keys } = await api("/api-keys");
    return `
      <div class="page-head">
        <div><h1>API keys</h1><p>Machine credentials for gateways and integrations. They can move
          evidence but cannot issue certificates or change the organisation.</p></div>
        <button class="primary" data-action="new-key">Create key</button>
      </div>
      <div class="card">
        ${keys.length === 0 ? `<div class="empty">No API keys.</div>` : `
        <table><thead><tr><th>Name</th><th>Scopes</th><th>Created</th><th>Last used</th><th></th></tr></thead><tbody>
          ${keys.map((k) => `<tr><td><b>${esc(k.name)}</b></td><td class="mono">${esc(k.scopes)}</td>
            <td class="inline-note">${fmtDate(k.created_at)}</td>
            <td class="inline-note">${k.last_used ? fmtDate(k.last_used) : "never"}</td>
            <td class="num"><button class="sm danger" data-action="revoke-key" data-id="${esc(k.id)}">Revoke</button></td></tr>`).join("")}
        </tbody></table>`}
      </div>`;
  }

  async function viewAudit() {
    const { events } = await api("/audit?limit=200");
    return `
      <div class="page-head"><div><h1>Audit log</h1>
        <p>Append-only record of everything that happened in this organisation, including the
           verdicts and the reasons behind them.</p></div></div>
      <div class="card">
        ${events.length === 0 ? `<div class="empty">No audit events yet.</div>` : `
        <table><thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Entity</th><th>Detail</th></tr></thead><tbody>
          ${events.map((e) => `<tr><td class="inline-note">${fmtDate(e.created_at)}</td>
            <td class="mono">${esc(e.actor)}</td>
            <td><span class="badge ${e.action.includes("verified") ? "ok" : "neutral"}">${esc(e.action)}</span></td>
            <td class="mono">${esc(e.entity_type || "—")} ${esc(short(e.entity_id, 10))}</td>
            <td class="inline-note mono">${esc(JSON.stringify(e.metadata))}</td></tr>`).join("")}
        </tbody></table>`}
      </div>`;
  }

  /* --------------------------------------------------------------- modals */

  function openModal(title, bodyHtml, wire) {
    const overlay = document.getElementById("overlay");
    overlay.innerHTML = `
      <div class="modal-backdrop" data-close>
        <div class="modal" role="dialog" aria-modal="true">
          <h2>${esc(title)}</h2>${bodyHtml}
        </div>
      </div>`;
    overlay.querySelector("[data-close]").addEventListener("click", (event) => {
      if (event.target.hasAttribute("data-close")) closeModal();
    });
    overlay.querySelectorAll("[data-dismiss]").forEach((n) => n.addEventListener("click", closeModal));
    if (wire) wire(overlay);
  }

  function closeModal() { document.getElementById("overlay").innerHTML = ""; }

  async function newWorkOrderModal(preAsset, preComponent) {
    const assets = state.cache.assets || (await api("/assets")).assets;
    state.cache.assets = assets;
    if (!assets.length) { toast("Register an asset first", "bad"); return; }
    openModal("New work order", `
      <div class="hint">The work order names one component on one asset. Start a verification
        session from the work order page.</div>
      <form id="wo-form">
        <label>Asset</label>
        <select name="asset_id" id="wo-asset">${assets.map((a) =>
          `<option value="${esc(a.id)}" ${a.id === preAsset ? "selected" : ""}>${esc(a.name)}</option>`).join("")}</select>
        <label>Component</label>
        <select name="component_id" id="wo-component">${(assets.find((a) => a.id === (preAsset || assets[0].id))?.components || [])
          .map((c) => `<option value="${esc(c.id)}" ${c.id === preComponent ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>
        <label>Title</label>
        <input name="title" required minlength="3" placeholder="Replace drive-end bearing"/>
        <label>Notes (optional)</label>
        <textarea name="notes" rows="3" placeholder="Customer report, symptoms, parts used…"></textarea>
        <div class="row-actions">
          <button type="button" data-dismiss>Cancel</button>
          <button class="primary" type="submit">Create work order</button>
        </div>
      </form>`, (root) => {
      root.querySelector("#wo-asset").addEventListener("change", (event) => {
        const asset = assets.find((a) => a.id === event.target.value);
        root.querySelector("#wo-component").innerHTML = (asset?.components || [])
          .map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("");
      });
      root.querySelector("#wo-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const values = Object.fromEntries(new FormData(event.target).entries());
        try {
          const order = await api("/work-orders", { method: "POST", body: values });
          closeModal(); location.hash = `#/work-orders/${order.id}`; render();
          toast("Work order created", "ok");
        } catch (error) { toast(error.message, "bad"); }
      });
    });
  }

  function captureModal(repairId, phase) {
    const catalog = state.cache.catalog || { records: [] };
    openModal(`Capture ${phase} reading`, `
      <div class="hint">The reading is scored immediately and stored with its evidence hash.
        Nothing is deleted after the fact — recapture means a new session.</div>
      <div class="tabs">
        <button class="tab active" data-tab="replay">Replay laboratory window</button>
        <button class="tab" data-tab="upload">Upload CSV window</button>
      </div>
      <div data-pane="replay">
        <label>Recording</label>
        <select id="cap-recording">${(catalog.records || []).map((r) =>
          `<option value="${esc(r.recording_id)}">${esc(r.recording_id)} — ${esc(r.fault_class)}</option>`).join("")}</select>
        <label>Window index</label>
        <input id="cap-index" type="number" value="0" min="0"/>
        <div class="row-actions">
          <button type="button" data-dismiss>Cancel</button>
          <button class="primary" data-do="replay">Capture replayed window</button>
        </div>
      </div>
      <div data-pane="upload" hidden>
        <label>Sampling frequency (Hz)</label>
        <input id="cap-fs" type="number" value="64000"/>
        <label>CSV file (one column of samples)</label>
        <input id="cap-file" type="file" accept=".csv,.txt"/>
        <div class="row-actions">
          <button type="button" data-dismiss>Cancel</button>
          <button class="primary" data-do="upload">Score and capture</button>
        </div>
      </div>`, (root) => {
      root.querySelectorAll("[data-tab]").forEach((tab) => tab.addEventListener("click", () => {
        root.querySelectorAll("[data-tab]").forEach((t) => t.classList.toggle("active", t === tab));
        root.querySelectorAll("[data-pane]").forEach((p) => { p.hidden = p.dataset.pane !== tab.dataset.tab; });
      }));
      const submit = async (payload, button) => {
        button.disabled = true; button.innerHTML = `<span class="spinner"></span> scoring`;
        try {
          await api(`/repairs/${repairId}/capture`, { method: "POST", body: payload });
          closeModal(); render(); toast(`${phase} reading captured`, "ok");
        } catch (error) {
          toast(error.message, "bad"); button.disabled = false;
          button.textContent = `Capture ${phase} reading`;
        }
      };
      root.querySelector('[data-do="replay"]').addEventListener("click", (event) => submit({
        phase, source: "replay",
        recording_id: root.querySelector("#cap-recording").value,
        window_index: Number(root.querySelector("#cap-index").value || 0),
      }, event.currentTarget));
      root.querySelector('[data-do="upload"]').addEventListener("click", async (event) => {
        const file = root.querySelector("#cap-file").files[0];
        if (!file) { toast("Choose a CSV file first", "bad"); return; }
        const signal = parseCsv(await file.text());
        if (signal.length < 16) { toast("The file needs at least 16 numeric samples", "bad"); return; }
        submit({
          phase, source: "upload", signal,
          sampling_frequency_hz: Number(root.querySelector("#cap-fs").value || 64000),
        }, event.currentTarget);
      });
    });
  }

  function parseCsv(text) {
    return text.split(/[\s,;\n\r]+/).map((token) => Number(token))
      .filter((value) => Number.isFinite(value));
  }

  function newAssetModal() {
    const sites = state.cache.sites || [];
    const customers = state.cache.customers || [];
    if (!sites.length) { toast("Create a customer and a site first", "bad"); return; }
    openModal("Register asset", `
      <form id="asset-form">
        <label>Customer</label>
        <select name="customer_id">${customers.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("")}</select>
        <label>Site</label>
        <select name="site_id">${sites.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("")}</select>
        <label>Asset name</label>
        <input name="name" required placeholder="Pump P-101"/>
        <div class="field-row">
          <div><label>Manufacturer</label><input name="manufacturer" placeholder="Sulzer"/></div>
          <div><label>Model</label><input name="model" placeholder="CA20"/></div>
          <div><label>Serial</label><input name="serial" placeholder="SN-4471"/></div>
        </div>
        <label>Component name</label>
        <input name="component_name" value="drive-end bearing"/>
        <div class="row-actions">
          <button type="button" data-dismiss>Cancel</button>
          <button class="primary" type="submit">Register</button>
        </div>
      </form>`, (root) => {
      root.querySelector("#asset-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
          await api("/assets", { method: "POST", body: Object.fromEntries(new FormData(event.target).entries()) });
          closeModal(); render(); toast("Asset registered", "ok");
        } catch (error) { toast(error.message, "bad"); }
      });
    });
  }

  function newDeviceModal() {
    const sites = state.cache.sites || [];
    openModal("Register device", `
      <div class="hint">The device key is the MQTT client identity. The broker maps it to this
        organisation, and its ACL only allows publishing under your tenant prefix.</div>
      <form id="device-form">
        <label>Device name</label>
        <input name="name" required placeholder="Plant 1 gateway"/>
        <label>Device key</label>
        <input name="device_key" required pattern="[A-Za-z0-9_-]+" placeholder="plant1-gw-01"/>
        <label>Site</label>
        <select name="site_id"><option value="">— none —</option>
          ${sites.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("")}</select>
        <label>Kind</label>
        <select name="kind"><option>mqtt_gateway</option><option>lora_gateway</option><option>manual_upload</option></select>
        <div class="row-actions">
          <button type="button" data-dismiss>Cancel</button>
          <button class="primary" type="submit">Register</button>
        </div>
      </form>`, (root) => {
      root.querySelector("#device-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
          await api("/devices", { method: "POST", body: Object.fromEntries(new FormData(event.target).entries()) });
          closeModal(); render(); toast("Device registered", "ok");
        } catch (error) { toast(error.message, "bad"); }
      });
    });
  }

  function inviteModal() {
    openModal("Invite member", `
      <form id="invite-form">
        <label>Name</label><input name="name" required placeholder="Sam Technician"/>
        <label>Email</label><input name="email" type="email" required/>
        <label>Role</label>
        <select name="role">
          <option value="technician">technician — capture evidence, update work orders</option>
          <option value="manager">manager — run verification, issue certificates</option>
          <option value="admin">admin — manage the organisation</option>
          <option value="viewer">viewer — read only</option>
          <option value="customer">customer — read only, scoped to their evidence</option>
        </select>
        <div class="row-actions">
          <button type="button" data-dismiss>Cancel</button>
          <button class="primary" type="submit">Invite</button>
        </div>
      </form>`, (root) => {
      root.querySelector("#invite-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
          const result = await api("/team", { method: "POST", body: Object.fromEntries(new FormData(event.target).entries()) });
          closeModal(); render();
          toast(result.account_created
            ? "Member invited — share the temporary password out of band"
            : "Member granted access", "ok");
        } catch (error) { toast(error.message, "bad"); }
      });
    });
  }

  function newKeyModal() {
    openModal("Create API key", `
      <form id="key-form">
        <label>Name</label><input name="name" required placeholder="Plant 1 gateway"/>
        <div class="row-actions">
          <button type="button" data-dismiss>Cancel</button>
          <button class="primary" type="submit">Create</button>
        </div>
      </form>`, (root) => {
      root.querySelector("#key-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
          const key = await api("/api-keys", { method: "POST", body: { name: new FormData(event.target).get("name") } });
          openModal("API key created", `
            <div class="notice bad"><b>Shown once</b>This secret cannot be retrieved again.
              Store it in your secret manager now.</div>
            <pre class="code">${esc(key.key)}</pre>
            <div class="row-actions"><button class="primary" data-dismiss>Done</button></div>`, (r2) => {
            r2.querySelectorAll("[data-dismiss]").forEach((n) => n.addEventListener("click", () => { closeModal(); render(); }));
          });
        } catch (error) { toast(error.message, "bad"); }
      });
    });
  }

  /* --------------------------------------------------------------- router */

  const VIEWS = {
    "#/dashboard": viewDashboard,
    "#/work-orders": viewWorkOrders,
    "#/assets": viewAssets,
    "#/import": viewImport,
    "#/certificates": viewCertificates,
    "#/devices": viewDevices,
    "#/team": viewTeam,
    "#/api-keys": viewApiKeys,
    "#/audit": viewAudit,
  };

  async function render() {
    if (!state.token) { renderAuth("login"); return; }
    if (!state.me) await loadMe();
    if (!state.me) { renderAuth("login"); return; }

    const route = state.route;
    const root = document.getElementById("root");
    try {
      let content;
      let match = route.match(/^#\/work-orders\/(.+)$/);
      if (match) content = await viewWorkOrder(match[1]);
      else if ((match = route.match(/^#\/assets\/(.+)$/))) content = await viewAsset(match[1]);
      else {
        const view = VIEWS[route] || viewDashboard;
        content = await view();
      }
      root.innerHTML = shell(content);
      wireCommon(root);
    } catch (error) {
      root.innerHTML = shell(`<div class="card"><div class="notice bad"><b>Something went wrong</b>
        ${esc(error.message)}</div><div class="row-actions">
        <button onclick="location.reload()">Reload</button></div></div>`);
      wireCommon(root);
    }
  }

  function wireCommon(root) {
    root.querySelector("#sign-out")?.addEventListener("click", () => signOut());
    root.querySelectorAll("[data-goto]").forEach((node) =>
      node.addEventListener("click", () => { location.hash = node.dataset.goto; }));
    root.querySelectorAll("[data-action]").forEach((node) =>
      node.addEventListener("click", () => handleAction(node.dataset, node)));
  }

  async function handleAction(data, node) {
    const route = state.route;
    const workOrderMatch = route.match(/^#\/work-orders\/(.+)$/);
    switch (data.action) {
      case "new-work-order":
        return newWorkOrderModal(data.asset, data.component);
      case "new-asset": return newAssetModal();
      case "new-device": return newDeviceModal();
      case "invite": return inviteModal();
      case "new-key": return newKeyModal();
      case "revoke-key":
        if (!confirm("Revoke this API key? Gateways using it will stop working.")) return;
        await api(`/api-keys/${data.id}`, { method: "DELETE" }); render(); toast("Key revoked", "ok"); return;
      case "start-repair": {
        const repair = await api(`/work-orders/${workOrderMatch[1]}/repairs`, { method: "POST", body: {} });
        render(); toast(`Session ${repair.id} started`, "ok"); return;
      }
      case "close-work-order":
        await api(`/work-orders/${workOrderMatch[1]}`, { method: "PATCH", body: { status: "closed" } });
        render(); toast("Work order closed", "ok"); return;
      case "capture": {
        const view = await api(`/repairs/${(await currentRepairId())}`);
        return captureModal(view.id, data.phase);
      }
      case "verify": {
        const repairId = await currentRepairId();
        node.disabled = true; node.innerHTML = `<span class="spinner"></span> verifying`;
        try {
          const result = await api(`/repairs/${repairId}/verify`, { method: "POST" });
          render();
          toast(`Verdict: ${result.result.verdict}`, result.result.verdict === "VERIFIED" ? "ok" : "");
        } catch (error) { toast(error.message, "bad"); node.disabled = false; }
        return;
      }
      case "certificate": {
        const repairId = await currentRepairId();
        try {
          const issued = await api(`/repairs/${repairId}/certificate`, { method: "POST" });
          render(); toast(`Certificate ${issued.certificate.id} issued`, "ok");
        } catch (error) { toast(error.message, "bad"); }
        return;
      }
      case "replay": {
        const assetId = document.getElementById("replay-asset")?.value;
        if (!assetId) { toast("Register an asset first", "bad"); return; }
        const asset = (state.cache.assets || []).find((a) => a.id === assetId);
        const componentId = asset?.components?.[0]?.id;
        node.disabled = true;
        try {
          const result = await api("/readings/replay", { method: "POST", body: {
            asset_id: assetId, component_id: componentId,
            recording_id: data.recording, window_index: Number(data.index) } });
          document.getElementById("replay-result").innerHTML = evidenceResult(result);
          toast("Window scored and stored", "ok");
        } catch (error) { toast(error.message, "bad"); }
        node.disabled = false; return;
      }
      case "upload": {
        const assetId = document.getElementById("upload-asset")?.value;
        const file = document.getElementById("upload-file")?.files[0];
        if (!file) { toast("Choose a CSV file", "bad"); return; }
        const asset = (state.cache.assets || []).find((a) => a.id === assetId);
        const signal = parseCsv(await file.text());
        if (signal.length < 16) { toast("The file needs at least 16 numeric samples", "bad"); return; }
        node.disabled = true;
        try {
          const result = await api("/readings", { method: "POST", body: {
            asset_id: assetId, component_id: asset?.components?.[0]?.id, signal,
            sampling_frequency_hz: Number(document.getElementById("upload-fs").value || 64000),
            source: "csv" } });
          document.getElementById("upload-result").innerHTML = evidenceResult(result);
          toast("Window scored and stored", "ok");
        } catch (error) { toast(error.message, "bad"); }
        node.disabled = false; return;
      }
      default: return;
    }
  }

  async function currentRepairId() {
    const workOrderMatch = state.route.match(/^#\/work-orders\/(.+)$/);
    const order = await api(`/work-orders/${workOrderMatch[1]}`);
    return (order.repairs || [])[0].id;
  }

  function evidenceResult(result) {
    const d = result.diagnostic;
    return `<div class="notice ${d.status === "FAULT_SUSPECTED" ? "bad" : d.status === "HEALTHY_WITHIN_VALIDATED_SCOPE" ? "ok" : ""}">
      <b>${esc(STATUS[d.status]?.[1] || d.status)}</b>
      class ${esc(d.fault_class || "—")} · confidence ${pct(d.confidence)}
      ${d.confidence_calibrated ? "" : " (raw model score, not calibrated)"}<br/>
      <span class="mono">${esc(d.event_id)}</span> · stored reading
      ${result.dataset ? `<br/>Documented dataset label: ${esc(result.dataset.fault_class)}
        (${esc(result.dataset.bearing_id)}), evidence marked as historical replay.` : ""}
    </div>`;
  }

  window.addEventListener("hashchange", () => {
    state.route = location.hash || "#/dashboard";
    render();
  });

  render();
})();
