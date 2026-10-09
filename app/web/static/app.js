/* ================================================================
   Studique QBGen — app.js
   Full rewrite: better state management, real-time job polling,
   drag-drop upload, improved review panel, math-aware preview.
================================================================ */

const state = {
  collections: [],
  collectionId: null,
  docs: [],
  // review state
  rvDocId: null,
  rvDoc: null,
  rvQuestions: [],
  rvMedia: [],
  rvPages: [],
  rvQid: null,
  rvPage: 0,
  // polling
  _pollTimer: null,
  _jobPollTimer: null,
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

/* ── API ─────────────────────────────────────────────────────── */
async function api(url, opts = {}) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return r.json();
}

/* ── TOAST ───────────────────────────────────────────────────── */
function toast(msg, type = "ok", duration = 3500) {
  const icons = { ok: "✅", err: "❌", warn: "⚠️" };
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.innerHTML = `<span>${icons[type] || "ℹ️"}</span><span>${esc(msg)}</span>`;
  $("toast-container").appendChild(el);
  setTimeout(() => el.remove(), duration);
}

/* ── STATUS DOT ──────────────────────────────────────────────── */
async function checkServer() {
  try {
    await fetch("/api/collections", { signal: AbortSignal.timeout(3000) });
    $("server-dot").className = "status-dot";
    $("server-label").textContent = "Online";
  } catch (_) {
    $("server-dot").className = "status-dot off";
    $("server-label").textContent = "Offline";
  }
}

/* ── TABS ────────────────────────────────────────────────────── */
document.querySelectorAll(".tab").forEach((tb) => {
  tb.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".tab-pane").forEach((x) => x.classList.add("hidden"));
    tb.classList.add("active");
    $("tab-" + tb.dataset.tab).classList.remove("hidden");
    if (tb.dataset.tab === "collections") {
      refreshCollections();
      // Restart the document poll if a collection is selected
      if (state.collectionId) startDocPoll(state.collectionId);
    }
    if (tb.dataset.tab === "review")      refreshReview();
    if (tb.dataset.tab === "generate")    refreshGenerate();
    if (tb.dataset.tab === "jobs")        refreshJobs();
  });
});

/* ═══════════════════════════════════════════════════════════════
   COLLECTIONS TAB
═══════════════════════════════════════════════════════════════ */
async function refreshCollections() {
  state.collections = await api("/api/collections");
  renderCollectionList();
  // Update generate dropdown too, preserving current selection
  const sel = $("gen-collection");
  if (sel) {
    const prevVal = sel.value;
    sel.innerHTML = state.collections.map(
      c => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
    if (prevVal && state.collections.some(c => String(c.id) === prevVal)) {
      sel.value = prevVal;
    } else {
      const withDocs = state.collections.find(c => c.document_count > 0);
      if (withDocs) sel.value = String(withDocs.id);
    }
  }
}

function renderCollectionList() {
  const el = $("collection-list");
  if (!state.collections.length) {
    el.innerHTML = `<div class="empty-state">
      <div class="es-icon">📚</div>
      <div class="es-title">No collections</div>
      <div class="es-sub">Create one above to get started</div>
    </div>`;
    return;
  }
  el.innerHTML = state.collections.map(c => `
    <div class="list-item ${c.id === state.collectionId ? "active" : ""}" onclick="selectCollection(${c.id})">
      <span class="li-icon">📁</span>
      <div class="li-body">
        <div class="li-name">${esc(c.name)}</div>
        <div class="li-meta">${c.document_count} paper${c.document_count !== 1 ? "s" : ""} · ${c.created_at.slice(0,10)}</div>
      </div>
      <div class="li-actions">
        <button class="btn sm red icon" title="Delete" onclick="event.stopPropagation();deleteCollection(${c.id})">🗑</button>
      </div>
    </div>`).join("");
}

async function createCollection() {
  const name = $("new-col-name").value.trim();
  if (!name) return toast("Enter a collection name", "err");
  try {
    const c = await api("/api/collections", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({name}),
    });
    $("new-col-name").value = "";
    await refreshCollections();
    await selectCollection(c.id);
    toast("Collection created");
  } catch (e) { toast(e.message, "err"); }
}

async function deleteCollection(id) {
  if (!confirm("Delete this collection and all its papers?")) return;
  try {
    await api("/api/collections/" + id, {method: "DELETE"});
    if (state.collectionId === id) {
      state.collectionId = null; state.docs = [];
      $("col-detail").innerHTML = `<div class="empty-state" style="margin-top:60px">
        <div class="es-icon">👈</div><div class="es-title">Select a collection</div></div>`;
    }
    await refreshCollections();
    toast("Collection deleted");
  } catch (e) { toast(e.message, "err"); }
}

async function selectCollection(id) {
  state.collectionId = id;
  renderCollectionList();
  try {
    const d = await api("/api/collections/" + id);
    state.docs = d.documents;
    renderColDetail(d);
    startDocPoll(id);
  } catch (e) { toast(e.message, "err"); }
}

function renderColDetail(col) {
  const el = $("col-detail");

  const processing = col.documents.filter(d => ["pending","processing","running"].includes(d.status)).length;
  const done = col.documents.filter(d => d.status === "done").length;
  const total = col.documents.length;

  el.innerHTML = `
    <div style="margin-bottom:14px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">
      <div class="section-title" style="margin-bottom:0">📂 ${esc(col.name)}</div>
      <div style="margin-left:auto;display:flex;gap:6px">
        <button class="btn sm primary" onclick="triggerUpload()">＋ Upload Papers</button>
      </div>
    </div>
    ${processing > 0 ? `<div class="info-box warn" style="margin-bottom:12px">
      ⏳ ${processing} paper${processing>1?"s":""} processing… <button class="btn sm amber" onclick="switchTab('jobs')" style="margin-left:6px">View Jobs</button>
    </div>` : ""}
    <div class="stats-row">
      <div class="stat-chip"><div class="stat-val">${total}</div><div class="stat-lbl">Papers</div></div>
      <div class="stat-chip"><div class="stat-val">${done}</div><div class="stat-lbl">Ready</div></div>
      <div class="stat-chip"><div class="stat-val">${col.documents.reduce((a,d)=>a+(d.question_count||0),0)}</div><div class="stat-lbl">Questions</div></div>
    </div>

    <!-- Dropzone -->
    <div class="dropzone" id="col-dropzone"
      onclick="triggerUpload()"
      ondragover="event.preventDefault();this.classList.add('dragover')"
      ondragleave="this.classList.remove('dragover')"
      ondrop="handleDrop(event)">
      <div class="dz-icon">📄</div>
      <div><b>Click or drag &amp; drop PDF files here</b></div>
      <div style="margin-top:4px;font-size:12px">Multiple files supported · Exam question papers only</div>
    </div>
    <input type="file" id="file-input" multiple accept=".pdf" onchange="handleFileInput(event)">
    <div id="upload-status" class="status-msg"></div>

    <div style="margin-top:16px">
      <div style="font-weight:700;font-size:13px;margin-bottom:8px;display:flex;align-items:center;gap:8px">
        Papers
        ${total > 0 ? `<span class="badge ${processing>0?"processing":"done"}">${processing>0?"Processing…":"All Ready"}</span>` : ""}
      </div>
      ${renderDocList(col.documents)}
    </div>`;
}

function renderDocList(docs) {
  if (!docs.length) return `<div class="empty-state"><div class="es-icon">📭</div><div class="es-sub">No papers uploaded yet</div></div>`;
  return docs.map(d => `
    <div class="list-item">
      <span class="li-icon">${d.status === "done" ? "✅" : d.status === "error" ? "❌" : "⏳"}</span>
      <div class="li-body">
        <div class="li-name">${esc(d.filename)}</div>
        <div class="li-meta">
          ${d.subject_code ? esc(d.subject_code) + " · " : ""}${esc(d.subject_name || "Subject TBD")}
          ${d.year ? " · " + d.year : ""} · ${d.question_count || 0} questions
          ${d.error ? ` · <span style="color:var(--red)">${esc(d.error.slice(0,60))}</span>` : ""}
        </div>
      </div>
      <div class="li-actions">
        <span class="badge ${esc(d.status)}">${esc(d.status)}</span>
        ${d.status === "done" ? `<button class="btn sm" onclick="reviewDoc(${d.id})" title="Review questions">🔍</button>` : ""}
        ${d.status !== "processing" ? `<button class="btn sm amber" onclick="reprocessDoc(${d.id})" title="Reprocess">↻</button>` : ""}
        <button class="btn sm red" onclick="deleteDoc(${d.id})" title="Delete">🗑</button>
      </div>
    </div>`).join("");
}

function triggerUpload() {
  if (!state.collectionId) return toast("Select a collection first", "err");
  $("file-input").click();
}

async function handleDrop(ev) {
  ev.preventDefault();
  ev.currentTarget.classList.remove("dragover");
  if (!state.collectionId) return toast("Select a collection first", "err");
  const files = Array.from(ev.dataTransfer.files).filter(f => f.name.endsWith(".pdf"));
  if (!files.length) return toast("Only PDF files are supported", "warn");
  await uploadFiles(files);
}

async function handleFileInput(ev) {
  const files = Array.from(ev.target.files);
  ev.target.value = "";
  await uploadFiles(files);
}

async function uploadFiles(files) {
  if (!files.length || !state.collectionId) return;
  const statusEl = $("upload-status");
  statusEl.textContent = `Uploading ${files.length} file(s)…`;
  statusEl.className = "status-msg";
  const fd = new FormData();
  files.forEach(f => fd.append("files", f));
  try {
    const r = await api("/api/collections/" + state.collectionId + "/documents", {method:"POST",body:fd});
    statusEl.textContent = `✓ ${r.uploaded.length} file(s) uploaded. Processing started.`;
    statusEl.className = "status-msg ok";
    toast(`${r.uploaded.length} paper(s) queued — processing in background`);
    await refreshColDocuments();
    // Restart poll (it may have been stopped if previous docs were all done)
    startDocPoll(state.collectionId);
  } catch (e) {
    statusEl.textContent = "Upload failed: " + e.message;
    statusEl.className = "status-msg err";
    toast(e.message, "err");
  }
}

async function refreshColDocuments() {
  if (!state.collectionId) return;
  try {
    const d = await api("/api/collections/" + state.collectionId);
    state.docs = d.documents;
    renderColDetail(d);
  } catch (_) {}
}

async function reprocessDoc(did) {
  try {
    await api("/api/documents/" + did + "/reprocess", {method:"POST"});
    toast("Reprocessing queued");
    setTimeout(refreshColDocuments, 600);
    switchTab("jobs");
  } catch (e) { toast(e.message, "err"); }
}

async function deleteDoc(did) {
  if (!confirm("Delete this paper and all its extracted questions?")) return;
  try {
    await api("/api/documents/" + did, {method:"DELETE"});
    toast("Paper deleted");
    await refreshColDocuments();
  } catch (e) { toast(e.message, "err"); }
}

function reviewDoc(did) {
  state.rvDocId = did;
  switchTab("review");
  loadRvDoc(did);
}

function startDocPoll(cid) {
  clearInterval(state._pollTimer);
  state._pollTimer = setInterval(async () => {
    // Stop polling only if the collection was changed, not on tab-switch
    if (state.collectionId !== cid) return clearInterval(state._pollTimer);
    try {
      const d = await api("/api/collections/" + cid);
      const hasInProgress = d.documents.some(x => ["pending","processing","running"].includes(x.status));
      const changed = d.documents.some((nd, i) => {
        const od = (state.docs || [])[i];
        return !od || nd.status !== od.status || nd.question_count !== od.question_count;
      });
      if (changed || hasInProgress) {
        state.docs = d.documents;
        // Only re-render collection detail if we're on the collections tab
        const onCollections = !$("tab-collections").classList.contains("hidden");
        if (onCollections) renderColDetail(d);
      }
      // Stop polling once all docs are in a terminal state
      if (!hasInProgress) clearInterval(state._pollTimer);
    } catch (_) {}
  }, 5000);
}

/* ═══════════════════════════════════════════════════════════════
   REVIEW TAB
═══════════════════════════════════════════════════════════════ */
async function refreshReview() {
  try {
    const cols = await api("/api/collections");
    // Fetch all collection details in parallel instead of serially
    const colDetails = await Promise.all(cols.map(c => api("/api/collections/" + c.id)));
    const allDocs = [];
    cols.forEach((c, i) => {
      colDetails[i].documents.filter(d => d.status === "done").forEach(d => {
        allDocs.push({...d, colName: c.name});
      });
    });
    const sel = $("rv-doc-select");
    if (!allDocs.length) {
      sel.innerHTML = `<option value="">— No processed papers —</option>`;
      $("rv-qlist").innerHTML = `<div class="empty-state"><div class="es-icon">📋</div>
        <div class="es-title">No questions yet</div>
        <div class="es-sub">Upload &amp; process a PDF in the Collections tab first</div></div>`;
      return;
    }
    sel.innerHTML = allDocs.map(d =>
      `<option value="${d.id}" ${d.id === state.rvDocId ? "selected" : ""}>${esc(d.filename)} (${esc(d.subject_code || d.colName)})</option>`
    ).join("");
    // Load the previously selected doc or default to the first
    const targetId = (state.rvDocId && allDocs.find(d => d.id === state.rvDocId))
      ? state.rvDocId
      : allDocs[0].id;
    await loadRvDoc(targetId);
  } catch (e) { toast(e.message, "err"); }
}

async function onRvDocChange() {
  const did = parseInt($("rv-doc-select").value);
  if (did) await loadRvDoc(did);
}

async function loadRvDoc(did) {
  state.rvDocId = did;
  state.rvQid = null;
  try {
    const d = await api("/api/documents/" + did);
    state.rvDoc = d;
    state.rvQuestions = d.questions;
    state.rvMedia = d.media;
    state.rvPages = d.pages;
    state.rvPage = d.pages.length ? d.pages[0].page_number : 0;
    renderRvStats();
    renderRvQList();
    $("rv-detail-panel").innerHTML = `<div class="empty-state" style="margin-top:60px">
      <div class="es-icon">⬅️</div><div class="es-title">Select a question to review</div></div>`;
  } catch (e) { toast(e.message, "err"); }
}

function renderRvStats() {
  const qs = state.rvQuestions;
  const counts = {approved:0, review:0, rejected:0, pending:0};
  qs.forEach(q => { counts[q.status] = (counts[q.status] || 0) + 1; });
  $("rv-stats").innerHTML = `
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px">
      <span class="badge done">✓ ${counts.approved} approved</span>
      <span class="badge review">⚠ ${counts.review} review</span>
      <span class="badge rejected">✗ ${counts.rejected} rejected</span>
    </div>
    <div class="progress-bar"><div class="progress-fill" style="width:${qs.length ? Math.round(counts.approved/qs.length*100) : 0}%"></div></div>`;
}

function renderRvQList() {
  const el = $("rv-qlist");
  const qs = state.rvQuestions;
  if (!qs.length) {
    el.innerHTML = `<div class="empty-state"><div class="es-icon">📋</div><div class="es-title">No questions extracted</div><div class="es-sub">Upload a real exam question paper PDF</div></div>`;
    return;
  }

  // Filter controls
  const filterHtml = `<div style="display:flex;gap:6px;margin-bottom:8px;flex-wrap:wrap">
    <select id="rv-filter" onchange="applyRvFilter()" style="flex:1;font-size:12px;padding:5px 8px">
      <option value="all">All Questions</option>
      <option value="approved">Approved</option>
      <option value="review">Needs Review</option>
      <option value="rejected">Rejected</option>
    </select>
    <select id="rv-part-filter" onchange="applyRvFilter()" style="flex:1;font-size:12px;padding:5px 8px">
      <option value="">All Parts</option>
      ${[...new Set(qs.map(q=>q.part).filter(Boolean))].sort().map(p=>`<option value="${p}">Part ${esc(p)}</option>`).join("")}
    </select>
  </div>`;

  el.innerHTML = filterHtml + `<div id="rv-qlist-items">${buildQListItems(qs)}</div>`;
}

function applyRvFilter() {
  const status = $("rv-filter")?.value || "all";
  const part = $("rv-part-filter")?.value || "";
  let qs = state.rvQuestions;
  if (status !== "all") qs = qs.filter(q => q.status === status);
  if (part) qs = qs.filter(q => q.part === part);
  const el = $("rv-qlist-items");
  if (el) el.innerHTML = buildQListItems(qs);
}

function buildQListItems(qs) {
  if (!qs.length) return `<div class="empty-state"><div class="es-sub">No questions match filter</div></div>`;
  return qs.map(q => {
    const conf = q.confidence?.overall ? Math.round(q.confidence.overall * 100) : "?";
    const textPreview = q.text.replace(/\\[\(\[\)\]]/g,"").replace(/\\.+?\{.*?\}/g,"(eq)").slice(0,60);
    return `<div class="q-item ${q.id === state.rvQid ? "active" : ""}" onclick="selectRvQ(${q.id})">
      <div class="q-item-head">
        <span class="q-num">${esc(q.number || "?")}</span>
        ${q.part ? `<span class="badge" style="background:var(--accent-soft);color:var(--accent);border-color:#fe6e0033">Part ${esc(q.part)}</span>` : ""}
        <span class="badge ${esc(q.status)}" style="margin-left:auto">${esc(q.status)}</span>
      </div>
      <div class="q-text-preview">${esc(textPreview)}${q.text.length > 60 ? "…" : ""}</div>
      <div class="q-item-meta" style="margin-top:4px">
        ${q.marks != null ? `<span class="pill">${q.marks}m</span>` : ""}
        <span class="pill" title="Confidence">🎯 ${conf}%</span>
        ${q.flags?.length ? `<span class="pill" style="color:var(--amber)">⚠ ${q.flags.length}</span>` : ""}
        ${q.subs?.length ? `<span class="pill">${q.subs.length} sub${q.subs.length>1?"s":""}</span>` : ""}
        ${q.options?.length ? `<span class="pill">MCQ</span>` : ""}
      </div>
    </div>`;
  }).join("");
}

function selectRvQ(qid) {
  state.rvQid = qid;
  // Refresh active state in list
  document.querySelectorAll(".q-item").forEach(el => el.classList.remove("active"));
  const active = document.querySelector(`.q-item[onclick="selectRvQ(${qid})"]`);
  if (active) active.classList.add("active");
  renderRvDetail();
}

function getQ() { return state.rvQuestions.find(q => q.id === state.rvQid); }

function renderRvDetail() {
  const q = getQ();
  const el = $("rv-detail-panel");
  if (!q) { el.innerHTML = ""; return; }

  const conf = q.confidence || {};
  const ovConf = Math.round((conf.overall ?? 0) * 100);
  const confColor = ovConf >= 80 ? "var(--green)" : ovConf >= 50 ? "var(--amber)" : "var(--red)";

  el.innerHTML = `
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;flex-wrap:wrap">
    <div style="font-weight:800;font-size:15px">Q ${esc(q.number)} — Part ${esc(q.part || "?")}</div>
    <span class="badge ${esc(q.status)}">${esc(q.status)}</span>
    <div style="margin-left:auto;display:flex;gap:6px">
      <button class="btn sm green" onclick="setQStatus('approved')">✓ Approve</button>
      <button class="btn sm amber" onclick="setQStatus('review')">⚠ Review</button>
      <button class="btn sm red" onclick="setQStatus('rejected')">✗ Reject</button>
    </div>
  </div>

  ${q.flags?.length ? `<div style="margin-bottom:10px">${q.flags.map(f=>`<span class="flag">⚠ ${esc(f.reason||f)}</span> `).join("")}</div>` : ""}

  <div class="grid2" style="gap:14px">
    <div>
      <div class="label" style="margin-top:0">Number</div>
      <div class="form-row">
        <input id="e-number" value="${esc(q.number)}" placeholder="1">
        <input id="e-part"   value="${esc(q.part)}"   placeholder="A" style="max-width:60px">
        <input id="e-marks" type="number" value="${q.marks ?? ""}" placeholder="Marks" style="max-width:80px">
      </div>

      <div class="label">Question Text</div>
      <textarea id="e-text" rows="5">${esc(q.text)}</textarea>
      <div class="math-hint">💡 Use \\( ... \\) for inline math, \\[ ... \\] for display equations</div>

      ${q.options?.length || true ? `
      <div class="label" style="margin-top:10px">Options (leave empty if not MCQ)</div>
      <div id="e-options">${renderOptionsHTML(q.options||[])}</div>
      <button class="btn sm" onclick="addOpt()" style="margin-top:4px">＋ Option</button>
      ` : ""}
    </div>

    <div>
      <div class="label" style="margin-top:0">Sub-questions</div>
      <div id="e-subs">${renderSubsHTML(q.subs||[])}</div>
      <button class="btn sm" onclick="addSub()" style="margin-top:4px">＋ Sub-question</button>

      <div class="label" style="margin-top:12px">Confidence</div>
      ${confBar("Overall", conf.overall)}
      ${confBar("OCR", conf.ocr)}
      ${confBar("Segmentation", conf.question_segmentation)}

      ${state.rvMedia.length ? `
      <div class="label" style="margin-top:12px">Media (click to attach/detach)</div>
      <div class="media-grid" id="e-media">${renderMediaPickerHTML(q.media_ids||[])}</div>
      ` : ""}
    </div>
  </div>

  <div class="btn-row" style="margin-top:14px">
    <button class="btn primary" onclick="saveQuestion()">💾 Save Changes</button>
    <button class="btn red sm" onclick="deleteQuestion()">🗑 Delete</button>
    <span id="e-saved" class="status-msg" style="margin-left:4px"></span>
  </div>

  <hr style="margin:14px 0">

  <!-- Page preview -->
  <div>
    <div style="font-weight:700;font-size:12px;color:var(--muted);margin-bottom:8px">📄 PAGE PREVIEW</div>
    <div class="page-strip">
      ${state.rvPages.map(p=>`<span class="page-btn ${p.page_number===state.rvPage?"active":""}" onclick="setRvPage(${p.page_number})">${p.page_number+1}</span>`).join("")}
    </div>
    ${renderPageImg()}
  </div>`;
}

function confBar(label, val) {
  const pct = Math.round((val ?? 0) * 100);
  const color = pct >= 80 ? "var(--green)" : pct >= 50 ? "var(--amber)" : "var(--red)";
  return `<div class="conf-row">
    <div class="conf-label">${label}</div>
    <div class="conf-bar"><div class="conf-fill" style="width:${pct}%;background:${color}"></div></div>
    <div class="conf-val">${pct}%</div>
  </div>`;
}

function renderPageImg() {
  const p = state.rvPages.find(p => p.page_number === state.rvPage);
  return p ? `<img class="page-img" src="${esc(p.render_url)}" alt="Page ${p.page_number+1}">` : "";
}

function setRvPage(n) {
  state.rvPage = n;
  // Re-render just the page strip + image
  document.querySelectorAll(".page-btn").forEach(el => {
    el.classList.toggle("active", parseInt(el.textContent) - 1 === n);
  });
  const imgs = document.querySelectorAll(".page-img");
  const p = state.rvPages.find(p => p.page_number === n);
  if (imgs.length && p) imgs[imgs.length-1].src = p.render_url;
}

function renderOptionsHTML(opts) {
  if (!opts.length) return `<div style="color:var(--muted2);font-size:12px;padding:4px 0">No options — this is a descriptive question</div>`;
  return opts.map((o, i) => `<div class="opt-row">
    <span class="opt-lbl">${String.fromCharCode(65+i)}.</span>
    <input class="opt-in" value="${esc(o)}" placeholder="Option text">
    <button class="btn sm red icon" onclick="removeOptRow(this)" style="padding:4px 7px">×</button>
  </div>`).join("");
}

function addOpt() {
  const opts = collectOpts();
  $("e-options").innerHTML = renderOptionsHTML([...opts, ""]);
}

function removeOptRow(btn) { btn.closest(".opt-row").remove(); }
function collectOpts() { return Array.from(document.querySelectorAll("#e-options .opt-in")).map(i=>i.value); }

function renderSubsHTML(subs) {
  if (!subs.length) return `<div style="color:var(--muted2);font-size:12px;padding:4px 0">No sub-questions</div>`;
  return subs.map((s,i) => `<div class="sub-row">
    <input class="sub-lbl" placeholder="(a)" value="${esc(s.label||"")}">
    <input class="sub-txt" placeholder="Sub-question text" value="${esc(s.text||"")}">
    <input class="sub-mk" type="number" placeholder="m" value="${s.marks??""}" title="Marks">
    <label class="sub-or" title="OR alternative">
      <input type="checkbox" class="or-chk" ${s.is_or_alternative?"checked":""}> OR
    </label>
    <button class="btn sm red icon" onclick="removeSubRow(this)" style="padding:4px 7px">×</button>
  </div>`).join("");
}

function addSub() {
  const subs = collectSubs();
  $("e-subs").innerHTML = renderSubsHTML([...subs, {label:"",text:"",marks:null,is_or_alternative:false}]);
}

function removeSubRow(btn) { btn.closest(".sub-row").remove(); }
function collectSubs() {
  return Array.from(document.querySelectorAll("#e-subs .sub-row")).map(r => ({
    label: r.querySelector(".sub-lbl").value,
    text:  r.querySelector(".sub-txt").value,
    marks: r.querySelector(".sub-mk").value ? parseInt(r.querySelector(".sub-mk").value) : null,
    is_or_alternative: r.querySelector(".or-chk").checked,
  }));
}

function renderMediaPickerHTML(attachedIds) {
  const set = new Set(attachedIds);
  if (!state.rvMedia.length) return `<span style="color:var(--muted2);font-size:12px">No media detected</span>`;
  return state.rvMedia.map(m => `
    <div class="media-thumb ${set.has(m.id)?"attached":""}" onclick="toggleMedia(${m.id})" title="${esc(m.type)}">
      <img src="${esc(m.thumb_url)}" alt="${esc(m.type)}">
      <span class="mt-tag">${esc(m.type)}</span>
    </div>`).join("");
}

function toggleMedia(mid) {
  const q = getQ();
  const set = new Set(q.media_ids || []);
  set.has(mid) ? set.delete(mid) : set.add(mid);
  q.media_ids = [...set];
  const el = $("e-media");
  if (el) el.innerHTML = renderMediaPickerHTML(q.media_ids);
}

async function saveQuestion() {
  const q = getQ();
  if (!q) return;
  const body = {
    number: $("e-number").value,
    part: $("e-part").value,
    marks: $("e-marks").value ? parseInt($("e-marks").value) : null,
    text: $("e-text").value,
    options: collectOpts().filter(o=>o.trim()),
    subs: collectSubs(),
    media_ids: q.media_ids,
  };
  try {
    await api("/api/questions/" + q.id, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify(body),
    });
    Object.assign(q, body);
    $("e-saved").textContent = "✓ Saved";
    $("e-saved").className = "status-msg ok";
    setTimeout(() => { if ($("e-saved")) $("e-saved").textContent = ""; }, 2500);
    renderRvStats();
    applyRvFilter();
    toast("Question saved");
  } catch (e) { toast(e.message, "err"); }
}

async function setQStatus(status) {
  const q = getQ();
  if (!q) return;
  try {
    await api("/api/questions/" + q.id + "/status", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({status}),
    });
    q.status = status;
    renderRvStats();
    applyRvFilter();
    renderRvDetail();
    toast("Status updated to: " + status);
  } catch (e) { toast(e.message, "err"); }
}

async function deleteQuestion() {
  if (!confirm("Delete this question permanently?")) return;
  try {
    await api("/api/questions/" + state.rvQid, {method:"DELETE"});
    state.rvQuestions = state.rvQuestions.filter(q => q.id !== state.rvQid);
    state.rvQid = null;
    renderRvStats();
    renderRvQList();
    $("rv-detail-panel").innerHTML = `<div class="empty-state" style="margin-top:60px">
      <div class="es-icon">⬅️</div><div class="es-title">Select a question</div></div>`;
    toast("Question deleted");
  } catch (e) { toast(e.message, "err"); }
}

/* ═══════════════════════════════════════════════════════════════
   GENERATE TAB
═══════════════════════════════════════════════════════════════ */
async function refreshGenerate() {
  try {
    const cols = await api("/api/collections");
    state.collections = cols;
    const sel = $("gen-collection");
    if (!cols.length) {
      sel.innerHTML = `<option value="">— No collections —</option>`;
    } else {
      const prevVal = sel.value;
      sel.innerHTML = cols.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
      // Preserve existing selection if still valid
      if (prevVal && cols.some(c => String(c.id) === prevVal)) {
        sel.value = prevVal;
      } else {
        // Default to first collection that has documents; fall back to first entry
        const withDocs = cols.find(c => c.document_count > 0);
        if (withDocs) sel.value = String(withDocs.id);
      }
    }
    // Auto-fill metadata from the selected collection
    await autoFillGenMeta(parseInt(sel.value));
  } catch (_) {}
  await refreshBankList();
}

async function autoFillGenMeta(cid) {
  if (!cid) return;
  try {
    const col = await api("/api/collections/" + cid);
    const docs = (col.documents || []).filter(d => d.status === "done");
    const hint = $("gen-col-hint");
    if (!docs.length) {
      if (hint) hint.textContent = "⚠ No processed documents in this collection";
      return;
    }
    const totalDocs = col.documents ? col.documents.length : 0;
    if (hint) hint.textContent = `${docs.length} processed paper${docs.length !== 1 ? "s" : ""} · ${totalDocs} total`;
    const d = docs[0];
    if (d.subject_name && !$("gen-subject-name").value)
      $("gen-subject-name").value = d.subject_name;
    if (d.subject_code && !$("gen-subject-code").value)
      $("gen-subject-code").value = d.subject_code;
    if (d.semester && !$("gen-semester").value)
      $("gen-semester").value = d.semester;
    if (d.degree && !$("gen-course").value)
      $("gen-course").value = d.degree;
  } catch (_) {}
}

async function refreshBankList() {
  try {
    const banks = await api("/api/banks");
    const el = $("bank-list");
    if (!banks.length) {
      el.innerHTML = `<div class="empty-state"><div class="es-icon">📄</div><div class="es-title">No banks generated yet</div></div>`;
      return;
    }
    el.innerHTML = banks.map(b => `
      <div class="bank-row">
        <span style="font-size:18px">📄</span>
        <div class="bank-info">
          <div class="bank-name">${esc(b.name)}</div>
          <div class="bank-meta">
            ${b.question_count} questions · ${esc(b.template)} ·
            ${b.created_at.slice(0,16).replace("T"," ")}
          </div>
        </div>
        <div class="bank-actions">
          ${b.url ? `<a class="btn sm primary" href="${esc(b.url)}" download>⬇ Download PDF</a>` : ""}
        </div>
      </div>`).join("");
  } catch (_) {}
}

async function generateBank() {
  const cid = parseInt($("gen-collection").value);
  if (!cid) return toast("Select a collection first", "err");
  const config = {
    bank_title:   $("gen-title").value || "QUESTION BANK",
    subject_name: $("gen-subject-name").value,
    subject_code: $("gen-subject-code").value,
    course:       $("gen-course").value,
    semester:     $("gen-semester").value,
    coverage:     $("gen-coverage").value || "All Units",
    group_by:     $("gen-group").value,
    notes:        $("gen-notes").value,
  };
  const statusEl = $("gen-status");
  statusEl.textContent = "Queuing…";
  statusEl.className = "status-msg";
  try {
    const r = await api("/api/collections/" + cid + "/generate", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify(config),
    });
    statusEl.textContent = `✓ Job #${r.job_id} queued. Generating PDF…`;
    statusEl.className = "status-msg ok";
    toast("Bank generation queued");
    pollForBank(r.job_id);
  } catch (e) {
    statusEl.textContent = "Error: " + e.message;
    statusEl.className = "status-msg err";
    toast(e.message, "err");
  }
}

async function pollForBank(jobId) {
  const statusEl = $("gen-status");
  let attempts = 0;
  const t = setInterval(async () => {
    attempts++;
    try {
      const jobs = await api("/api/jobs");
      const job = jobs.find(j => j.id === jobId);
      if (!job) return clearInterval(t);
      if (job.status === "done") {
        clearInterval(t);
        statusEl.textContent = "✓ PDF generated!";
        statusEl.className = "status-msg ok";
        toast("Question Bank PDF ready! Check Generated Banks.", "ok", 5000);
        await refreshBankList();
      } else if (job.status === "error") {
        clearInterval(t);
        statusEl.textContent = "Error: " + (job.error || "Generation failed");
        statusEl.className = "status-msg err";
        toast("PDF generation failed: " + (job.error || "unknown error"), "err");
      }
    } catch (_) {}
    if (attempts > 60) clearInterval(t);
  }, 5000);
}

async function generateAnswers() {
  const cid = parseInt($("gen-collection").value);
  if (!cid) return toast("Select a collection first", "err");
  const statusEl = $("gen-status");
  try {
    const r = await api("/api/collections/" + cid + "/answers/generate", {method:"POST"});
    statusEl.textContent = `Answer generation job #${r.job_id} queued. Check Jobs tab for progress.`;
    statusEl.className = "status-msg ok";
    toast("Answer generation queued");
  } catch (e) { toast(e.message, "err"); }
}

async function generateAnswerKey() {
  const cid = parseInt($("gen-collection").value);
  if (!cid) return toast("Select a collection first", "err");
  const config = {
    bank_title:   "ANSWER KEY",
    subject_name: $("gen-subject-name").value,
    subject_code: $("gen-subject-code").value,
    course:       $("gen-course").value,
    semester:     $("gen-semester").value,
    coverage:     $("gen-coverage").value || "All Units",
    group_by:     $("gen-group").value,
    notes:        $("gen-notes").value,
  };
  const statusEl = $("gen-status");
  try {
    const r = await api("/api/collections/" + cid + "/answerkey/generate", {
      method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(config),
    });
    statusEl.textContent = `Answer Key job #${r.job_id} queued. Generate answers first if not done.`;
    statusEl.className = "status-msg ok";
    toast("Answer Key queued");
    pollForBank(r.job_id);
  } catch (e) { toast(e.message, "err"); }
}

/* ═══════════════════════════════════════════════════════════════
   JOBS TAB
═══════════════════════════════════════════════════════════════ */
async function refreshJobs() {
  try {
    const jobs = await api("/api/jobs");
    const el = $("jobs-list");
    if (!jobs.length) {
      el.innerHTML = `<div class="empty-state"><div class="es-icon">✅</div><div class="es-title">No jobs</div></div>`;
      return;
    }

    const typeIcon = {
      process_document: "📄",
      generate_bank: "⚡",
      generate_answers: "🤖",
      generate_answerkey: "📋",
    };

    el.innerHTML = jobs.map(j => {
      const icon = typeIcon[j.type] || "⚙️";
      const dur = j.updated_at
        ? Math.round((new Date(j.updated_at) - new Date(j.created_at)) / 1000)
        : null;
      return `<div class="job-row">
        <span style="font-size:18px">${icon}</span>
        <div class="job-info">
          <div class="job-title">#${j.id} ${esc(j.type.replace(/_/g," "))}</div>
          <div class="job-meta">
            ${j.created_at.slice(0,16).replace("T"," ")}
            ${dur != null && j.status === "done" ? ` · took ${dur}s` : ""}
            ${j.error ? ` · <span style="color:var(--red)">${esc(j.error.slice(0,80))}</span>` : ""}
          </div>
        </div>
        <span class="badge ${esc(j.status)}">${esc(j.status)}</span>
      </div>`;
    }).join("");

    // Auto-refresh if any jobs are in progress
    const inProgress = jobs.some(j => ["pending","running","processing","queued"].includes(j.status));
    if (inProgress) {
      clearTimeout(state._jobPollTimer);
      state._jobPollTimer = setTimeout(() => refreshJobs(), 4000);
    }
  } catch (e) { toast(e.message, "err"); }
}

/* ═══════════════════════════════════════════════════════════════
   UTILITIES
═══════════════════════════════════════════════════════════════ */
function switchTab(name) {
  const tab = document.querySelector(`.tab[data-tab="${name}"]`);
  if (tab) tab.click();
}

/* ═══════════════════════════════════════════════════════════════
   INIT
═══════════════════════════════════════════════════════════════ */
(async function init() {
  checkServer();
  setInterval(checkServer, 30000);

  await refreshCollections();
  if (state.collections.length) {
    await selectCollection(state.collections[0].id);
  }
})();
