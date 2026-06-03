const api = (p, opts) => fetch(p, opts).then(r => {
  if (!r.ok) return r.json().then(e => Promise.reject(e.detail || r.statusText));
  return r.json();
});

const state = {
  tab: "live",        // live | movie | series
  group: null,
  q: "",
  page: 1,
  pageSize: 100,
  total: 0,
};

const $ = sel => document.querySelector(sel);
const el = (tag, cls, txt) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt != null) e.textContent = txt;
  return e;
};

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 2600);
}

const fmtBytes = n => {
  n = Number(n);
  if (!n) return "";
  const u = ["B", "KB", "MB", "GB"]; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
};
const fmtTime = t => t ? t.replace("T", " ").replace("+00:00", "Z") : "";

// --- status / sync --------------------------------------------------------
let hasSource = false;
async function refreshStatus() {
  const s = await api("/api/status");
  const c = s.counts;
  hasSource = !!s.source_url;
  let txt = `live ${c.live} · movies ${c.movie} · series ${c.series} · imported ${c.imported}`;
  if (!s.source_url) txt = "no source set — click Source…";
  else if (s.last_sync) txt += ` · synced ${fmtTime(s.last_sync)}` + (s.last_bytes ? ` (${fmtBytes(s.last_bytes)})` : "");
  else txt += " · not synced yet — click Sync";
  $("#status").textContent = txt;
  return s;
}

async function runSync() {
  if (!hasSource) { openSource(); toast("Set a source URL first"); return; }
  $("#syncBtn").disabled = true;
  const label = $("#syncBtn").textContent;
  $("#syncBtn").textContent = "Syncing…";
  try {
    const r = await api("/api/sync", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    toast(`Synced ${fmtBytes(r.bytes)} · parsed ${r.parsed.toLocaleString()} entries`);
    await refreshStatus();
    await loadGroups();
    await loadList();
  } catch (e) {
    toast("Sync failed: " + e);
  } finally {
    $("#syncBtn").disabled = false;
    $("#syncBtn").textContent = label;
  }
}
$("#syncBtn").onclick = runSync;

// --- source settings ------------------------------------------------------
function subRow(sub) {
  sub = sub || {};
  const row = el("div", "subrow");
  const url = el("input", "sub-url"); url.type = "text"; url.placeholder = "Server URL (http://provider:80)"; url.value = sub.url || "";
  const u = el("input", "sub-user"); u.type = "text"; u.placeholder = "username"; u.value = sub.username || "";
  const p = el("input", "sub-pass"); p.type = "text"; p.placeholder = "password"; p.value = sub.password || "";
  const rm = el("button", "", "✕"); rm.type = "button"; rm.title = "Remove"; rm.onclick = () => row.remove();
  row.append(url, u, p, rm);
  return row;
}
async function openSource() {
  const s = await api("/api/source");
  const list = $("#subsList");
  list.innerHTML = "";
  const subs = (s.subs && s.subs.length) ? s.subs : [{}];
  subs.forEach(sub => list.append(subRow(sub)));
  $("#sourceHint").textContent = s.last_sync ? `Last synced ${fmtTime(s.last_sync)}` : "Not synced yet.";
  $("#sourceModal").classList.remove("hidden");
}
$("#addSub").onclick = () => $("#subsList").append(subRow());
function collectSubs() {
  return [...$("#subsList").querySelectorAll(".subrow")].map(r => ({
    url: r.querySelector(".sub-url").value.trim(),
    username: r.querySelector(".sub-user").value.trim(),
    password: r.querySelector(".sub-pass").value.trim(),
  })).filter(s => s.url || s.username || s.password);
}
async function saveSource() {
  const subs = collectSubs();
  if (!subs.length || subs.some(s => !s.url || !s.username || !s.password)) {
    toast("Each subscription needs URL, username and password"); return false;
  }
  await api("/api/source", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ subs }) });
  await refreshStatus();
  return true;
}
$("#sourceBtn").onclick = openSource;
$("#closeSource").onclick = () => $("#sourceModal").classList.add("hidden");
$("#saveSource").onclick = async () => {
  try { if (await saveSource()) { $("#sourceModal").classList.add("hidden"); toast("Source saved"); } }
  catch (e) { toast("Save failed: " + e); }
};
$("#saveSyncSource").onclick = async () => {
  try {
    if (await saveSource()) { $("#sourceModal").classList.add("hidden"); await runSync(); }
  } catch (e) { toast("Save failed: " + e); }
};

// --- tabs -----------------------------------------------------------------
document.querySelectorAll(".tab").forEach(btn => {
  btn.onclick = () => {
    document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    state.tab = btn.dataset.tab;
    state.group = null;
    state.q = "";
    state.page = 1;
    $("#search").value = "";
    if (state.tab === "imported") {
      $("#groups").innerHTML = "";
      loadImported();
    } else {
      loadGroups();
      loadList();
    }
  };
});

// --- imported view --------------------------------------------------------
let impData = [];
async function removeIds(ids) {
  await api("/api/unimport", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids }) });
  await refreshStatus();
  await loadImported();
}

async function loadImported() {
  const r = await api("/api/imported");
  impData = r.imported;
  const list = $("#list");
  list.innerHTML = "";
  $("#pageInfo").textContent = ""; $("#prev").disabled = true; $("#next").disabled = true;
  if (!impData.length) {
    $("#resultMeta").textContent = "0 imported";
    list.append(el("div", "empty", "Nothing imported yet."));
    return;
  }
  const live = impData.filter(x => x.kind === "live");
  const movies = impData.filter(x => x.kind === "movie");
  const seriesEps = impData.filter(x => x.kind === "series");
  // group series episodes by series_key
  const seriesMap = new Map();
  for (const e of seriesEps) {
    const g = seriesMap.get(e.series_key) || { key: e.series_key, name: e.series_name || e.name, seasons: new Set(), eps: [] };
    g.seasons.add(e.season); g.eps.push(e); seriesMap.set(e.series_key, g);
  }
  $("#resultMeta").textContent =
    `${live.length} live · ${movies.length} movies · ${seriesMap.size} series (${seriesEps.length} ep)`;

  if (live.length) { list.append(impHeader("Live")); live.forEach(it => list.append(impFlatRow(it))); }
  if (movies.length) { list.append(impHeader("Movies")); movies.forEach(it => list.append(impFlatRow(it))); }
  if (seriesMap.size) {
    list.append(impHeader("Series"));
    [...seriesMap.values()].sort((a, b) => a.name.localeCompare(b.name)).forEach(g => list.append(impSeriesRow(g)));
  }
}
function impHeader(t) { return el("div", "imp-section", t); }

function impFlatRow(it) {
  const row = el("div", "row");
  row.append(el("span", "name", it.name));
  row.append(el("span", "grp", it.group_title || ""));
  const btn = el("button", "danger", "Remove");
  btn.onclick = () => { btn.disabled = true; removeIds([it.id]).catch(e => { toast("Remove failed: " + e); btn.disabled = false; }); };
  row.append(btn);
  return row;
}

function impSeriesRow(g) {
  const row = el("div", "row");
  const name = el("span", "name");
  name.append(document.createTextNode(g.name + " "));
  name.append(el("span", "badge", `${g.seasons.size} season${g.seasons.size === 1 ? "" : "s"} · ${g.eps.length} ep`));
  row.append(name);
  const browse = el("button", "", "Browse");
  browse.onclick = () => openImpSeries(g.key);
  const rm = el("button", "danger", "Remove all");
  rm.onclick = () => { rm.disabled = true; removeIds(g.eps.map(e => e.id)).catch(e => { toast("Remove failed: " + e); rm.disabled = false; }); };
  row.append(browse, rm);
  return row;
}

function openImpSeries(key) {
  const eps = impData.filter(x => x.kind === "series" && x.series_key === key);
  if (!eps.length) return;
  $("#impSeriesTitle").textContent = eps[0].series_name || eps[0].name;
  $("#removeWholeSeries").onclick = () => removeIds(eps.map(e => e.id)).then(() => $("#impSeriesModal").classList.add("hidden")).catch(e => toast("Remove failed: " + e));
  const wrap = $("#impSeasons");
  wrap.innerHTML = "";
  const bySeason = new Map();
  for (const e of eps) { const s = e.season ?? 0; (bySeason.get(s) || bySeason.set(s, []).get(s)).push(e); }
  [...bySeason.keys()].sort((a, b) => a - b).forEach(season => {
    const seasonEps = bySeason.get(season);
    const head = el("div", "season-head");
    head.append(el("h3", "", season ? `Season ${season}` : "Other / ungrouped"));
    const sb = el("button", "danger", "Remove season");
    sb.onclick = () => removeIds(seasonEps.map(e => e.id)).then(() => openImpSeries(key)).catch(e => toast("Remove failed: " + e));
    head.append(sb);
    wrap.append(head);
    seasonEps.sort((a, b) => (a.episode || 0) - (b.episode || 0)).forEach(ep => {
      const row = el("div", "ep");
      const label = ep.episode ? `E${String(ep.episode).padStart(2, "0")} — ${ep.name}` : ep.name;
      row.append(el("span", "name", label));
      const btn = el("button", "danger", "Remove");
      btn.onclick = () => removeIds([ep.id]).then(() => openImpSeries(key)).catch(e => toast("Remove failed: " + e));
      row.append(btn);
      wrap.append(row);
    });
  });
  $("#impSeriesModal").classList.remove("hidden");
}
$("#closeImpSeries").onclick = () => $("#impSeriesModal").classList.add("hidden");

// --- Dispatcharr setup ----------------------------------------------------
$("#dispBtn").onclick = async () => {
  try {
    const s = await api("/api/xc-info");
    $("#dispUrl").value = s.server_url;
    const wrap = $("#dispAccounts");
    wrap.innerHTML = "";
    s.accounts.forEach((a, i) => {
      const head = el("label", "", `Account ${i + 1} (${i === 0 ? "screen 1" : "screen " + (i + 1)})`);
      wrap.append(head);
      const rowU = el("input"); rowU.type = "text"; rowU.readOnly = true; rowU.value = `username: ${a.username}`;
      const rowP = el("input"); rowP.type = "text"; rowP.readOnly = true; rowP.value = `password: ${a.password}`;
      wrap.append(rowU, rowP);
    });
    $("#dispModal").classList.remove("hidden");
  } catch (e) { toast("Failed: " + e); }
};
$("#closeDisp").onclick = () => $("#dispModal").classList.add("hidden");

// --- groups (sidebar) -----------------------------------------------------
let allGroups = [];
async function loadGroups() {
  const r = await api(`/api/groups?kind=${state.tab}`);
  allGroups = r.groups;
  renderGroups();
}
function renderGroups() {
  const filter = $("#groupFilter").value.toLowerCase();
  const ul = $("#groups");
  ul.innerHTML = "";
  const all = el("li", state.group === null ? "active" : "", "");
  all.append(el("span", "", "All categories"));
  all.onclick = () => { state.group = null; state.page = 1; renderGroups(); loadList(); };
  ul.append(all);
  for (const g of allGroups) {
    if (filter && !(g.name || "").toLowerCase().includes(filter)) continue;
    const li = el("li", state.group === g.name ? "active" : "");
    li.append(el("span", "", g.name || "(no category)"));
    li.append(el("span", "count", String(g.count)));
    li.onclick = () => { state.group = g.name; state.page = 1; renderGroups(); loadList(); };
    ul.append(li);
  }
}
$("#groupFilter").oninput = renderGroups;

// --- list -----------------------------------------------------------------
let searchTimer;
$("#search").oninput = e => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.q = e.target.value.trim(); state.page = 1; loadList(); }, 250);
};
$("#prev").onclick = () => { if (state.page > 1) { state.page--; loadList(); } };
$("#next").onclick = () => {
  if (state.page * state.pageSize < state.total) { state.page++; loadList(); }
};

const seasonText = (seasons, eps) =>
  seasons == null ? "…" : `${seasons} season${seasons === 1 ? "" : "s"} · ${eps} ep`;
let seriesBadges = {};
let listGen = 0;

async function loadSeriesCounts(keys, gen) {
  for (let i = 0; i < keys.length; i += 20) {
    if (gen !== listGen) return;  // user navigated away
    const chunk = keys.slice(i, i + 20);
    try {
      const r = await api("/api/series/counts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ series_keys: chunk }) });
      if (gen !== listGen) return;
      for (const [key, c] of Object.entries(r.counts)) {
        const b = seriesBadges[key];
        if (b) b.textContent = seasonText(c.seasons, c.episodes);
      }
    } catch (e) { /* leave "…" on failure */ }
  }
}

async function loadList() {
  const gen = ++listGen;
  seriesBadges = {};
  const list = $("#list");
  list.innerHTML = "";
  const params = new URLSearchParams({ page: state.page, page_size: state.pageSize });
  if (state.group) params.set("group", state.group);
  if (state.q) params.set("q", state.q);

  if (state.tab === "series") {
    const r = await api(`/api/series?${params}`);
    state.total = r.total;
    renderPager();
    if (!r.series.length) { list.append(el("div", "empty", "No series.")); return; }
    for (const s of r.series) list.append(seriesRow(s));
    const unknown = r.series.filter(s => s.total_seasons == null).map(s => s.series_key);
    if (unknown.length) loadSeriesCounts(unknown, gen);
  } else {
    params.set("kind", state.tab);
    const r = await api(`/api/items?${params}`);
    state.total = r.total;
    renderPager();
    if (!r.items.length) { list.append(el("div", "empty", "No items.")); return; }
    for (const it of r.items) list.append(itemRow(it));
  }
}

function renderPager() {
  $("#resultMeta").textContent = `${state.total.toLocaleString()} ${state.tab === "series" ? "series" : "items"}`;
  const pages = Math.max(1, Math.ceil(state.total / state.pageSize));
  $("#pageInfo").textContent = `Page ${state.page} / ${pages}`;
  $("#prev").disabled = state.page <= 1;
  $("#next").disabled = state.page >= pages;
}

function itemRow(it) {
  const row = el("div", "row");
  row.append(el("span", "name", it.name));
  row.append(el("span", "grp", it.group || ""));
  const btn = el("button", it.imported ? "imported" : "", it.imported ? "Imported ✓" : "Import");
  if (!it.imported) {
    btn.onclick = async () => {
      btn.disabled = true;
      try {
        await api("/api/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: [it.id] }) });
        btn.className = "imported"; btn.textContent = "Imported ✓"; btn.disabled = true;
        refreshStatus();
      } catch (e) { toast("Import failed: " + e); btn.disabled = false; }
    };
  }
  row.append(btn);
  return row;
}

function seriesRow(s) {
  const row = el("div", "row");
  const name = el("span", "name");
  name.append(document.createTextNode(s.name + " "));
  const tb = el("span", "badge", seasonText(s.total_seasons, s.total_episodes));
  if (s.total_seasons == null) seriesBadges[s.series_key] = tb;  // fill in lazily
  name.append(tb);
  if (s.imported) name.append(el("span", "badge imported-badge", `✓ ${s.imp_episodes} ep`));
  row.append(name);
  row.append(el("span", "grp", s.group || ""));
  const open = el("button", "", "Browse");
  open.onclick = () => openSeries(s);
  const imp = el("button", "primary", "Import all");
  imp.onclick = async () => {
    imp.disabled = true;
    try {
      const r = await api("/api/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ series_key: s.series_key }) });
      toast(`Imported ${r.imported}, skipped ${r.skipped_existing}`);
      refreshStatus();
    } catch (e) { toast("Import failed: " + e); } finally { imp.disabled = false; }
  };
  row.append(open);
  row.append(imp);
  return row;
}

// --- series modal ---------------------------------------------------------
let currentSeries = null;
async function openSeries(s) {
  currentSeries = s;
  $("#seriesTitle").textContent = s.name;
  const r = await api(`/api/series/detail?series_key=${encodeURIComponent(s.series_key)}`);
  const wrap = $("#seasons");
  wrap.innerHTML = "";
  for (const season of r.seasons) {
    const head = el("div", "season-head");
    head.append(el("h3", "", season.season ? `Season ${season.season}` : "Other / ungrouped"));
    const sb = el("button", "", "Import season");
    sb.onclick = async () => {
      sb.disabled = true;
      try {
        const res = await api("/api/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ series_key: s.series_key, season: season.season || null }) });
        toast(`Season: imported ${res.imported}, skipped ${res.skipped_existing}`);
        openSeries(s); refreshStatus();
      } catch (e) { toast("Import failed: " + e); sb.disabled = false; }
    };
    head.append(sb);
    wrap.append(head);
    for (const ep of season.episodes) {
      const row = el("div", "ep");
      const label = ep.episode ? `E${String(ep.episode).padStart(2, "0")} — ${ep.name}` : ep.name;
      row.append(el("span", "name", label));
      const btn = el("button", ep.imported ? "imported" : "", ep.imported ? "Imported ✓" : "Import");
      if (!ep.imported) {
        btn.onclick = async () => {
          btn.disabled = true;
          try {
            await api("/api/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ series_key: s.series_key, episode_ids: [ep.ep_id] }) });
            btn.className = "imported"; btn.textContent = "Imported ✓"; refreshStatus();
          } catch (e) { toast("Import failed: " + e); btn.disabled = false; }
        };
      }
      row.append(btn);
      wrap.append(row);
    }
  }
  $("#seriesModal").classList.remove("hidden");
}
$("#closeSeries").onclick = () => $("#seriesModal").classList.add("hidden");
$("#importWholeSeries").onclick = async () => {
  if (!currentSeries) return;
  try {
    const r = await api("/api/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ series_key: currentSeries.series_key }) });
    toast(`Imported ${r.imported}, skipped ${r.skipped_existing}`);
    openSeries(currentSeries); refreshStatus();
  } catch (e) { toast("Import failed: " + e); }
};

// --- init -----------------------------------------------------------------
(async function init() {
  const s = await refreshStatus();
  await loadGroups();
  await loadList();
  if (!s.source_url) openSource();   // first run: prompt for a URL
})();
