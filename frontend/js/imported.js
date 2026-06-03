// Imported tab: grouped view with episode/season/series removal + in-memory search.
import { api, $, el, toast, jsonPost } from "./util.js";
import { state } from "./state.js";
import { refreshStatus } from "./status.js";

let impData = [];

async function removeIds(ids) {
  await jsonPost("/api/unimport", { ids });
  await refreshStatus();
  await loadImported();
}

export async function loadImported() {
  const r = await api("/api/imported");
  impData = r.imported;
  renderImported();
}

export function renderImported() {
  const q = (state.q || "").toLowerCase();
  const match = s => !q || (s || "").toLowerCase().includes(q);
  const list = $("#list");
  list.innerHTML = "";
  $("#pageInfo").textContent = ""; $("#prev").disabled = true; $("#next").disabled = true;
  if (!impData.length) {
    $("#resultMeta").textContent = "0 imported";
    list.append(el("div", "empty", "Nothing imported yet."));
    return;
  }
  const live = impData.filter(x => x.kind === "live" && match(x.name));
  const movies = impData.filter(x => x.kind === "movie" && match(x.name));
  const seriesMap = new Map();
  for (const e of impData.filter(x => x.kind === "series")) {
    const g = seriesMap.get(e.series_key) || { key: e.series_key, name: e.series_name || e.name, seasons: new Set(), eps: [] };
    g.seasons.add(e.season); g.eps.push(e); seriesMap.set(e.series_key, g);
  }
  const series = [...seriesMap.values()].filter(g => match(g.name)).sort((a, b) => a.name.localeCompare(b.name));
  const totalEps = series.reduce((n, g) => n + g.eps.length, 0);

  $("#resultMeta").textContent =
    `${live.length} live · ${movies.length} movies · ${series.length} series (${totalEps} ep)` + (q ? " · filtered" : "");

  if (live.length) { list.append(impHeader("Live")); live.forEach(it => list.append(impFlatRow(it))); }
  if (movies.length) { list.append(impHeader("Movies")); movies.forEach(it => list.append(impFlatRow(it))); }
  if (series.length) { list.append(impHeader("Series")); series.forEach(g => list.append(impSeriesRow(g))); }
  if (!live.length && !movies.length && !series.length) list.append(el("div", "empty", "No matches."));
}

const impHeader = t => el("div", "imp-section", t);

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
  $("#removeWholeSeries").onclick = () =>
    removeIds(eps.map(e => e.id)).then(() => $("#impSeriesModal").classList.add("hidden")).catch(e => toast("Remove failed: " + e));
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
