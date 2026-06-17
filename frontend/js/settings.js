// Settings popup: left-nav sections (Overview / Source / Import / Xtream / Downstream / Schedule).
import { $ } from "./util.js";
import { refreshStatus } from "./status.js";
import { renderSource } from "./source.js";
import { renderXtream } from "./xtream.js";
import { renderDownstream } from "./downstream.js";
import { renderSchedule } from "./schedule.js";
import { renderAccount } from "./account.js";

// What to refresh when a section becomes visible.
const onShow = {
  overview: refreshStatus,
  source: renderSource,
  xtream: renderXtream,
  import: () => {},
  downstream: renderDownstream,
  schedule: renderSchedule,
  account: renderAccount,
};

function showSection(name) {
  document.querySelectorAll("#settingsNav li").forEach(li =>
    li.classList.toggle("active", li.dataset.section === name));
  document.querySelectorAll("#settingsContent .panel").forEach(p =>
    p.classList.toggle("hidden", p.dataset.panel !== name));
  (onShow[name] || (() => {}))();
}

export function openSettings(section = "overview") {
  showSection(section);
  $("#settingsModal").classList.remove("hidden");
}

document.querySelectorAll("#settingsNav li").forEach(li => {
  li.onclick = () => showSection(li.dataset.section);
});
$("#settingsBtn").onclick = () => openSettings("overview");
$("#closeSettings").onclick = () => $("#settingsModal").classList.add("hidden");
