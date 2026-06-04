// Settings → Source: provider subscriptions (add/remove/save).
import { api, $, el, toast, fmtTime, jsonPost } from "./util.js";
import { refreshStatus, runSync } from "./status.js";

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

export async function renderSource() {
  const s = await api("/api/source");
  const list = $("#subsList");
  list.innerHTML = "";
  const subs = (s.subs && s.subs.length) ? s.subs : [{}];
  subs.forEach(sub => list.append(subRow(sub)));
  $("#sourceHint").textContent = s.last_sync ? `Last synced ${fmtTime(s.last_sync)}` : "Not synced yet.";
}

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
  await jsonPost("/api/source", { subs });
  await refreshStatus();
  return true;
}

$("#addSub").onclick = () => $("#subsList").append(subRow());
$("#saveSource").onclick = async () => {
  try { if (await saveSource()) toast("Source saved"); }
  catch (e) { toast("Save failed: " + e); }
};
$("#saveSyncSource").onclick = async () => {
  try { if (await saveSource()) await runSync(); }
  catch (e) { toast("Save failed: " + e); }
};
