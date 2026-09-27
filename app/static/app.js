// ===========================================================================
// i18n - one catalog (i18n.json) shared with the backend, which renders the export
// and the Catalan fallback text from the same entries.
// ===========================================================================
const STATUSES = ["ok", "needs_review", "mismatch", "missing_certificate"];
// What needs attention first - the default tab, and the order used after a decision.
const ATTENTION_ORDER = ["needs_review", "mismatch", "missing_certificate"];
const RECORD_FIELDS = [
  "reference", "specified_norma", "specified_material", "extracted_material", "certificate",
  "certificate_type", "certificate_type_warning", "committed_material", "status", "reason",
  "suggested_action", "human_confirmed",
];
const CERT_EXTENSIONS = [".pdf", ".docx", ".doc", ".zip"];

let catalog = { ca: {}, es: {} };
let lang = "ca";

const $ = (id) => document.getElementById(id);

function storageGet(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function storageSet(key, value) {
  try { localStorage.setItem(key, value); } catch { /* private mode etc. - defaults are fine */ }
}

function t(key, params = {}) {
  const text = catalog[lang]?.[key] ?? catalog.ca?.[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (match, name) => (params[name] ?? match));
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

function formatDate(value) {
  if (!value) return "—";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(lang === "es" ? "es-ES" : "ca-ES", { dateStyle: "short", timeStyle: "short" }).format(date);
}

function applyStaticTranslations() {
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll("[data-i18n-attr]").forEach((el) => {
    const [attr, key] = el.dataset.i18nAttr.split(":");
    el.setAttribute(attr, t(key));
  });
  document.querySelectorAll("[data-lang]").forEach((btn) => btn.setAttribute("aria-pressed", String(btn.dataset.lang === lang)));
}

function setLang(next) {
  lang = catalog[next] ? next : "ca";
  storageSet("cm4.lang", lang);
  applyStaticTranslations();
  renderInputs();
  if (run) { renderResults(); updateExportLink(); }
  if (historyData) renderHistory();
}

function recordAction(r) {
  return r.action_key ? t(r.action_key, r.action_params || {}) : (r.suggested_action || "");
}
function recordWarning(r) {
  return r.warning_key ? t(r.warning_key, r.warning_params || {}) : (r.certificate_type_warning || "");
}

// ===========================================================================
// Shared bits: status marks, stacked bar, tabs, sortable tables
// ===========================================================================
function countByStatus(records) {
  const counts = Object.fromEntries(STATUSES.map((s) => [s, 0]));
  for (const r of records) if (r.status in counts) counts[r.status] += 1;
  return counts;
}

function defaultTab(records) {
  const counts = countByStatus(records);
  return ATTENTION_ORDER.find((s) => counts[s] > 0) || "all";
}

function statusHtml(status, reason) {
  let html = `<span class="st st-${status}"><i></i>${escapeHtml(t(`status.${status}`))}</span>`;
  if (reason) html += `<span class="why">${escapeHtml(t(`reason.${reason}`))}</span>`;
  return html;
}

function stackHtml(counts, total) {
  if (!total) return "";
  return STATUSES.filter((s) => counts[s] > 0)
    .map((s) => `<span class="seg-${s}" style="flex:${counts[s]}" title="${escapeHtml(t(`status.${s}`))}: ${counts[s]}"></span>`)
    .join("");
}

function tabsHtml(records, active) {
  const counts = countByStatus(records);
  const tab = (key, label, n) => `<button type="button" role="tab" class="tab tab-${key}" data-status="${key}"
      aria-selected="${key === active}" ${n === 0 ? "disabled" : ""}>${key === "all" ? "" : "<i></i>"}${escapeHtml(label)}<span class="n">${n}</span></button>`;
  return tab("all", t("tab.all"), records.length) + STATUSES.map((s) => tab(s, t(`status.${s}`), counts[s])).join("");
}

function matchesQuery(r, query) {
  if (!query) return true;
  return [r.reference, r.specified_material, r.extracted_material, r.certificate_filename ?? r.certificate]
    .some((v) => String(v ?? "").toLowerCase().includes(query));
}

function highlight(value, query) {
  const raw = value == null ? "" : String(value);
  const i = query ? raw.toLowerCase().indexOf(query) : -1;
  if (i < 0) return escapeHtml(raw);
  return escapeHtml(raw.slice(0, i)) + `<mark>${escapeHtml(raw.slice(i, i + query.length))}</mark>` + escapeHtml(raw.slice(i + query.length));
}

const STATUS_RANK = { needs_review: 0, mismatch: 1, missing_certificate: 2, ok: 3 };
function sortRows(rows, sort, valueOf) {
  if (!sort?.key) return rows;
  const dir = sort.dir === "desc" ? -1 : 1;
  return [...rows].sort((a, b) => {
    let va = valueOf(a, sort.key), vb = valueOf(b, sort.key);
    if (sort.key === "status") { va = STATUS_RANK[va] ?? 9; vb = STATUS_RANK[vb] ?? 9; }
    if (va == null || va === "") return 1;
    if (vb == null || vb === "") return -1;
    return (va > vb ? 1 : va < vb ? -1 : 0) * dir;
  });
}

function headHtml(columns, sort) {
  return columns.map((c) => {
    const active = sort?.key === c.key;
    const arrow = active ? (sort.dir === "desc" ? "▼" : "▲") : "↕";
    return `<th class="${c.cls || ""}" data-sort="${c.key}" aria-sort="${active ? (sort.dir === "desc" ? "descending" : "ascending") : "none"}">${escapeHtml(c.label)}<span class="sort">${arrow}</span></th>`;
  }).join("");
}

function nextSort(sort, key) {
  if (sort?.key !== key) return { key, dir: "asc" };
  return sort.dir === "asc" ? { key, dir: "desc" } : { key: null, dir: "asc" };
}

// ===========================================================================
// Views
// ===========================================================================
function showView(view) {
  const next = view === "history" ? "history" : "verify";
  $("view-verify").hidden = next !== "verify";
  $("view-history").hidden = next !== "history";
  document.querySelectorAll("[data-view]").forEach((btn) => btn.setAttribute("aria-selected", String(btn.dataset.view === next)));
  if (location.hash !== `#${next}`) history.replaceState(null, "", `#${next}`);
  if (next === "history") loadHistory();
}

// ===========================================================================
// Inputs
// ===========================================================================
const inputs = {
  bom: { file: null, state: "empty", summary: null, error: null },
  certs: { entries: [], ignored: [], showIgnored: false },
  corr: { file: null, state: "empty", summary: null, error: null },
};

function isCertificatePath(path) {
  const name = path.split("/").pop();
  return !name.startsWith("~$") && CERT_EXTENSIONS.some((ext) => name.toLowerCase().endsWith(ext));
}

function setCertificates(entries) {
  // Dotfiles (.DS_Store, ...) are noise, not "ignored certificates" worth reporting.
  const visible = entries.filter((e) => !e.path.split("/").pop().startsWith("."));
  inputs.certs.entries = visible.filter((e) => isCertificatePath(e.path));
  inputs.certs.ignored = visible.filter((e) => !isCertificatePath(e.path)).map((e) => e.path);
  inputs.certs.showIgnored = false;
  renderInputs();
}

async function previewFile(kind, file) {
  const slot = inputs[kind];
  slot.file = file;
  slot.state = file ? "checking" : "empty";
  slot.summary = slot.error = null;
  renderInputs();
  if (!file) return;
  const field = kind === "bom" ? "bom" : "correspondence";
  const form = new FormData();
  form.append(field, file, file.name);
  try {
    const response = await fetch(`/api/preview/${field}`, { method: "POST", body: form });
    const data = await response.json().catch(() => ({}));
    if (slot.file !== file) return; // replaced meanwhile
    if (response.ok) { slot.state = "ok"; slot.summary = data; }
    else {
      slot.state = "error";
      slot.error = data.detail?.key ? t(data.detail.key) : (data.detail?.message || data.detail || `${response.status}`);
    }
  } catch (err) {
    slot.state = "error";
    slot.error = err.message;
  }
  renderInputs();
}

function dzState(el, state) {
  el.classList.remove("is-empty", "is-ok", "is-error", "is-checking");
  el.classList.add(`is-${state}`);
  el.querySelector(".dz-x").hidden = state === "empty";
}

function renderInputs() {
  // BOM
  const bom = inputs.bom;
  dzState($("dz-bom"), bom.state);
  $("bom-body").innerHTML = bom.state === "empty"
    ? `<p class="dz-hint">${escapeHtml(t("dz.drop"))}</p><p class="dz-help">${escapeHtml(t("dz.bom.help"))}</p>`
    : `<p class="dz-file mono">${escapeHtml(bom.file.name)}</p>` + (
      bom.state === "checking" ? `<p class="dz-facts muted">${escapeHtml(t("bom.checking"))}</p>`
      : bom.state === "error" ? `<p class="dz-facts err">${escapeHtml(t("bom.error", { detail: bom.error }))}</p>`
      : `<p class="dz-facts">${escapeHtml(bom.summary.category
          ? t("bom.summary", { parts: bom.summary.parts, category: bom.summary.category, rows: bom.summary.rows })
          : t("bom.summary_nocat", { parts: bom.summary.parts }))}</p>`);

  // Certificates
  const { entries, ignored, showIgnored } = inputs.certs;
  const hasAny = entries.length + ignored.length > 0;
  dzState($("dz-certs"), !hasAny ? "empty" : entries.length ? "ok" : "error");
  if (!hasAny) {
    $("certs-body").innerHTML = `<p class="dz-hint">${escapeHtml(t("dz.drop_folder"))}</p><p class="dz-help">${escapeHtml(t("dz.certs.help"))}</p>`;
  } else {
    const bySupplier = new Map();
    for (const e of entries) {
      const supplier = e.path.includes("/") ? e.path.split("/")[0] : "—";
      const ext = e.path.split(".").pop().toLowerCase();
      const type = ext === "zip" ? "ZIP" : ext === "pdf" ? "PDF" : "Word";
      const s = bySupplier.get(supplier) || {};
      s[type] = (s[type] || 0) + 1;
      bySupplier.set(supplier, s);
    }
    const chips = [...bySupplier].map(([supplier, types]) =>
      `<span class="chip">${escapeHtml(supplier)} · ${Object.entries(types).map(([k, n]) => `${n} ${k}`).join(", ")}</span>`).join("");
    $("certs-body").innerHTML = (entries.length
      ? `<p class="dz-facts"><strong>${escapeHtml(t("certs.summary", { files: entries.length, suppliers: bySupplier.size }))}</strong></p><div class="chips">${chips}</div>`
      : `<p class="dz-facts err">${escapeHtml(t("certs.none"))}</p>`)
      + (ignored.length ? `<p class="dz-warn">${escapeHtml(t("certs.ignored", { n: ignored.length }))} —
          <button type="button" class="linkish" data-toggle-ignored>${escapeHtml(t(showIgnored ? "certs.hide" : "certs.show"))}</button></p>
          ${showIgnored ? `<ul class="ignored mono">${ignored.map((p) => `<li>${escapeHtml(p)}</li>`).join("")}</ul>` : ""}` : "");
  }

  // Correspondence
  const corr = inputs.corr;
  dzState($("dz-corr"), corr.state);
  $("corr-body").innerHTML = corr.state === "empty"
    ? `<p class="dz-hint">${escapeHtml(t("dz.drop"))}</p><p class="dz-help">${escapeHtml(t("dz.corr.help"))}</p>`
    : `<p class="dz-file mono">${escapeHtml(corr.file.name)}</p>` + (
      corr.state === "checking" ? `<p class="dz-facts muted">${escapeHtml(t("corr.checking"))}</p>`
      : corr.state === "error" ? `<p class="dz-facts err">${escapeHtml(t("corr.error", { detail: corr.error }))}</p>`
      : `<p class="dz-facts">${escapeHtml(t("corr.summary", { rows: corr.summary.rows, without: corr.summary.without_lote }))}</p>`);

  // Run button
  const ready = bom.state === "ok" && entries.length > 0 && corr.state !== "error" && corr.state !== "checking";
  const btn = $("submit-btn");
  if (!running) btn.disabled = !ready;
  btn.title = ready ? "" : t("run.needs_inputs");
}

// Click / keyboard / drag-and-drop on each drop zone
const INPUT_FOR = { bom: "bom-input", certs: "certs-input", corr: "correspondence-input" };

document.querySelectorAll(".dz").forEach((dz) => {
  const kind = dz.dataset.kind;
  const input = $(INPUT_FOR[kind]);
  dz.addEventListener("click", (e) => {
    if (e.target.closest("[data-remove], [data-toggle-ignored], ul")) return;
    input.click();
  });
  dz.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target === dz) { e.preventDefault(); input.click(); }
  });
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("is-drag"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("is-drag"));
  dz.addEventListener("drop", async (e) => {
    e.preventDefault();
    dz.classList.remove("is-drag");
    if (kind === "certs") setCertificates(await entriesFromDrop(e.dataTransfer));
    else if (e.dataTransfer.files[0]) previewFile(kind, e.dataTransfer.files[0]);
  });
});

$("bom-input").addEventListener("change", (e) => { previewFile("bom", e.target.files[0] || null); e.target.value = ""; });
$("correspondence-input").addEventListener("change", (e) => { previewFile("corr", e.target.files[0] || null); e.target.value = ""; });
$("certs-input").addEventListener("change", (e) => {
  // A picked folder arrives as "certificats/EBRO/x.zip" - drop the picked folder itself so the
  // first remaining segment is the supplier, as the server expects.
  setCertificates(Array.from(e.target.files).map((file) => {
    const parts = (file.webkitRelativePath || file.name).split("/");
    return { file, path: parts.length > 1 ? parts.slice(1).join("/") : parts[0] };
  }));
  e.target.value = "";
});

$("upload-form").addEventListener("click", (e) => {
  const remove = e.target.closest("[data-remove]");
  if (remove) {
    e.stopPropagation();
    if (remove.dataset.remove === "certs") setCertificates([]);
    else previewFile(remove.dataset.remove, null);
  }
  if (e.target.closest("[data-toggle-ignored]")) {
    e.stopPropagation();
    inputs.certs.showIgnored = !inputs.certs.showIgnored;
    renderInputs();
  }
});

async function entriesFromDrop(dataTransfer) {
  const roots = Array.from(dataTransfer.items || []).map((item) => item.webkitGetAsEntry?.()).filter(Boolean);
  if (!roots.length) return Array.from(dataTransfer.files).map((file) => ({ file, path: file.name }));
  const out = [];
  const readAll = (reader) => new Promise((resolve) => {
    const all = [];
    const next = () => reader.readEntries((batch) => { if (!batch.length) resolve(all); else { all.push(...batch); next(); } }, () => resolve(all));
    next();
  });
  async function walk(entry, prefix) {
    if (entry.isFile) {
      const file = await new Promise((resolve) => entry.file(resolve, () => resolve(null)));
      if (file) out.push({ file, path: prefix + entry.name });
    } else if (entry.isDirectory) {
      for (const child of await readAll(entry.createReader())) await walk(child, `${prefix}${entry.name}/`);
    }
  }
  // One dropped folder is "the certificates folder" (its subfolders are suppliers), like the picker;
  // several dropped folders are taken to be the supplier folders themselves.
  if (roots.length === 1 && roots[0].isDirectory) {
    for (const child of await readAll(roots[0].createReader())) await walk(child, "");
  } else {
    for (const root of roots) await walk(root, "");
  }
  return out;
}

// ===========================================================================
// Running a verification
// ===========================================================================
let running = false;
const STAGE_RANGE = { read: [0, 80], verify: [80, 96], prepare: [96, 100] };

function showProgress(event) {
  const [from, to] = STAGE_RANGE[event.stage] || [0, 0];
  const fraction = event.total ? event.done / event.total : 0;
  $("progress-fill").style.width = `${from + (to - from) * fraction}%`;
  $("progress-stage").textContent = t(`stage.${event.stage}`);
  $("progress-count").textContent = event.total ? `${event.done}/${event.total}` : "";
}

async function readVerifyStream(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (!line) continue;
      const event = JSON.parse(line);
      if (event.stage === "done") return event;
      if (event.stage === "error") throw new Error(t("err.run_failed", { detail: event.detail }));
      showProgress(event);
    }
  }
  throw new Error(t("err.no_result"));
}

$("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const formError = $("form-error");
  formError.hidden = true;
  if (inputs.bom.state !== "ok" || !inputs.certs.entries.length) {
    formError.textContent = t("run.needs_inputs");
    formError.hidden = false;
    return;
  }

  running = true;
  const btn = $("submit-btn");
  btn.disabled = true;
  btn.textContent = t("btn.processing");
  $("progress").hidden = false;
  showProgress({ stage: "read" });

  try {
    const form = new FormData();
    form.append("lang", lang);
    form.append("bom", inputs.bom.file, inputs.bom.file.name);
    for (const { file, path } of inputs.certs.entries) form.append("certificates", file, path);
    if (inputs.corr.file && inputs.corr.state === "ok") form.append("correspondence", inputs.corr.file, inputs.corr.file.name);

    const response = await fetch("/api/verify", { method: "POST", body: form });
    if (!response.ok) throw new Error(t("err.server", { status: response.status, detail: await response.text() }));
    const data = await readVerifyStream(response);
    const suppliers = [...new Set(inputs.certs.entries.map((e) => e.path.split("/")[0]))];
    run = {
      id: data.run_id,
      records: data.records,
      orphans: data.orphan_correspondence_entries || [],
      savedAs: data.saved_as,
      exportsDir: data.exports_dir,
      bom: inputs.bom.file.name,
      date: new Date(),
      certCount: inputs.certs.entries.length,
      suppliers,
      tab: defaultTab(data.records),
      query: "",
      sort: { key: null, dir: "asc" },
    };
    reviewRef = null;
    historyData = null; // a new file was saved - reload the history next time
    $("results-search").value = "";
    updateExportLink();
    renderResults();
  } catch (err) {
    formError.textContent = err.message;
    formError.hidden = false;
  } finally {
    running = false;
    btn.textContent = t("btn.run");
    $("progress").hidden = true;
    renderInputs();
  }
});

// ===========================================================================
// Results
// ===========================================================================
let run = null;
let reviewRef = null;

function updateExportLink() {
  $("export-link").href = `/api/runs/${run.id}/export?lang=${lang}`;
}

function resultColumns() {
  const split = reviewRef !== null;
  const cols = [
    { key: "reference", label: t("th.reference"), cls: "col-ref" },
    { key: "specified_material", label: t("th.specified") },
    { key: "extracted_material", label: t("review.from_cert") },
  ];
  if (!split) cols.push({ key: "certificate_filename", label: t("th.certificate") }, { key: "certificate_type", label: t("th.cert_type") });
  cols.push({ key: "status", label: t("th.result") });
  if (!split) cols.push({ key: "action", label: t("th.action") });
  return cols;
}

function visibleRecords() {
  const rows = run.records.filter((r) => (run.tab === "all" || r.status === run.tab) && matchesQuery(r, run.query));
  return sortRows(rows, run.sort, (r, key) => r[key]);
}

function certTypeHtml(r) {
  if (!r.certificate_type) return '<span class="muted">—</span>';
  const warn = recordWarning(r);
  return `<span class="${warn ? "cert-warn" : ""}"${warn ? ` title="${escapeHtml(warn)}"` : ""}>${escapeHtml(r.certificate_type)}${warn ? " ⚠" : ""}</span>`;
}

function renderResults() {
  $("empty-state").hidden = true;
  $("results").hidden = false;
  const records = run.records;
  const counts = countByStatus(records);

  $("run-title").textContent = run.bom;
  $("run-meta").textContent = t("run.meta", { date: formatDate(run.date), certs: run.certCount, suppliers: run.suppliers.join(", ") });
  $("run-stack").innerHTML = stackHtml(counts, records.length);
  const pending = records.length - counts.ok;
  $("run-resolved").innerHTML = `<strong>${escapeHtml(t("run.resolved", { done: counts.ok, total: records.length }))}</strong>`;
  $("run-pending").textContent = pending ? t("run.pending", { n: pending }) : t("run.all_resolved");
  const note = $("saved-note");
  note.hidden = false;
  note.className = run.savedAs ? "small muted" : "small err";
  note.textContent = run.savedAs ? t("saved.ok", { file: run.savedAs }) : t("saved.failed", { folder: run.exportsDir || "" });
  $("status-tabs").innerHTML = tabsHtml(records, run.tab);

  const columns = resultColumns();
  $("results-head").innerHTML = headHtml(columns, run.sort);
  const rows = visibleRecords();
  $("no-rows").hidden = rows.length > 0;
  $("results-body").innerHTML = rows.map((r) => {
    const cells = {
      reference: `<td class="col-ref mono">${highlight(r.reference, run.query)}</td>`,
      specified_material: `<td>${highlight(r.specified_material, run.query)}</td>`,
      extracted_material: `<td>${r.extracted_material ? highlight(r.extracted_material, run.query) : '<span class="muted">—</span>'}</td>`,
      certificate_filename: `<td class="mono small">${r.certificate_filename ? highlight(r.certificate_filename, run.query) : '<span class="muted">—</span>'}</td>`,
      certificate_type: `<td>${certTypeHtml(r)}</td>`,
      status: `<td>${statusHtml(r.status, r.reason)}</td>`,
      action: `<td class="col-action">${escapeHtml(recordAction(r))}</td>`,
    };
    return `<tr data-ref="${escapeHtml(r.reference)}" class="${r.reference === reviewRef ? "is-selected" : ""}" tabindex="0">${columns.map((c) => cells[c.key]).join("")}</tr>`;
  }).join("");

  $("orphans").hidden = run.orphans.length === 0;
  $("orphans-list").innerHTML = run.orphans
    .map((o) => `<li>${escapeHtml(o.reference)} → ${escapeHtml(o.lote || "—")}</li>`).join("");

  $("work").classList.toggle("is-split", reviewRef !== null);
  $("review").hidden = reviewRef === null;
  if (reviewRef !== null) renderReview();
}

$("status-tabs").addEventListener("click", (e) => {
  const tab = e.target.closest("[data-status]");
  if (!tab) return;
  run.tab = tab.dataset.status;
  if (reviewRef && !visibleRecords().some((r) => r.reference === reviewRef)) { reviewRef = null; renderedReviewKey = null; }
  renderResults();
});
$("results-search").addEventListener("input", (e) => {
  run.query = e.target.value.trim().toLowerCase();
  renderResults();
});
$("results-head").addEventListener("click", (e) => {
  const th = e.target.closest("[data-sort]");
  if (!th) return;
  run.sort = nextSort(run.sort, th.dataset.sort);
  renderResults();
});
$("results-body").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-ref]");
  if (tr) openReview(tr.dataset.ref);
});
$("results-body").addEventListener("keydown", (e) => {
  const tr = e.target.closest("tr[data-ref]");
  if (tr && e.key === "Enter") openReview(tr.dataset.ref);
});

// ===========================================================================
// Review (split view)
// ===========================================================================
let viewerToken = 0;
let renderedReviewKey = null;

function openReview(ref) {
  reviewRef = ref;
  renderResults();
  document.querySelector(`tr[data-ref="${CSS.escape(ref)}"]`)?.scrollIntoView({ block: "nearest" });
}

function closeReview() {
  reviewRef = null;
  renderedReviewKey = null;
  renderResults();
}

function moveReview(step) {
  const rows = visibleRecords();
  if (!rows.length) return;
  const i = rows.findIndex((r) => r.reference === reviewRef);
  const next = i < 0 ? rows[0] : rows[Math.min(rows.length - 1, Math.max(0, i + step))];
  openReview(next.reference);
}

function renderReview() {
  const record = run.records.find((r) => r.reference === reviewRef);
  if (!record) { closeReview(); return; }
  const rows = visibleRecords();
  const pos = rows.findIndex((r) => r.reference === reviewRef);
  const tabLabel = run.tab === "all" ? t("tab.all") : t(`status.${run.tab}`);

  $("rv-ref").textContent = record.reference;
  $("rv-pos").textContent = pos >= 0 ? t("review.position", { i: pos + 1, n: rows.length, tab: tabLabel }) : "";
  $("rv-prev").disabled = pos <= 0;
  $("rv-next").disabled = pos < 0 || pos >= rows.length - 1;

  // Only rebuild the body (and reload the certificate) when the record or its state changed -
  // not on every table re-render, so the viewer doesn't flicker.
  const key = `${record.reference}|${record.status}|${record.reason}|${record.human_confirmed}|${lang}`;
  if (key !== renderedReviewKey) {
    renderedReviewKey = key;
    $("rv-body").innerHTML = reviewBodyHtml(record);
    if (record.certificate_filename) loadViewer(record);
  }

  $("rv-actions").innerHTML = record.certificate_filename
    ? `<button type="button" class="btn btn-ok" data-resolution="ok" ${record.status === "ok" ? "disabled" : ""}>${escapeHtml(t("review.confirm_ok"))} <span class="kbd">O</span></button>
       <button type="button" class="btn btn-bad" data-resolution="mismatch" ${record.status === "mismatch" ? "disabled" : ""}>${escapeHtml(t("review.mark_mismatch"))} <span class="kbd">D</span></button>
       <span class="small muted rv-after">${escapeHtml(record.human_confirmed ? t("review.confirmed_note") : t("review.after_confirm"))}</span>`
    : `<span class="small muted">${escapeHtml(t("review.no_cert_actions"))}</span>`;
}

function explainHtml(r) {
  const key = r.reason || r.status;
  const params = {
    cert_type: r.certificate_type || "",
    specified: r.equivalence_specified_value || r.specified_material,
    extracted: r.equivalence_extracted_value || r.extracted_material || "",
  };
  return `<div class="explain explain-${r.status}">
      <strong>${escapeHtml(t(`explain.${key}.title`, params))}</strong>
      <span>${escapeHtml(t(`explain.${key}.body`, params))}</span>
      <span class="explain-action">${escapeHtml(recordAction(r))}</span>
    </div>`;
}

function reviewBodyHtml(r) {
  const sourceText = r.source === "ocr"
    ? (r.grade_confidence != null ? t("source.ocr", { conf: Math.round(r.grade_confidence) }) : t("source.ocr_noconf"))
    : r.source ? t(`source.${r.source}`) : null;
  const facts = [
    r.certificate_type && [t("review.facts.type"), `EN 10204 ${r.certificate_type}`],
    sourceText && [t("review.facts.source"), sourceText],
    r.match_strategy && [t("review.facts.linked"), t(`strategy.${r.match_strategy}`)],
    r.candidates?.length && [t("review.facts.candidates"), r.candidates.join(", ")],
  ].filter(Boolean);

  let html = `<div class="compare">
      <div><div class="k">${escapeHtml(t("review.from_bom"))}</div><div class="v">${escapeHtml(r.specified_material)}</div></div>
      <div><div class="k">${escapeHtml(t("review.from_cert"))}</div><div class="v">${escapeHtml(r.extracted_material || "—")}</div></div>
    </div>
    ${explainHtml(r)}`;
  if (facts.length) {
    html += `<dl class="facts">${facts.map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join("")}</dl>`;
  }
  if (r.certificate_filename) {
    const url = `/api/runs/${run.id}/certificate?path=${encodeURIComponent(r.certificate_filename)}`;
    html += `<div class="doc">
        <div class="doc-bar"><span class="mono small">${escapeHtml(r.certificate_filename)}</span>
          <a class="small" href="${url}" target="_blank" rel="noopener">${escapeHtml(t("review.open_original"))}</a></div>
        <div class="doc-legend small" id="doc-legend"></div>
        <div class="doc-pages" id="doc-pages"></div>
      </div>`;
  }
  return html;
}

async function loadViewer(record) {
  const token = ++viewerToken;
  const path = encodeURIComponent(record.certificate_filename);
  const pages = $("doc-pages");
  if (!record.certificate_filename.toLowerCase().endsWith(".pdf")) {
    $("doc-legend").textContent = t("review.word_note");
    pages.innerHTML = `<iframe sandbox src="/api/runs/${run.id}/certificate/preview?path=${path}" title="${escapeHtml(record.certificate_filename)}"></iframe>`;
    return;
  }
  let layout = { pages: 1, highlights: [] };
  try {
    layout = await (await fetch(`/api/runs/${run.id}/certificate/layout?path=${path}`)).json();
  } catch { /* show the pages without marks */ }
  if (token !== viewerToken) return;

  const kinds = [...new Set(layout.highlights.map((h) => h.kind))];
  const legend = $("doc-legend");
  legend.innerHTML = kinds.length
    ? `${escapeHtml(t("review.legend"))} ${kinds.map((k) => `<span class="lg lg-${k}"></span>${escapeHtml(t(`legend.${k}`))}`).join(" ")}`
    : (record.extracted_material ? escapeHtml(t("review.not_located")) : "");

  pages.innerHTML = Array.from({ length: layout.pages }, (_, i) => {
    const boxes = layout.highlights.filter((h) => h.page === i).map((h) =>
      `<span class="hl hl-${h.kind}" style="left:${h.x0 * 100}%;top:${h.y0 * 100}%;width:${(h.x1 - h.x0) * 100}%;height:${(h.y1 - h.y0) * 100}%"></span>`).join("");
    return `<figure class="doc-page"><div class="page-img"><img loading="lazy" alt="${escapeHtml(t("review.page", { n: i + 1, total: layout.pages }))}"
        src="/api/runs/${run.id}/certificate/page?path=${path}&n=${i + 1}">${boxes}</div>
        <figcaption class="small muted">${escapeHtml(t("review.page", { n: i + 1, total: layout.pages }))}</figcaption></figure>`;
  }).join("");

  // Bring the first mark into view once its page image has a size.
  const first = pages.querySelector(".hl-grade") || pages.querySelector(".hl");
  if (first) {
    const img = first.parentElement.querySelector("img");
    const scroll = () => { if (token === viewerToken) first.scrollIntoView({ block: "center" }); };
    if (img.complete) scroll(); else img.addEventListener("load", scroll, { once: true });
  }
}

$("rv-prev").addEventListener("click", () => moveReview(-1));
$("rv-next").addEventListener("click", () => moveReview(1));
$("rv-close").addEventListener("click", closeReview);
$("rv-actions").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-resolution]");
  if (btn && !btn.disabled) decide(btn.dataset.resolution);
});

async function decide(resolution) {
  const record = run.records.find((r) => r.reference === reviewRef);
  if (!record?.certificate_filename || record.status === resolution) return;
  const before = visibleRecords();
  const index = before.findIndex((r) => r.reference === record.reference);

  const response = await fetch(`/api/runs/${run.id}/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reference: record.reference, resolution, lang }),
  });
  if (!response.ok) { showToast(t("err.confirm", { detail: await response.text() })); return; }
  const data = await response.json();
  applyRecord(data);

  // Move on to what was next in the list the user was working through.
  const after = visibleRecords();
  const candidate = before.slice(index + 1).find((r) => after.some((a) => a.reference === r.reference));
  if (candidate) reviewRef = candidate.reference;
  else if (!after.some((r) => r.reference === record.reference)) reviewRef = after[0]?.reference ?? null;
  renderedReviewKey = null;
  renderResults();
  showToast(t("toast.decided", { ref: record.reference, status: t(`status.${data.record.status}`) }), record.reference);
}

function applyRecord(data) {
  const i = run.records.findIndex((r) => r.reference === data.record.reference);
  if (i !== -1) run.records[i] = data.record;
  run.savedAs = data.saved_as ?? run.savedAs;
  historyData = null;
}

async function undo(reference) {
  const response = await fetch(`/api/runs/${run.id}/undo`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reference, lang }),
  });
  if (!response.ok) return;
  const data = await response.json();
  applyRecord(data);
  if (run.tab !== "all" && data.record.status !== run.tab) run.tab = data.record.status;
  reviewRef = reference;
  renderedReviewKey = null;
  renderResults();
  showToast(t("toast.undone", { ref: reference }));
}

let toastTimer = null;
function showToast(message, undoRef = null) {
  const toast = $("toast");
  toast.innerHTML = `<span>${escapeHtml(message)}</span>` + (undoRef ? `<button type="button" class="linkish" data-undo="${escapeHtml(undoRef)}">${escapeHtml(t("toast.undo"))}</button>` : "");
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, undoRef ? 7000 : 3000);
}
$("toast").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-undo]");
  if (!btn) return;
  $("toast").hidden = true;
  undo(btn.dataset.undo);
});

document.addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.target.closest("input, textarea, select")) return;
  if (!run || $("view-verify").hidden) return;
  const key = e.key.toLowerCase();
  if (key === "escape" && reviewRef) { closeReview(); e.preventDefault(); }
  else if (key === "j") { moveReview(1); e.preventDefault(); }
  else if (key === "k") { moveReview(-1); e.preventDefault(); }
  else if (key === "o" && reviewRef) { decide("ok"); e.preventDefault(); }
  else if (key === "d" && reviewRef) { decide("mismatch"); e.preventDefault(); }
});

// ===========================================================================
// History
// ===========================================================================
let historyData = null;
const historyState = { expanded: new Set(), tabs: {}, sorts: {}, query: "" };

async function loadHistory(force = false) {
  if (historyData && !force) { renderHistory(); return; }
  try {
    const response = await fetch("/api/history");
    if (!response.ok) throw new Error(`${response.status}`);
    historyData = await response.json();
  } catch (err) {
    historyData = null;
    $("history-status").textContent = t("history.load_failed", { detail: err.message });
    $("history-list").innerHTML = "";
    return;
  }
  renderHistory();
}

function historyCell(key, r, query) {
  const v = r[key];
  if (key === "status") return `<td>${statusHtml(v, r.reason)}</td>`;
  if (key === "reason") return `<td class="small">${v ? escapeHtml(t(`reason.${v}`)) : ""}</td>`;
  if (key === "human_confirmed") return `<td>${v === true ? escapeHtml(t("history.yes")) : `<span class="muted">${escapeHtml(t("history.no"))}</span>`}</td>`;
  if (v == null || v === "") return '<td class="muted">—</td>';
  const cls = key === "reference" ? "col-ref mono" : key === "certificate" ? "mono small" : (key === "suggested_action" || key === "certificate_type_warning") ? "col-action" : "";
  const searchable = ["reference", "specified_material", "extracted_material", "certificate"].includes(key);
  return `<td class="${cls}">${searchable ? highlight(v, query) : escapeHtml(v)}</td>`;
}

function runDetailHtml(r, key, query) {
  const tab = historyState.tabs[key] ?? (query ? "all" : defaultTab(r.records));
  const sort = historyState.sorts[key] || { key: null, dir: "asc" };
  const rows = sortRows(r.records.filter((rec) => tab === "all" || rec.status === tab), sort, (rec, k) => rec[k]);
  const fields = RECORD_FIELDS.filter((f) => ["reference", "status"].includes(f) || r.records.some((rec) => rec[f] != null && rec[f] !== ""));
  const columns = fields.map((f) => ({ key: f, label: t(`export.h.${f}`), cls: f === "reference" ? "col-ref" : "" }));
  const facts = [
    [t("history.facts.verified"), formatDate(r.date) + (r.date_is_approximate ? " *" : "")],
    r.exported_at && [t("summary.exported_at"), formatDate(r.exported_at)],
    r.bom && [t("summary.bom"), r.bom],
    r.certificates != null && [t("summary.certificates"), r.certificates],
    r.suppliers && [t("summary.suppliers"), r.suppliers],
    r.correspondence && [t("summary.correspondence"), r.correspondence],
    [t("summary.human_confirmed"), r.human_confirmed],
    r.run_id && [t("summary.run_id"), String(r.run_id).slice(0, 8)],
  ].filter(Boolean);

  return `<div class="run-detail" data-run="${escapeHtml(key)}">
      <dl class="facts facts-wide">${facts.map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd></div>`).join("")}</dl>
      <div class="stack">${stackHtml(r.summary, r.total)}</div>
      <div class="status-tabs">${tabsHtml(r.records, tab)}</div>
      <div class="table-wrap table-wrap-inline"><table class="grid sortable">
        <thead><tr>${headHtml(columns, sort)}</tr></thead>
        <tbody>${rows.map((rec) => `<tr class="${query && matchesQuery(rec, query) ? "is-match" : ""}">${fields.map((f) => historyCell(f, rec, query)).join("")}</tr>`).join("")}</tbody>
      </table></div>
      ${r.orphans.length ? `<h3>${escapeHtml(t("orphans.title"))}</h3><ul class="mono-list">${r.orphans.map((o) => `<li>${escapeHtml(o.reference)} → ${escapeHtml(o.lote || "—")}</li>`).join("")}</ul>` : ""}
    </div>`;
}

function renderHistory() {
  if (!historyData) return;
  $("history-folder").textContent = historyData.folder;
  const query = historyState.query;
  const runs = query ? historyData.runs.filter((r) => r.records.some((rec) => matchesQuery(rec, query))) : historyData.runs;

  $("history-status").textContent = historyData.runs.length === 0 ? t("history.empty")
    : query && runs.length === 0 ? t("history.no_matches", { query })
    : t("history.runs_loaded", { n: runs.length });

  const head = runs.length ? `<div class="hrow hrow-head">
      <span></span><span>${escapeHtml(t("history.h.date"))}</span><span>${escapeHtml(t("history.h.bom"))}</span>
      <span class="num">${escapeHtml(t("history.h.parts"))}</span><span>${escapeHtml(t("history.h.results"))}</span>
      <span>${escapeHtml(t("history.h.file"))}</span></div>` : "";

  $("history-list").innerHTML = head + runs.map((r) => {
    const key = r.file;
    const open = historyState.expanded.has(key);
    const matches = query ? r.records.filter((rec) => matchesQuery(rec, query)).length : 0;
    const counts = STATUSES.filter((s) => r.summary[s] > 0)
      .map((s) => `<span class="cnt cnt-${s}" title="${escapeHtml(t(`status.${s}`))}"><i></i>${r.summary[s]}</span>`).join("");
    return `<div class="hrun ${open ? "is-open" : ""}">
      <button type="button" class="hrow" data-run-toggle="${escapeHtml(key)}" aria-expanded="${open}">
        <span class="chev" aria-hidden="true">${open ? "▾" : "▸"}</span>
        <span>${escapeHtml(formatDate(r.date))}${r.date_is_approximate ? ` <span class="muted" title="${escapeHtml(t("history.approx_date"))}">*</span>` : ""}</span>
        <span class="ellipsis">${escapeHtml(r.bom || t("history.no_bom"))}</span>
        <span class="num">${r.total}</span>
        <span class="hrow-results"><span class="stack stack-mini">${stackHtml(r.summary, r.total)}</span>${counts}${matches ? `<span class="match-count">${escapeHtml(t("history.matches", { n: matches }))}</span>` : ""}</span>
        <span class="mono small muted ellipsis">${escapeHtml(r.file)}</span>
      </button>
      ${open ? runDetailHtml(r, key, query) : ""}
    </div>`;
  }).join("");

  const approx = historyData.runs.some((r) => r.date_is_approximate);
  const ignored = historyData.ignored || [];
  const el = $("history-ignored");
  el.hidden = !approx && !ignored.length;
  el.innerHTML = [
    approx ? `* ${escapeHtml(t("history.approx_date"))}` : "",
    ignored.length ? `${escapeHtml(t("history.ignored", { n: ignored.length }))}: ${ignored.map((i) =>
      `<span class="mono">${escapeHtml(i.file)}</span> (${escapeHtml(t(`history.reason.${i.reason}`))})`).join(", ")}` : "",
  ].filter(Boolean).join("<br>");
}

$("history-list").addEventListener("click", (e) => {
  const toggle = e.target.closest("[data-run-toggle]");
  if (toggle) {
    const key = toggle.dataset.runToggle;
    if (historyState.expanded.has(key)) historyState.expanded.delete(key); else historyState.expanded.add(key);
    renderHistory();
    return;
  }
  const detail = e.target.closest("[data-run]");
  if (!detail) return;
  const key = detail.dataset.run;
  const tab = e.target.closest("[data-status]");
  const th = e.target.closest("[data-sort]");
  if (tab) historyState.tabs[key] = tab.dataset.status;
  else if (th) historyState.sorts[key] = nextSort(historyState.sorts[key], th.dataset.sort);
  else return;
  renderHistory();
});
$("history-search").addEventListener("input", (e) => {
  historyState.query = e.target.value.trim().toLowerCase();
  historyState.tabs = {}; // searching shows "all" in each run so every match is visible
  renderHistory();
});
$("history-refresh").addEventListener("click", () => loadHistory(true));

// ===========================================================================
// Startup
// ===========================================================================
document.querySelectorAll("[data-lang]").forEach((b) => b.addEventListener("click", () => setLang(b.dataset.lang)));
document.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));
window.addEventListener("hashchange", () => showView(location.hash.slice(1)));

(async function init() {
  try { catalog = await (await fetch("/static/i18n.json")).json(); } catch { /* keys shown as-is */ }
  setLang(storageGet("cm4.lang") || "ca");
  showView(location.hash.slice(1));
  try { $("exports-dir").textContent = (await (await fetch("/api/config")).json()).exports_dir; } catch { /* optional */ }
})();
