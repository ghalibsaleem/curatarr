// Entry point: wire tabs + search, bootstrap. Importing the feature modules runs
// their own button wiring (Settings shell, source, xtream, import).
import { $ } from "./util.js";
import { state } from "./state.js";
import { refreshStatus } from "./status.js";
import { loadGroups, loadList, renderGroups } from "./browse.js";
import { loadImported, renderImported, renderImportedSidebar, resetImportedFilters } from "./imported.js";
import { openSettings } from "./settings.js";
import "./m3uimport.js";

// Tabs ↔ URL hash, so a refresh / back-forward keeps the current tab.
const TAB_TO_HASH = { live: "live", movie: "movies", series: "series", imported: "imported" };
const HASH_TO_TAB = { live: "live", movies: "movie", series: "series", imported: "imported" };
const tabFromHash = () => HASH_TO_TAB[(location.hash || "").replace(/^#/, "")];

function showTab(tab) {
  if (!TAB_TO_HASH[tab]) tab = "live";
  document.querySelectorAll(".tab").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  state.tab = tab;          // set before touching the hash so hashchange no-ops
  state.group = null;
  state.q = "";
  state.page = 1;
  $("#search").value = "";
  location.hash = TAB_TO_HASH[tab];
  if (tab === "imported") {
    resetImportedFilters();
    loadImported();
  } else {
    loadGroups();
    loadList();
  }
}

document.querySelectorAll(".tab").forEach(btn => {
  btn.onclick = () => showTab(btn.dataset.tab);
});

// Back/forward or manual hash edits.
window.addEventListener("hashchange", () => {
  const tab = tabFromHash();
  if (tab && tab !== state.tab) showTab(tab);
});

// Category-filter box: drives the browse sidebar or the imported sidebar.
$("#groupFilter").oninput = () =>
  state.tab === "imported" ? renderImportedSidebar() : renderGroups();

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

// Bootstrap — restore the tab from the URL hash (default: live).
(async function init() {
  const s = await refreshStatus();
  showTab(tabFromHash() || "live");
  if (!s.source_url) openSettings("source");  // first run: prompt for a source
})();
