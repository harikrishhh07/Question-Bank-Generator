/* Studique QBGen - single user review UI */

const state = {
  collections: [],
  collectionId: null,
  docs: [],
  doc: null,          // selected document detail
  questions: [],
  media: [],
  pages: [],
  questionId: null,
  page: 0,
  polls: [],
};

const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(url, opts = {}) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  return res.json();
}

function toast(msg, type = "ok") {
  const t = document.createElement("div");
  t.className = "toast " + type;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3500);
}

/* ---------------- tabs ---------------- */
document.querySelectorAll(".tab").forEach((tb) => {
  tb.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".tab-pane").forEach((x) => x.classList.add("hidden"));
    tb.classList.add("active");
    $("tab-" + tb.dataset.tab).classList.remove("hidden");
    if (tb.dataset.tab === "collections") refreshCollections();
    if (tb.dataset.tab === "generate") refreshGenerate();
    if (tb.dataset.tab === "jobs") refreshJobs();
  });
});

/* ---------------- collections ---------------- */
async function refreshCollections() {
  state.collections = await api("/api/collections");
  const list = $("collection-list");
  list.innerHTML = state.collections.length
    ? state.collections.map((c) => `
      <div class="doc-item ${c.id === state.collectionId ? "selected" : ""}" onclick="selectCollection(${c.id})">
        <div style="flex:1">
          <div><b>${esc(c.name)}</b></div>
          <div class="qmeta">${c.document_count} document(s) &middot; ${esc(c.created_at.slice(0, 10))}</div>
        </div>
        <button class="btn small red" onclick="event.stopPropagation();deleteCollection(${c.id})">Delete</button>
      </div>`).join("")
    : '<div class="status-line">No collections yet. Create one to begin.</div>';

  // generate dropdown
  const sel = $("gen-collection");
  sel.innerHTML = state.collections.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
}

async function createCollection() {
  const name = $("new-col-name").value.trim();
  if (!name) return toast("Enter a name", "err");
  const c = await api("/api/collections", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  $("new-col-name").value = "";
  state.collectionId = c.id;
  await selectCollection(c.id);
  toast("Collection created");
}

async function deleteCollection(id) {
  if (!confirm("Delete this collection and all its documents?")) return;
  await api("/api/collections/" + id, { method: "DELETE" });
  if (state.collectionId === id) { state.collectionId = null; state.docs = []; $("docs-list").innerHTML = ""; }
  await refreshCollections();
}

async function selectCollection(id) {
  state.collectionId = id;
  await refreshCollections();
  const d = await api("/api/collections/" + id);
  state.docs = d.documents;
  $("col-title").textContent = "Papers in: " + d.name;
  renderDocs();
}

function renderDocs() {
  const list = $("docs-list");
  list.innerHTML = state.docs.length
    ? state.docs.map((d) => `
      <div class="doc-item" onclick="selectDoc(${d.id})">
        <div style="flex:1">
          <div><b>${esc(d.filename)}</b> <span class="badge ${esc(d.status)}">${esc(d.status)}</span></div>
          <div class="qmeta">
            ${d.subject_code ? esc(d.subject_code) + " &middot; " : ""}${esc(d.subject_name || "subject TBD")}
            ${d.year ? " &middot; " + d.year : ""} &middot; ${d.question_count || 0} questions
          </div>
        </div>
      </div>`).join("")
    : '<div class="status-line">No papers uploaded.</div>';
}

async function refreshReviewSelect() {
  const sel = $("rv-doc-select");
  sel.innerHTML = state.docs.map((d) => `<option value="${d.id}">${esc(d.filename)} (${d.subject_code || "?"})</option>`).join("");
  if (state.docs.length) {
    const did = parseInt(sel.value);
    await selectDoc(did);
  }
}

async function selectDoc(did) {
  state.doc = await api("/api/documents/" + did);
  state.questions = state.doc.questions;
  state.media = state.doc.media;
  state.pages = state.doc.pages;
  state.questionId = null;
  state.page = state.pages.length ? state.pages[0].page_number : 0;
  renderQList();
  renderDetail();
  renderPage();
}

function renderQList() {
  const list = $("rv-qlist");
  const counts = { approved: 0, review: 0, rejected: 0, pending: 0 };
  state.questions.forEach((q) => { counts[q.status] = (counts[q.status] || 0) + 1; });
  $("rv-qcount").textContent = `${state.questions.length} total · ${counts.approved || 0} approved · ${counts.review || 0} review`;

  const warnings = (state.doc.warnings || []).map((w) => `<span class="flag">[${esc(w.code || "")}] ${esc(w.reason || "")}</span>`).join("");
  list.innerHTML = (warnings ? `<div style="margin-bottom:8px">${warnings}</div>` : "") +
    state.questions.map((q) => {
    const conf = q.confidence && q.confidence.overall ? Math.round(q.confidence.overall * 100) : "?";
    return `
    <div class="q-item ${q.id === state.questionId ? "selected" : ""}" onclick="selectQuestion(${q.id})">
      <div class="row" style="justify-content:space-between">
        <span class="qnum">${esc(q.number || "?")}</span>
        <span class="badge ${esc(q.status)}">${esc(q.status)}</span>
      </div>
      <div class="qmeta">Part ${esc(q.part || "?")} &middot; ${q.marks ?? "?"} marks &middot; conf ${conf}%${q.flags && q.flags.length ? " &middot; " + q.flags.length + " flag(s)" : ""}</div>
    </div>`;
  }).join("") || '<div class="status-line">No questions yet.</div>';
}

function selectQuestion(qid) {
  state.questionId = qid;
  renderQList();
  renderDetail();
}

function findQ() {
  return state.questions.find((q) => q.id === state.questionId);
}

function renderDetail() {
  const el = $("rv-detail");
  const q = findQ();
  if (!q) { el.innerHTML = '<div class="status-line">Select a question to review it.</div>'; return; }

  const conf = q.confidence || {};
  el.innerHTML = `
    <div class="row" style="justify-content:space-between;margin-bottom:10px">
      <h2 style="margin:0">Question ${esc(q.number)} <span class="badge ${esc(q.status)}">${esc(q.status)}</span></h2>
      <div class="row">
        <button class="btn green small" onclick="setStatus('approved')">Approve</button>
        <button class="btn small" onclick="setStatus('review')">Needs Review</button>
        <button class="btn red small" onclick="setStatus('rejected')">Reject</button>
        <button class="btn red small" onclick="deleteQ()">Delete</button>
      </div>
    </div>
    <div class="grid2">
      <div>
        <label>Number</label>
        <div class="row"><input id="e-number" value="${esc(q.number)}" style="flex:1"><input id="e-marks" type="number" value="${q.marks ?? ""}" style="width:90px"></div>
        <label>Part</label>
        <input id="e-part" value="${esc(q.part)}">
        <label>Question text</label>
        <textarea id="e-text" rows="6">${esc(q.text)}</textarea>
        <label>Options (MCQ)</label>
        <div id="e-options"></div>
        <button class="btn small" onclick="addOption()">+ Option</button>
      </div>
      <div>
        <label>Sub-questions</label>
        <div id="e-subs"></div>
        <button class="btn small" onclick="addSub()">+ Sub-question</button>
        <label>Associated media (click to toggle)</label>
        <div class="media-grid" id="e-media"></div>
        <label>Confidence</label>
        <div>
          <span class="pill">overall ${Math.round((conf.overall ?? 0) * 100)}%</span>
          <span class="pill">ocr ${Math.round((conf.ocr ?? 0) * 100)}%</span>
          <span class="pill">seg ${Math.round((conf.question_segmentation ?? 0) * 100)}%</span>
        </div>
        ${q.flags && q.flags.length ? `<label>Flags</label>${q.flags.map((f) => `<span class="flag">[${esc(f.code || "")}] ${esc(f.reason || f)}</span>`).join("")}` : ""}
      </div>
    </div>
    <div class="row" style="margin-top:14px">
      <button class="btn primary" onclick="saveQuestion()">Save changes</button>
      <span id="e-saved" class="status-line"></span>
    </div>`;

  renderOptions(q.options);
  renderSubs(q.subs);
  renderMediaPicker(q.media_ids);
}

function renderOptions(options) {
  const el = $("e-options");
  el.innerHTML = options.map((o, i) => `
    <div class="opt-row">
      <span class="lbl">${String.fromCharCode(65 + i)}.</span>
      <input class="opt-in" value="${esc(o)}">
      <button class="btn small red" onclick="removeRow(this,'opt')">&times;</button>
    </div>`).join("") || '<div class="status-line">No options.</div>';
}

function addOption() { renderOptions([...collectOptions(), ""]); }
function collectOptions() {
  return Array.from(document.querySelectorAll("#e-options .opt-in")).map((i) => i.value);
}
function removeRow(btn) {
  const row = btn.closest(".opt-row, .sub-row");
  row.remove();
}

function renderSubs(subs) {
  const el = $("e-subs");
  el.innerHTML = subs.map((s, i) => `
    <div class="sub-row">
      <input class="lbl" placeholder="(a)" value="${esc(s.label || "")}">
      <input class="sub-txt" placeholder="text" value="${esc(s.text || "")}">
      <input class="mk" type="number" placeholder="m" value="${s.marks ?? ""}">
      <label class="orbox"><input type="checkbox" class="or-chk" ${s.is_or_alternative ? "checked" : ""}> OR</label>
      <button class="btn small red" onclick="removeRow(this)">&times;</button>
    </div>`).join("") || '<div class="status-line">No sub-questions.</div>';
}

function addSub() {
  renderSubs([...collectSubs(), { label: "", text: "", marks: null }]);
}

function collectSubs() {
  const rows = Array.from(document.querySelectorAll("#e-subs .sub-row"));
  return rows.map((r) => ({
    label: r.querySelector(".lbl").value,
    text: r.querySelector(".sub-txt").value,
    marks: r.querySelector(".mk").value ? parseInt(r.querySelector(".mk").value) : null,
    is_or_alternative: r.querySelector(".or-chk").checked,
  }));
}

function renderMediaPicker(attached) {
  const el = $("e-media");
  const set = new Set(attached || []);
  el.innerHTML = state.media.length
    ? state.media.map((m) => `
      <div class="media-thumb ${set.has(m.id) ? "attached" : ""}" onclick="toggleMedia(${m.id})" title="type: ${esc(m.type)}">
        <img src="${esc(m.thumb_url)}">
        <span class="tag">${esc(m.type)}${m.shared ? " · shared" : ""}</span>
      </div>`).join("")
    : '<div class="status-line">No media detected.</div>';
}

function toggleMedia(mid) {
  const q = findQ();
  const set = new Set(q.media_ids || []);
  set.has(mid) ? set.delete(mid) : set.add(mid);
  q.media_ids = Array.from(set);
  renderMediaPicker(q.media_ids);
}

async function saveQuestion() {
  const q = findQ();
  const body = {
    number: $("e-number").value,
    marks: $("e-marks").value ? parseInt($("e-marks").value) : null,
    part: $("e-part").value,
    text: $("e-text").value,
    options: collectOptions(),
    subs: collectSubs(),
    media_ids: q.media_ids,
  };
  await api("/api/questions/" + q.id, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  Object.assign(q, body);
  $("e-saved").textContent = "Saved ✓  (version " + (q.version + 1) + ")";
  renderQList();
  toast("Question saved");
}

async function setStatus(status) {
  await api("/api/questions/" + state.questionId + "/status", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }),
  });
  findQ().status = status;
  renderQList();
  renderDetail();
}

async function deleteQ() {
  if (!confirm("Delete this question?")) return;
  await api("/api/questions/" + state.questionId, { method: "DELETE" });
  state.questions = state.questions.filter((q) => q.id !== state.questionId);
  state.questionId = null;
  renderQList();
  renderDetail();
}

/* ---------------- page preview ---------------- */
function renderPage() {
  const el = $("rv-page");
  if (!el) return;
  const p = state.pages.find((p) => p.page_number === state.page);
  el.innerHTML = `
    <div class="page-select">
      ${state.pages.map((pg) => `<span class="page-btn ${pg.page_number === state.page ? "active" : ""}" onclick="setPage(${pg.page_number})">${pg.page_number + 1}</span>`).join("")}
    </div>
    ${p ? `<img src="${esc(p.render_url)}" alt="page ${p.page_number + 1}">` : ""}`;
}

function setPage(n) { state.page = n; renderPage(); }

/* ---------------- generate ---------------- */
async function refreshGenerate() {
  await refreshCollections();
  const banks = await api("/api/banks");
  $("bank-list").innerHTML = banks.length
    ? banks.map((b) => `
      <div class="bank-item">
        <div>
          <b>${esc(b.name)}</b>
          <div class="qmeta">${b.question_count} questions · ${esc(b.template)} · ${esc(b.created_at.slice(0, 16).replace("T", " "))}</div>
        </div>
        ${b.url ? `<a class="btn primary small" href="${esc(b.url)}" target="_blank">Download PDF</a>` : ""}
      </div>`).join("")
    : '<div class="status-line">No banks generated yet.</div>';
}

async function generateBank() {
  const cid = parseInt($("gen-collection").value);
  if (!cid) return toast("Select a collection", "err");
  const config = {
    bank_title: $("gen-title").value || "QUESTION BANK",
    subject_name: $("gen-subject-name").value,
    subject_code: $("gen-subject-code").value,
    course: $("gen-course").value,
    semester: $("gen-semester").value,
    coverage: $("gen-coverage").value || "All Units",
    group_by: $("gen-group").value,
    notes: $("gen-notes").value,
    watermark: { image: $("gen-wm-image").value || undefined },
  };
  const r = await api("/api/collections/" + cid + "/generate", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(config),
  });
  $("gen-status").textContent = "Queued job #" + r.job_id + ". Refresh the Jobs tab for status, then the Generate tab for the PDF.";
  toast("Generation queued");
}

async function generateAnswers() {
  const cid = parseInt($("gen-collection").value);
  if (!cid) return toast("Select a collection", "err");
  const r = await api("/api/collections/" + cid + "/answers/generate", { method: "POST" });
  $("gen-status").textContent = "Answer generation queued (job #" + r.job_id + "). This solves each question with the AI model — can take a few minutes.";
  toast("Answer generation queued");
}

async function generateAnswerKey() {
  const cid = parseInt($("gen-collection").value);
  if (!cid) return toast("Select a collection", "err");
  const config = {
    bank_title: "ANSWER KEY",
    subject_name: $("gen-subject-name").value,
    subject_code: $("gen-subject-code").value,
    course: $("gen-course").value,
    semester: $("gen-semester").value,
    coverage: $("gen-coverage").value || "All Units",
    group_by: $("gen-group").value,
    notes: $("gen-notes").value,
    watermark: { image: $("gen-wm-image").value || undefined },
  };
  const r = await api("/api/collections/" + cid + "/answerkey/generate", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(config),
  });
  $("gen-status").textContent = "Answer key queued (job #" + r.job_id + "). Generate answers first if not done yet.";
  toast("Answer key queued");
}

/* ---------------- jobs ---------------- */
async function refreshJobs() {
  const jobs = await api("/api/jobs");
  $("jobs-list").innerHTML = jobs.map((j) => `
    <div class="bank-item">
      <div>
        <b>#${j.id} ${esc(j.type)}</b> <span class="badge ${esc(j.status)}">${esc(j.status)}</span>
        <div class="qmeta">${esc(j.created_at.slice(0, 16).replace("T", " "))}${j.error ? " · " + esc(j.error) : ""}</div>
      </div>
    </div>`).join("") || '<div class="status-line">No jobs.</div>';
}

/* ---------------- upload ---------------- */
$("file-input").addEventListener("change", async (ev) => {
  const files = Array.from(ev.target.files);
  if (!files.length || !state.collectionId) return toast("Create/select a collection first", "err");
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f));
  $("upload-status").textContent = `Uploading ${files.length} file(s)...`;
  try {
    const r = await api("/api/collections/" + state.collectionId + "/documents", { method: "POST", body: fd });
    $("upload-status").textContent = `Uploaded ${r.uploaded.length} file(s). Processing started — watch the Jobs tab.`;
    toast("Uploaded, processing queued");
    setTimeout(async () => {
      const d = await api("/api/collections/" + state.collectionId);
      state.docs = d.documents;
      renderDocs();
      refreshReviewSelect();
    }, 800);
  } catch (e) {
    $("upload-status").textContent = "Upload failed: " + e.message;
  }
  ev.target.value = "";
});

/* ---------------- init ---------------- */
(async function init() {
  await refreshCollections();
  if (state.collections.length) {
    state.collectionId = state.collections[0].id;
    const d = await api("/api/collections/" + state.collectionId);
    state.docs = d.documents;
    renderDocs();
    refreshReviewSelect();
  }
  setInterval(async () => {
    try {
      if (state.collectionId) {
        const d = await api("/api/collections/" + state.collectionId);
        const changed = JSON.stringify(d.documents) !== JSON.stringify(state.docs.map((x) => ({ id: x.id, status: x.status, question_count: x.question_count })));
        if (changed || d.documents.some((x) => ["processing", "pending"].includes(x.status))) {
          state.docs = d.documents;
          renderDocs();
        }
      }
    } catch (e) {}
  }, 5000);
})();
