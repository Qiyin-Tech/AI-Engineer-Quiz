/* Keepstack single-page client. Vanilla JS, no build step. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const state = { token: localStorage.getItem("keepstack_token"), user: null,
  q: "", media: null, collection: null, includeTags: [], excludeTags: [],
  sort: "newest", page: 1, pageSize: 12 };

// ---------- API helper ----------
async function api(path, opts = {}) {
  const headers = opts.headers || {};
  if (state.token) headers["Authorization"] = "Bearer " + state.token;
  if (opts.json) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.json);
    // JSON payloads are writes; default to POST when the caller did not say.
    if (!opts.method) opts.method = "POST";
  }
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401) { logout(); throw new Error("unauthorised"); }
  if (!res.ok) { const t = await res.text(); throw new Error(t || res.statusText); }
  return res.headers.get("content-type")?.includes("json") ? res.json() : res;
}

function toast(msg, kind = "ok") {
  const t = $("#toast"); t.textContent = msg; t.className = "toast " + kind;
  setTimeout(() => t.classList.add("hidden"), 2600);
}
const fmtBytes = (b) => { if (!b) return "0 B"; const u = ["B","KB","MB","GB","TB"]; const i = Math.floor(Math.log(b)/Math.log(1024)); return (b/Math.pow(1024,i)).toFixed(i?1:0)+" "+u[i]; };
const esc = (s) => (s ?? "").toString().replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

// ---------- auth ----------
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const r = await api("/api/auth/login", { json: { username: $("#login-user").value, password: $("#login-pass").value } });
    state.token = r.token; localStorage.setItem("keepstack_token", r.token);
    state.user = r.user; enterApp();
  } catch { $("#login-error").textContent = "Invalid credentials"; $("#login-error").classList.remove("hidden"); }
});
$("#btn-logout").addEventListener("click", logout);
function logout() { localStorage.removeItem("keepstack_token"); state.token = null; state.user = null;
  $("#app").classList.add("hidden"); $("#login").classList.remove("hidden"); }

function enterApp() {
  $("#login").classList.add("hidden"); $("#app").classList.remove("hidden");
  $("#user-label").textContent = `${state.user.username} (${state.user.role})`;
  const isAdmin = state.user.role === "admin";
  $$(".admin-only").forEach(el => el.classList.toggle("hidden", !isAdmin));
  loadLibrary();
  loadCollectionOptions();
}

// ---------- navigation ----------
$$(".nav-item").forEach(btn => btn.addEventListener("click", () => {
  $$(".nav-item").forEach(b => b.classList.remove("active")); btn.classList.add("active");
  const view = btn.dataset.view;
  $$(".view").forEach(v => v.classList.add("hidden"));
  $("#view-" + (view === "library" ? "library" : view)).classList.remove("hidden");
  if (view === "library") loadLibrary();
  if (view === "collections") loadCollections();
  if (view === "dashboard") loadDashboard();
  if (view === "users") loadUsers();
  if (view === "audit") loadAudit();
}));

// ---------- library / search ----------
let searchTimer;
let librarySeq = 0;
let libraryAbort = null;

$("#search").addEventListener("input", (e) => {
  state.q = e.target.value;
  state.page = 1;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadLibrary, 250);
});
$("#sort").addEventListener("change", (e) => { state.sort = e.target.value; state.page = 1; loadLibrary(); });
$("#filter-collection").addEventListener("change", (e) => {
  state.collection = e.target.value || null; state.page = 1; loadLibrary();
});
$("#filter-include").addEventListener("change", applyTagInputs);
$("#filter-exclude").addEventListener("change", applyTagInputs);
["filter-include", "filter-exclude"].forEach(id => $("#" + id).addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); e.target.blur(); }
}));
$("#filter-clear").addEventListener("click", () => {
  state.media = null; state.collection = null;
  state.includeTags = []; state.excludeTags = [];
  state.page = 1; syncFilterInputs(); loadLibrary();
});
$("#page-prev").addEventListener("click", () => { if (state.page > 1) { state.page -= 1; loadLibrary(); } });
$("#page-next").addEventListener("click", () => { state.page += 1; loadLibrary(); });
$("#btn-export").addEventListener("click", exportCsv);
$("#btn-retry").addEventListener("click", loadLibrary);

function parseTags(value) {
  return [...new Set((value || "").split(",").map(t => t.trim().toLowerCase()).filter(Boolean))];
}
function applyTagInputs() {
  state.includeTags = parseTags($("#filter-include").value);
  state.excludeTags = parseTags($("#filter-exclude").value);
  state.page = 1;
  syncFilterInputs();
  loadLibrary();
}
function syncFilterInputs() {
  $("#filter-collection").value = state.collection || "";
  $("#filter-include").value = state.includeTags.join(", ");
  $("#filter-exclude").value = state.excludeTags.join(", ");
}
function libraryParams(paged = true) {
  const p = new URLSearchParams();
  if (state.q.trim()) p.set("q", state.q);
  p.set("sort", state.sort);
  if (state.media) p.set("media_type", state.media);
  if (state.collection) p.set("collection_id", state.collection);
  state.includeTags.forEach(t => p.append("include_tags", t));
  state.excludeTags.forEach(t => p.append("exclude_tags", t));
  if (paged) { p.set("page", state.page); p.set("page_size", state.pageSize); }
  return p;
}

async function loadLibrary() {
  // Abort the previous request and ignore stale responses: the last filter
  // state the user picked always wins.
  const seq = ++librarySeq;
  if (libraryAbort) libraryAbort.abort();
  libraryAbort = new AbortController();
  try {
    const data = await api("/api/assets?" + libraryParams().toString(),
                           { signal: libraryAbort.signal });
    if (seq !== librarySeq) return;
    renderFacets(data.facets);
    renderGrid(data.items, data.total);
    renderPager(data);
    renderActiveFilters();
    $("#load-error").classList.add("hidden");
  } catch (err) {
    if (err.name === "AbortError" || seq !== librarySeq) return;
    $("#grid").innerHTML = "";
    $("#empty").classList.add("hidden");
    $("#load-error").classList.remove("hidden");
    $("#page-prev").disabled = true;
    $("#page-next").disabled = true;
    $("#page-info").textContent = "";
    toast("Failed to load assets: " + (err.message || "unknown error"), "err");
  }
}

function renderGrid(items, total) {
  $("#result-title").textContent = state.q.trim() ? `Results (${total})` : `Library (${total})`;
  $("#empty").classList.toggle("hidden", items.length > 0);
  $("#grid").innerHTML = items.map(a => `
    <div class="card" data-uuid="${a.uuid}">
      <img class="thumb" loading="lazy" src="/api/assets/${a.uuid}/thumb" alt="${esc(a.alt_text || a.title)}">
      <div class="meta">
        <span class="title" title="${esc(a.title)}">${esc(a.title)}</span>
        <span class="sub"><span class="badge">${a.media_type}</span><span>${fmtBytes(a.size)}</span></span>
      </div>
    </div>`).join("");
  $$("#grid .card").forEach(c => c.addEventListener("click", () => openAsset(c.dataset.uuid)));
}

function renderPager(data) {
  const pages = data.pages || 0;
  $("#page-prev").disabled = data.page <= 1;
  $("#page-next").disabled = pages === 0 || data.page >= pages;
  $("#page-info").textContent = data.total
    ? `Page ${data.page} of ${pages} · ${data.total} match${data.total === 1 ? "" : "es"}`
    : "No matches";
}

function renderFacets(facets) {
  $("#facet-media").innerHTML = (facets.media_types || []).map(m =>
    `<li class="${state.media===m.value?'active':''}" data-media="${esc(m.value)}"><span>${esc(m.value)}</span><span class="count">${m.count}</span></li>`).join("");
  $("#facet-tags").innerHTML = (facets.tags || []).slice(0, 18).map(t =>
    `<li class="${state.includeTags.includes(t.value)?'active':''}" data-tag="${esc(t.value)}"><span>${esc(t.value)}</span><span class="count">${t.count}</span></li>`).join("");
  $$("#facet-media li").forEach(li => li.addEventListener("click", () => {
    state.media = state.media === li.dataset.media ? null : li.dataset.media;
    state.page = 1; loadLibrary();
  }));
  $$("#facet-tags li").forEach(li => li.addEventListener("click", () => {
    const tag = li.dataset.tag;
    state.includeTags = state.includeTags.includes(tag)
      ? state.includeTags.filter(t => t !== tag)
      : [...state.includeTags, tag];
    state.page = 1; syncFilterInputs(); loadLibrary();
  }));
}

function renderActiveFilters() {
  const chip = (kind, label, value = "") =>
    `<span class="chip" data-kind="${kind}" data-value="${esc(value)}">${esc(label)}</span>`;
  const chips = [];
  if (state.media) chips.push(chip("media", state.media));
  if (state.collection) {
    const opt = $("#filter-collection").selectedOptions[0];
    chips.push(chip("collection", opt ? opt.textContent : "collection"));
  }
  state.includeTags.forEach(t => chips.push(chip("include", "+ " + t, t)));
  state.excludeTags.forEach(t => chips.push(chip("exclude", "− " + t, t)));
  const box = $("#active-filters");
  box.innerHTML = chips.length ? chips.join("") : '<span class="muted">No filters applied</span>';
  $$(".chip", box).forEach(el => el.addEventListener("click", () => {
    const kind = el.dataset.kind, value = el.dataset.value;
    if (kind === "media") state.media = null;
    if (kind === "collection") state.collection = null;
    if (kind === "include") state.includeTags = state.includeTags.filter(t => t !== value);
    if (kind === "exclude") state.excludeTags = state.excludeTags.filter(t => t !== value);
    state.page = 1; syncFilterInputs(); loadLibrary();
  }));
}

async function exportCsv() {
  const btn = $("#btn-export");
  btn.disabled = true;
  try {
    const res = await fetch("/api/assets/export.csv?" + libraryParams(false).toString(), {
      headers: state.token ? { Authorization: "Bearer " + state.token } : {},
    });
    if (!res.ok) throw new Error((await res.text()) || res.statusText);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "assets-export.csv";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    toast("CSV downloaded");
  } catch (err) {
    toast("Export failed: " + (err.message || "unknown error"), "err");
  } finally {
    btn.disabled = false;
  }
}

// ---------- asset detail drawer ----------
const drawer = $("#drawer");
$("#drawer-close").addEventListener("click", closeDrawer);
$(".drawer-backdrop").addEventListener("click", closeDrawer);
function closeDrawer() { drawer.classList.add("hidden"); document.body.classList.remove("modal-open"); }

async function openAsset(uuid) {
  drawer.classList.remove("hidden");
  document.body.classList.add("modal-open");
  $("#drawer-body").innerHTML = '<p class="muted">Loading...</p>';
  const [d, meta, sim] = await Promise.all([
    api("/api/assets/" + uuid),
    api("/api/assets/" + uuid + "/metadata"),
    api("/api/assets/" + uuid + "/similar"),
  ]);
  const a = d.asset;
  const canEdit = ["admin","editor","contributor"].includes(state.user.role);
  const preview = a.media_type === "image"
    ? `<img class="detail-preview" src="/api/assets/${uuid}/preview" alt="${esc(a.alt_text||a.title)}">`
    : `<img class="detail-preview" src="/api/assets/${uuid}/thumb" alt="${esc(a.title)}">`;
  const dc = a.dublin_core || {};
  $("#drawer-body").innerHTML = `
    ${preview}
    <h2 class="detail-title">${esc(a.title)}</h2>
    <p class="muted">${a.media_type} &middot; ${a.ext?.toUpperCase()} &middot; ${fmtBytes(a.size)} ${a.width?`&middot; ${a.width}&times;${a.height}`:""}</p>
    <div class="btn-row">
      <a class="btn" href="/api/assets/${uuid}/file" download>Download</a>
      ${canEdit?`<button class="btn" id="d-share">Create share link</button>`:""}
      ${["admin","editor"].includes(state.user.role)?`<button class="btn btn-danger" id="d-delete">Delete</button>`:""}
    </div>

    ${canEdit ? `<div class="detail-section"><h4>Edit metadata</h4>
      <div class="edit-grid">
        <label>Title<input id="e-title" value="${esc(a.title)}"></label>
        <label>Description<textarea id="e-description" rows="2">${esc(a.description)}</textarea></label>
        <label>Alt text (accessibility)<input id="e-alt_text" value="${esc(a.alt_text)}"></label>
        <label>Creator<input id="e-dc_creator" value="${esc(a.dc_creator)}"></label>
        <label>Rights / license<input id="e-rights_statement" value="${esc(a.rights_statement)}"></label>
        <label>Retention until<input id="e-retention_until" type="date" value="${esc((a.retention_until||'').slice(0,10))}"></label>
      </div>
      <div class="btn-row"><button class="btn btn-primary" id="d-save">Save changes</button></div></div>` : ""}

    <div class="detail-section"><h4>Tags</h4>
      <div class="tag-row" id="tag-row">
        ${d.tags.map(t => `<span class="tag ${t.source==='ai'?'ai':''}">${esc(t.name)}${canEdit?`<span class="rm" data-tag="${esc(t.name)}">&times;</span>`:""}</span>`).join("")}
        ${canEdit?`<input class="tag-add" id="tag-add" placeholder="+ add tag">`:""}
      </div></div>

    ${sim.items.length?`<div class="detail-section"><h4>Similar assets</h4>
      <div class="similar-strip">${sim.items.slice(0,8).map(s=>`<img src="/api/assets/${s.uuid}/thumb" data-uuid="${s.uuid}" title="${esc(s.title)}">`).join("")}</div></div>`:""}

    <div class="detail-section"><h4>Dublin Core</h4>
      ${Object.entries(dc).filter(([,v])=>v).map(([k,v])=>`<div class="field-row"><span class="k">${k}</span><span class="v">${esc(v)}</span></div>`).join("")}
      <div class="btn-row"><a class="btn btn-ghost" href="/oai?verb=GetRecord&metadataPrefix=oai_dc&identifier=oai::${a.uuid}" target="_blank">View OAI-PMH XML</a></div>
    </div>

    ${Object.keys(meta.exif||{}).length||Object.keys(meta.iptc||{}).length?`<div class="detail-section"><h4>Embedded metadata (EXIF / IPTC)</h4>
      <div class="kv-scroll">${[...Object.entries(meta.iptc||{}),...Object.entries(meta.exif||{})].slice(0,40).map(([k,v])=>`<div class="field-row"><span class="k">${esc(k)}</span><span class="v">${esc(typeof v==='object'?JSON.stringify(v):v)}</span></div>`).join("")}</div></div>`:""}

    ${d.versions.length?`<div class="detail-section"><h4>Versions</h4>${d.versions.map(v=>`<div class="field-row"><span class="k">v${v.version_no}</span><span class="v">${esc(v.note||'')} &middot; ${fmtBytes(v.size)} &middot; ${(v.created_at||'').slice(0,10)}</span></div>`).join("")}</div>`:""}
  `;

  // wire detail actions
  $$("#drawer-body .similar-strip img").forEach(img => img.addEventListener("click", () => openAsset(img.dataset.uuid)));
  if (canEdit) {
    $("#d-save")?.addEventListener("click", async () => {
      const body = {};
      ["title","description","alt_text","dc_creator","rights_statement","retention_until"].forEach(f => { const el = $("#e-"+f); if (el) body[f] = el.value; });
      await api("/api/assets/"+uuid, { method: "PATCH", json: body });
      toast("Saved"); openAsset(uuid); loadLibrary();
    });
    $("#tag-add")?.addEventListener("keydown", async (e) => {
      if (e.key === "Enter" && e.target.value.trim()) {
        await api("/api/assets/"+uuid+"/tags", { json: { name: e.target.value.trim() } });
        openAsset(uuid);
      }
    });
    $$("#tag-row .rm").forEach(rm => rm.addEventListener("click", async () => {
      await api("/api/assets/"+uuid+"/tags/"+encodeURIComponent(rm.dataset.tag), { method: "DELETE" });
      openAsset(uuid);
    }));
    $("#d-share")?.addEventListener("click", async () => {
      const r = await api("/api/shares", { json: { target_type: "asset", target_id: a.id, permission: "download" } });
      await navigator.clipboard?.writeText(r.url).catch(()=>{});
      toast("Share link copied: " + r.url);
    });
  }
  $("#d-delete")?.addEventListener("click", async () => {
    if (!confirm("Delete this asset?")) return;
    await api("/api/assets/"+uuid, { method: "DELETE" });
    toast("Deleted"); closeDrawer(); loadLibrary();
  });
}

// ---------- upload ----------
const uploadModal = $("#upload-modal");
$("#btn-upload").addEventListener("click", () => { uploadModal.classList.remove("hidden"); document.body.classList.add("modal-open"); });
$("#upload-cancel").addEventListener("click", () => { uploadModal.classList.add("hidden"); document.body.classList.remove("modal-open"); loadLibrary(); });
$(".modal-backdrop").addEventListener("click", () => { uploadModal.classList.add("hidden"); document.body.classList.remove("modal-open"); });
const dz = $("#dropzone"), fileInput = $("#file-input");
dz.addEventListener("click", () => fileInput.click());
dz.addEventListener("dragover", e => { e.preventDefault(); dz.classList.add("drag"); });
dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
dz.addEventListener("drop", e => { e.preventDefault(); dz.classList.remove("drag"); uploadFiles(e.dataTransfer.files); });
fileInput.addEventListener("change", () => uploadFiles(fileInput.files));

async function uploadFiles(files) {
  const list = $("#upload-list");
  for (const file of files) {
    const row = document.createElement("div");
    row.className = "upload-item"; row.innerHTML = `<span>${esc(file.name)}</span><span>uploading...</span>`;
    list.prepend(row);
    const fd = new FormData(); fd.append("files", file); fd.append("run_ai", $("#ai-toggle").checked);
    try {
      const r = await api("/api/assets", { method: "POST", body: fd });
      const created = r.created[0];
      row.classList.add(created.duplicate ? "dup" : "ok");
      row.lastChild.textContent = created.duplicate ? "duplicate (deduped)" : "done";
    } catch (err) { row.classList.add("err"); row.lastChild.textContent = "failed"; }
  }
}

// ---------- collections ----------
function renderCollectionOptions(collections) {
  const select = $("#filter-collection");
  const current = state.collection || "";
  select.innerHTML = '<option value="">All collections</option>' +
    collections.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
  select.value = current;
}

async function loadCollectionOptions() {
  try {
    const data = await api("/api/collections");
    renderCollectionOptions(data.collections);
  } catch { /* the library still works without the dropdown */ }
}

async function loadCollections() {
  const data = await api("/api/collections");
  renderCollectionOptions(data.collections);
  $("#collection-list").innerHTML = data.collections.map(c =>
    `<div class="collection-card" data-id="${c.id}"><span class="n">${esc(c.name)}</span><span class="muted">${c.asset_count} assets</span><span class="muted">${esc(c.description||"")}</span></div>`).join("")
    || '<p class="muted">No collections yet.</p>';
  $$("#collection-list .collection-card").forEach(card => card.addEventListener("click", () => {
    state.collection = card.dataset.id; state.media = null;
    state.page = 1; syncFilterInputs();
    $$(".nav-item").forEach(b => b.classList.remove("active"));
    $('.nav-item[data-view="library"]').classList.add("active");
    $$(".view").forEach(v => v.classList.add("hidden")); $("#view-library").classList.remove("hidden");
    loadLibrary();
  }));
}
$("#btn-new-collection").addEventListener("click", async () => {
  const name = prompt("Collection name"); if (!name) return;
  await api("/api/collections", { json: { name } }); loadCollections();
});

// ---------- dashboard ----------
async function loadDashboard() {
  const s = await api("/api/stats");
  const cards = [
    ["Total assets", s.total_assets], ["Storage used", fmtBytes(s.total_bytes)],
    ["Saved by dedup", fmtBytes(s.dedup_saved_bytes)], ["Tags", s.tags], ["Collections", s.collections],
    ...Object.entries(s.by_type).map(([k,v]) => [k.charAt(0).toUpperCase()+k.slice(1), v]),
  ];
  $("#stat-cards").innerHTML = cards.map(([l,n]) => `<div class="stat-card"><div class="n">${n}</div><div class="l">${l}</div></div>`).join("");
}
$("#btn-fixity").addEventListener("click", async () => {
  $("#fixity-result").textContent = "Checking...";
  const r = await api("/api/admin/fixity", { method: "POST" });
  $("#fixity-result").textContent = r.ok ? `All ${r.checked} blobs verified. Integrity intact.` : `${r.failures.length} of ${r.checked} failed fixity.`;
});

// ---------- users ----------
async function loadUsers() {
  const data = await api("/api/users");
  $("#user-rows").innerHTML = data.users.map(u =>
    `<tr><td>${esc(u.username)}</td><td>${esc(u.email||"")}</td><td>${u.role}</td><td>${u.is_active?"Yes":"No"}</td><td>${(u.last_login||"").slice(0,16).replace("T"," ")}</td></tr>`).join("");
}
$("#btn-new-user").addEventListener("click", async () => {
  const username = prompt("Username"); if (!username) return;
  const password = prompt("Temporary password"); if (!password) return;
  const role = prompt("Role (admin/editor/contributor/viewer)", "contributor") || "viewer";
  await api("/api/users", { json: { username, password, role } }); loadUsers();
});

// ---------- audit ----------
async function loadAudit() {
  const data = await api("/api/audit");
  $("#audit-rows").innerHTML = data.entries.map(e =>
    `<tr><td>${(e.ts||"").slice(0,19).replace("T"," ")}</td><td>${esc(e.username||"-")}</td><td>${esc(e.action)}</td>
     <td>${esc(e.target_type||"")} ${esc((e.target_id||"").slice(0,8))}</td><td>${esc(e.detail||"")}</td><td>${esc(e.ip||"")}</td></tr>`).join("");
}

// ---------- boot ----------
(async function boot() {
  if (state.token) {
    try { state.user = await api("/api/auth/me"); enterApp(); return; } catch {}
  }
  $("#login").classList.remove("hidden");
})();
