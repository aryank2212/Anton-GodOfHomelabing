/* Argus command center — view + command Anton's internet intelligence. */
"use strict";

const TOKEN_KEY = "argus.commandToken";

/* ------------------------------------------------------------- utilities */
const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "html") node.innerHTML = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c) node.append(c);
  }
  return node;
};

function esc(s) {
  const div = document.createElement("div");
  div.textContent = s == null ? "" : String(s);
  return div.innerHTML;
}

function fmt(ts) {
  if (!ts) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString();
}

function setTokenPrompt() {
  $("#token-btn").textContent = getToken() ? "🔑 token ✓" : "🔑 Set token";
}

function getToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

async function api(path, { method = "GET", body, auth = false } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth) {
    const t = getToken();
    if (t) headers["Authorization"] = `Bearer ${t}`;
  }
  const res = await fetch(path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return null;
  let data = null;
  try {
    data = await res.json();
  } catch (_) {
    /* no body */
  }
  if (res.status === 401 || res.status === 403) {
    throw Object.assign(new Error((data && data.detail) || "Authed required"), { status: res.status, authed: true });
  }
  if (!res.ok) {
    throw Object.assign(new Error((data && data.detail) || `HTTP ${res.status}`), { status: res.status });
  }
  return data;
}

function toast(msg, type = "info", ms = 4000) {
  const node = el("div", { class: `toast ${type}` }, el("span", { text: msg }));
  $("#toast-root").append(node);
  setTimeout(() => node.remove(), ms);
}

function openModal(title, bodyNode, actions = []) {
  const backdrop = el("div", { class: "modal-backdrop" });
  const close = () => backdrop.remove();
  backdrop.addEventListener("click", (e) => {
    if (e.target === backdrop) close();
  });
  const modal = el(
    "div",
    { class: "modal" },
    el("h2", { text: title }),
    bodyNode,
    el(
      "div",
      { class: "modal-actions" },
      ...actions.map((a) => el("button", { class: a.cls || "btn", ...(a.onclick ? { onclick: () => { a.onclick(); close(); } } : {}) }, a.text)),
      el("button", { class: "btn", onclick: close }, "Close"),
    ),
  );
  backdrop.append(modal);
  $("#modal-root").append(backdrop);
  return { close, backdrop, modal };
}

function promptToken() {
  const input = el("input", { type: "password", placeholder: "command token" });
  openModal("Command token", el("div", {},
    el("p", { class: "dim", text: "Token required to start/cancel investigations, research sessions and manage dot watches." }),
    el("div", { class: "field" }, el("label", { text: "Bearer token" }), input),
  ), [
    { text: "Save", cls: "btn primary", onclick: () => { localStorage.setItem(TOKEN_KEY, input.value.trim()); setTokenPrompt(); toast("Token saved", "info"); } },
    { text: "Clear", cls: "btn", onclick: () => { localStorage.removeItem(TOKEN_KEY); setTokenPrompt(); toast("Token cleared"); } },
  ]);
  setTimeout(() => input.focus(), 30);
  return input;
}

/* ------------------------------------------------------------- state */
let healthTimer = null;

async function refreshHealth() {
  const pill = $("#health-pill");
  try {
    const h = await api("/v1/health");
    const degraded = h.sources && h.sources.some((s) => s.degraded);
    pill.className = `pill ${degraded ? "warn" : "ok"}`;
    pill.textContent = degraded ? "degraded" : `healthy · ${h.observations || 0} obs`;
  } catch (_) {
    pill.className = "pill offline";
    pill.textContent = "offline";
  }
}

/* ------------------------------------------------------------- router */
const VIEWS = {
  overview: { title: "Overview", group: "Monitor" },
  sources: { title: "Sources", group: "Monitor" },
  evidence: { title: "Evidence", group: "Monitor" },
  entities: { title: "Entities", group: "Understand" },
  changes: { title: "Changes", group: "Understand" },
  hypotheses: { title: "Hypotheses", group: "Understand" },
  graph: { title: "Knowledge Graph", group: "Understand" },
  dots: { title: "Investigations (Dots)", group: "Command" },
  research: { title: "Research Sessions", group: "Command" },
  reports: { title: "Reports", group: "Monitor" },
  how: { title: "How it works", group: "" },
};

const RENDERERS = {
  overview: renderOverview,
  sources: renderSources,
  evidence: renderEvidence,
  entities: renderEntities,
  changes: renderChanges,
  hypotheses: renderHypotheses,
  graph: renderGraph,
  dots: renderDots,
  research: renderResearch,
  reports: renderReports,
  how: renderHow,
};

function buildNav() {
  const groups = {};
  for (const [key, v] of Object.entries(VIEWS)) {
    if (!groups[v.group]) groups[v.group] = [];
    groups[v.group].push(key);
  }
  const nav = $("#nav");
  nav.innerHTML = "";
  for (const [group, keys] of Object.entries(groups)) {
    if (group) nav.append(el("div", { class: "nav-group", text: group || " " }));
    for (const key of keys) {
      const item = el("button", { class: "nav-item", text: VIEWS[key].title, onclick: () => location.hash = `#/${key}` });
      item.dataset.view = key;
      nav.append(item);
    }
  }
}

function currentView() {
  const h = location.hash.replace(/^#\/?/, "");
  return VIEWS[h] ? h : "overview";
}

async function route() {
  const view = currentView();
  for (const n of document.querySelectorAll(".nav-item")) {
    n.classList.toggle("active", n.dataset.view === view);
  }
  $("#view-title").textContent = VIEWS[view].title;
  const target = $("#view");
  target.innerHTML = '<div style="text-align:center;padding:40px"><span class="spinner"></span></div>';
  try {
    await RENDERERS[view](target);
  } catch (err) {
    target.innerHTML = "";
    target.append(el("div", { class: "empty", text: `Failed to load: ${err.message}` }));
    if (err.authed) toast("Command token required", "warn");
  }
  refreshHealth();
}

/* =================================================================== views */

/* ------- overview */
async function renderOverview(target) {
  const [health, sources] = await Promise.all([
    api("/v1/health").catch(() => ({})),
    api("/v1/sources").catch(() => ({ items: [] })),
  ]);
  const metrics = el("div", { class: "cards" },
    metricCard("Observations", health.observations || 0),
    metricCard("Observers", (sources.items || []).length),
    metricCard("Active situations", (health.situations?.active ?? 0)),
    metricCard("Oracle", health.oracle_enabled ? "on" : "off"),
  );

  const deg = (sources.items || []).filter((s) => s.degraded);
  let sourceBlock = el("div", { class: "empty", text: "No sources configured." });
  if (sources.items && sources.items.length) {
    const rows = sources.items.map((s) => el("tr", {},
      el("td", {}, s.name),
      el("td", {}, s.enabled ? (s.running ? "running" : "idle") : "disabled"),
      el("td", {}, s.degraded ? el("span", { class: "chip", html: `<span style="color:var(--err)">degraded</span>` }) : (s.consecutive_failures ? `${s.consecutive_failures} fails` : "ok")),
      el("td", {}, s.backoff ? `${Math.round(s.backoff)}s` : "—"),
      el("td", {}, fmt(s.last_collect_at)),
      el("td", {}, s.items_count),
    ));
    sourceBlock = el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, th("Source"), th("State"), th("Health"), th("Backoff"), th("Last collect"), th("Items"))),
      el("tbody", {}, ...rows),
    ));
  }

  target.append(
    el("div", { class: "status-box", style: "margin-bottom:18px" },
      el("p", {}, `Argus is ${health.status === "ok" ? "healthy" : "degraded"}. `,
        el("span", { class: "dim" }, `Uptime ${Math.round((health.uptime_seconds || 0) / 3600)}h · ${health.environment} · v${health.version}`)),
    ),
    metrics,
    el("h3", { style: "margin:26px 0 12px;color:var(--accent)" }, "Collectors"),
    sourceBlock,
    deg.length ? el("div", { style: "margin-top:14px" }, ...deg.map((d) => toastEl("warn", `${d.name} is degraded: ${d.last_error || "collecting failures"}`))) : null,
  );
}

function metricCard(label, value) {
  return el("div", { class: "card" },
    el("div", { class: "metric" }, String(value == null ? "—" : value)),
    el("div", { class: "metric-label" }, label),
  );
}
function toastEl(kind, text) {
  return el("div", { class: "card", style: "margin-top:10px;border-color:var(--warn)" }, text);
}
function th(text) { return el("th", { text }); }
function td(text) { return el("td", {}, el("span", { text })); }

/* ------- sources */
async function renderSources(target) {
  const data = await api("/v1/sources");
  target.append(renderSourcesTable(data));
  target.append(
    el("div", { style: "margin-top:18px" },
      el("h3", { style: "color:var(--accent);margin-bottom:8px" }, "Refresh / trigger"),
      el("p", { class: "dim", text: "Collectors run on their own schedules. There is no manual collect trigger today." }),
    ),
  );
}

function renderSourcesTable(data) {
  if (!data.items || !data.items.length) return el("div", { class: "empty", text: "No sources." });
  const rows = data.items.map((s) => el("tr", {},
    el("td", {}, s.name),
    el("td", {}, s.enabled ? (s.running ? "running" : "idle") : "disabled"),
    el("td", {}, s.degraded ? el("span", { style: "color:var(--err)" }, "DEGRADED") : (s.consecutive_failures ? "degrading" : "ok")),
    el("td", {}, `${s.consecutive_failures} fails`),
    el("td", {}, s.backoff ? `${Math.round(s.backoff)}s` : "—"),
    el("td", {}, fmt(s.last_collect_at)),
    el("td", {}, s.items_count),
  ));
  return el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, th("Source"), th("State"), th("Health"), th("Fails"), th("Backoff"), th("Last"), th("Items"))),
    el("tbody", {}, ...rows),
  ));
}

/* ------- evidence */
async function renderEvidence(target, ctx = {}) {
  const q = ctx.q || "";
  const data = await api(`/v1/evidence?limit=50${q ? `&q=${encodeURIComponent(q)}` : ""}`);
  const search = el("input", { type: "text", placeholder: "Search evidence…", value: q, style: "width:280px" });
  search.addEventListener("keydown", (e) => {
    if (e.key === "Enter") renderEvidence(target, { q: search.value });
  });
  const box = el("div", { style: "margin-bottom:16px;display:flex;gap:8px" }, search,
    el("button", { class: "btn", onclick: () => renderEvidence(target, { q: search.value }) }, "Search"));
  target.append(box);
  if (!data.items || !data.items.length) { target.append(el("div", { class: "empty", text: "No evidence yet." })); return; }
  for (const item of data.items) {
    target.append(el("div", { class: "list-item" },
      el("div", { class: "title", text: item.title || "(untitled)" }),
      el("div", { class: "meta", text: `${item.source_type} · ${item.source} · ${fmt(item.fetched_at)}` }),
      el("div", { class: "meta dim wrap", text: item.url || "" }),
      item.body ? el("div", { class: "body", text: trimBody(item.body, 400) }) : null,
      el("div", { class: "tags" }, ...(item.tags || []).map((t) => el("span", { class: "tag", text: t }))),
    ));
  }
}
function trimBody(b, n) { return b.length > n ? b.slice(0, n) + "…" : b; }

/* ------- entities */
async function renderEntities(target) {
  const data = await api("/v1/entities?limit=100");
  if (!data.items || !data.items.length) { target.append(el("div", { class: "empty", text: "No entities resolved yet." })); return; }
  const rows = data.items.map((e) => el("tr", {},
    el("td", {}, e.name),
    el("td", {}, e.type),
    el("td", {}, e.category || "—"),
    el("td", {}, e.confidence != null ? e.confidence.toFixed(2) : "—"),
  ));
  target.append(el("p", { class: "dim", text: `${data.total} entities` }),
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, th("Name"), th("Type"), th("Category"), th("Confidence"))),
      el("tbody", {}, ...rows),
    )));
}

/* ------- changes */
async function renderChanges(target) {
  const data = await api("/v1/changes?limit=50");
  if (!data.items || !data.items.length) { target.append(el("div", { class: "empty", text: "No changes detected." })); return; }
  for (const c of data.items) {
    target.append(el("div", { class: "list-item" },
      el("div", { class: "title", text: `${c.change_type} · ${c.target}` }),
      el("div", { class: "meta", text: `${c.severity} · ${fmt(c.detected_at)}` }),
      c.before || c.after ? el("div", { class: "body" },
        c.before ? el("div", {}, `before: ${trimBody(c.before, 200)}`) : null,
        c.after ? el("div", {}, `after:  ${trimBody(c.after, 200)}`) : null,
      ) : null,
    ));
  }
}

/* ------- hypotheses */
async function renderHypotheses(target) {
  const data = await api("/v1/hypotheses?limit=50");
  if (!data.items || !data.items.length) { target.append(el("div", { class: "empty", text: "No hypotheses yet." })); return; }
  for (const h of data.items) {
    target.append(el("div", { class: "list-item" },
      el("div", { class: "title", text: `${h.status} · ${h.title}` }),
      el("div", { class: "meta", text: `confidence ${h.confidence != null ? h.confidence.toFixed(2) : "—"} · ${fmt(h.created_at)}` }),
      h.summary ? el("div", { class: "body", text: h.summary }) : null,
      el("div", { class: "tags" }, ...(h.tags || []).map((t) => el("span", { class: "tag", text: t }))),
    ));
  }
}

/* ------- graph */
async function renderGraph(target) {
  const data = await api("/v1/graph/relations");
  const rels = data.relations || data.items || [];
  if (!rels.length) { target.append(el("div", { class: "empty", text: "No relations in the knowledge graph yet." })); return; }
  const rows = rels.map((r) => el("tr", {},
    el("td", {}, r.source || r.from || "—"),
    el("td", {}, "→"),
    el("td", {}, r.predicate || r.relation || r.type || "—"),
    el("td", {}, "→"),
    el("td", {}, r.target || r.to || "—"),
  ));
  target.append(el("p", { class: "dim", text: `${rels.length} relations` }),
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, th("Source"), th(""), th("Relation"), th(""), th("Target"))),
      el("tbody", {}, ...rows),
    )));
}

/* ------- reports */
async function renderReports(target) {
  const data = await api("/v1/reports?limit=50");
  if (!data.items || !data.items.length) { target.append(el("div", { class: "empty", text: "No reports yet." })); return; }
  for (const r of data.items) {
    target.append(el("div", { class: "list-item" },
      el("div", { class: "title", text: r.title || `Report ${r.report_id}` }),
      el("div", { class: "meta", text: `${r.status || ""} · ${fmt(r.created_at)}` }),
      r.summary ? el("div", { class: "body", text: trimBody(r.summary, 300) }) : null,
    ));
  }
}

/* ------- dots (command) */
async function renderDots(target) {
  const [runs, watches] = await Promise.all([
    api("/v1/dots?limit=20").catch(() => ({ items: [] })),
    api("/v1/dots/watches").catch(() => ({ items: [] })),
  ]);

  // action bar
  target.append(el("div", { class: "view-actions", style: "margin-bottom:16px" },
    el("button", { class: "btn primary", onclick: () => dotModal() }, "+ Start investigation"),
    el("button", { class: "btn", onclick: () => watchModal() }, "+ New watch"),
  ));

  // runs table
  target.append(el("h3", { style: "color:var(--accent);margin:8px 0 10px" }, `Runs (${(runs.items || []).length})`));
  if (!runs.items || !runs.items.length) {
    target.append(el("div", { class: "empty", text: "No dot investigations yet." }));
  } else {
    const rows = runs.items.map((run) => el("tr", {},
      el("td", { class: "wrap" }, run.topic),
      el("td", {}, statusChip(run.status)),
      el("td", {}, run.iterations_target != null ? `${run.iterations_progress || 0}/${run.iterations_target}` : "—"),
      el("td", {}, fmt(run.created_at)),
      el("td", {}, el("div", { style: "display:flex;gap:6px" },
        el("button", { class: "btn small", onclick: () => dotDetail(run) }, "view"),
        run.status === "running" || run.status === "queued" ? el("button", { class: "btn small danger", onclick: () => cancelDot(run) }, "cancel") : null,
      )),
    ));
    target.append(el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, th("Topic"), th("Status"), th("Progress"), th("Started"), th(""))),
      el("tbody", {}, ...rows),
    )));
  }

  // watches
  target.append(el("h3", { style: "color:var(--accent);margin:26px 0 10px" }, `Watches (${(watches.items || []).length})`));
  if (!watches.items || !watches.items.length) {
    target.append(el("div", { class: "empty", text: "No dot watches." }));
  } else {
    for (const w of watches.items) {
      target.append(el("div", { class: "list-item" },
        el("div", { class: "title", text: w.topic },
          el("span", { class: "chip", style: w.enabled ? "border-color:var(--ok);color:var(--ok);margin-left:8px" : "margin-left:8px", text: w.enabled ? "enabled" : "paused" })),
        el("div", { class: "meta", text: `every ${w.interval_hours}h · iterations ${w.iterations}` }),
        el("div", { style: "display:flex;gap:8px;margin-top:8px" },
          el("button", { class: "btn small", onclick: () => triggerWatch(w) }, "run now"),
          el("button", { class: "btn small", onclick: () => watchModal(w) }, "edit"),
          el("button", { class: "btn small danger", onclick: () => deleteWatch(w) }, "delete"),
        ),
      ));
    }
  }
}

function statusChip(s) {
  const color = { completed: "var(--ok)", running: "var(--accent)", queued: "var(--accent)", failed: "var(--err)", cancelled: "var(--warn)" }[s] || "var(--text-dim)";
  return el("span", { class: "chip", style: `border-color:${color};color:${color}` }, s);
}

async function dotModal() {
  const topic = el("input", { type: "text", placeholder: "e.g. investigate the recent supply-chain compromise chatter" });
  const iterations = el("input", { type: "number", min: 1, max: 30, value: "12" });
  openModal("Start an investigation", el("div", {},
    el("div", { class: "field" }, el("label", { text: "Topic" }), topic),
    el("div", { class: "field" }, el("label", { text: "Iterations (max rounds)" }), iterations),
    el("p", { class: "dim", text: "Executed by the dot-matching engine via Oracle + web search." }),
  ), [
    { text: "Start", cls: "btn primary", onclick: async () => {
      try {
        await api("/v1/dots", { method: "POST", auth: true, body: { topic: topic.value.trim(), iterations: parseInt(iterations.value, 10) || 12 } });
        toast("Investigation queued");
        location.hash = "#/dots";
      } catch (e) { toast(e.message, "error"); }
    } },
  ]);
  topic.focus();
}

async function cancelDot(run) {
  if (!confirm(`Cancel investigation "${run.topic}"?`)) return;
  try { await api(`/v1/dots/${run.dot_run_id}/cancel`, { method: "POST", auth: true }); toast("Cancelling…"); route(); }
  catch (e) { toast(e.message, "error"); }
}

async function dotDetail(run) {
  try {
    const full = await api(`/v1/dots/${run.dot_run_id}`);
    let report = null;
    try { report = await api(`/v1/dots/${run.dot_run_id}/report`); } catch (_) {}
    const body = el("div", {},
      el("div", { class: "k" }, el("strong", { text: run.topic })),
      el("div", { class: "meta dim", text: `id ${run.dot_run_id}` }),
      el("div", { class: "meta", text: `status ` }, statusChip(full.status || run.status)),
      el("div", { class: "meta", text: `providers: ${(full.providers || []).join(", ") || "default"}` }),
      report && report.summary ? el("div", { class: "body", text: report.summary }) : null,
    );
    openModal("Investigation", body);
  } catch (e) { toast(e.message, "error"); }
}

async function watchModal(w = null) {
  const topic = el("input", { type: "text", value: w ? w.topic : "" });
  const interval = el("input", { type: "number", min: 0.1, max: 8760, step: "0.1", value: w ? w.interval_hours : 24 });
  const iterations = el("input", { type: "number", min: 1, max: 30, value: w ? w.iterations : 12 });
  const enabled = el("input", { type: "checkbox", checked: w ? w.enabled : true });
  openModal(w ? "Edit dot watch" : "New dot watch", el("div", {},
    el("div", { class: "field" }, el("label", { text: "Topic" }), topic),
    el("div", { class: "field" }, el("label", { text: "Interval (hours)" }), interval),
    el("div", { class: "field" }, el("label", { text: "Iterations" }), iterations),
    el("div", { class: "field" }, el("label", { text: "Enabled" }), enabled),
  ), [
    { text: w ? "Save" : "Create", cls: "btn primary", onclick: async () => {
      const body = { topic: topic.value.trim(), interval_hours: parseFloat(interval.value) || 24, iterations: parseInt(iterations.value, 10) || 12, enabled: enabled.checked };
      try {
        if (w) await api(`/v1/dots/watches/${w.dot_watch_id}`, { method: "PATCH", auth: true, body });
        else await api("/v1/dots/watches", { method: "POST", auth: true, body });
        toast(w ? "Watch updated" : "Watch created");
        route();
      } catch (e) { toast(e.message, "error"); }
    } },
  ]);
}

async function triggerWatch(w) {
  if (!confirm(`Run watch "${w.topic}" now?`)) return;
  try { await api(`/v1/dots/watches/${w.dot_watch_id}/run`, { method: "POST", auth: true }); toast("Watch queued for immediate run"); }
  catch (e) { toast(e.message, "error"); }
}

async function deleteWatch(w) {
  if (!confirm(`Delete watch "${w.topic}"?`)) return;
  try { await api(`/v1/dots/watches/${w.dot_watch_id}`, { method: "DELETE", auth: true }); toast("Watch deleted"); route(); }
  catch (e) { toast(e.message, "error"); }
}

/* ------- research (command) */
function targetField(label, input) {
  return el("div", { class: "field" }, el("label", { text: label }), input);
}

function renderTargetedPanel(target) {
  const target_n = el("input", { type: "text", placeholder: "the who/what — person, company, CVE, project…" });
  const place = el("input", { type: "text", placeholder: "geographic or organisational scope" });
  const df = el("input", { type: "text", placeholder: "from (e.g. 2026-01-01)" });
  const dt = el("input", { type: "text", placeholder: "to (e.g. 2026-03-01)" });
  const keywords = el("input", { type: "text", placeholder: "comma-separated keywords" });
  const mode = el("select", {}, ...[
    el("option", { value: "single_pass", text: "single-pass" }),
    el("option", { value: "progressive", text: "progressive" }),
    el("option", { value: "contradictory", text: "contradictory" }),
  ]);
  const note = el("textarea", { placeholder: "anything else, or a free-form question to refine the search" });

  const submit = el("button", { class: "btn primary", onclick: async () => {
    const fields = { target: target_n.value.trim(), place: place.value.trim(), date_from: df.value.trim(), date_to: dt.value.trim() };
    const kw = keywords.value.split(",").map((k) => k.trim()).filter(Boolean);
    let question = note.value.trim();
    if (!question && !fields.target && !fields.place && !kw.length) {
      toast("Give a target, place or keywords", "warn");
      return;
    }
    const body = { mode: mode.value, question, context: "" };
    if (fields.target || fields.place || fields.date_from || fields.date_to || kw.length || question) {
      body.target = { ...fields, keywords: kw, note: "" };
    }
    try {
      await api("/v1/research/sessions", { method: "POST", auth: true, body });
      toast("Targeted research queued");
      route();
    } catch (e) { toast(e.message, "error"); }
  } }, "Start research");
  const clear = el("button", { class: "btn", onclick: () => { target_n.value = place.value = df.value = dt.value = keywords.value = note.value = ""; } }, "Clear");

  target.append(
    el("div", { class: "panel", style: "margin-bottom:20px" },
      el("h3", { style: "color:var(--accent);margin-bottom:4px" }, "Targeted research"),
      el("p", { class: "dim", text: "Give Argus a who/what, where, and when to investigate. Structured fields are stored on the session." }),
      el("div", { class: "grid2" },
        targetField("Target", target_n),
        targetField("Place", place),
        targetField("Date — from", df),
        targetField("Date — to", dt),
      ),
      targetField("Keywords", keywords),
      el("div", { class: "field" }, el("label", { text: "Mode" }), mode),
      el("div", { class: "field" }, el("label", { text: "Note / question (optional)" }), note),
      el("div", { style: "display:flex;gap:8px;margin-top:4px" }, submit, clear),
    ),
  );
}

function renderTargetChips(t) {
  if (!t) return null;
  const m = t.metadata && t.metadata.target;
  if (!m) return null;
  const bits = [];
  if (m.target) bits.push(`target: ${m.target}`);
  if (m.place) bits.push(`place: ${m.place}`);
  if (m.date_from || m.date_to) bits.push(`when: ${m.date_from || "?"}→${m.date_to || "?"}`);
  if (m.keywords && m.keywords.length) bits.push(`kw: ${m.keywords.join(", ")}`);
  if (!bits.length) return null;
  return el("div", { class: "tags", style: "margin-top:6px" }, ...bits.map((b) => el("span", { class: "tag", text: b })));
}

async function renderResearch(target) {
  const data = await api("/v1/research/sessions?limit=20").catch(() => ({ items: [] }));

  renderTargetedPanel(target);

  target.append(el("div", { class: "view-actions", style: "margin-bottom:12px" },
    el("button", { class: "btn", onclick: () => researchModal() }, "+ Free-form question"),
  ));

  if (!data.items || !data.items.length) {
    target.append(el("div", { class: "empty", text: "No research sessions yet." }));
    return;
  }
  target.append(el("h3", { style: "color:var(--accent);margin:8px 0 10px" }, `Sessions (${data.items.length})`));
  for (const s of data.items) {
    target.append(el("div", { class: "list-item" },
      el("div", { class: "title", text: s.question }, statusChip(s.status || "queued")),
      el("div", { class: "meta", text: `mode ${s.mode || "default"} · ${fmt(s.created_at)}` }),
      renderTargetChips(s),
      s.summary ? el("div", { class: "body", text: trimBody(s.summary, 300) }) : null,
      el("div", { style: "display:flex;gap:8px;margin-top:8px" },
        el("button", { class: "btn small", onclick: () => researchDetail(s) }, "view"),
        s.status === "queued" || s.status === "running" ? el("button", { class: "btn small danger", onclick: () => cancelResearch(s) }, "cancel") : null,
      ),
    ));
  }
}

async function researchModal() {
  const question = el("textarea", { placeholder: "A goal-directed research question for the Oracle research worker…" });
  const mode = el("select", {}, ...[
    el("option", { value: "single_pass", text: "single-pass" }),
    el("option", { value: "progressive", text: "progressive" }),
    el("option", { value: "contradictory", text: "contradictory" }),
  ]);
  openModal("New research session (free-form)", el("div", {},
    el("div", { class: "field" }, el("label", { text: "Question" }), question),
    el("div", { class: "field" }, el("label", { text: "Mode" }), mode),
    el("p", { class: "dim", text: "Decomposed into research angles, each run as a dot investigation, then synthesised into a report." }),
  ), [
    { text: "Start", cls: "btn primary", onclick: async () => {
      if (!question.value.trim()) { toast("Question must not be empty", "warn"); return; }
      try {
        await api("/v1/research/sessions", { method: "POST", auth: true, body: { question: question.value.trim(), mode: mode.value } });
        toast("Research session queued");
        location.hash = "#/research";
      } catch (e) { toast(e.message, "error"); }
    } },
  ]);
}

async function cancelResearch(s) {
  if (!confirm(`Cancel research "${s.question}"?`)) return;
  try { await api(`/v1/research/sessions/${s.research_session_id}/cancel`, { method: "POST", auth: true }); toast("Cancelling…"); route(); }
  catch (e) { toast(e.message, "error"); }
}

async function researchDetail(s) {
  try {
    let report = null;
    try { report = await api(`/v1/research/sessions/${s.research_session_id}/report`); } catch (_) {}
    const body = el("div", {},
      el("div", { class: "k" }, el("strong", { text: s.question })),
      el("div", { class: "meta dim", text: `id ${s.research_session_id}` }),
      el("div", { class: "meta" }, `status `, statusChip(s.status || "queued")),
      report && report.summary ? el("div", { class: "body", text: report.summary }) : null,
    );
    openModal("Research session", body);
  } catch (e) { toast(e.message, "error"); }
}

/* ------- how it works */
function renderHow(target) {
  target.append(el("div", { class: "status-box" },
    el("h2", { text: "How Argus works" }),
    el("p", {}, "Argus is the internet-facing perception layer of <b>Anton</b>. Where Sentinel watches the LAN, Argus watches the internet — it ingests raw signals, resolves who/what they mention, correlates them, detects change, and distils it all into intelligence reports."),
    el("div", { class: "flow" },
      step("1 · Ingest", "Collectors (RSS feeds, tracked websites, OSINT APIs, Telegram) gather raw signals — every item becomes an immutable ContentItem / evidence record."),
      step("2 · Extract", "Entities are pulled out of each item (people, CVEs, organisations, IOCs)."),
      step("3 · Resolve", "A knowledge graph resolves mentions to canonical entities across sources."),
      step("4 · Correlate", "Relationships are drawn between entities the moment they co-occur."),
      step("5 · Change", "The change detector flags when a tracked page or entity shifts."),
      step("6 · Hypothesise", "The Oracle proposes hypotheses about what it all means; it can assess research progress round by round."),
      step("7 · Investigate", "On-demand dot investigations and goal-directed research sessions dig deeper via web search + Oracle."),
      step("8 · Report", "Everything is distilled into intelligence reports and published to Hermes, which owns notifications."),
    ),
    el("h2", { style: "margin-top:28px" }, "Collector resilience"),
    el("p", {}, "Collectors back off exponentially when they keep failing and report a <code>degraded</code> state threshold, emitting Hermes events on degrade/recover."),
    el("h2", { style: "margin-top:24px" }, "Commanding Argus"),
    el("p", {}, "The views under <b>Command</b> let you start/cancel investigations and research sessions and manage dot watches. Command actions require the <b>command token</b> (top of the sidebar); monitoring views are open."),
  ));
}
function step(title, text) {
  return el("div", { class: "step" }, el("b", { text: title }), el("p", { text }));
}

/* ------------------------------------------------------------- init */
function init() {
  buildNav();
  setTokenPrompt();
  $("#token-btn").addEventListener("click", promptToken);
  window.addEventListener("hashchange", route);
  route();
  healthTimer = setInterval(refreshHealth, 15000);
}
document.addEventListener("DOMContentLoaded", init);
