// Settings → Account: show the signed-in user and change the password.
import { $, toast, jsonPost } from "./util.js";

export async function renderAccount() {
  $("#acctMsg").textContent = "";
  $("#acctCurrent").value = "";
  $("#acctNew").value = "";
  try {
    const me = await fetch("/api/auth/me").then(r => r.json());
    $("#acctUser").textContent = me.user ? me.user.username : "—";
  } catch {
    $("#acctUser").textContent = "—";
  }
}

$("#acctSave").onclick = async () => {
  const current_password = $("#acctCurrent").value;
  const new_password = $("#acctNew").value;
  $("#acctMsg").textContent = "";
  try {
    await jsonPost("/api/auth/password", { current_password, new_password });
    $("#acctCurrent").value = "";
    $("#acctNew").value = "";
    toast("Password changed");
  } catch (e) {
    $("#acctMsg").textContent = String(e);
  }
};
