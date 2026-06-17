// Settings → Schedule: configure the scheduled auto-sync (frequency + time +
// timezone). The UI builds a structured config; the server turns it into cron
// and computes the next run, which we preview live.
import { api, $, el, toast, jsonPost, fmtTime } from "./util.js";

const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]; // index = cron weekday
let selectedDays = [0];

export async function renderSchedule() {
  buildTimezones();
  buildDayOfMonth();
  buildDayToggles();

  const s = await api("/api/schedule");
  const c = s.config;
  $("#schEnabled").checked = !!c.enabled;
  $("#schFreq").value = c.frequency;
  $("#schTime").value = c.time;
  $("#schTz").value = c.tz;
  $("#schDownstream").checked = !!c.downstream;
  $("#schDom").value = String(c.day_of_month || 1);
  selectedDays = (c.days && c.days.length) ? c.days.slice() : [0];

  syncFreqUI();
  renderNext(s.next_sync);
  renderLast(s.last_result);
}

// --- field builders ---------------------------------------------------------
function buildTimezones() {
  const sel = $("#schTz");
  if (sel.options.length) return; // build once
  const zones = (Intl.supportedValuesOf && Intl.supportedValuesOf("timeZone")) || ["UTC"];
  if (!zones.includes("UTC")) zones.unshift("UTC");
  const localTz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  zones.forEach(z => sel.append(new Option(z, z)));
  sel.value = zones.includes(localTz) ? localTz : "UTC";
}

function buildDayOfMonth() {
  const sel = $("#schDom");
  if (sel.options.length) return;
  for (let d = 1; d <= 31; d++) sel.append(new Option(String(d), String(d)));
}

function buildDayToggles() {
  const wrap = $("#schDays");
  if (wrap.childElementCount) return;
  DOW.forEach((name, i) => {
    const b = el("button", "sch-day", name);
    b.type = "button";
    b.dataset.dow = String(i);
    b.onclick = () => toggleDay(i);
    wrap.append(b);
  });
}

// --- frequency-driven selection rules --------------------------------------
function freq() { return $("#schFreq").value; }

function toggleDay(i) {
  const f = freq();
  if (f === "weekly") {
    selectedDays = [i];                       // exactly one
  } else if (f === "twice-weekly") {
    if (selectedDays.includes(i)) selectedDays = selectedDays.filter(d => d !== i);
    else if (selectedDays.length < 2) selectedDays = [...selectedDays, i].sort();
    else { toast("Pick exactly two days"); return; }
  }
  paintDays();
  preview();
}

function paintDays() {
  $("#schDays").querySelectorAll(".sch-day").forEach(b =>
    b.classList.toggle("active", selectedDays.includes(Number(b.dataset.dow))));
}

function syncFreqUI() {
  const f = freq();
  const showDays = f === "weekly" || f === "twice-weekly";
  $("#schDaysRow").classList.toggle("hidden", !showDays);
  $("#schDomRow").classList.toggle("hidden", f !== "monthly");
  $("#schDaysLabel").textContent = f === "twice-weekly" ? "Days (pick two)" : "Day";
  // Normalize the selection to the new frequency's arity.
  if (f === "weekly" && selectedDays.length !== 1) selectedDays = [selectedDays[0] ?? 0];
  if (f === "twice-weekly" && selectedDays.length !== 2)
    selectedDays = [...new Set([...selectedDays, 0, 3])].slice(0, 2).sort();
  paintDays();
}

// --- config + preview -------------------------------------------------------
function collect() {
  return {
    enabled: $("#schEnabled").checked,
    frequency: freq(),
    time: $("#schTime").value || "03:00",
    days: selectedDays,
    day_of_month: Number($("#schDom").value || 1),
    tz: $("#schTz").value || "UTC",
    downstream: $("#schDownstream").checked,
  };
}

function renderNext(iso) {
  $("#schNext").textContent = "Next run: " + (iso ? fmtTime(iso) : "—");
}

let previewTimer;
function preview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(async () => {
    try {
      const r = await jsonPost("/api/schedule/preview", collect());
      renderNext(r.next_sync);
    } catch (e) {
      $("#schNext").textContent = "Next run: " + e;
    }
  }, 250);
}

function renderLast(last) {
  const box = $("#schLast");
  box.innerHTML = "";
  if (!last) return;
  const icon = { ok: "✓", error: "✕", skipped: "—" };
  box.append(el("p", "hint", `Last auto-sync: ${fmtTime(last.at)} · ${last.ok ? "ok" : "failed"}`));
  (last.stages || []).forEach(s => {
    const row = el("div", `ds-stage ds-${s.status}`);
    row.append(el("span", "ds-ic", icon[s.status] || "•"),
      el("span", "ds-name", s.name),
      el("span", "ds-msg", s.message || ""));
    box.append(row);
  });
}

// --- wiring -----------------------------------------------------------------
$("#schFreq").onchange = () => { syncFreqUI(); preview(); };
["#schTime", "#schTz", "#schDom", "#schEnabled", "#schDownstream"].forEach(sel =>
  $(sel).addEventListener("change", preview));

$("#schSave").onclick = async () => {
  try {
    const s = await jsonPost("/api/schedule", collect());
    renderNext(s.next_sync);
    renderLast(s.last_result);
    toast(s.config.enabled ? "Auto-sync schedule saved" : "Auto-sync disabled");
  } catch (e) {
    toast("Save failed: " + e);
  }
};
