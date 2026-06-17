// App version, Overview stats, and Sync.
import { api, $, el, fmtTime, jsonPost, toast } from "./util.js";
import { loadGroups, loadList } from "./browse.js";
import { openSettings } from "./settings.js";

let hasSource = false;

export async function refreshStatus() {
  const s = await api("/api/status");
  const c = s.counts;
  hasSource = !!s.source_url;
  if (s.version) $("#appVersion").textContent = "v" + s.version;
  const ov = $("#ovStats");
  if (ov) {
    ov.innerHTML = "";
    const row = (k, v) => {
      const d = el("div", "ov-row");
      d.append(el("span", "k", k), el("span", "v", String(v)));
      return d;
    };
    ov.append(row("Live", c.live.toLocaleString()));
    ov.append(row("Movies", c.movie.toLocaleString()));
    ov.append(row("Series", c.series.toLocaleString()));
    ov.append(row("Imported", c.imported.toLocaleString()));
    ov.append(row("Source", s.source_url
      ? `${s.source_url} · ${s.sub_count} sub${s.sub_count === 1 ? "" : "s"}` : "not set"));
    ov.append(row("Last sync", s.last_sync ? fmtTime(s.last_sync) : "never"));
    if (s.auto_sync) {
      ov.append(row("Next sync", s.next_sync ? fmtTime(s.next_sync) : "—"));
    }
    if (s.last_auto_sync) {
      const a = s.last_auto_sync;
      ov.append(row("Last auto-sync", `${fmtTime(a.at)} · ${a.ok ? "ok" : "failed"}`));
    }
  }
  return s;
}

export async function runSync() {
  if (!hasSource) { openSettings("source"); toast("Add a subscription first"); return; }
  const spin = $("#syncSpinner");
  const btns = [$("#syncBtn"), $("#syncBtnOverview")].filter(Boolean);
  btns.forEach(b => { b.disabled = true; });
  if (spin) spin.classList.remove("hidden");   // header indicator (any trigger)
  toast("Sync started…");
  try {
    const r = await jsonPost("/api/sync", {});
    toast(`Synced · ${r.parsed.toLocaleString()} entries`);
    await refreshStatus();
    await loadGroups();
    await loadList();
  } catch (e) {
    toast("Sync failed: " + e);
  } finally {
    btns.forEach(b => { b.disabled = false; });
    if (spin) spin.classList.add("hidden");
  }
}

$("#syncBtn").onclick = runSync;
$("#syncBtnOverview").onclick = runSync;
