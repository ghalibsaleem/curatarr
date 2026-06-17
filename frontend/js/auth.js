// UI authentication gate: shows a login (or first-run setup) overlay until a
// valid session exists, and re-shows it when a session expires (401).
import { $, setUnauthorizedHandler } from "./util.js";

let resolveGate = null;

// Resolves once authenticated; called by main.js before bootstrapping the app.
export function ensureAuth() {
  return new Promise(async (resolve) => {
    resolveGate = resolve;
    try {
      const me = await fetch("/api/auth/me").then(r => r.json());
      if (me.user) { finish(); return; }
      showAuth(me.needs_setup);
    } catch {
      showAuth(false);
    }
  });
}

function finish() {
  $("#authOverlay").classList.add("hidden");
  $("#authPass").value = "";
  if (resolveGate) { resolveGate(); resolveGate = null; }
}

function showAuth(needsSetup) {
  const ov = $("#authOverlay");
  ov.dataset.mode = needsSetup ? "setup" : "login";
  $("#authTitle").textContent = needsSetup ? "Create admin account" : "Sign in";
  $("#authSubmit").textContent = needsSetup ? "Create account" : "Sign in";
  $("#authHint").textContent = needsSetup
    ? "First run — create the admin account that protects this instance."
    : "";
  $("#authUser").autocomplete = needsSetup ? "username" : "username";
  $("#authPass").autocomplete = needsSetup ? "new-password" : "current-password";
  $("#authError").textContent = "";
  ov.classList.remove("hidden");
  $("#authUser").focus();
}

// On a 401 during normal use (expired session), re-show login. After a fresh
// login here we reload so the app re-fetches its state cleanly.
setUnauthorizedHandler(() => {
  if (resolveGate === null) resolveGate = () => location.reload();
  showAuth(false);
});

async function submit(e) {
  if (e) e.preventDefault();
  const mode = $("#authOverlay").dataset.mode === "setup" ? "setup" : "login";
  const username = $("#authUser").value.trim();
  const password = $("#authPass").value;
  $("#authError").textContent = "";
  // Use a raw fetch (not the shared api helper) so a bad-credentials 401 shows
  // the real message instead of triggering the global unauthorized handler.
  try {
    const r = await fetch(`/api/auth/${mode}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { $("#authError").textContent = data.detail || "Failed"; return; }
    finish();
  } catch (err) {
    $("#authError").textContent = String(err);
  }
}

$("#authForm").addEventListener("submit", submit);
$("#logoutBtn").onclick = async () => {
  await fetch("/api/auth/logout", { method: "POST" });
  location.reload();
};
