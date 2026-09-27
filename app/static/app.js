// ---------------------------------------------------------------------------
// i18n - one catalog (i18n.json) shared with the backend, which renders the export
// and the Catalan fallback text from the same entries.
// ---------------------------------------------------------------------------
const STATUSES = ["ok", "needs_review", "mismatch", "missing_certificate"];
// What needs attention first - the default status tab is the first non-empty one of these.
const ATTENTION_ORDER = ["needs_review", "mismatch", "missing_certificate"];
const RECORD_FIELDS = [
  "reference", "specified_norma", "specified_material", "extracted_material", "certificate",
  "certificate_type", "certificate_type_warning", "committed_material", "status", "reason",
  "suggested_action", "human_confirmed",
];

let catalog = { ca: {}, es: {} };
let lang = "ca";

function storageGet(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function storageSet(key, value) {
  try { localStorage.setItem(key, value); } catch { /* private mode etc. - default is fine */ }
}

function t(key, params = {}) {
  const text = catalog[lang]?.[key] ?? catalog.ca?.[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (match, name) => (params[name] ?? match));
}

function applyStaticTranslations() {
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll("[data-i18n-html]").forEach((el) => { el.innerHTML = t(el.dataset.i18nHtml); });
  document.querySelectorAll("[data-i18n-attr]").forEach((el) => {
    const [attr, key] = el.dataset.i18nAttr.split(":");
    el.setAttribute(attr, t(key));
  });
  document.querySelectorAll("[data-lang]").forEach((btn) => {
    btn.setAttribute("aria-pressed", String(btn.dataset.lang === lang));
  });
}

function setLang(next) {
  lang = catalog[next] ? next : "ca";
  storageSet("cm4.lang", lang);
  applyStaticTranslations();
  refreshInputLabels();
  if (currentRun) {
    renderResults();
    updateExportLink();
  }
  if (historyData) renderHistory();
  if (openRecord) openReview(openRecord);
}

function recordAction(record) {
  return record.action_key ? t(record.action_key, record.action_params || {}) : (record.suggested_action || "");
}
function recordWarning(record) {
  return record.warning_key ? t(record.warning_key, record.warning_params || {}) : (record.certificate_type_warning || "");
}

function formatDate(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat(lang === "es" ? "es-ES" : "ca-ES", { dateStyle: "short", timeStyle: "short" }).format(date);
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

// ---------------------------------------------------------------------------
// Views (Verificació / Historial)
// ---------------------------------------------------------------------------
function showView(view) {
  const next = view === "history" ? "history" : "verify";
  document.getElementById("view-verify").hidden = next !== "verify";
  document.getElementById("view-history").hidden = next !== "history";
  document.querySelectorAll("[data-view]").forEach((btn) => {
    btn.setAttribute("aria-selected", String(btn.dataset.view === next));
  });
  if (location.hash !== `#${next}`) history.replaceState(null, "", `#${next}`);
  if (next === "history") loadHistory();
}

// ---------------------------------------------------------------------------
// Status tabs - shared by the verify results and every expanded history run
// ---------------------------------------------------------------------------
function countByStatus(records) {
  const counts = Object.fromEntries(STATUSES.map((s) => [s, 0]));
  for (const r of records) if (r.status in counts) counts[r.status] += 1;
  return counts;
}

function defaultStatusTab(records) {
  const counts = countByStatus(records);
  return ATTENTION_ORDER.find((s) => counts[s] > 0) || "all";
}

function statusTabsHtml(records, active) {
  const counts = countByStatus(records);
  const tab = (key, label, n) => `
    <button type="button" role="tab" class="status-tab ${key}" data-status="${key}"
      aria-selected="${key === active}" ${n === 0 ? "disabled" : ""}>
      ${key === "all" ? "" : '<span class="marker"></span>'}${escapeHtml(label)} <span class="n">${n}</span>
    </button>`;
  return tab("all", t("tab.all"), records.length) + STATUSES.map((s) => tab(s, t(`status.${s}`), counts[s])).join("");
}

function statusText(status, reason) {
  let html = `<span class="status ${status}"><span class="marker"></span>${escapeHtml(t(`status.${status}`))}</span>`;
  if (reason) html += `<span class="reason">${escapeHtml(t(`reason.${reason}`))}</span>`;
  return html;
}

// ---------------------------------------------------------------------------
// Upload form
// ---------------------------------------------------------------------------
const CERT_EXTENSIONS = [".pdf", ".docx", ".doc", ".zip"];
const form = document.getElementById("upload-form");
const formError = document.getElementById("form-error");
const submitBtn = document.getElementById("submit-btn");
const bomInput = document.getElementById("bom-input");
const certsInput = document.getElementById("certs-input");
const correspondenceInput = document.getElementById("correspondence-input");
const stagesList = document.getElementById("stages");
const STAGE_ORDER = ["read", "verify", "prepare"];

function isCertificateFile(file) {
  const name = file.name.toLowerCase();
  return !file.name.startsWith("~$") && CERT_EXTENSIONS.some((ext) => name.endsWith(ext));
}

function refreshInputLabels() {
  const bom = bomInput.files[0];
  document.getElementById("bom-filename").textContent = bom ? bom.name : t("dz.nofile");
  document.getElementById("bom-row").classList.toggle("has-file", !!bom);

  const all = Array.from(certsInput.files);
  const certs = all.filter(isCertificateFile);
  const certsLabel = document.getElementById("certs-filename");
  if (all.length === 0) {
    certsLabel.textContent = t("dz.nofolder");
  } else {
    const suppliers = new Set(certs.map((f) => (f.webkitRelativePath || f.name).split("/")[1]).filter(Boolean));
    const skipped = all.length - certs.length;
    certsLabel.textContent = skipped
      ? t("dz.certs.count_skipped", { files: certs.length, suppliers: suppliers.size, skipped })
      : t("dz.certs.count", { files: certs.length, suppliers: suppliers.size });
  }
  document.getElementById("certs-row").classList.toggle("has-file", certs.length > 0);

  const corr = correspondenceInput.files[0];
  document.getElementById("correspondence-filename").textContent = corr ? corr.name : t("dz.nofile");
  document.getElementById("correspondence-row").classList.toggle("has-file", !!corr);
}

[bomInput, certsInput, correspondenceInput].forEach((input) => {
  input.addEventListener("change", () => {
    formError.hidden = true;
    document.querySelectorAll(".input-row.missing").forEach((row) => row.classList.remove("missing"));
    refreshInputLabels();
  });
});

// Stage progress is driven entirely by real NDJSON events from the server (see
// /api/verify and pipeline.run_verification_stream) - each update reflects a phase that
// has actually happened, not a fixed-timing animation.
function resetStages() {
  stagesList.hidden = false;
  for (const li of stagesList.children) {
    li.classList.remove("active", "done");
    li.querySelector(".stage-detail").textContent = "";
  }
}

function updateStage(event) {
  const items = Array.from(stagesList.children);
  const stageIndex = STAGE_ORDER.indexOf(event.stage);
  if (stageIndex === -1) return;
  items.forEach((li, i) => {
    li.classList.toggle("done", i < stageIndex);
    li.classList.toggle("active", i === stageIndex);
  });
  let detail = "";
  if (event.total != null) {
    detail = t(event.stage === "read" ? "detail.certs" : "detail.refs", { done: event.done, total: event.total });
  }
  items[stageIndex].querySelector(".stage-detail").textContent = detail ? ` — ${detail}` : "";
}

async function readVerifyStream(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newlineIndex;
    while ((newlineIndex = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newlineIndex).trim();
      buffer = buffer.slice(newlineIndex + 1);
      if (!line) continue;
      const event = JSON.parse(line);
      if (event.stage === "done") return event;
      if (event.stage === "error") throw new Error(t("err.run_failed", { detail: event.detail }));
      updateStage(event);
    }
  }
  throw new Error(t("err.no_result"));
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formError.hidden = true;

  const bomFile = bomInput.files[0];
  const certFiles = Array.from(certsInput.files).filter(isCertificateFile);
  document.getElementById("bom-row").classList.toggle("missing", !bomFile);
  document.getElementById("certs-row").classList.toggle("missing", certFiles.length === 0);
  if (!bomFile || certFiles.length === 0) {
    formError.textContent = !bomFile && certFiles.length === 0 ? t("err.missing_both")
      : !bomFile ? t("err.missing_bom") : t("err.missing_certs");
    formError.hidden = false;
    return;
  }

  submitBtn.disabled = true;
  submitBtn.textContent = t("btn.processing");
  resetStages();

  try {
    const formData = new FormData();
    formData.append("lang", lang);
    formData.append("bom", bomFile, bomFile.name);
    for (const file of certFiles) {
      const rel = file.webkitRelativePath || file.name;
      const parts = rel.split("/");
      formData.append("certificates", file, parts.length > 1 ? parts.slice(1).join("/") : rel);
    }
    const correspondenceFile = correspondenceInput.files[0];
    if (correspondenceFile) formData.append("correspondence", correspondenceFile, correspondenceFile.name);

    const response = await fetch("/api/verify", { method: "POST", body: formData });
    if (!response.ok) {
      throw new Error(t("err.server", { status: response.status, detail: await response.text() }));
    }
    const data = await readVerifyStream(response);
    stagesList.hidden = true;
    currentRun = {
      id: data.run_id,
      records: data.records,
      orphans: data.orphan_correspondence_entries || [],
      savedAs: data.saved_as,
      exportsDir: data.exports_dir,
      tab: defaultStatusTab(data.records),
    };
    historyData = null; // a new file was saved - reload the history next time it's opened
    updateExportLink();
    renderResults();
    document.getElementById("results").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    stagesList.hidden = true;
    formError.textContent = err.message;
    formError.hidden = false;
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = t("btn.run");
  }
});

// ---------------------------------------------------------------------------
// Results of the current run
// ---------------------------------------------------------------------------
let currentRun = null;
let openRecord = null;

function updateExportLink() {
  document.getElementById("export-link").href = `/api/runs/${currentRun.id}/export?lang=${lang}`;
}

function certTypeHtml(record) {
  if (!record.certificate_type) return '<span class="muted">—</span>';
  const warn = recordWarning(record);
  return `<span class="${warn ? "cert-warn" : ""}"${warn ? ` title="${escapeHtml(warn)}"` : ""}>${escapeHtml(record.certificate_type)}${warn ? " ⚠" : ""}</span>`;
}

function renderResults() {
  document.getElementById("results").hidden = false;
  const { records } = currentRun;

  const tabs = document.getElementById("status-tabs");
  tabs.innerHTML = statusTabsHtml(records, currentRun.tab);
  tabs.querySelectorAll("[data-status]").forEach((btn) => {
    btn.addEventListener("click", () => { currentRun.tab = btn.dataset.status; renderResults(); });
  });

  const note = document.getElementById("saved-note");
  note.hidden = false;
  note.textContent = currentRun.savedAs
    ? t("saved.ok", { file: currentRun.savedAs })
    : t("saved.failed", { folder: currentRun.exportsDir || "" });
  note.classList.toggle("error", !currentRun.savedAs);

  const body = document.getElementById("results-body");
  body.innerHTML = "";
  const visible = currentRun.tab === "all" ? records : records.filter((r) => r.status === currentRun.tab);
  for (const record of visible) {
    const tr = document.createElement("tr");
    tr.className = "clickable";
    tr.title = t("row.click_to_review");
    tr.tabIndex = 0;
    tr.addEventListener("click", () => openReview(record));
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") openReview(record); });
    tr.innerHTML = `
      <td class="mono nowrap">${escapeHtml(record.reference)}</td>
      <td>${escapeHtml(record.specified_material)}</td>
      <td>${escapeHtml(record.extracted_material || "—")}</td>
      <td class="mono small">${escapeHtml(record.certificate_filename || "—")}</td>
      <td>${certTypeHtml(record)}</td>
      <td class="nowrap">${statusText(record.status, record.reason)}</td>
      <td class="action">${escapeHtml(recordAction(record))}</td>`;
    body.appendChild(tr);
  }

  const orphans = document.getElementById("orphans");
  orphans.hidden = currentRun.orphans.length === 0;
  document.getElementById("orphans-list").innerHTML = currentRun.orphans
    .map((o) => `<li><span class="mono">${escapeHtml(o.reference)}</span> → <span class="mono">${escapeHtml(o.lote || "—")}</span></li>`)
    .join("");
}

// ---------------------------------------------------------------------------
// Review panel
// ---------------------------------------------------------------------------
const reviewPanel = document.getElementById("review-panel");
const reviewBody = document.getElementById("review-body");

function closeReview() {
  reviewPanel.hidden = true;
  openRecord = null;
}
document.getElementById("review-close").addEventListener("click", closeReview);
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !reviewPanel.hidden) closeReview(); });

function openReview(record) {
  openRecord = record;
  reviewPanel.hidden = false;
  document.getElementById("review-title").textContent = record.reference;

  const warning = recordWarning(record);
  let body = `<p>${statusText(record.status, record.reason)}</p>
    <dl class="review-dl">
      <dt>${escapeHtml(t("review.specified"))}</dt><dd>${escapeHtml(record.specified_material)}</dd>
      <dt>${escapeHtml(t("review.extracted"))}</dt><dd>${escapeHtml(record.extracted_material || "—")}</dd>
      ${record.committed_material ? `<dt>${escapeHtml(t("review.committed"))}</dt><dd>${escapeHtml(record.committed_material)}</dd>` : ""}
      ${warning ? `<dt>${escapeHtml(t("review.cert_warning"))}</dt><dd>${escapeHtml(warning)}</dd>` : ""}
      <dt>${escapeHtml(t("review.action"))}</dt><dd>${escapeHtml(recordAction(record) || "—")}</dd>
    </dl>`;

  if (!record.certificate_filename) {
    reviewBody.innerHTML = body; // nothing to look at or confirm against - view only
    return;
  }

  const path = encodeURIComponent(record.certificate_filename);
  const certUrl = `/api/runs/${currentRun.id}/certificate?path=${path}`;
  const isPdf = record.certificate_filename.toLowerCase().endsWith(".pdf");
  body += `<div class="cert-view">
      <div class="cert-view-bar"><span class="mono small">${escapeHtml(record.certificate_filename)}</span>
        <a class="link small" href="${certUrl}" ${isPdf ? 'target="_blank" rel="noopener"' : "download"}>${escapeHtml(t("review.download"))}</a></div>
      ${isPdf
        ? `<embed src="${certUrl}" type="application/pdf" />`
        : `<p class="muted small">${escapeHtml(t("review.word_note"))}</p>
           <iframe sandbox src="/api/runs/${currentRun.id}/certificate/preview?path=${path}" title="${escapeHtml(record.certificate_filename)}"></iframe>`}
    </div>`;

  // Offered on every linked row, even an already-resolved ok/mismatch one: a decision can be
  // changed after actually looking at the certificate.
  body += `<div class="review-actions">
      <button type="button" class="btn-primary" data-resolution="ok" ${record.status === "ok" ? "disabled" : ""}>${escapeHtml(t("review.confirm_ok"))}</button>
      <button type="button" class="btn-danger" data-resolution="mismatch" ${record.status === "mismatch" ? "disabled" : ""}>${escapeHtml(t("review.mark_mismatch"))}</button>
    </div>`;
  if (record.human_confirmed) body += `<p class="muted small">${escapeHtml(t("review.confirmed_note"))}</p>`;

  reviewBody.innerHTML = body;
  reviewBody.querySelectorAll("[data-resolution]").forEach((btn) => {
    btn.addEventListener("click", () => confirmRow(record, btn.dataset.resolution));
  });
}

async function confirmRow(record, resolution) {
  const response = await fetch(`/api/runs/${currentRun.id}/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reference: record.reference, resolution, lang }),
  });
  if (!response.ok) {
    alert(t("err.confirm", { detail: await response.text() }));
    return;
  }
  const data = await response.json();
  const index = currentRun.records.findIndex((r) => r.reference === record.reference);
  if (index !== -1) currentRun.records[index] = data.record;
  currentRun.savedAs = data.saved_as ?? currentRun.savedAs;
  // Stay on the current tab while it still has rows; otherwise fall back to what needs attention.
  if (currentRun.tab !== "all" && !currentRun.records.some((r) => r.status === currentRun.tab)) {
    currentRun.tab = defaultStatusTab(currentRun.records);
  }
  historyData = null;
  renderResults();
  closeReview();
}

// ---------------------------------------------------------------------------
// History - every export in the server's history folder
// ---------------------------------------------------------------------------
let historyData = null;
const historyState = { expanded: new Set(), tabs: {}, query: "" };
const historyList = document.getElementById("history-list");
const historyStatus = document.getElementById("history-status");

async function loadHistory(force = false) {
  if (historyData && !force) { renderHistory(); return; }
  try {
    const response = await fetch("/api/history");
    if (!response.ok) throw new Error(`${response.status}`);
    historyData = await response.json();
  } catch (err) {
    historyData = null;
    historyStatus.textContent = t("history.load_failed", { detail: err.message });
    historyList.innerHTML = "";
    return;
  }
  renderHistory();
}

function recordMatches(record, query) {
  if (!query) return false;
  return ["reference", "specified_material", "extracted_material", "certificate"]
    .some((key) => String(record[key] ?? "").toLowerCase().includes(query));
}

function highlight(value, query) {
  const text = escapeHtml(value);
  if (!query) return text;
  const lower = String(value ?? "").toLowerCase();
  const i = lower.indexOf(query);
  if (i < 0) return text;
  const raw = String(value);
  return escapeHtml(raw.slice(0, i)) + `<mark>${escapeHtml(raw.slice(i, i + query.length))}</mark>` + escapeHtml(raw.slice(i + query.length));
}

function historyCell(key, record, query) {
  const value = record[key];
  if (key === "status") return `<td class="nowrap">${statusText(value, record.reason)}</td>`;
  if (key === "reason") return `<td class="small">${value ? escapeHtml(t(`reason.${value}`)) : ""}</td>`;
  if (key === "human_confirmed") return `<td>${value === true ? escapeHtml(t("history.yes")) : '<span class="muted">' + escapeHtml(t("history.no")) + "</span>"}</td>`;
  if (value == null || value === "") return '<td class="muted">—</td>';
  const mono = key === "reference" || key === "certificate";
  const nowrap = key === "reference" ? " nowrap" : "";
  const searchable = ["reference", "specified_material", "extracted_material", "certificate"].includes(key);
  const long = key === "suggested_action" || key === "certificate_type_warning" ? " action" : "";
  return `<td class="${mono ? "mono small" : ""}${nowrap}${long}">${searchable ? highlight(value, query) : escapeHtml(value)}</td>`;
}

function runDetailHtml(run, key, query) {
  const tab = historyState.tabs[key] ?? (query ? "all" : defaultStatusTab(run.records));
  const rows = tab === "all" ? run.records : run.records.filter((r) => r.status === tab);
  const fields = RECORD_FIELDS.filter((f) => run.records.some((r) => r[f] != null && r[f] !== "") || ["reference", "status"].includes(f));
  const meta = [
    run.exported_at && t("history.meta.exported", { date: formatDate(run.exported_at) }),
    run.certificates != null && t("history.meta.certs", { n: run.certificates }),
    run.suppliers && t("history.meta.suppliers", { list: run.suppliers }),
    run.correspondence && t("history.meta.correspondence", { file: run.correspondence }),
    t("history.meta.confirmed", { n: run.human_confirmed }),
    run.run_id && t("history.meta.run", { id: String(run.run_id).slice(0, 8) }),
  ].filter(Boolean);

  return `<div class="run-detail">
    <p class="muted small">${meta.map(escapeHtml).join(" · ")}</p>
    <div class="status-tabs" role="tablist" data-run="${escapeHtml(key)}">${statusTabsHtml(run.records, tab)}</div>
    <div class="table-scroll"><table class="grid">
      <thead><tr>${fields.map((f) => `<th>${escapeHtml(t(`export.h.${f}`))}</th>`).join("")}</tr></thead>
      <tbody>${rows.map((r) => `<tr class="${recordMatches(r, query) ? "match" : ""}">${fields.map((f) => historyCell(f, r, query)).join("")}</tr>`).join("")}</tbody>
    </table></div>
    ${run.orphans.length ? `<h3>${escapeHtml(t("orphans.title"))}</h3><ul class="mono-list">${run.orphans.map((o) => `<li><span class="mono">${escapeHtml(o.reference)}</span> → <span class="mono">${escapeHtml(o.lote || "—")}</span></li>`).join("")}</ul>` : ""}
  </div>`;
}

function renderHistory() {
  if (!historyData) return;
  document.getElementById("history-folder").textContent = historyData.folder;
  const query = historyState.query;
  const runs = query ? historyData.runs.filter((run) => run.records.some((r) => recordMatches(r, query))) : historyData.runs;

  if (historyData.runs.length === 0) historyStatus.textContent = t("history.empty");
  else if (query && runs.length === 0) historyStatus.textContent = t("history.no_matches", { query });
  else historyStatus.textContent = t("history.runs_loaded", { n: runs.length });

  const header = runs.length ? `<div class="run-row run-head">
      <span></span><span>${escapeHtml(t("history.h.date"))}</span><span>${escapeHtml(t("history.h.bom"))}</span>
      <span class="num">${escapeHtml(t("history.h.parts"))}</span><span>${escapeHtml(t("history.h.results"))}</span>
      <span>${escapeHtml(t("history.h.file"))}</span></div>` : "";

  historyList.innerHTML = header + runs.map((run) => {
    const key = run.file;
    const expanded = historyState.expanded.has(key);
    const matches = query ? run.records.filter((r) => recordMatches(r, query)).length : 0;
    const counts = STATUSES.filter((s) => run.summary[s] > 0)
      .map((s) => `<span class="status ${s}" title="${escapeHtml(t(`status.${s}`))}"><span class="marker"></span>${run.summary[s]}</span>`)
      .join(" ");
    return `<div class="run ${expanded ? "expanded" : ""}">
      <button type="button" class="run-row" data-run-toggle="${escapeHtml(key)}" aria-expanded="${expanded}"
        title="${escapeHtml(t(expanded ? "history.collapse" : "history.expand"))}">
        <span class="chev" aria-hidden="true">${expanded ? "▾" : "▸"}</span>
        <span>${escapeHtml(formatDate(run.date))}${run.date_is_approximate ? ` <span class="muted small" title="${escapeHtml(t("history.approx_date"))}">*</span>` : ""}</span>
        <span>${escapeHtml(run.bom || t("history.no_bom"))}</span>
        <span class="num">${run.total}</span>
        <span class="counts">${counts}${matches ? ` <span class="small match-count">${escapeHtml(t("history.matches", { n: matches }))}</span>` : ""}</span>
        <span class="mono small muted">${escapeHtml(run.file)}</span>
      </button>
      ${expanded ? runDetailHtml(run, key, query) : ""}
    </div>`;
  }).join("");

  const approx = historyData.runs.some((r) => r.date_is_approximate);
  const ignored = historyData.ignored || [];
  const ignoredEl = document.getElementById("history-ignored");
  ignoredEl.hidden = !approx && ignored.length === 0;
  ignoredEl.innerHTML = [
    approx ? `* ${escapeHtml(t("history.approx_date"))}` : "",
    ignored.length ? `${escapeHtml(t("history.ignored", { n: ignored.length }))}: ` +
      ignored.map((i) => `<span class="mono">${escapeHtml(i.file)}</span> (${escapeHtml(t(`history.reason.${i.reason}`))})`).join(", ") : "",
  ].filter(Boolean).join("<br>");
}

historyList.addEventListener("click", (e) => {
  const toggle = e.target.closest("[data-run-toggle]");
  if (toggle) {
    const key = toggle.dataset.runToggle;
    if (historyState.expanded.has(key)) historyState.expanded.delete(key);
    else historyState.expanded.add(key);
    renderHistory();
    return;
  }
  const tab = e.target.closest("[data-status]");
  const group = tab?.closest("[data-run]");
  if (tab && group) {
    historyState.tabs[group.dataset.run] = tab.dataset.status;
    renderHistory();
  }
});

document.getElementById("history-search").addEventListener("input", (e) => {
  historyState.query = e.target.value.trim().toLowerCase();
  historyState.tabs = {}; // searching resets each run to "all" so every match is visible
  renderHistory();
});
document.getElementById("history-refresh").addEventListener("click", () => loadHistory(true));

// ---------------------------------------------------------------------------
// Startup
// ---------------------------------------------------------------------------
document.querySelectorAll("[data-lang]").forEach((btn) => btn.addEventListener("click", () => setLang(btn.dataset.lang)));
document.querySelectorAll("[data-view]").forEach((btn) => btn.addEventListener("click", () => showView(btn.dataset.view)));
window.addEventListener("hashchange", () => showView(location.hash.slice(1)));

(async function init() {
  try {
    catalog = await (await fetch("/static/i18n.json")).json();
  } catch { /* keys are shown as-is; the app still works */ }
  lang = storageGet("cm4.lang") || "ca";
  setLang(lang);
  showView(location.hash.slice(1));
})();
