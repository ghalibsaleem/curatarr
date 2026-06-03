// Status bar + Sync.
import { api, $, toast, fmtBytes, fmtTime, jsonPost } from "./util.js";
import { openSource } from "./source.js";
import { loadGroups, loadList } from "./browse.js";

let hasSource = false;

export async function refreshStatus() {
  const s = await api("/api/status");
  const c = s.counts;
  hasSource = !!s.source_url;
  let txt = `live ${c.live} · movies ${c.movie} · series ${c.series} · imported ${c.imported}`;
  if (!s.source_url) txt = "no source set — click Source…";
  else if (s.last_sync) txt += ` · synced ${fmtTime(s.last_sync)}`;
  else txt += " · not synced yet — click Sync";
  $("#status").textContent = txt;
  return s;
}

export async function runSync() {
  if (!hasSource) { openSource(); toast("Set a source first"); return; }
  const btn = $("#syncBtn");
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = "Syncing…";
  try {
    const r = await jsonPost("/api/sync", {});
    toast(`Synced · parsed ${r.parsed.toLocaleString()} entries`);
    await refreshStatus();
    await loadGroups();
    await loadList();
  } catch (e) {
    toast("Sync failed: " + e);
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

$("#syncBtn").onclick = runSync;
