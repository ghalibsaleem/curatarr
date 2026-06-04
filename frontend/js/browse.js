// Browse tabs: category sidebar, paginated list, series detail modal, lazy counts.
import { api, $, el, toast, jsonPost } from "./util.js";
import { state } from "./state.js";
import { refreshStatus } from "./status.js";

// --- category sidebar -----------------------------------------------------
let allGroups = [];

export async function loadGroups() {
  const r = await api(`/api/groups?kind=${state.tab}`);
  allGroups = r.groups;
  renderGroups();
}

export function renderGroups() {
  const filter = $("#groupFilter").value.toLowerCase();
  const ul = $("#groups");
  ul.innerHTML = "";
  const all = el("li", state.group === null ? "active" : "");
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

// --- list + pager ---------------------------------------------------------
export const seasonText = (seasons, eps) =>
  seasons == null ? "…" : `${seasons} season${seasons === 1 ? "" : "s"} · ${eps} ep`;
let seriesBadges = {};
let listGen = 0;

async function loadSeriesCounts(keys, gen) {
  for (let i = 0; i < keys.length; i += 20) {
    if (gen !== listGen) return;  // user navigated away
    try {
      const r = await jsonPost("/api/series/counts", { series_keys: keys.slice(i, i + 20) });
      if (gen !== listGen) return;
      for (const [key, c] of Object.entries(r.counts)) {
        const b = seriesBadges[key];
        if (b) b.textContent = seasonText(c.seasons, c.episodes);
      }
    } catch (e) { /* leave "…" on failure */ }
  }
}

export async function loadList() {
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

// --- rows -----------------------------------------------------------------
function itemRow(it) {
  const row = el("div", "row");
  row.append(el("span", "name", it.name));
  row.append(el("span", "grp", it.group || ""));
  const btn = el("button", it.imported ? "imported" : "", it.imported ? "Imported ✓" : "Import");
  if (!it.imported) {
    btn.onclick = async () => {
      btn.disabled = true;
      try {
        await jsonPost("/api/import", { ids: [it.id] });
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
      const r = await jsonPost("/api/import", { series_key: s.series_key });
      toast(`Imported ${r.imported}, skipped ${r.skipped_existing}`);
      refreshStatus();
    } catch (e) { toast("Import failed: " + e); } finally { imp.disabled = false; }
  };
  row.append(open, imp);
  return row;
}

// --- series detail modal --------------------------------------------------
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
        const res = await jsonPost("/api/import", { series_key: s.series_key, season: season.season || null });
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
            await jsonPost("/api/import", { series_key: s.series_key, episode_ids: [ep.ep_id] });
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

// --- wiring ---------------------------------------------------------------
// #groupFilter is wired by main.js (tab-aware: browse vs imported sidebar).
$("#prev").onclick = () => { if (state.page > 1) { state.page--; loadList(); } };
$("#next").onclick = () => { if (state.page * state.pageSize < state.total) { state.page++; loadList(); } };
$("#closeSeries").onclick = () => $("#seriesModal").classList.add("hidden");
$("#importWholeSeries").onclick = async () => {
  if (!currentSeries) return;
  try {
    const r = await jsonPost("/api/import", { series_key: currentSeries.series_key });
    toast(`Imported ${r.imported}, skipped ${r.skipped_existing}`);
    openSeries(currentSeries); refreshStatus();
  } catch (e) { toast("Import failed: " + e); }
};
