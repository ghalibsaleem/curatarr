// Low-level helpers shared across modules.

export const api = (p, opts) => fetch(p, opts).then(r => {
  if (!r.ok) return r.json().then(e => Promise.reject(e.detail || r.statusText));
  return r.json();
});

export const $ = sel => document.querySelector(sel);

export const el = (tag, cls, txt) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt != null) e.textContent = txt;
  return e;
};

export function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 2600);
}

export const fmtBytes = n => {
  n = Number(n);
  if (!n) return "";
  const u = ["B", "KB", "MB", "GB"]; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
};

export const fmtTime = t => t ? t.replace("T", " ").replace("+00:00", "Z") : "";

export const jsonPost = (url, body) =>
  api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
