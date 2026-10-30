/* Keepstack single-page client. Vanilla JS, no build step. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const state = { token: localStorage.getItem("keepstack_token"), user: null,
  q: "", media: null, includeTags: [], excludeTags: [], sort: "newest", collection: null,
  page: 0, pageSize: 24, total: 0, ready: false, exporting: false, collections: [] };

// ---------- API helper ----------
async function api(path, opts = {}) {
  const headers = opts.headers || {};
  if (state.token) headers["Authorization"] = "Bearer " + state.token;
  if (opts.json) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); opts.method ||= "POST"; }
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
  invalidateLibrary();
  $("#app").classList.add("hidden"); $("#login").classList.remove("hidden"); }

function enterApp() {
  $("#login").classList.add("hidden"); $("#app").classList.remove("hidden");
  $("#user-label").textContent = `${state.user.username} (${state.user.role})`;
  const isAdmin = state.user.role === "admin";
  $$(".admin-only").forEach(el => el.classList.toggle("hidden", !isAdmin));
  loadLibrary();
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
let searchTimer, libraryController, exportController, libraryVersion = 0;
$("#search").addEventListener("input", (e) => { state.q = e.target.value; filtersChanged(true); });
$("#sort").addEventListener("change", (e) => { state.sort = e.target.value; filtersChanged(); });
$("#filter-collection").addEventListener("change", e => { state.collection = e.target.value || null; filtersChanged(); });
$("#filter-media").addEventListener("change", e => { state.media = e.target.value || null; filtersChanged(); });
for (const kind of ["include", "exclude"]) {
  $("#" + kind + "-form").addEventListener("submit", e => {
    e.preventDefault();
    const input = $("#" + kind + "-input"), value = input.value.trim().toLowerCase();
    if (!value) return;
    const tags = kind === "include" ? state.includeTags : state.excludeTags;
    if (!tags.includes(value)) tags.push(value);
    input.value = "";
    filtersChanged();
  });
}
$("#clear-filters").addEventListener("click", () => {
  state.q = ""; state.media = null; state.collection = null;
  state.includeTags = []; state.excludeTags = [];
  $("#search").value = "";
  $("#include-input").value = ""; $("#exclude-input").value = "";
  filtersChanged();
});
$("#retry-library").addEventListener("click", () => loadLibrary());
$("#prev-page").addEventListener("click", () => { if (state.ready && state.page > 0) { state.page--; loadLibrary(); } });
$("#next-page").addEventListener("click", () => {
  if (state.ready && (state.page + 1) * state.pageSize < state.total) { state.page++; loadLibrary(); }
});

function filterParams() {
  const p = new URLSearchParams({ q: state.q, sort: state.sort });
  if (state.media) p.set("media_type", state.media);
  if (state.collection) p.set("collection_id", state.collection);
  state.includeTags.forEach(t => p.append("include_tags", t));
  state.excludeTags.forEach(t => p.append("exclude_tags", t));
  return p;
}

function updatePageControls() {
  $("#prev-page").disabled = !state.ready || state.page === 0;
  $("#next-page").disabled = !state.ready || (state.page + 1) * state.pageSize >= state.total;
  $("#btn-export").disabled = !state.ready || state.exporting;
}

function invalidateLibrary() {
  clearTimeout(searchTimer);
  libraryVersion++;
  libraryController?.abort(); exportController?.abort();
  state.ready = false;
  updatePageControls();
}

function filtersChanged(debounce = false) {
  state.page = 0;
  // Invalidate immediately, including the debounce interval before the next fetch.
  invalidateLibrary();
  renderActiveFilters();
  $("#grid").innerHTML = "";
  $("#empty").classList.add("hidden");
  $("#library-error").classList.add("hidden");
  $("#export-status").textContent = "";
  $("#result-title").textContent = "Results";
  $("#library-status").textContent = "Loading results…";
  $("#page-info").textContent = "Loading…";
  if (debounce) searchTimer = setTimeout(loadLibrary, 250);
  else loadLibrary();
}

function renderActiveFilters() {
  $("#filter-collection").value = state.collection || "";
  $("#filter-media").value = state.media || "";
  const target = $("#active-filters"); target.replaceChildren();
  const add = (text, remove) => {
    const btn = document.createElement("button"); btn.className = "chip";
    btn.textContent = text; btn.setAttribute("aria-label", "Remove " + text);
    btn.addEventListener("click", () => { remove(); filtersChanged(); }); target.append(btn);
  };
  if (state.q.trim()) add("Keyword: " + state.q, () => { state.q = ""; $("#search").value = ""; });
  if (state.collection) add("Collection: " + (state.collections.find(c => String(c.id) === String(state.collection))?.name || state.collection), () => state.collection = null);
  if (state.media) add("Type: " + state.media, () => state.media = null);
  for (const [kind, tags] of [["Include", state.includeTags], ["Exclude", state.excludeTags]]) {
    tags.forEach(t => add(kind + ": " + t, () => tags.splice(tags.indexOf(t), 1)));
  }
  if (!target.children.length) target.textContent = "All active assets";
}

async function loadLibrary() {
  invalidateLibrary();
  const version = libraryVersion;
  const controller = libraryController = new AbortController();
  const p = filterParams(); p.set("limit", state.pageSize); p.set("offset", state.page * state.pageSize);
  $("#grid").setAttribute("aria-busy", "true"); $("#grid").innerHTML = "";
  $("#empty").classList.add("hidden"); $("#library-error").classList.add("hidden");
  $("#library-status").textContent = "Loading results…";
  $("#page-info").textContent = "Loading…";
  try {
    const [data, collections, tags] = await Promise.all([
      api("/api/assets?" + p, { signal: controller.signal }),
      api("/api/collections", { signal: controller.signal }),
      api("/api/tags", { signal: controller.signal }),
    ]);
    if (version !== libraryVersion) return;
    state.collections = collections.collections; state.total = data.total;
    // A deletion may empty the last page; move back to the last available page.
    if (state.page > 0 && state.page * state.pageSize >= data.total) {
      state.page = Math.max(0, Math.ceil(data.total / state.pageSize) - 1);
      return loadLibrary();
    }
    $("#filter-collection").innerHTML = '<option value="">All collections</option>' + state.collections.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
    $("#filter-media").innerHTML = '<option value="">All media types</option>' + data.facets.media_types.map(m => `<option value="${esc(m.value)}">${esc(m.value)}</option>`).join("");
    $("#tag-options").innerHTML = tags.tags.map(t => `<option value="${esc(t.name)}"></option>`).join("");
    renderFacets(data.facets); renderActiveFilters(); renderGrid(data.items, data.total);
    state.ready = true;
    $("#library-status").textContent = `${data.total} matching assets`;
    $("#page-info").textContent = data.total ? `Page ${state.page + 1} of ${Math.ceil(data.total / state.pageSize)} · ${data.total} matches` : "0 matches";
  } catch (err) {
    if (version !== libraryVersion || err.name === "AbortError") return;
    $("#library-status").textContent = "";
    $("#page-info").textContent = "Results unavailable";
    $("#library-error").classList.remove("hidden");
  } finally {
    if (version === libraryVersion) { $("#grid").setAttribute("aria-busy", "false"); updatePageControls(); }
  }
}

$("#btn-export").addEventListener("click", async () => {
  if (!state.ready || state.exporting) return;
  state.exporting = true; updatePageControls();
  const controller = exportController = new AbortController();
  const version = libraryVersion;
  $("#export-status").textContent = "Preparing CSV for all matching assets…";
  try {
    const res = await api("/api/assets/export.csv?" + filterParams(), { signal: controller.signal });
    const blob = await res.blob();
    if (version !== libraryVersion) return;
    const url = URL.createObjectURL(blob), link = document.createElement("a");
    link.href = url; link.download = "assets.csv"; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    $("#export-status").textContent = "CSV download started (all matching assets).";
  } catch (err) {
    if (version === libraryVersion && err.name !== "AbortError") $("#export-status").textContent = "CSV download failed. Please try again.";
  } finally { state.exporting = false; updatePageControls(); }
});

function renderGrid(items, total) {
  $("#result-title").textContent = state.q ? `Results (${total})` : `Library (${total})`;
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

function renderFacets(facets) {
  $("#facet-media").innerHTML = (facets.media_types || []).map(m =>
    `<li class="${state.media===m.value?'active':''}" data-media="${m.value}"><span>${m.value}</span><span class="count">${m.count}</span></li>`).join("");
  $("#facet-tags").innerHTML = (facets.tags || []).slice(0, 18).map(t =>
    `<li class="${state.includeTags.includes(t.value)?'active':''}" data-tag="${esc(t.value)}"><span>${esc(t.value)}</span><span class="count">${t.count}</span></li>`).join("");
  $$("#facet-media li").forEach(li => li.addEventListener("click", () => { state.media = state.media===li.dataset.media?null:li.dataset.media; filtersChanged(); }));
  $$("#facet-tags li").forEach(li => li.addEventListener("click", () => {
    const tag = li.dataset.tag, idx = state.includeTags.indexOf(tag);
    if (idx < 0) state.includeTags.push(tag); else state.includeTags.splice(idx, 1);
    filtersChanged();
  }));
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
        openAsset(uuid); loadLibrary();
      }
    });
    $$("#tag-row .rm").forEach(rm => rm.addEventListener("click", async () => {
      await api("/api/assets/"+uuid+"/tags/"+encodeURIComponent(rm.dataset.tag), { method: "DELETE" });
      openAsset(uuid); loadLibrary();
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
async function loadCollections() {
  const data = await api("/api/collections");
  $("#collection-list").innerHTML = data.collections.map(c =>
    `<div class="collection-card" data-id="${c.id}"><span class="n">${esc(c.name)}</span><span class="muted">${c.asset_count} assets</span><span class="muted">${esc(c.description||"")}</span></div>`).join("")
    || '<p class="muted">No collections yet.</p>';
  $$("#collection-list .collection-card").forEach(card => card.addEventListener("click", () => {
    state.collection = card.dataset.id;
    $$(".nav-item").forEach(b => b.classList.remove("active"));
    $('.nav-item[data-view="library"]').classList.add("active");
    $$(".view").forEach(v => v.classList.add("hidden")); $("#view-library").classList.remove("hidden");
    filtersChanged();
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
