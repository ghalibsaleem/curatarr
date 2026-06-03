// Dispatcharr-setup modal: shows the Xtream account(s) to add in Dispatcharr.
import { api, $, el, toast } from "./util.js";

$("#dispBtn").onclick = async () => {
  try {
    const s = await api("/api/xc-info");
    $("#dispUrl").value = s.server_url;
    const wrap = $("#dispAccounts");
    wrap.innerHTML = "";
    s.accounts.forEach((a, i) => {
      wrap.append(el("label", "", `Account ${i + 1} (screen ${i + 1})`));
      const u = el("input"); u.type = "text"; u.readOnly = true; u.value = `username: ${a.username}`;
      const p = el("input"); p.type = "text"; p.readOnly = true; p.value = `password: ${a.password}`;
      wrap.append(u, p);
    });
    $("#dispModal").classList.remove("hidden");
  } catch (e) { toast("Failed: " + e); }
};
$("#closeDisp").onclick = () => $("#dispModal").classList.add("hidden");
