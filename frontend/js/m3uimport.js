// Settings → Import: bulk-import an existing curated .m3u; result shown in-panel.
import { api, $, el, toast } from "./util.js";
import { refreshStatus } from "./status.js";

$("#importM3uBtn").onclick = () => $("#m3uFile").click();

$("#m3uFile").onchange = async e => {
  const f = e.target.files[0];
  if (!f) return;
  const btn = $("#importM3uBtn");
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = "Importing…";
  try {
    const fd = new FormData();
    fd.append("file", f, f.name);
    const r = await api("/api/import-m3u", { method: "POST", body: fd });
    renderResult(r);
    await refreshStatus();
  } catch (err) {
    toast("Import failed: " + err);
  } finally {
    btn.disabled = false;
    btn.textContent = label;
    e.target.value = "";  // allow re-selecting the same file
  }
};

function stat(k, v) {
  const d = el("div", "m3u-stat");
  d.append(el("span", "k", k), el("span", "v", String(v)));
  return d;
}

function renderResult(r) {
  const wrap = $("#m3uResult");
  wrap.innerHTML = "";
  wrap.append(stat("Entries in file", r.total));
  wrap.append(stat("Imported", r.imported));
  wrap.append(stat("Already imported", r.already));
  wrap.append(stat("Not found in catalogue", r.not_found));
  wrap.append(stat("Failed to parse", r.failed));
  const byKind = Object.entries(r.by_kind || {}).map(([k, v]) => `${v} ${k}`).join(", ");
  if (byKind) wrap.append(stat("New by type", byKind));
  if (r.not_found_samples && r.not_found_samples.length) {
    wrap.append(el("p", "hint", `Not found (${r.not_found_samples.length} of ${r.not_found}) — not in the current catalogue:`));
    const list = el("div", "m3u-missing");
    r.not_found_samples.forEach(n => list.append(el("div", "", n)));
    wrap.append(list);
  }
}
