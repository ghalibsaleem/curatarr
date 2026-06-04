// Entry point: wire cross-cutting controls (tabs, search) and bootstrap.
// Importing the feature modules also runs their own button wiring.
import { $ } from "./util.js";
import { state } from "./state.js";
import { refreshStatus } from "./status.js";
import { loadGroups, loadList } from "./browse.js";
import { loadImported, renderImported } from "./imported.js";
import { openSource } from "./source.js";
import "./dispatcharr.js";
import "./m3uimport.js";

// Tabs
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

// Search (server-side for browse tabs, in-memory for imported)
let searchTimer;
$("#search").oninput = e => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.q = e.target.value.trim();
    state.page = 1;
    if (state.tab === "imported") renderImported(); else loadList();
  }, 250);
};

// Bootstrap
(async function init() {
  const s = await refreshStatus();
  await loadGroups();
  await loadList();
  if (!s.source_url) openSource();  // first run: prompt for a source
})();
