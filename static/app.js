"use strict";

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const CAT_LABEL = { civil: "Civil", criminal: "Criminal", writ: "Civil writ", auto: "Auto" };
const RUNNING = ["queued", "reading", "reasoning"];

const state = { config: null, reviewId: null, review: null, filter: "open", poll: null, listPoll: null };

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) { /* not JSON */ }
    throw new Error(msg);
  }
  return res.json();
}
const jsonOpts = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2600);
}
function fmtDate(iso) { return iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : ""; }
function statusPill(status) {
  const cls = status === "done" ? "done" : status === "error" ? "error" : "running";
  const spin = RUNNING.includes(status) ? '<span class="spinner"></span> ' : "";
  return `<span class="pill ${cls}">${spin}${esc(status || "-")}</span>`;
}

// ------------------------------------------------------------ routing
function show(view) {
  document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
  $(`#view-${view}`).classList.remove("hidden");
  document.querySelectorAll(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === view || (view === "review" && b.dataset.view === "filings")));
  if (view !== "review") { clearInterval(state.poll); state.poll = null; }
  if (view !== "filings") { clearInterval(state.listPoll); state.listPoll = null; }
}
function route() {
  const h = location.hash.slice(1);
  const m = h.match(/^review\/(\d+)$/);
  if (m) return openReview(Number(m[1]));
  if (h === "checklists") { show("checklists"); return loadChecklist(); }
  if (h === "notes") { show("notes"); return loadNotes(); }
  show("filings"); loadFilings();
}
document.querySelectorAll(".nav-btn").forEach((b) => b.addEventListener("click", () => { location.hash = b.dataset.view; }));
window.addEventListener("hashchange", route);

// ------------------------------------------------------------ filings
async function loadFilings() {
  const rows = await api("/api/filings");
  $("#filings-empty").classList.toggle("hidden", rows.length > 0);
  $("#filings-body").innerHTML = rows.map((f) => `
    <tr class="click" data-review="${f.review_id}">
      <td><strong>${esc(f.title)}</strong><div class="small muted">${esc(f.filename)}${f.case_type ? " &middot; " + esc(f.case_type) : ""}</div></td>
      <td>${esc(CAT_LABEL[f.review_category] || CAT_LABEL[f.category] || f.category)}</td>
      <td>${f.pages}</td>
      <td>${statusPill(f.status)}${RUNNING.includes(f.status) ? `<div class="small muted">${esc(f.progress)}</div>` : ""}</td>
      <td>${f.readiness ?? "-"}</td>
      <td>${f.status === "done" ? `${f.open_findings}${f.open_high ? ` <span class="pill high">${f.open_high} high</span>` : ""}` : "-"}</td>
      <td class="small muted">${fmtDate(f.uploaded_at)}</td>
    </tr>`).join("");
  document.querySelectorAll("#filings-body tr").forEach((tr) => tr.addEventListener("click", () => { location.hash = `review/${tr.dataset.review}`; }));
  const busy = rows.some((f) => RUNNING.includes(f.status));
  if (busy && !state.listPoll) state.listPoll = setInterval(() => { if (!$("#view-filings").classList.contains("hidden")) loadFilings(); }, 4000);
  if (!busy && state.listPoll) { clearInterval(state.listPoll); state.listPoll = null; }
}

const drop = $("#drop"), fileInput = $("#file");
drop.addEventListener("click", () => fileInput.click());
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => {
  e.preventDefault(); drop.classList.remove("over");
  if (e.dataTransfer.files.length) { fileInput.files = e.dataTransfer.files; onFile(); }
});
fileInput.addEventListener("change", onFile);
function onFile() {
  const f = fileInput.files[0];
  $("#drop-name").textContent = f ? `${f.name} (${(f.size / 1048576).toFixed(1)} MB)` : "";
  if (f && !$("#title").value) $("#title").value = f.name.replace(/\.pdf$/i, "");
}

$("#upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = fileInput.files[0];
  if (!f) return toast("Choose a PDF first");
  const fd = new FormData();
  fd.append("file", f);
  fd.append("title", $("#title").value);
  fd.append("category", $("#category").value);
  fd.append("case_type_hint", $("#case_type").value);
  fd.append("reader", $("#reader").value);
  $("#upload-btn").disabled = true; $("#upload-status").textContent = "Uploading...";
  try {
    const r = await api("/api/filings", { method: "POST", body: fd });
    $("#upload-form").reset(); $("#drop-name").textContent = ""; $("#upload-status").textContent = "";
    location.hash = `review/${r.review_id}`;
  } catch (err) {
    $("#upload-status").textContent = err.message;
  } finally { $("#upload-btn").disabled = false; }
});

// ------------------------------------------------------------ review
async function openReview(id) {
  show("review");
  if (state.reviewId !== id) { state.filter = "open"; }
  state.reviewId = id;
  await refreshReview();
}

async function refreshReview() {
  const rv = await api(`/api/reviews/${state.reviewId}`);
  state.review = rv;
  const f = rv.filing;
  $("#rv-title").textContent = f.title;
  $("#rv-sub").textContent = `${f.filename} · ${f.pages} pages · reader ${rv.reader_model} · reasoner ${rv.reasoner_model}`;
  $("#rv-pdf").href = `/api/filings/${f.id}/pdf`;

  const filing = await api(`/api/filings/${f.id}`);
  $("#rv-history").innerHTML = filing.reviews.map((r) =>
    `<option value="${r.id}" ${r.id === rv.id ? "selected" : ""}>Review #${r.id} · ${esc(r.status)}${r.readiness != null ? " · " + r.readiness : ""} · ${fmtDate(r.created_at)}</option>`).join("");

  const running = RUNNING.includes(rv.status);
  $("#rv-running").classList.toggle("hidden", !running);
  $("#rv-progress").textContent = rv.progress || rv.status;
  $("#rv-error").classList.toggle("hidden", rv.status !== "error");
  $("#rv-error").textContent = rv.status === "error" ? `Review failed: ${rv.error}` : "";
  $("#rv-body").classList.toggle("hidden", rv.status !== "done");
  $("#rerun-btn").disabled = running;

  if (running && !state.poll) state.poll = setInterval(refreshReview, 3000);
  if (!running && state.poll) { clearInterval(state.poll); state.poll = null; }
  if (rv.status === "done") renderReview(rv);
}

function renderReview(rv) {
  const pct = rv.readiness ?? 0;
  const col = pct >= 90 ? "var(--pass)" : pct >= 70 ? "var(--low)" : pct >= 40 ? "var(--medium)" : "var(--high)";
  $("#gauge").style.background = `conic-gradient(${col} ${pct * 3.6}deg, var(--surface-2) 0)`;
  $("#gauge-num").textContent = pct;
  $("#rv-casetype").textContent = `${CAT_LABEL[rv.category] || rv.category} checklist · ${rv.case_type}`;
  $("#rv-summary").textContent = rv.summary;

  const open = rv.findings.filter((x) => x.status === "open");
  const count = (sev) => open.filter((x) => x.severity === sev).length;
  $("#rv-stats").innerHTML = `
    <span class="pill high">${count("high")} high</span>
    <span class="pill medium">${count("medium")} medium</span>
    <span class="pill low">${count("low")} low</span>
    <span class="pill">${rv.findings.length - open.length} resolved</span>`;

  $("#rv-preflight").innerHTML = rv.preflight.map((p) => `
    <div class="pf"><span class="pf-dot ${esc(p.status)}"></span>
      <div><strong>${esc(p.check)}</strong> <span class="code">${esc(p.code)}</span> - ${esc(p.detail)}
      ${p.pages ? `<span class="small muted"> Pages: ${pageLinks(p.pages)}</span>` : ""}</div></div>`).join("") || '<div class="muted">No machine checks.</div>';

  renderFilters(rv);
  renderFindings(rv);

  $("#strengths-card").classList.toggle("hidden", !rv.strengths.length);
  $("#rv-strengths").innerHTML = rv.strengths.map((s) => `<li>${esc(s)}</li>`).join("");

  $("#page-map").innerHTML = rv.page_map.map((p) => `
    <tr><td><span class="page-link" data-page="${p.page}">p.${p.page}</span></td>
    <td>${esc(p.doc_type)}</td><td>${esc(p.document_title)}</td>
    <td>${esc(p.printed_page_number || "-")}</td><td>${esc(p.legibility)}</td>
    <td class="muted">${esc((p.concerns || []).join("; "))}</td></tr>`).join("");

  const u = rv.usage || {};
  const r = u.reader || {}, o = u.reasoner || {};
  $("#rv-usage").textContent = r.input_tokens != null
    ? `Tokens - reader: ${r.input_tokens} in / ${r.output_tokens} out; reasoner: ${o.input_tokens} in / ${o.output_tokens} out.` : "";

  renderChat(rv);
  bindPageLinks();
}

function pageLinks(pages) {
  return esc(pages).replace(/\d+/g, (n) => `<span class="page-link" data-page="${n}">${n}</span>`);
}
function bindPageLinks() {
  document.querySelectorAll(".page-link").forEach((el) => {
    el.onclick = () => window.open(`/api/filings/${state.review.filing.id}/pdf#page=${el.dataset.page}`, "_blank", "noopener");
  });
}

function renderFilters(rv) {
  const groups = [...new Set(rv.findings.map((f) => f.grp))];
  const opts = [["open", "Open"], ["all", "All"], ["high", "High"], ["medium", "Medium"], ["low", "Low"], ["resolved", "Resolved"],
    ...groups.map((g) => [`grp:${g}`, (state.config.groups[g] || g)])];
  $("#filters").innerHTML = opts.map(([k, l]) => `<button class="chip ${state.filter === k ? "on" : ""}" data-f="${esc(k)}">${esc(l)}</button>`).join("");
  document.querySelectorAll("#filters .chip").forEach((c) => c.addEventListener("click", () => { state.filter = c.dataset.f; renderFilters(rv); renderFindings(rv); bindPageLinks(); }));
}

function matches(f) {
  const k = state.filter;
  if (k === "all") return true;
  if (k === "open") return f.status === "open";
  if (k === "resolved") return f.status !== "open";
  if (k.startsWith("grp:")) return f.grp === k.slice(4);
  return f.severity === k && f.status === "open";
}

function renderFindings(rv) {
  const list = rv.findings.filter(matches);
  if (!rv.findings.length) { $("#findings").innerHTML = '<div class="card empty">No objections predicted. Check the machine checks and page map before filing.</div>'; return; }
  if (!list.length) { $("#findings").innerHTML = '<div class="card empty">Nothing in this view.</div>'; return; }
  $("#findings").innerHTML = list.map((f) => `
    <div class="card finding ${esc(f.severity)} ${esc(f.status)}" data-id="${f.id}">
      <div class="finding-head">
        <div>
          <div class="finding-title">${esc(f.title)}</div>
          <div class="finding-meta">
            <span class="code">Code ${esc(f.code)}</span>
            <span class="pill ${esc(f.severity)}">${esc(f.severity)}</span>
            <span class="pill">${esc(f.confidence)}</span>
            ${f.pages ? `<span class="small muted">Pages ${pageLinks(f.pages)}</span>` : ""}
            ${f.status !== "open" ? `<span class="pill done">${esc(f.status)}</span>` : ""}
          </div>
        </div>
      </div>
      <div class="finding-body">
        <div><div class="lbl">What we saw</div>${esc(f.evidence)}</div>
        <div><div class="lbl">How to fix</div>${esc(f.fix)}</div>
        ${f.draft_text ? `<div><div class="lbl">Draft text</div><div class="draft"><button class="btn sm copy">Copy</button>${esc(f.draft_text)}</div></div>` : ""}
        ${f.user_note ? `<div><div class="lbl">Office note</div>${esc(f.user_note)}</div>` : ""}
        <div class="finding-actions">
          ${f.status === "open"
            ? `<button class="btn sm act" data-status="fixed">Mark fixed</button><button class="btn sm act" data-status="dismissed">Dismiss</button>`
            : `<button class="btn sm act" data-status="open">Reopen</button>`}
          <button class="btn sm note">Add note</button>
          <button class="btn sm house">Save as house note</button>
          <button class="btn sm ask">Ask about this</button>
        </div>
      </div>
    </div>`).join("");

  document.querySelectorAll(".finding").forEach((card) => {
    const f = rv.findings.find((x) => x.id === Number(card.dataset.id));
    card.querySelectorAll(".act").forEach((b) => b.addEventListener("click", async () => {
      await api(`/api/findings/${f.id}`, jsonOpts("PATCH", { status: b.dataset.status }));
      refreshReview();
    }));
    card.querySelector(".note").addEventListener("click", async () => {
      const note = prompt("Note for this objection", f.user_note || "");
      if (note === null) return;
      await api(`/api/findings/${f.id}`, jsonOpts("PATCH", { user_note: note }));
      refreshReview();
    });
    card.querySelector(".house").addEventListener("click", async () => {
      const note = prompt(`House note for code ${f.code} (${CAT_LABEL[rv.category]}). Future reviews will follow it.`, f.user_note || "");
      if (!note) return;
      await api("/api/house-notes", jsonOpts("POST", { category: rv.category, code: f.code, note }));
      toast("Saved. It applies to the next review.");
    });
    card.querySelector(".ask").addEventListener("click", () => {
      $("#chat-input").value = `About objection ${f.code} ("${f.title}"): `;
      $("#chat-input").focus();
    });
    const copy = card.querySelector(".copy");
    if (copy) copy.addEventListener("click", async () => { await navigator.clipboard.writeText(f.draft_text); toast("Copied"); });
  });
}

// ------------------------------------------------------------ chat
const SUGGESTIONS = [
  "Which objections will get the file returned outright?",
  "Draft the notes below index for this filing.",
  "Is the memo of parties consistent with the impugned order?",
  "Give me a fix-it checklist for the clerk, in page order.",
];
function renderChat(rv) {
  const log = $("#chat-log");
  if (!rv.chat.length) {
    log.innerHTML = `<div class="muted small">Try one of these:</div><div class="suggestions">${SUGGESTIONS.map((s) => `<button class="chip sugg">${esc(s)}</button>`).join("")}</div>`;
    log.querySelectorAll(".sugg").forEach((b) => b.addEventListener("click", () => sendChat(b.textContent)));
  } else {
    log.innerHTML = rv.chat.map((m) => `<div class="msg ${esc(m.role)}">${esc(m.content)}</div>`).join("");
  }
  log.scrollTop = log.scrollHeight;
}
async function sendChat(text) {
  const msg = (text ?? $("#chat-input").value).trim();
  if (!msg) return;
  const log = $("#chat-log");
  if (!state.review.chat.length) log.innerHTML = "";
  log.insertAdjacentHTML("beforeend", `<div class="msg user">${esc(msg)}</div><div class="msg assistant" id="pending"><span class="spinner"></span> Thinking...</div>`);
  log.scrollTop = log.scrollHeight;
  $("#chat-input").value = ""; $("#chat-send").disabled = true;
  try {
    await api(`/api/reviews/${state.reviewId}/chat`, jsonOpts("POST", { message: msg }));
    await refreshReview();
  } catch (err) {
    $("#pending").textContent = `Error: ${err.message}`; $("#pending").removeAttribute("id");
  } finally { $("#chat-send").disabled = false; }
}
$("#chat-form").addEventListener("submit", (e) => { e.preventDefault(); sendChat(); });
$("#chat-input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); } });

// ------------------------------------------------------------ review actions
$("#back-btn").addEventListener("click", () => { location.hash = "filings"; });
$("#rv-history").addEventListener("change", (e) => { location.hash = `review/${e.target.value}`; });
$("#rerun-btn").addEventListener("click", async () => {
  try {
    const r = await api(`/api/filings/${state.review.filing.id}/reviews`, jsonOpts("POST", { reader: $("#rerun-reader").value }));
    location.hash = `review/${r.review_id}`;
  } catch (err) { toast(err.message); }
});
$("#delete-btn").addEventListener("click", async () => {
  if (!confirm("Delete this filing, its PDF and all its reviews?")) return;
  await api(`/api/filings/${state.review.filing.id}`, { method: "DELETE" });
  location.hash = "filings";
});

// ------------------------------------------------------------ checklists
async function loadChecklist() {
  const cat = $("#cl-category").value;
  const items = await api(`/api/checklists/${cat}`);
  let html = "", grp = null;
  for (const it of items) {
    if (it.grp !== grp) { grp = it.grp; html += `<div class="group-title">${esc(state.config.groups[grp] || grp)}</div>`; }
    html += `<div class="cl-item"><div><span class="code">${esc(it.code)}</span>${it.cis_code ? `<div class="small muted">CIS ${esc(it.cis_code)}</div>` : ""}</div>
      <div>${esc(it.text)}${it.applies_when ? `<div class="cl-when">Applies when: ${esc(it.applies_when)}</div>` : ""}</div></div>`;
  }
  $("#cl-body").innerHTML = html;
}
$("#cl-category").addEventListener("change", loadChecklist);

// ------------------------------------------------------------ house notes
async function loadNotes() {
  const notes = await api("/api/house-notes");
  $("#notes-body").innerHTML = notes.length ? notes.map((n) => `
    <div class="note-row"><div><span class="pill">${esc(CAT_LABEL[n.category] || "All")}</span>
      ${n.code ? `<span class="code">${esc(n.code)}</span>` : ""} ${esc(n.note)}</div>
      <button class="btn sm del" data-id="${n.id}">Remove</button></div>`).join("")
    : '<div class="empty">No house notes yet. Add lessons from objection memos you receive, or save them from a finding.</div>';
  document.querySelectorAll("#notes-body .del").forEach((b) => b.addEventListener("click", async () => {
    await api(`/api/house-notes/${b.dataset.id}`, { method: "DELETE" }); loadNotes();
  }));
}
$("#note-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/house-notes", jsonOpts("POST", { category: $("#note-category").value, code: $("#note-code").value, note: $("#note-text").value }));
    $("#note-form").reset(); loadNotes();
  } catch (err) { toast(err.message); }
});

// ------------------------------------------------------------ boot
(async function boot() {
  state.config = await api("/api/config");
  $("#reader").value = state.config.default_reader;
  $("#model-foot").innerHTML = `Reader: ${esc(state.config.readers.haiku)} / ${esc(state.config.readers.sonnet)}<br>Reasoner: ${esc(state.config.reasoner.model)} (${esc(state.config.reasoner.effort)})`;
  route();
})();
