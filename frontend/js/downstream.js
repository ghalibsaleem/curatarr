// Settings → Downstream: configure + trigger the Dispatcharr → Jellyfin sequence.
// Both account and library selections are multi-pick and fully re-editable: on
// reopen we show what's saved (by name), and re-discovery keeps them checked.
import { api, $, el, toast, jsonPost } from "./util.js";

let cfg = { dispatcharr: {}, jellyfin: {} };

export async function renderDownstream() {
  cfg = await api("/api/downstream/config");
  const d = cfg.dispatcharr || {}, j = cfg.jellyfin || {};
  $("#dsDispUrl").value = d.url || "";
  $("#dsDispUser").value = d.username || "";
  $("#dsDispPass").value = d.password || "";
  $("#dsJfUrl").value = j.url || "";
  $("#dsJfKey").value = j.api_key || "";

  // Show the saved selections (by name) before any re-discovery.
  renderChecks($("#dsDispAccounts"), [], savedPairs(d.account_ids, d.account_names));
  setOptions($("#dsJfTask"), j.task_id ? [{ value: j.task_id, label: j.task_name || j.task_id }] : [], j.task_id);
  renderChecks($("#dsJfLibs"), [], savedPairs(j.library_ids, j.library_names));
}

// --- generic helpers --------------------------------------------------------
function savedPairs(ids, names) {
  return (ids || []).map((id, i) => ({ id: String(id), name: (names || [])[i] || `id ${id}` }));
}

// discovered: [{id, name, sub}] ; saved: [{id, name}] (re-checks these)
function renderChecks(wrap, discovered, saved) {
  const savedById = new Map(saved.map(s => [String(s.id), s.name]));
  const seen = new Set();
  const rows = [];
  discovered.forEach(o => {
    const id = String(o.id); seen.add(id);
    rows.push({ id, name: o.name, sub: o.sub || "", checked: savedById.has(id) });
  });
  // Keep saved-but-not-(re)discovered selections visible so they aren't lost.
  saved.forEach(s => {
    const id = String(s.id);
    if (!seen.has(id)) rows.push({ id, name: s.name, sub: "saved", checked: true });
  });

  wrap.innerHTML = "";
  if (!rows.length) { wrap.append(el("p", "hint", "Discover to list options.")); return; }
  rows.forEach(r => {
    const row = el("label", "ds-lib");
    const cb = el("input"); cb.type = "checkbox"; cb.value = r.id;
    cb.dataset.name = r.name; cb.checked = r.checked;
    row.append(cb, el("span", "", `${r.name}${r.sub ? ` · ${r.sub}` : ""}`));
    wrap.append(row);
  });
}

function collectChecks(wrap) {
  const ids = [], names = [];
  wrap.querySelectorAll("input:checked").forEach(c => { ids.push(c.value); names.push(c.dataset.name); });
  return { ids, names };
}

function setOptions(sel, opts, selected) {
  sel.innerHTML = "";
  if (!opts.length) sel.append(new Option("— discover first —", ""));
  opts.forEach(o => sel.append(new Option(o.label, o.value)));
  if (selected != null) sel.value = String(selected);
}

function collect() {
  const acc = collectChecks($("#dsDispAccounts"));
  const lib = collectChecks($("#dsJfLibs"));
  const taskSel = $("#dsJfTask");
  return {
    dispatcharr: {
      url: $("#dsDispUrl").value.trim(),
      username: $("#dsDispUser").value.trim(),
      password: $("#dsDispPass").value,
      account_ids: acc.ids.map(Number),
      account_names: acc.names,
    },
    jellyfin: {
      url: $("#dsJfUrl").value.trim(),
      api_key: $("#dsJfKey").value,
      task_id: taskSel.value || "",
      task_name: taskSel.value ? taskSel.selectedOptions[0].text : "",
      library_ids: lib.ids,
      library_names: lib.names,
    },
  };
}

async function save(silent) {
  cfg = await jsonPost("/api/downstream/config", collect());
  if (!silent) toast("Downstream settings saved");
}

// --- discovery --------------------------------------------------------------
$("#dsDispDiscover").onclick = async () => {
  const btn = $("#dsDispDiscover"); btn.disabled = true;
  try {
    const r = await jsonPost("/api/downstream/dispatcharr/accounts", {
      url: $("#dsDispUrl").value.trim(),
      username: $("#dsDispUser").value.trim(),
      password: $("#dsDispPass").value,
    });
    const discovered = r.accounts.map(a => ({
      id: a.id, name: a.name, sub: a.type || "",
    }));
    const saved = savedPairs(cfg.dispatcharr?.account_ids, cfg.dispatcharr?.account_names);
    renderChecks($("#dsDispAccounts"), discovered, saved);
    toast(`Found ${discovered.length} account${discovered.length === 1 ? "" : "s"}`);
  } catch (e) { toast("Dispatcharr: " + e); }
  finally { btn.disabled = false; }
};

$("#dsJfDiscover").onclick = async () => {
  const btn = $("#dsJfDiscover"); btn.disabled = true;
  try {
    const r = await jsonPost("/api/downstream/jellyfin/discover", {
      url: $("#dsJfUrl").value.trim(),
      api_key: $("#dsJfKey").value,
    });
    const tasks = (r.tasks || []).map(t => ({ value: t.id, label: t.name }));
    const preTask = cfg.jellyfin?.task_id || (r.task && r.task.id) || "";
    setOptions($("#dsJfTask"), tasks, preTask);
    const discovered = (r.libraries || []).map(l => ({
      id: l.id, name: l.name, sub: l.type || "",
    }));
    const saved = savedPairs(cfg.jellyfin?.library_ids, cfg.jellyfin?.library_names);
    renderChecks($("#dsJfLibs"), discovered, saved);
    toast(`Found ${discovered.length} libraries`);
  } catch (e) { toast("Jellyfin: " + e); }
  finally { btn.disabled = false; }
};

// --- save + run -------------------------------------------------------------
$("#dsSave").onclick = async () => {
  try { await save(false); } catch (e) { toast("Save failed: " + e); }
};

$("#dsRun").onclick = async () => {
  const spin = $("#dsSpinner"), run = $("#dsRun");
  run.disabled = true; spin.classList.remove("hidden");
  $("#dsResult").innerHTML = "";
  try {
    await save(true);                       // run uses the saved config
    toast("Downstream sync started…");
    const r = await jsonPost("/api/downstream/run", {});
    renderResult(r);
    toast(r.ok ? "Downstream sync complete" : "Downstream sync finished with errors");
  } catch (e) { toast("Run failed: " + e); }
  finally { run.disabled = false; spin.classList.add("hidden"); }
};

function renderResult(r) {
  const box = $("#dsResult");
  box.innerHTML = "";
  const icon = { ok: "✓", error: "✕", skipped: "—" };
  (r.stages || []).forEach(s => {
    const row = el("div", `ds-stage ds-${s.status}`);
    row.append(el("span", "ds-ic", icon[s.status] || "•"),
      el("span", "ds-name", s.name),
      el("span", "ds-msg", s.message || ""));
    box.append(row);
  });
}
